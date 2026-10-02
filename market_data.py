"""
market_data.py — gets the list of stocks and their price history.

Data sources (all free):
  - Wikipedia: current S&P 500 and Nasdaq-100 member lists
  - yfinance:  daily prices + company info from Yahoo Finance
"""

import pickle
from concurrent.futures import ThreadPoolExecutor
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

HEADERS = {"User-Agent": "Mozilla/5.0 (stock-dip-alerts personal project)"}
FALLBACK_FILE = Path(__file__).parent / "tickers_fallback.txt"
LOAD_WARNINGS: list[str] = []      # names of stock lists that failed to load this run


TICKER_IN_CELL = r"\b([A-Z]{1,5}(?:[.-][A-Z])?)\b"   # first ticker-shaped word: AAPL, BRK.B
NAME_WORDS = ("security", "company", "name")          # how the tables label the company column


def _clean_name(text) -> str:
    """Remove Wikipedia footnote markers: 'Apple Inc.[3]' -> 'Apple Inc.'"""
    return pd.Series([str(text)]).str.replace(r"\[.*?\]", "", regex=True).str.strip()[0]


def _tickers_from_wikipedia(url: str, min_rows: int) -> dict[str, str]:
    """
    Find the table on a Wikipedia page that has a Symbol/Ticker column.
    Returns {ticker: company name}.
    Forgiving on purpose: header names can carry footnotes ("Ticker[5]"),
    come through as two-level headers, or cells can hold extra text
    ("Nasdaq: ADBE"), so we match loosely and pull out the ticker-shaped word.
    """
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()            # a blocked request fails loudly, not silently
    seen = []
    for table in pd.read_html(StringIO(response.text)):
        names = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in table.columns]
        seen.append(f"{len(table)} rows: {names[:4]}")
        name_col = next((c for c, n in zip(table.columns, names)
                         if any(w in n.lower() for w in NAME_WORDS)), None)
        for col, name in zip(table.columns, names):
            if "ticker" not in name.lower() and "symbol" not in name.lower():
                continue
            tickers = table[col].astype(str).str.extract(TICKER_IN_CELL)[0]
            company = table[name_col] if name_col is not None else tickers
            result = {t: _clean_name(c) for t, c in zip(tickers, company) if isinstance(t, str)}
            if len(result) >= min_rows:
                return result
    raise ValueError(f"No ticker table found at {url}. Tables seen: {seen[:6]}")


def _tickers_from_nasdaq_api() -> dict[str, str]:
    """Backup source for the Nasdaq-100: nasdaq.com, the company that runs the index."""
    response = requests.get(
        "https://api.nasdaq.com/api/quote/list-type/nasdaq100",
        headers={**HEADERS, "Accept": "application/json"}, timeout=30)
    response.raise_for_status()
    rows = response.json()["data"]["data"]["rows"]
    result = {r["symbol"].strip(): (r.get("companyName") or r["symbol"]).strip()
              for r in rows if r.get("symbol")}
    if len(result) < 90:
        raise ValueError(f"nasdaq.com returned only {len(result)} tickers")
    return result


def get_universe_with_names() -> dict[str, str]:
    """S&P 500 + Nasdaq-100 as {ticker: company name}, ~520 stocks."""
    LOAD_WARNINGS.clear()
    # Each list has one or more sources, tried in order until one works.
    sources = {
        "S&P 500": [lambda: _tickers_from_wikipedia(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 400)],
        "Nasdaq-100": [lambda: _tickers_from_wikipedia("https://en.wikipedia.org/wiki/Nasdaq-100", 90),
                       _tickers_from_nasdaq_api],
    }
    found = {}
    for label, attempts in sources.items():
        errors = []
        for attempt in attempts:
            try:
                found.update(attempt())
                break
            except Exception as err:
                errors.append(str(err))
        else:
            # Only warn if EVERY source for this list failed
            print(f"Warning: could not load {label} list. Errors: {' | '.join(errors)}")
            LOAD_WARNINGS.append(label)        # lets the web page show it too

    if found:
        # Yahoo writes class shares with a dash: BRK.B -> BRK-B
        universe = {t.replace(".", "-"): name for t, name in sorted(found.items())}
        FALLBACK_FILE.write_text("\n".join(f"{t}\t{n}" for t, n in universe.items()),
                                 encoding="utf-8")   # keep a backup copy
        return universe

    print("Could not load any lists from Wikipedia; using fallback file.")
    if not FALLBACK_FILE.exists():
        raise SystemExit("No fallback ticker list yet. Run once with internet access "
                         "to create tickers_fallback.txt, then commit it to GitHub.")
    universe = {}
    for line in FALLBACK_FILE.read_text(encoding="utf-8").splitlines():
        ticker, _, name = line.partition("\t")      # older backups have tickers only
        if ticker.strip():
            universe[ticker.strip()] = name.strip() or ticker.strip()
    return universe


def get_universe() -> list[str]:
    """Just the tickers (what scanner.py and backtest.py need)."""
    return list(get_universe_with_names())


def get_market_caps(tickers: list[str]) -> dict[str, float | None]:
    """
    Market cap (share price x number of shares) for each stock, in dollars.
    One small Yahoo request per stock, so we run 16 at a time in parallel.
    A stock whose lookup fails gets None ("unknown") instead of being dropped.
    """
    def one(t):
        try:
            return t, yf.Ticker(t).fast_info["market_cap"]
        except Exception:
            return t, None
    with ThreadPoolExecutor(max_workers=16) as pool:
        return dict(pool.map(one, tickers))


MIN_HISTORY_DAYS = 250   # need ~1 year for the 200-day average
BATCH_SIZE = 100         # smaller batches = fewer silent refusals from Yahoo


def _download_batch(tickers: list[str], period: str, threads: bool = True) -> dict[str, pd.DataFrame]:
    """One yf.download call. Returns only tickers that came back with data."""
    raw = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                      auto_adjust=True, threads=threads, progress=False)
    got = {}
    for t in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if t not in raw.columns.get_level_values(0):
                continue
            df = raw[t]
        else:                      # a single ticker can come back without the ticker level
            df = raw
        df = df.dropna(how="all")
        if not df.empty:
            got[t] = df
    return got


def download_prices(tickers: list[str], period: str = "2y",
                    cache_file: str | None = None, max_age_hours: float = 20) -> dict[str, pd.DataFrame]:
    """
    Downloads daily prices. Returns {ticker: DataFrame with Open/High/Low/Close/Volume}.
    auto_adjust=True corrects old prices for splits/dividends so a 2-for-1
    split doesn't look like a 50% crash.

    Reliability steps (Yahoo sometimes silently refuses part of a big request):
      1. download in batches of 100 instead of 500 at once
      2. retry anything that failed, slowly, one batch without parallel threads
      3. print exactly how many stocks we got, so you can't be fooled

    cache_file: if given, prices are saved to that file and re-used for
    max_age_hours. The backtest uses this so every run sees IDENTICAL data,
    which means a change in results is caused by your settings, not by Yahoo.
    """
    cache = Path(__file__).parent / cache_file if cache_file else None
    if cache and cache.exists():
        age_hours = (time.time() - cache.stat().st_mtime) / 3600
        if age_hours < max_age_hours:
            with open(cache, "rb") as f:
                prices = pickle.load(f)
            print(f"Using saved prices from {cache.name} ({age_hours:.1f} hours old, "
                  f"{len(prices)} stocks). Delete the file or use --refresh to re-download.")
            return {t: df for t, df in prices.items() if t in tickers}

    got = {}
    for i in range(0, len(tickers), BATCH_SIZE):
        got.update(_download_batch(tickers[i:i + BATCH_SIZE], period))

    missing = [t for t in tickers if t not in got]
    if missing:
        print(f"Retrying {len(missing)} stocks that didn't download...")
        time.sleep(5)
        got.update(_download_batch(missing, period, threads=False))
        missing = [t for t in tickers if t not in got]

    prices = {t: df for t, df in got.items() if len(df) >= MIN_HISTORY_DAYS}
    too_short = len(got) - len(prices)
    print(f"Downloaded {len(prices)} of {len(tickers)} stocks"
          + (f" ({too_short} skipped: under 1 year of history)" if too_short else "")
          + (f". Still failed: {', '.join(missing)}" if missing else ""))

    if cache:
        with open(cache, "wb") as f:
            pickle.dump(prices, f)
    return prices


def get_company_details(ticker: str) -> dict:
    """
    Fundamentals + news for ONE stock. This is slow (one web call per stock),
    so we only call it for the handful of stocks that already passed the
    price-based rules.
    """
    tk = yf.Ticker(ticker)
    details = {"ticker": ticker, "name": ticker, "market_cap": None,
               "profitable": None, "sector": "", "next_earnings": None, "news": []}
    try:
        info = tk.info
        details["name"] = info.get("shortName") or ticker
        details["market_cap"] = info.get("marketCap")
        details["sector"] = info.get("sector", "")
        eps = info.get("trailingEps")
        margin = info.get("profitMargins")
        if eps is not None or margin is not None:
            details["profitable"] = (eps or 0) > 0 or (margin or 0) > 0
    except Exception:
        pass

    try:
        cal = tk.calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if dates:
            details["next_earnings"] = pd.Timestamp(dates[0]).date()
    except Exception:
        pass

    try:
        for item in (tk.news or [])[:3]:
            # yfinance has used two different news formats over time
            content = item.get("content", item)
            title = content.get("title")
            link = (content.get("canonicalUrl") or {}).get("url") or content.get("link")
            if title:
                details["news"].append((title, link))
    except Exception:
        pass
    return details
