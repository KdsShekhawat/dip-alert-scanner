"""
app.py — Kuldeep Investments dip scanner, the web version.

Run on your PC (PowerShell, with .venv active):
    streamlit run app.py

How Streamlit works, in one sentence: every time you touch a control, this whole
file re-runs from top to bottom with the new values. The slow parts (downloading
prices) are wrapped in @st.cache_data so they only run once and are remembered.

Phone-friendly by design: one centered column (no sidebar), filters at the top,
signals shown as cards that stack on a narrow screen.
"""

import base64
from datetime import date
import os
from pathlib import Path

import pandas as pd
import streamlit as st

import subscribers
import market_data
from mailer import send_email
from market_data import (download_prices, get_company_details, get_market_caps,
                         get_universe_with_names)
from signals import SETTINGS, compute_signals

ASSETS = Path(__file__).parent / "assets"     # logo files live here
TOP_N = 5                                       # max signals shown per day
HOLD_DAYS = 20                                  # backtest: sell after ~4 weeks at most

st.set_page_config(page_title="Dip Alert & Scanner · Kuldeep Investments",
                   page_icon=str(ASSETS / "favicon.png"), layout="centered")

# Streamlit secrets (on the cloud, or .streamlit/secrets.toml locally) -> environment
# variables, so subscribers.py and mailer.py read them the same way the scanner does.
try:
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(key, value)
except Exception:
    pass                                        # no secrets file: sign-ups stay switched off


def yahoo_url(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}"


# ---------------------------------------------------------------------------
# Confirm / unsubscribe links from emails arrive as ?confirm=... or ?unsubscribe=...
# ---------------------------------------------------------------------------
params = st.query_params
if subscribers.is_configured() and ("confirm" in params or "unsubscribe" in params):
    try:
        if "confirm" in params:
            ok = subscribers.confirm(params["confirm"])
            (st.success if ok else st.warning)(
                "You're subscribed! You'll get an email on days with a buy signal."
                if ok else "That confirmation link isn't valid any more. Try signing up again.")
        else:
            ok = subscribers.remove(params["unsubscribe"])
            st.info("You're unsubscribed. No more alert emails." if ok
                    else "This email was already unsubscribed.")
    except Exception:
        st.error("Couldn't reach the sign-up list right now. Please try the link again later.")
    st.query_params.clear()


# ---------------------------------------------------------------------------
# Data (cached: downloaded once, re-used for 12 hours)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=24 * 3600, show_spinner="Loading the S&P 500 and Nasdaq-100 lists...")
def load_universe() -> tuple[dict[str, str], list[str]]:
    """({ticker: company name}, [names of any lists that failed to load])"""
    universe = get_universe_with_names()
    return universe, list(market_data.LOAD_WARNINGS)


@st.cache_data(ttl=12 * 3600, show_spinner="Getting 5 years of prices for ~500 stocks. "
                                             "First visit of the day takes 1-3 minutes...")
def load_prices() -> dict[str, pd.DataFrame]:
    return download_prices(list(load_universe()[0]) + ["SPY"], period="5y")


@st.cache_data(ttl=24 * 3600, show_spinner="Checking company sizes...")
def load_market_caps(tickers: tuple[str, ...]) -> dict[str, float | None]:
    # Only looked up for stocks that already passed the rules: a few dozen, not 500
    return get_market_caps(list(tickers))


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def load_details(ticker: str) -> dict:
    return get_company_details(ticker)


@st.cache_data(ttl=12 * 3600, show_spinner="Applying the rules to every stock...")
def run_rules(settings: dict) -> dict[str, pd.DataFrame]:
    return {t: compute_signals(df, settings) for t, df in load_prices().items() if t != "SPY"}


# ---------------------------------------------------------------------------
# Header + the three filters (on top, not in a sidebar, so they work on a phone)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phone fit. Streamlit's defaults are built for laptops, so we adjust a few things:
#   - hide Streamlit's own top bar and remove the ~6rem of empty space it reserves
#   - tighter side margins on small screens
#   - keep columns side by side on phones (Streamlit normally stacks them)
#   - 16px input text: iPhone Safari zooms into any smaller input, shifting the page
# ---------------------------------------------------------------------------
CONTACT_EMAIL = "ksjkinvestments@gmail.com"
CONTACT_LINK = f"mailto:{CONTACT_EMAIL}?subject=Dip%20Alert%20%26%20Scanner"

st.markdown("""
<style>
  [data-testid="stHeader"] { display: none; }
  [data-testid="stMainBlockContainer"], .block-container {
      padding-top: max(0.75rem, env(safe-area-inset-top)) !important;
      padding-bottom: 1.5rem !important;
  }
  input, textarea, [data-baseweb="select"] * { font-size: 16px !important; }
  .ki-contact {
      margin-left: auto; flex-shrink: 0; text-decoration: none !important;
      color: #BF5700 !important; border: 1.5px solid #BF5700; border-radius: 18px;
      padding: 5px 12px; font-size: 14px; white-space: nowrap; line-height: 1.2;
  }
  .ki-footer { text-align: center; font-size: 13px; color: #5F6B76; margin-top: 28px;
               padding-top: 12px; border-top: 1px solid #E2D3BD; line-height: 1.7; }
  .ki-footer a { color: #9A4600 !important; }
  @media (max-width: 640px) {
      [data-testid="stMainBlockContainer"], .block-container {
          padding-left: 0.75rem !important; padding-right: 0.75rem !important;
      }
      [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem !important; }
      [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
      [data-testid="stMetricValue"] { font-size: 1.25rem !important; }
      [data-testid="stMetricLabel"] p { font-size: 0.75rem !important; }
      h4 { font-size: 1.05rem !important; }
      .ki-contact { padding: 6px 10px; font-size: 16px; }
  }
  @media (max-width: 400px) {
      .ki-contact-label { display: none; }            /* icon only on the narrowest phones */
  }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Header: emblem + app name + contact link. Built in HTML so it stays on ONE row
# on a phone (Streamlit columns would stack the logo above the name).
# ---------------------------------------------------------------------------
@st.cache_data
def emblem_base64() -> str:
    return base64.b64encode((ASSETS / "favicon.png").read_bytes()).decode()


st.markdown(f"""
<div style="display:flex;align-items:center;gap:10px;padding:2px 0 10px 0;
            border-bottom:3px solid #D4A017;margin-bottom:12px">
  <img src="data:image/png;base64,{emblem_base64()}" alt="" style="width:52px;height:52px;flex-shrink:0">
  <div style="line-height:1.15;min-width:0">
    <div style="font-family:Georgia,serif;font-weight:700;font-size:clamp(19px,5.6vw,32px);
                color:#1F2A36;white-space:nowrap">Dip Alert &amp; Scanner</div>
    <div style="font-family:Georgia,serif;font-size:11px;letter-spacing:3.5px;color:#BF5700;
                white-space:nowrap">KULDEEP INVESTMENTS</div>
  </div>
  <a class="ki-contact" href="{CONTACT_LINK}" title="Contact us: {CONTACT_EMAIL}">
    ✉<span class="ki-contact-label">&nbsp;Contact us</span></a>
</div>""", unsafe_allow_html=True)

PEAKS = {"3 months": 60, "YTD": "ytd", "1 year": 252, "5 years": None}

# Default values for every rule. "Reset to defaults" puts these back.
DEFAULTS = {"peak": "3 months", "dip": (15, 40), "cap": 2,
            "price": int(SETTINGS["min_price"]), "liq": int(SETTINGS["min_avg_dollar_volume"] / 1e6),
            "trend": SETTINGS["require_trend"], "oversold": SETTINGS["require_oversold"],
            "rsi": int(SETTINGS["rsi_oversold"]), "upside": int(SETTINGS["min_upside_to_peak"] * 100),
            "bounce": SETTINGS["require_reversal"]}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


def reset_rules():
    for key, value in DEFAULTS.items():
        st.session_state[key] = value


with st.container(border=True):
    peak_choice = st.segmented_control(
        "Drop measured from the highest price of the past...", list(PEAKS), key="peak",
        help="A 25% drop over 1 year = the stock is 25% below its best price of the past year. "
             "YTD = its best price since January 1.")
    dip_range = st.slider("Drop size (%)", 5, 70, key="dip",
                          help="Show stocks that fell at least the left number and at most the right one.")
    min_cap_b = st.slider("Company size: at least ($ billions)", 0, 200, step=1, key="cap",
                          format="$%dB",
                          help="Market cap: what the whole company is worth. 0 = any size, "
                               "200 = $200B and up (only the giants).")

    # The other rules: tucked away so the phone screen stays clean
    with st.expander("More rules"):
        a, b = st.columns(2)
        min_price = a.number_input("Min share price ($)", 1, 1000, step=5, key="price",
                                   help="Skips cheap stocks.")
        min_liq_m = b.number_input("Min traded per day ($M)", 0, 5000, step=10, key="liq",
                                   help="Average dollars traded daily. Higher = easier to buy and sell.")
        require_trend = st.toggle("Long-term trend still rising", key="trend",
                                  help="200-day average higher than 2 months ago: the dip is a pause "
                                       "in an uptrend, not a collapse.")
        require_oversold = st.toggle("Must be oversold", key="oversold",
                                     help="RSI measures how hard a stock was sold. Lower = more beaten down.")
        rsi_level = st.slider("Oversold means RSI below", 10, 60, key="rsi",
                              disabled=not require_oversold)
        min_upside = st.slider("Min room back to the high (%)", 0, 60, key="upside",
                               help="How far it could climb back to its high: your potential gain.")
        require_reversal = st.toggle("Wait for the bounce to start", key="bounce",
                                     help="Off = list stocks while they may still be falling.")
        st.button("Reset to defaults", on_click=reset_rules)

peak_choice = peak_choice or "3 months"         # segmented controls can be clicked "off"
min_cap = min_cap_b * 1e9
cap_label = "any size" if min_cap_b == 0 else f"${min_cap_b}B+"
peak_label = {"3 months": "3-month", "YTD": "year-to-date", "1 year": "1-year",
              "5 years": "5-year"}[peak_choice]
settings = {**SETTINGS, "peak_lookback": PEAKS[peak_choice],
            "min_dip": dip_range[0] / 100, "max_dip": dip_range[1] / 100,
            "min_price": float(min_price), "min_avg_dollar_volume": min_liq_m * 1e6,
            "require_trend": require_trend, "require_oversold": require_oversold,
            "rsi_oversold": rsi_level, "min_upside_to_peak": min_upside / 100,
            "require_reversal": require_reversal}

prices = load_prices()
names, list_warnings = load_universe()
all_sigs = run_rules(settings)
last_date = prices["SPY"].index[-1]


def size_filter(tickers: list[str]) -> tuple[list[str], dict]:
    """Keep big-enough companies. A failed size lookup keeps the stock (unknown != small)."""
    if min_cap == 0 or not tickers:
        return tickers, {}
    caps = load_market_caps(tuple(sorted(tickers)))
    return [t for t in tickers if caps.get(t) is None or caps[t] >= min_cap], caps


def fmt_cap(value) -> str:
    return f"${value / 1e9:,.0f}B" if value else ""


tab_today, tab_backtest, tab_alerts = st.tabs(["Today", "Backtest", "Get alerts"])


# ---------------------------------------------------------------------------
# Tab 1: today's signals as cards + compact watchlist
# ---------------------------------------------------------------------------
with tab_today:
    st.caption(f"Scanning {len(all_sigs)} stocks (S&P 500 + Nasdaq-100) · prices up to "
               f"{last_date:%a %b %d, %Y} · drop from {peak_label} high · not financial advice")
    if list_warnings:
        st.warning(f"Couldn't load the {' and '.join(list_warnings)} list today, so those stocks "
                   f"aren't being scanned. It retries automatically tomorrow.")

    def todays(column: str) -> list[str]:
        hits = [t for t, sig in all_sigs.items() if sig.iloc[-1][column]]
        hits.sort(key=lambda t: all_sigs[t].iloc[-1]["reward_risk"], reverse=True)
        return hits

    signal_tickers, caps = size_filter(todays("signal"))
    signal_tickers = signal_tickers[:TOP_N]
    st.subheader(f"Buy signals today: {len(signal_tickers)}")
    if not signal_tickers:
        st.write("Nothing passed every rule today. That's normal; good setups are rare. "
                 "Check the watchlist below, or widen the drop size.")

    for t in signal_tickers:
        last = all_sigs[t].iloc[-1]
        with st.container(border=True):
            st.markdown(f"#### [{t}]({yahoo_url(t)}) · {names.get(t, t)}")
            a, b, c = st.columns(3)
            a.metric("Price", f"${last['close']:.2f}")
            b.metric("Drop", f"-{last['dip']:.0%}")
            c.metric("Room to high", f"+{last['upside_to_peak']:.0%}")
            st.caption(f"RSI {last['rsi']:.0f} · reward/risk {last['reward_risk']:.1f} to 1"
                       + (f" · market cap {fmt_cap(caps.get(t))}" if caps.get(t) else ""))
            with st.expander("Why did it drop? Company check and news"):
                d = load_details(t)
                st.write(f"**{d['name']}** · {d.get('sector') or 'sector unknown'} · "
                         f"profitable: {d['profitable'] if d['profitable'] is not None else 'unknown'} · "
                         f"next earnings: {d['next_earnings'] or 'unknown'}")
                for title, link in d["news"] or [("No recent headlines found", None)]:
                    st.write(f"- [{title}]({link})" if link else f"- {title}")
                st.write(f"[Full details on Yahoo Finance]({yahoo_url(t)})")

    # With the bounce rule off, every buy-zone stock is already a signal: no watchlist needed
    if require_reversal:
        # Watchlist = in the buy zone but NOT bounced yet (bounced ones were already alerted)
        watch_tickers, wcaps = size_filter([t for t in todays("in_buy_zone")
                                            if t not in signal_tickers and not all_sigs[t].iloc[-1]["ok_reversal"]])
        st.subheader(f"Watchlist: {len(watch_tickers)}")
        st.caption("In the buy zone but no bounce yet. They may still be falling.")
        if watch_tickers:
            watch = pd.DataFrame([{
                "Ticker": yahoo_url(t), "Company": names.get(t, t),
                "Price": all_sigs[t].iloc[-1]["close"], "Drop": all_sigs[t].iloc[-1]["dip"],
            } for t in watch_tickers])
            st.dataframe(
                watch, hide_index=True, width="stretch",
                column_config={
                    "Ticker": st.column_config.LinkColumn(
                        "Ticker", display_text=r"https://finance\.yahoo\.com/quote/(.*)", width="small"),
                    "Company": st.column_config.TextColumn(width="medium"),
                    "Price": st.column_config.NumberColumn(format="$%.2f", width="small"),
                    "Drop": st.column_config.NumberColumn(format="percent", width="small"),
                })

    # ----- Check any stock: shows WHY a stock is or isn't listed -----
    st.subheader("Check any stock")
    options = sorted(all_sigs)
    pick = st.selectbox("Type a ticker or company name", options, index=None,
                        placeholder="e.g. NVDA or Nvidia",
                        format_func=lambda t: f"{t} · {names.get(t, t)}")
    if pick:
        last = all_sigs[pick].iloc[-1]
        lo, hi = dip_range
        # Each check: (label, passed?, the actual number, is this rule switched on?)
        liq_now = all_sigs[pick]["dollar_vol20"].iloc[-1] / 1e6
        checks = [
            (f"Share price ${min_price}+ and ${min_liq_m}M+ traded per day",
             bool(last["ok_price"] and last["ok_liquidity"]),
             f"${last['close']:.2f}, ${liq_now:,.0f}M/day", True),
            ("Long-term trend still rising", bool(last["ok_trend"]),
             "200-day average vs 2 months ago", require_trend),
            (f"Dropped {lo}-{hi}% from its {peak_label} high", bool(last["ok_dip"]),
             f"down {last['dip']:.0%}", True),
            (f"Oversold (RSI under {rsi_level} in the last week)", bool(last["ok_oversold"]),
             f"RSI now {last['rsi']:.0f}", require_oversold),
            (f"{min_upside}%+ room back to the high", bool(last["ok_upside"]),
             f"+{last['upside_to_peak']:.0%}", True),
            ("Bounce started today", bool(last["ok_reversal"]),
             "closed above the last 3 days' highs", require_reversal),
        ]
        if min_cap:
            cap = load_market_caps((pick,)).get(pick)
            checks.append((f"Company size {cap_label}", cap is None or cap >= min_cap,
                           fmt_cap(cap) or "unknown", True))
        active = [c for c in checks if c[3]]
        failed = sum(not ok for _, ok, _, _ in active)

        with st.container(border=True):
            st.markdown(f"**[{pick}]({yahoo_url(pick)}) · {names.get(pick, pick)}**")
            if failed == 0 and last["signal"]:
                st.success("Buy signal today. It's in the list above.")
            elif failed == 0:
                st.info("Passes every rule, but it already signaled in the last 10 days, "
                        "so it isn't repeated.")
            elif failed == 1 and require_reversal and not last["ok_reversal"]:
                st.info("In the buy zone, waiting for a bounce. It's on the watchlist.")
            else:
                st.write(f"Not listed: fails {failed} of {len(active)} rules.")
            for label, ok, detail, on in checks:
                icon = ("✅" if ok else "❌") if on else "➖"
                st.write(f"{icon} {label} · {detail}" + ("" if on else " · rule off"))


# ---------------------------------------------------------------------------
# Tab 2: backtest with the filters above + your stop and target
# ---------------------------------------------------------------------------
def play_trade(future: pd.DataFrame, entry: float, stop_pct: int, target_pct: int) -> tuple[str, float]:
    """Walk forward day by day: target first, stop first, or neither?"""
    stop, target = entry * (1 - stop_pct / 100), entry * (1 + target_pct / 100)
    for _, bar in future.iterrows():
        if bar["Low"] <= stop:              # same-day tie -> assume the stop (cautious)
            return "Stopped", -stop_pct / 100
        if bar["High"] >= target:
            return "Target", target_pct / 100
    return "Sold day 20", future["Close"].iloc[-1] / entry - 1


with tab_backtest:
    st.write("How would the filters above have done over the last 5 years?")
    left, right = st.columns(2)
    stop_pct = left.slider("Stop-loss (%)", 3, 30, 12, help="Sell if it falls this much below your buy price.")
    target_pct = right.slider("Profit target (%)", 3, 50, 15, help="Sell when it gains this much.")

    if st.button("Run backtest", type="primary", width="stretch"):
        trades = []
        with st.spinner("Replaying 5 years..."):
            for t, sig in all_sigs.items():
                df = prices[t]
                for day in sig.index[sig["signal"]]:
                    i = df.index.get_loc(day)
                    future = df.iloc[i + 1: i + 1 + HOLD_DAYS]
                    if len(future) < HOLD_DAYS:
                        continue
                    outcome, pnl = play_trade(future, df["Close"].iloc[i], stop_pct, target_pct)
                    trades.append({"Date": day.date(), "Ticker": t,
                                   "Reward/risk": sig.loc[day, "reward_risk"],
                                   "Outcome": outcome, "Result": pnl})
            if trades:
                res = pd.DataFrame(trades)
                keep, _ = size_filter(sorted(res["Ticker"].unique()))
                res = res[res["Ticker"].isin(keep)]
        if not trades or res.empty:
            st.warning("No signals in 5 years with these filters. Try a wider drop size.")
        else:
            res = (res.sort_values("Reward/risk", ascending=False)
                      .groupby("Date").head(TOP_N).sort_values("Date"))
            spy = prices["SPY"]["Close"]
            spy_fwd = (spy.shift(-HOLD_DAYS) / spy - 1).dropna().mean()

            a, b = st.columns(2)
            a.metric("Avg per trade", f"{res['Result'].mean():+.1%}")
            b.metric("S&P 500, any 4 weeks", f"{spy_fwd:+.1%}",
                     help="The 'do nothing' baseline. Good rules beat this.")
            a, b, c = st.columns(3)
            a.metric("Signals", len(res), f"{len(res) / 60:.1f}/month", delta_color="off")
            b.metric("Hit target", f"{(res['Outcome'] == 'Target').mean():.0%}")
            c.metric("Hit stop", f"{(res['Outcome'] == 'Stopped').mean():.0%}")

            st.caption("Total profit if every signal got $1,000")
            curve = (res["Result"] * 1000).cumsum()
            curve.index = pd.to_datetime(res["Date"])
            st.line_chart(curve, y_label="$", color="#BF5700", height=220)

            with st.expander(f"All {len(res)} trades"):
                st.dataframe(
                    res.drop(columns=["Reward/risk"]).assign(Ticker=lambda d: d["Ticker"].map(yahoo_url)),
                    hide_index=True, width="stretch",
                    column_config={
                        "Ticker": st.column_config.LinkColumn(
                            "Ticker", display_text=r"https://finance\.yahoo\.com/quote/(.*)"),
                        "Result": st.column_config.NumberColumn(format="percent")})
            st.caption("Not included: trading costs, taxes, and survivorship bias (only today's "
                       "index members, at today's size, are tested). Real results would be worse.")

    with st.expander("Use these filters for the daily email (owner only)"):
        changed = {k: v for k, v in settings.items() if SETTINGS.get(k) != v}
        if changed:
            st.write("Replace these lines in `SETTINGS` at the top of `signals.py`:")
            st.code("\n".join(f'    "{k}": {v!r},' for k, v in changed.items()), language="python")
        else:
            st.write("The daily email already uses these filters.")


# ---------------------------------------------------------------------------
# Tab 3: email sign-up (no account needed, double opt-in)
# ---------------------------------------------------------------------------
def confirmation_email(token: str) -> str:
    app_url = os.environ.get("APP_URL", "").rstrip("/")
    link = f"{app_url}/?confirm={token}"
    return (f"<div style='font-family:Georgia,serif'>"
            f"<h2 style='color:#1F2A36'>Confirm your Kuldeep Investments alerts</h2>"
            f"<p>Someone (hopefully you) asked to get dip-buy alerts at this address.</p>"
            f"<p><a href='{link}' style='background:#BF5700;color:#fff;padding:10px 18px;"
            f"text-decoration:none;border-radius:6px'>Yes, send me alerts</a></p>"
            f"<p style='color:#666'>Didn't ask for this? Just ignore this email; "
            f"you won't hear from us again.</p></div>")


with tab_alerts:
    st.subheader("Get buy signals by email")
    st.write("On days the scanner finds a buy signal, you'll get one email after the market closes "
             "with up to 5 stocks. No account needed, and every email has an unsubscribe link.")
    st.caption("Alerts use the default filters (15-40% drop from the 3-month high). Not financial advice.")

    if not subscribers.is_configured():
        st.info("Email sign-up isn't switched on yet. The site owner needs to add the "
                "Supabase settings (see the README).")
    else:
        with st.form("signup", clear_on_submit=True, border=False):
            email = st.text_input("Your email", placeholder="you@example.com")
            submitted = st.form_submit_button("Send me alerts", type="primary", width="stretch")
        if submitted:
            sent = st.session_state.setdefault("signups_sent", 0)
            if not subscribers.valid_email(email):
                st.error("That doesn't look like an email address.")
            elif sent >= 3:
                st.warning("That's a few sign-ups already. Please check your inbox.")
            else:
                try:
                    status, token = subscribers.add(email)
                    if status == "confirmed":
                        st.success("You're already subscribed.")
                    else:
                        send_email(email.strip(), "Confirm your Kuldeep Investments alerts",
                                   confirmation_email(token))
                        st.session_state["signups_sent"] = sent + 1
                        st.success("Almost done! Check your inbox (and spam folder) "
                                   "and click the confirmation link.")
                except Exception:
                    st.error("Something went wrong saving your email. Please try again in a minute.")


# ---------------------------------------------------------------------------
# Footer (outside the tabs, so it shows on every tab)
# ---------------------------------------------------------------------------
st.markdown(f"""
<div class="ki-footer">
  Questions or ideas? <a href="{CONTACT_LINK}">{CONTACT_EMAIL}</a><br>
  © {date.today().year} Kuldeep Investments · For education only, not financial advice
</div>""", unsafe_allow_html=True)
