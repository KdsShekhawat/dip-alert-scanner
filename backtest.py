"""
backtest.py — "how would these rules have done in the past?"

For every day in the last 3 years where a signal fired, it plays out a trade
for every COMBINATION of stop-loss and profit target over the next 20 trading
days (~4 weeks):
  - price touches the target first -> you win the target %
  - price touches the stop first   -> you lose down to the stop
  - neither within 20 days         -> you sell at the day-20 close

Run (PowerShell):
    python backtest.py             # uses saved prices if less than 20 hours old
    python backtest.py --refresh   # force a fresh download

Honest caveats:
  - Only price rules are tested (old fundamentals/news aren't available free).
  - Survivorship bias: we test TODAY's index members, which are the
    companies that survived. Real results would be somewhat worse.
  - No trading costs or taxes are included.
"""

import argparse

import pandas as pd

from market_data import download_prices, get_universe
from signals import SETTINGS, compute_signals

HOLD_DAYS = 20                        # about 4 weeks
TARGETS = [0.08, 0.10, 0.15, 0.20]    # profit targets to compare
MAX_ALERTS_PER_DAY = 5                # same top-5 cap as scanner.py
CACHE_FILE = "price_cache_3y.pkl"


def stop_prices(entry: float, row: pd.Series) -> dict[str, float]:
    """The stop-loss styles to compare. Each returns a price to sell at."""
    return {
        "10-day low": row["stop_level"],                  # the current rule
        "Fixed -8%": entry * 0.92,
        "Fixed -12%": entry * 0.88,
        "2x daily move (ATR)": entry - 2 * row["atr"],    # adapts to each stock
    }


def evaluate(df: pd.DataFrame, sig: pd.DataFrame, ticker: str) -> list[dict]:
    """One result row per (signal, stop style, target) combination."""
    results = []
    for day in sig.index[sig["signal"]]:
        i = df.index.get_loc(day)
        future = df.iloc[i + 1: i + 1 + HOLD_DAYS]
        if len(future) < HOLD_DAYS:
            continue                      # too recent to judge
        row = sig.iloc[i]
        entry = df["Close"].iloc[i]
        ret_20d = future["Close"].iloc[-1] / entry - 1

        for stop_name, stop in stop_prices(entry, row).items():
            for target in TARGETS:
                target_price = entry * (1 + target)
                outcome, pnl = "neither", ret_20d
                # Walk forward day by day: which happened first, target or stop?
                # (Both on the same day -> assume the stop: the cautious choice.)
                for _, bar in future.iterrows():
                    if bar["Low"] <= stop:
                        outcome, pnl = "stopped", stop / entry - 1
                        break
                    if bar["High"] >= target_price:
                        outcome, pnl = "target", target
                        break
                results.append({
                    "ticker": ticker, "date": day.date(), "reward_risk": row["reward_risk"],
                    "ret_20d": ret_20d, "stop": stop_name, "target": f"+{target:.0%}",
                    "outcome": outcome, "pnl": pnl,
                })
    return results


def grid(res: pd.DataFrame, value: str) -> pd.DataFrame:
    """Table with stop styles as rows and targets as columns, in a sensible order."""
    if value == "avg":
        g = res.pivot_table(index="stop", columns="target", values="pnl", aggfunc="mean")
        fmt = lambda v: f"{v:+.1%}"
    else:
        g = res.assign(hit=res["outcome"] == value).pivot_table(
            index="stop", columns="target", values="hit", aggfunc="mean")
        fmt = lambda v: f"{v:.0%}"
    stops = [s for s in ["10-day low", "Fixed -8%", "Fixed -12%", "2x daily move (ATR)"] if s in g.index]
    targets = [f"+{t:.0%}" for t in TARGETS if f"+{t:.0%}" in g.columns]
    return g.loc[stops, targets].map(fmt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-download prices")
    args = parser.parse_args()

    tickers = get_universe()
    if args.refresh:
        print("Fresh download requested.")
    prices = download_prices(tickers, period="3y", cache_file=CACHE_FILE,
                             max_age_hours=0 if args.refresh else 20)

    rows = []
    for ticker, df in prices.items():
        rows += evaluate(df, compute_signals(df), ticker)
    if not rows:
        print("No signals in history. Try loosening SETTINGS in signals.py.")
        return
    res = pd.DataFrame(rows)

    # ----- Market-wide drop vs stock fell alone (uses ALL signals, no cap) -----
    # Market dip = how far SPY was below its 3-month high, measured the same
    # way as a stock's dip. Small = the stock fell on its own news.
    spy = download_prices(["SPY"], period="3y")["SPY"]["Close"]
    spy_dip = 1 - spy.rolling(10).min() / spy.rolling(60).max()
    spy_dip.index = spy_dip.index.date
    res["market_dip"] = res["date"].map(spy_dip)
    res["situation"] = pd.cut(res["market_dip"], bins=[-1, 0.05, 0.10, 1],
                              labels=["Stock fell alone (market down <5%)",
                                      "Market pullback (5-10%)",
                                      "Market selloff (10%+)"])
    all_signals = res.drop_duplicates(["ticker", "date"])
    print("\n===== Does it matter WHY the stock dropped? (all signals, no top-5 cap) =====")
    print("Stop = Fixed -12% (the best stop in earlier runs). Avg result per trade by target:\n")
    best_stop_rows = res[res["stop"] == "Fixed -12%"]
    by_group = best_stop_rows.pivot_table(index="situation", columns="target",
                                          values="pnl", aggfunc="mean", observed=True)
    by_group = by_group[[f"+{t:.0%}" for t in TARGETS]].map(lambda v: f"{v:+.1%}")
    counts = all_signals.groupby("situation", observed=True).agg(
        signals=("ticker", "size"), different_days=("date", "nunique"),
        held_20d=("ret_20d", "mean"))
    counts["held_20d"] = counts["held_20d"].map(lambda v: f"{v:+.1%}")
    print(counts.join(by_group).to_string())
    print("\n'different_days' matters: 50 signals on ONE crash day are really one bet, not 50.")

    # ----- Which "high" should the drop be measured from? -----
    peak_options = {"3-month high": 60, "Year-to-date high": "ytd", "1-year high": 252,
                    "3-year high (all data)": None}
    print("\n===== Drop measured from which high? (all signals, stop = Fixed -12%) =====\n")
    table = []
    for label, lookback in peak_options.items():
        settings = {**SETTINGS, "peak_lookback": lookback}
        variant_rows = []
        for ticker, df in prices.items():
            variant_rows += evaluate(df, compute_signals(df, settings), ticker)
        if not variant_rows:
            table.append({"Drop from": label, "signals": 0})
            continue
        v = pd.DataFrame(variant_rows)
        sigs = v.drop_duplicates(["ticker", "date"])
        row = {"Drop from": label, "signals": len(sigs), "different_days": sigs["date"].nunique(),
               "held_20d": f"{sigs['ret_20d'].mean():+.1%}"}
        v12 = v[v["stop"] == "Fixed -12%"]
        for t in TARGETS:
            row[f"+{t:.0%}"] = f"{v12[v12['target'] == f'+{t:.0%}']['pnl'].mean():+.1%}"
        table.append(row)
    print(pd.DataFrame(table).set_index("Drop from").fillna("").to_string())

    # Apply the same top-5-per-day cap the scanner uses
    signals = res.drop_duplicates(["ticker", "date"])
    busy_days = int((signals.groupby("date").size() > MAX_ALERTS_PER_DAY).sum())
    keep = (signals.sort_values("reward_risk", ascending=False)
                   .groupby("date").head(MAX_ALERTS_PER_DAY)[["ticker", "date"]])
    res = res.merge(keep, on=["ticker", "date"])
    n = len(keep)

    print(f"\n===== Backtest: {n} signals over 3 years (top {MAX_ALERTS_PER_DAY} per day) =====")
    print(f"Signals per month (avg): {n / 36:.1f}   "
          f"Days with more than {MAX_ALERTS_PER_DAY} signals: {busy_days}")
    print(f"Average 20-day return if just held: "
          f"{res.drop_duplicates(['ticker', 'date'])['ret_20d'].mean():+.1%}")

    print("\nAVERAGE RESULT PER TRADE  (the number that matters)")
    print(grid(res, "avg").to_string())
    print("\nHow often the TARGET was hit first")
    print(grid(res, "target").to_string())
    print("\nHow often the STOP was hit first")
    print(grid(res, "stopped").to_string())

    # The single best combination, and whether the ranking helps for it
    avg = res.groupby(["stop", "target"])["pnl"].mean()
    best_stop, best_target = avg.idxmax()
    best = res[(res["stop"] == best_stop) & (res["target"] == best_target)]
    median_score = best["reward_risk"].median()
    hi = best[best["reward_risk"] >= median_score]["pnl"].mean()
    lo = best[best["reward_risk"] < median_score]["pnl"].mean()
    print(f"\nBest combination: stop = {best_stop}, target = {best_target} "
          f"-> {avg.max():+.1%} per trade")
    print(f"  Higher-ranked half: {hi:+.1%} per trade   Lower-ranked half: {lo:+.1%} per trade")

    res.to_csv("backtest_results.csv", index=False)
    print("\nEvery trade saved to backtest_results.csv")


if __name__ == "__main__":
    main()
