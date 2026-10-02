"""
signals.py — the "brain" of the scanner.

Everything here works on ONE stock's daily price history (a pandas DataFrame
with Open, High, Low, Close, Volume columns) and answers one question per day:
"Would my rules have fired a buy signal on this day?"

Because the function returns a True/False for EVERY day (not just today),
the same code powers both:
  - scanner.py  -> looks only at the last row (today)
  - backtest.py -> looks at every row in history to see how the rules did

Analogy: think of each rule as a sieve. A stock only gets an alert on days
when it falls through ALL the sieves at once.
"""

import pandas as pd

# ---------------------------------------------------------------------------
# Settings you can tune. Change these numbers, re-run backtest.py, and see
# whether results get better or worse. That's how you learn what works.
# ---------------------------------------------------------------------------
SETTINGS = {
    # Layer 1: "strong company" checks we can do from price data alone
    "min_price": 20.0,                 # no cheap/penny-ish stocks
    "min_avg_dollar_volume": 50e6,     # $50M traded per day -> easy to buy/sell

    # Layer 2: long-term trend still healthy
    "sma_long": 200,                   # 200-day average = the long-term trend line
    "sma_slope_lookback": 40,          # compare today's 200-day avg with 40 days ago
    "max_below_sma_long": 0.15,        # allow price up to 15% below the 200-day avg

    # Layer 3: sharp short-term drop + oversold
    "peak_lookback": 60,               # drop measured from: 60 = 3-month high, 252 = 1-year, None = all data, "ytd" = since Jan 1
    "dip_window": 10,                  # the drop's low must be within the last 10 days
    "min_dip": 0.15,                   # dropped at least 15% from the recent high...
    "max_dip": 0.40,                   # ...but not more than 40% (that may be broken)
    "rsi_period": 14,
    "rsi_oversold": 30,                # RSI under 30 = sold off hard
    "rsi_recent_days": 5,              # RSI must have been oversold in the last 5 days

    # Layer 4: the bounce is starting
    "breakout_days": 3,                # close above the highest high of prior 3 days
    "min_volume_ratio": 1.0,           # today's volume at least the 20-day average

    # Upside room: is there 20%+ to climb back to the recent high?
    "min_upside_to_peak": 0.20,

    # Don't repeat the same alert every day: skip if it already fired recently
    "cooldown_days": 10,

    # On/off switches (the web app exposes these)
    "require_trend": True,             # Layer 2 on?
    "require_oversold": True,          # RSI rule on?
    "require_reversal": True,          # Layer 4 on? Off = alert while still falling
}


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (0-100). It compares the size of recent up-days
    with recent down-days. Under 30 means sellers have been in control for a
    while, which often means the stock is "stretched" to the downside.
    Uses Wilder's smoothing, the standard way RSI is calculated.
    """
    change = close.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_signals(df: pd.DataFrame, s: dict = SETTINGS) -> pd.DataFrame:
    """
    Takes one stock's daily OHLCV data and returns a DataFrame with:
      - helper numbers (RSI, moving averages, dip size, ...)
      - one True/False column per rule
      - a final 'signal' column = all rules passed (first day only)
    """
    out = pd.DataFrame(index=df.index)
    close, high, volume = df["Close"], df["High"], df["Volume"]

    # ----- helper numbers ---------------------------------------------------
    out["close"] = close
    out["sma_long"] = close.rolling(s["sma_long"]).mean()
    out["rsi"] = rsi(close, s["rsi_period"])
    out["vol_avg20"] = volume.rolling(20).mean()
    out["dollar_vol20"] = (close * volume).rolling(20).mean()

    # Recent high (peak) and how deep the drop from it went.
    # peak_lookback = 60 -> 3-month high, 252 -> 1-year high, None -> highest in all data,
    # "ytd" -> highest since January 1 of that same year
    if s["peak_lookback"] == "ytd":
        out["peak"] = close.groupby(close.index.year).cummax()
    elif s["peak_lookback"]:
        out["peak"] = close.rolling(s["peak_lookback"]).max()
    else:
        out["peak"] = close.expanding().max()
    out["recent_low"] = close.rolling(s["dip_window"]).min()
    out["dip"] = 1 - out["recent_low"] / out["peak"]          # 0.20 = fell 20%
    out["upside_to_peak"] = out["peak"] / close - 1            # room to climb back
    out["stop_level"] = df["Low"].rolling(s["dip_window"]).min()  # recent low = stop idea

    # ATR (Average True Range) = how much this stock normally moves in a day,
    # in dollars. A stop placed 2 ATRs away sits outside everyday wobble.
    prev_close = close.shift(1)
    true_range = pd.concat([high - df["Low"],
                            (high - prev_close).abs(),
                            (df["Low"] - prev_close).abs()], axis=1).max(axis=1)
    out["atr"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    # Ranking score: reward-to-risk. Reward = room back to the recent high,
    # risk = distance down to the stop. 6.0 means "could gain 6x what I'd lose".
    out["risk"] = (1 - out["stop_level"] / close).clip(lower=0.01)
    out["reward_risk"] = out["upside_to_peak"] / out["risk"]

    # ----- Layer 1: tradable, not cheap -------------------------------------
    out["ok_price"] = close >= s["min_price"]
    out["ok_liquidity"] = out["dollar_vol20"] >= s["min_avg_dollar_volume"]

    # ----- Layer 2: long-term trend still up --------------------------------
    sma_rising = out["sma_long"] > out["sma_long"].shift(s["sma_slope_lookback"])
    not_broken = close >= out["sma_long"] * (1 - s["max_below_sma_long"])
    out["ok_trend"] = sma_rising & not_broken

    # ----- Layer 3: sharp drop + oversold -----------------------------------
    out["ok_dip"] = out["dip"].between(s["min_dip"], s["max_dip"])
    recent_min_rsi = out["rsi"].rolling(s["rsi_recent_days"]).min()
    out["ok_oversold"] = recent_min_rsi < s["rsi_oversold"]

    # ----- Layer 4: bounce confirmation --------------------------------------
    prior_high = high.shift(1).rolling(s["breakout_days"]).max()
    out["ok_reversal"] = (close > prior_high) & (volume >= out["vol_avg20"] * s["min_volume_ratio"])

    # ----- Upside room --------------------------------------------------------
    out["ok_upside"] = out["upside_to_peak"] >= s["min_upside_to_peak"]

    rules = ["ok_price", "ok_liquidity", "ok_dip", "ok_upside"]
    if s.get("require_oversold", True):
        rules.append("ok_oversold")
    if s.get("require_trend", True):
        rules.append("ok_trend")
    if s.get("require_reversal", True):
        rules.append("ok_reversal")
    all_pass = out[rules].all(axis=1)
    # "Buy zone" = everything except the bounce: a watchlist of stocks to keep an eye on
    out["in_buy_zone"] = out[[r for r in rules if r != "ok_reversal"]].all(axis=1)

    # Only fire on the FIRST day in a cooldown window, so you don't get
    # the same stock emailed to you 5 days in a row.
    fired_recently = (
        all_pass.shift(1, fill_value=False)
        .astype(int)
        .rolling(s["cooldown_days"], min_periods=1)
        .max()
        .astype(bool)
    )
    out["signal"] = all_pass & ~fired_recently
    return out
