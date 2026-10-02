"""
mailer.py — sends one HTML email through Gmail. Used by scanner.py (daily alerts)
and app.py (sign-up confirmation emails).

Needs these settings (GitHub secrets for the scanner, Streamlit secrets for the app):
    EMAIL_ADDRESS       the Gmail address that sends
    EMAIL_APP_PASSWORD  its 16-character app password
"""

import os
import smtplib
from email.mime.text import MIMEText


def send_email(to: str, subject: str, html: str) -> None:
    sender = os.environ["EMAIL_ADDRESS"]
    msg = MIMEText(html, "html")
    msg["Subject"], msg["From"], msg["To"] = subject, f"Kuldeep Investments <{sender}>", to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, os.environ["EMAIL_APP_PASSWORD"])
        server.send_message(msg)
