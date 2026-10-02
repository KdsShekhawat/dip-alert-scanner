# Stock dip alerts

Scans ~520 large US stocks (S&P 500 + Nasdaq-100) every weekday after the close
and emails you when a strong company has dropped sharply for short-term reasons
and is starting to bounce.

**This is a screener, not a crystal ball.** It finds stocks that match your rules.
Run `backtest.py` to see how often those rules actually worked in the past.
Not financial advice.

## How the signal works

A stock must pass all four layers on the same day:

| Layer | Rule | Why |
|---|---|---|
| 1. Strong company | Price ≥ $20, $50M+ traded daily, market cap ≥ $10B, profitable | No penny stocks, no struggling businesses |
| 2. Long-term trend healthy | 200-day average is rising, price not more than 15% below it | The dip is a pause in an uptrend, not a collapse |
| 3. Sharp short-term drop | Down 15–40% from its 3-month high, RSI under 30 recently | It got oversold, so there's room to rebound |
| 4. Bounce starting | Closes above the prior 3 days' highs on normal+ volume | Buyers are stepping back in; don't catch a falling knife |

Plus: at least 20% of room back up to the recent high, no earnings report in the
next 5 days, and the same stock won't alert twice within 10 trading days.

**Analogy:** a good athlete (layer 1) who's been in great form all season (layer 2)
trips and falls (layer 3). You don't bet on them while they're still falling. You
wait until they're back on their feet (layer 4).

The one thing code can't judge is *why* it dropped. The email includes recent
headlines so you can decide: temporary scare (good) or broken business (avoid).

## Files

| File | What it does |
|---|---|
| `signals.py` | The rules. All the numbers you might tweak are in `SETTINGS` at the top |
| `market_data.py` | Gets the stock lists (Wikipedia) and prices/news (Yahoo Finance via yfinance) |
| `scanner.py` | Runs the daily scan and sends the email |
| `backtest.py` | Tests the rules on 5 years of history |
| `app.py` | The phone-friendly website: filters, signal cards, backtest, email sign-up |
| `subscribers.py` | The email sign-up list (Supabase), with confirm and unsubscribe |
| `mailer.py` | Sends emails through Gmail (used by the scanner and the website) |
| `assets/` | Kuldeep Investments logo (SVG originals + PNG versions the app uses) |
| `.streamlit/config.toml` | The web app's color theme |
| `.github/workflows/scan.yml` | Tells GitHub to run `scanner.py` every weekday after the close |

## Setup

### 1. Try it on your PC first (PowerShell, in VSCode)

```powershell
cd stock-dip-alerts
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python backtest.py            # how would the rules have done? (~1-2 min)
python scanner.py --dry-run   # today's scan, printed instead of emailed
```

If `Activate.ps1` is blocked, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2. Make a Gmail "app password"

The script logs into Gmail to send the email. Gmail won't accept your normal
password from a script, so you create a separate one:

1. Turn on 2-Step Verification for your Google account (required).
2. Go to <https://myaccount.google.com/apppasswords>, create one named "dip scanner".
3. Copy the 16-character password. You'll paste it into GitHub next.

### 3. Put it on GitHub

1. Create a new **public** repository on GitHub (see "Deploy for free" below for why public is safe) and upload these files
   (including the `.github` folder).
2. In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Add three secrets:
   - `EMAIL_ADDRESS` — your Gmail address
   - `EMAIL_APP_PASSWORD` — the 16-character app password
   - `EMAIL_TO` — where alerts go (can be the same Gmail)
3. Go to the **Actions** tab → "Daily dip scanner" → **Run workflow** to test it now.

From then on it runs every weekday around 4:37 PM Central (3:37 PM in winter).

Tip: to make sure it's alive, set `SEND_EMAIL_WHEN_EMPTY = True` in `scanner.py`
for the first week. You'll get a "no signals" email on quiet days.

Tip: the Gmail app on your phone can notify you only for these emails. Make a
Gmail filter for the subject "Dip scanner" and mark it important.

## Web app

`app.py` is the phone-friendly website: three filters on top (drop measured from,
drop size, company size), today's signals as cards, a watchlist, a backtest tab, and
an email sign-up tab. Try it on your PC first:

```powershell
streamlit run app.py
```

## Deploy for free and share with friends

Four free services, each doing one job:

| Service | Job | Cost |
|---|---|---|
| GitHub | Stores the code, runs the daily email job | Free |
| Streamlit Community Cloud | Runs the website | Free |
| Supabase | Stores the email sign-up list | Free tier |
| Gmail | Sends confirmation and alert emails | Free |

The repo is **public** so friends can open the site without logging in. That's safe:
passwords and keys live in "secrets" settings, never in the code, and sign-up emails
live in Supabase, not in the repo.

### Step 1: Supabase (sign-up list), about 5 minutes

1. Sign up at <https://supabase.com> and click **New project** (free plan; pick any region
   near Texas, e.g. US East or Central).
2. Open **SQL Editor**, paste the `create table ...` block from the top of `subscribers.py`, click **Run**.
3. Open **Project Settings → API**. Copy two things: the **Project URL** and the
   **Secret key** (Settings → API Keys, starts with `sb_secret_`). Keep it secret.

### Step 2: GitHub (code + daily emails), about 5 minutes

1. Create a new **public** repository, e.g. `kuldeep-investments`.
2. Upload all project files. Check that `.github`, `.streamlit` and `assets` folders made it.
   **Do not upload** `.venv`, `price_cache_5y.pkl` or `.streamlit/secrets.toml`.
3. **Settings → Secrets and variables → Actions → New repository secret**, add:
   `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD`, `EMAIL_TO`, `SUPABASE_URL`, `SUPABASE_KEY`,
   and `APP_URL` (fill this in after Step 3).

### Step 3: Streamlit Community Cloud (website), about 5 minutes

1. Go to <https://share.streamlit.io>, sign in with GitHub, click **Create app**.
2. Pick your repo, branch `main`, main file `app.py`. Choose a custom address such as
   `kuldeep-investments` → your site is `https://kuldeep-investments.streamlit.app`.
3. **Advanced settings**: Python 3.12, and paste into **Secrets**:

   ```toml
   EMAIL_ADDRESS = "you@gmail.com"
   EMAIL_APP_PASSWORD = "your16charapppassword"
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "your-service-role-key"
   APP_URL = "https://kuldeep-investments.streamlit.app"
   ```

4. Click **Deploy**. Then put the same `APP_URL` into the GitHub secrets (Step 2.3).

### Step 4: Test, then share

1. Open the site on your iPhone, sign up with your own email, click the confirmation link.
2. GitHub → **Actions → Daily dip scanner → Run workflow** to test the daily email.
3. Send friends the link. Tip: in iPhone Safari, **Share → Add to Home Screen** makes it
   open like an app.

### Good to know

- **Sleeping:** free Streamlit apps sleep after about 12 hours without visitors. The next
  visitor sees a "wake up" button; then prices load for 1-3 minutes. After that it's fast.
- **Daily job pause:** GitHub pauses scheduled jobs in public repos after 60 days with no
  new commits. It emails you first; click to re-enable, or commit any small change.
- **Gmail limit:** about 500 emails a day, plenty for friends.
- **Friends get the default filters** (15-40% drop from the 3-month high). Change
  `SETTINGS` in `signals.py` to change what everyone gets.
- **Test sign-ups locally:** put the same five lines in `.streamlit/secrets.toml`, with
  `APP_URL = "http://localhost:8501"`. That file is in `.gitignore`, so it never gets uploaded.

## Tuning

1. Change a number in `SETTINGS` (e.g. `min_dip` from 0.15 to 0.20).
2. Run `python backtest.py`.
3. Compare "Hit +20% before stop-loss" and "Average 20-day return".

Looser rules = more alerts, lower quality. Stricter rules = fewer, better alerts.
Expect maybe a handful of signals a month, sometimes none for weeks.

## Known limits

- Backtest only tests price rules (historical fundamentals and news aren't free).
- It uses today's index members, which are the survivors, so real past results
  would be a bit worse than the backtest shows.
- Yahoo Finance data is free and unofficial; occasionally a ticker is missing.
