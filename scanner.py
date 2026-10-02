"""
scanner.py — runs once a day after the market closes.

Steps:
  1. Load ~520 tickers (S&P 500 + Nasdaq-100)
  2. Download 2 years of daily prices
  3. Run the 4-layer rules from signals.py on each stock
  4. For stocks that pass, check fundamentals (big + profitable) and earnings date
  5. Email the survivors to you

Run locally (PowerShell):
    python scanner.py --dry-run      # prints the email instead of sending it
    python scanner.py                # sends the email (needs env variables)
"""

import argparse
import os
from datetime import date

import pandas as pd

import subscribers
from mailer import send_email
from market_data import download_prices, get_company_details, get_universe
from signals import compute_signals

MIN_MARKET_CAP = 10e9          # $10 billion+: large, established companies
SKIP_IF_EARNINGS_WITHIN = 5    # days; earnings can swing a stock either way
SEND_EMAIL_WHEN_EMPTY = False  # True = get a "nothing today" email (proves it's running)
MAX_ALERTS = 5                 # only email the best N, ranked by reward-to-risk

# Text-only brand header: email apps often block images, but colors always show
BRAND_HEADER = ("<div style='background:#1F2A36;padding:14px 18px;border-bottom:4px solid #D4A017'>"
                "<span style='color:#F5EBDD;font-family:Georgia,serif;font-size:22px;"
                "letter-spacing:4px;font-weight:bold'>&#9733; KULDEEP</span>"
                "<span style='color:#BF5700;font-family:Georgia,serif;font-size:14px;"
                "letter-spacing:5px'>&nbsp;&nbsp;INVESTMENTS</span></div>")


def scan(prices: dict[str, pd.DataFrame]) -> list[dict]:
    """Returns stocks whose LAST day passed every price-based rule."""
    spy = prices.get("SPY")
    hits = []
    for ticker, df in prices.items():
        if ticker == "SPY":
            continue
        sig = compute_signals(df)
        today = sig.iloc[-1]
        if not today["signal"]:
            continue
        row = {
            "ticker": ticker,
            "price": today["close"],
            "dip": today["dip"],
            "rsi": today["rsi"],
            "upside": today["upside_to_peak"],
            "peak": today["peak"],
            "stop": today["stop_level"],
            "reward_risk": today["reward_risk"],
        }
        # How much did the whole market move over the same window?
        # If SPY also fell a lot, the drop was market-wide, not company-specific.
        if spy is not None and len(spy) > 60:
            row["market_move"] = spy["Close"].iloc[-1] / spy["Close"].iloc[-60] - 1
        hits.append(row)
    return hits


def add_fundamentals(hits: list[dict]) -> list[dict]:
    """Keep only big, profitable companies without earnings in the next few days."""
    final = []
    for h in hits:
        d = get_company_details(h["ticker"])
        h.update(d)
        if d["market_cap"] is not None and d["market_cap"] < MIN_MARKET_CAP:
            continue
        if d["profitable"] is False:
            continue
        if d["next_earnings"]:
            days_away = (d["next_earnings"] - date.today()).days
            if 0 <= days_away <= SKIP_IF_EARNINGS_WITHIN:
                continue
        final.append(h)
    # Best first: most potential gain per unit of risk. Keep only the top few.
    return sorted(final, key=lambda h: h["reward_risk"], reverse=True)[:MAX_ALERTS]


def build_email(stocks: list[dict]) -> tuple[str, str]:
    today = date.today().strftime("%b %d, %Y")
    if not stocks:
        return (f"Dip scanner {today}: no signals",
                BRAND_HEADER + "<p>No stocks passed all the rules today. That's normal &mdash; "
                "good setups are rare.</p>")

    subject = f"Dip scanner {today}: top {len(stocks)} - " + ", ".join(s["ticker"] for s in stocks)
    parts = [BRAND_HEADER, f"<h2 style='color:#1F2A36'>Best buy-zone candidates for {today}</h2>",
             "<p>Ranked best first by reward-to-risk. Read the news before acting: "
             "a short-term dip is a chance, a broken business is a trap.</p>"]
    for rank, s in enumerate(stocks, start=1):
        cap = f"${s['market_cap'] / 1e9:,.0f}B" if s.get("market_cap") else "n/a"
        mkt = f"{s['market_move']:+.1%}" if "market_move" in s else "n/a"
        news = "".join(f'<li><a href="{link}">{title}</a></li>' if link else f"<li>{title}</li>"
                       for title, link in s.get("news", [])) or "<li>No headlines found</li>"
        parts.append(f"""
        <hr><h3>#{rank} <a href="https://finance.yahoo.com/quote/{s['ticker']}">{s['ticker']}</a> &mdash; {s.get('name', '')}</h3>
        <table cellpadding="4">
          <tr><td>Price</td><td><b>${s['price']:.2f}</b></td></tr>
          <tr><td>Reward-to-risk</td><td><b>{s['reward_risk']:.1f} to 1</b></td></tr>
          <tr><td>Drop from recent high</td><td>-{s['dip']:.0%} (high ${s['peak']:.2f})</td></tr>
          <tr><td>Room back to high</td><td>+{s['upside']:.0%}</td></tr>
          <tr><td>RSI</td><td>{s['rsi']:.0f}</td></tr>
          <tr><td>Stop-loss idea (10-day low)</td><td>${s['stop']:.2f} ({s['stop'] / s['price'] - 1:.0%})</td></tr>
          <tr><td>Market (S&amp;P 500) last ~3 months</td><td>{mkt}</td></tr>
          <tr><td>Market cap / sector</td><td>{cap} / {s.get('sector', '')}</td></tr>
          <tr><td>Next earnings</td><td>{s.get('next_earnings') or 'unknown'}</td></tr>
        </table>
        <p><b>Why did it drop? Recent headlines:</b></p><ul>{news}</ul>""")
    parts.append("<hr><p style='color:gray'>Automated screen, not financial advice.</p>")
    return subject, "".join(parts)


def send_to_everyone(subject: str, html: str, has_signals: bool) -> None:
    """
    Owner gets every email (including 'no signals' days if switched on).
    Confirmed subscribers get signal days only, one email each, so every
    person gets their own unsubscribe link and nobody sees anyone else's address.
    """
    owner = os.environ.get("EMAIL_TO", os.environ["EMAIL_ADDRESS"])
    send_email(owner, subject, html)
    print(f"Email sent to owner {owner}")

    if not has_signals or not subscribers.is_configured():
        return
    app_url = os.environ.get("APP_URL", "").rstrip("/")
    sent = 0
    for person in subscribers.confirmed_list():
        if person["email"] == owner.lower():
            continue                                  # owner already got it
        footer = (f"<p style='color:gray;font-size:12px'>You signed up at Kuldeep Investments. "
                  f"<a href='{app_url}/?unsubscribe={person['token']}'>Unsubscribe</a></p>")
        try:
            send_email(person["email"], subject, html + footer)
            sent += 1
        except Exception as err:                      # one bad address shouldn't stop the rest
            print(f"Could not email a subscriber: {err}")
    print(f"Emailed {sent} subscribers")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print instead of emailing")
    args = parser.parse_args()

    tickers = get_universe()
    print(f"Scanning {len(tickers)} stocks...")
    prices = download_prices(tickers + ["SPY"])
    print(f"Got price data for {len(prices)} stocks")

    hits = scan(prices)
    print(f"{len(hits)} passed the price rules: {[h['ticker'] for h in hits]}")
    stocks = add_fundamentals(hits)
    print(f"{len(stocks)} passed fundamentals: {[s['ticker'] for s in stocks]}")

    subject, html = build_email(stocks)
    if args.dry_run:
        print("\n" + subject + "\n" + html)
    elif stocks or SEND_EMAIL_WHEN_EMPTY:
        send_to_everyone(subject, html, has_signals=bool(stocks))
    else:
        print("No signals today, no email sent.")


if __name__ == "__main__":
    main()
