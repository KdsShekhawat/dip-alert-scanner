"""
subscribers.py — the email sign-up list, stored in a free Supabase database.

Why a database and not a file? Streamlit Cloud wipes files whenever the app
restarts, and the daily email job runs on a different computer (GitHub).
A small online database is the one place both can read and write.

Double opt-in, the standard way to do sign-ups:
  1. Someone types an email -> saved as NOT confirmed, with a random secret token
  2. We email them a link containing the token
  3. They click it -> confirmed. Only confirmed emails get alerts.
That stops anyone from signing up someone else's address.
The same token powers the one-click unsubscribe link in every alert.

Needs (Streamlit secrets for the app, GitHub secrets for the scanner):
    SUPABASE_URL   e.g. https://abcdxyz.supabase.co
    SUPABASE_KEY   a Secret key (starts with sb_secret_) from Settings -> API Keys.
                   Keep it secret, never in code.

One-time table setup (paste into Supabase -> SQL Editor -> Run):

    create table subscribers (
      id bigint generated always as identity primary key,
      email text unique not null,
      token text not null,
      confirmed boolean not null default false,
      created_at timestamptz not null default now()
    );
    alter table subscribers enable row level security;
"""

import os
import re
import secrets

import requests

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def is_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


def _request(method: str, query: str = "", **kwargs) -> requests.Response:
    key = os.environ["SUPABASE_KEY"]
    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/subscribers{query}"
    headers = {"apikey": key, "Content-Type": "application/json", "Prefer": "return=representation"}
    # New-style keys (sb_secret_...) go ONLY in the apikey header. Old-style keys
    # (service_role, retired by Supabase at the end of 2026) also need "Bearer".
    if not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    response = requests.request(method, url, headers=headers, timeout=20, **kwargs)
    response.raise_for_status()
    return response


def valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email.strip()))


def add(email: str) -> tuple[str, str]:
    """
    Start a sign-up. Returns (status, token):
      ("new", token)          -> send a confirmation email
      ("pending", token)      -> signed up before but never clicked; resend it
      ("confirmed", token)    -> already getting alerts
    """
    email = email.strip().lower()
    rows = _request("GET", f"?email=eq.{requests.utils.quote(email)}&select=token,confirmed").json()
    if rows:
        return ("confirmed" if rows[0]["confirmed"] else "pending"), rows[0]["token"]
    token = secrets.token_urlsafe(24)       # long and random: impossible to guess
    _request("POST", json={"email": email, "token": token})
    return "new", token


def confirm(token: str) -> bool:
    """Mark the sign-up with this token as confirmed. True if it existed."""
    return bool(_request("PATCH", f"?token=eq.{requests.utils.quote(token)}",
                         json={"confirmed": True}).json())


def remove(token: str) -> bool:
    """Unsubscribe: delete the row with this token. True if it existed."""
    return bool(_request("DELETE", f"?token=eq.{requests.utils.quote(token)}").json())


def confirmed_list() -> list[dict]:
    """Everyone who confirmed: [{'email': ..., 'token': ...}, ...]"""
    return _request("GET", "?confirmed=is.true&select=email,token").json()
