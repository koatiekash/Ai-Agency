"""Gmail: send approved outreach and read seller replies.

Uses Gmail's SMTP/IMAP with an *app password* (Google Account -> Security -> 2-Step
Verification -> App passwords). Put these in .env — never in chat or code:

    GMAIL_ADDRESS=you@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
    BUSINESS_NAME=...            BUSINESS_ADDRESS=...   (CAN-SPAM: a real postal address)
    GMAIL_DAILY_LIMIT=40         # keep cold email volume low to protect the account

Every email gets the business name, postal address and an unsubscribe line. Replies
containing "unsubscribe"/"stop"/"remove" add the sender to data/unsubscribed.csv, and
nobody on that list is ever emailed again.
"""
from __future__ import annotations

import csv
import datetime as dt
import email
import imaplib
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
SUPPRESS = os.path.join(DATA, "unsubscribed.csv")
SENT_LOG = os.path.join(DATA, "sent_emails.csv")
OPT_OUT_WORDS = ("unsubscribe", "stop", "remove me", "do not contact", "don't contact")


def configured() -> bool:
    return bool(os.environ.get("GMAIL_ADDRESS") and os.environ.get("GMAIL_APP_PASSWORD"))


def footer() -> str:
    return (f"\n\n{os.environ.get('BUSINESS_NAME', '')}\n{os.environ.get('BUSINESS_ADDRESS', '')}\n"
            "Reply 'unsubscribe' and I won't contact you again.")


def suppressed() -> set[str]:
    if not os.path.exists(SUPPRESS):
        return set()
    with open(SUPPRESS) as f:
        return {r[0].strip().lower() for r in csv.reader(f) if r}


def _suppress(addr: str) -> None:
    os.makedirs(DATA, exist_ok=True)
    with open(SUPPRESS, "a", newline="") as f:
        csv.writer(f).writerow([addr.lower(), dt.datetime.now().isoformat(timespec="seconds")])


def sent_today() -> int:
    if not os.path.exists(SENT_LOG):
        return 0
    today = dt.date.today().isoformat()
    with open(SENT_LOG) as f:
        return sum(1 for r in csv.reader(f) if r and r[0].startswith(today))


def can_send(to: str) -> list[str]:
    """Reasons an email can't go out (empty = OK)."""
    v = []
    if not configured():
        v.append("Gmail is not configured.")
    if not to or "@" not in to:
        v.append("Lead has no email address.")
    elif to.lower() in suppressed():
        v.append("Recipient has unsubscribed.")
    if not os.environ.get("BUSINESS_ADDRESS"):
        v.append("BUSINESS_ADDRESS (postal address) is required in every email (CAN-SPAM).")
    if sent_today() >= int(os.environ.get("GMAIL_DAILY_LIMIT", 40)):
        v.append("Daily email limit reached.")
    return v


def send(to: str, subject: str, body: str) -> dict:
    problems = can_send(to)
    if problems:
        raise RuntimeError("; ".join(problems))
    msg = EmailMessage()
    msg["From"] = f"{os.environ.get('BUSINESS_NAME', '')} <{os.environ['GMAIL_ADDRESS']}>"
    msg["To"], msg["Subject"] = to, subject
    msg["List-Unsubscribe"] = f"<mailto:{os.environ['GMAIL_ADDRESS']}?subject=unsubscribe>"
    msg.set_content(body.rstrip() + footer())
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        s.send_message(msg)
    os.makedirs(DATA, exist_ok=True)
    with open(SENT_LOG, "a", newline="") as f:
        csv.writer(f).writerow([dt.datetime.now().isoformat(timespec="seconds"), to, subject])
    return {"to": to, "subject": subject, "status": "sent"}


def fetch_replies(days: int = 7, limit: int = 30) -> list[dict]:
    """Recent unread replies (marked read once fetched). Opt-outs are suppressed automatically."""
    if not configured():
        return []
    out = []
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as m:
        m.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        m.select("INBOX")
        _, ids = m.search(None, f'(UNSEEN SINCE "{since}")')
        for i in ids[0].split()[-limit:]:
            _, data = m.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])
            body = ""
            for part in (msg.walk() if msg.is_multipart() else [msg]):
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                    break
            sender = parseaddr(msg.get("From", ""))[1]
            reply = body.split("\nOn ")[0].strip()[:1500]          # drop the quoted thread
            opted_out = any(w in reply.lower() for w in OPT_OUT_WORDS)
            if opted_out:
                _suppress(sender)
            out.append({"from": sender, "subject": msg.get("Subject", ""), "date": msg.get("Date", ""),
                        "text": reply, "opted_out": opted_out})
    return out
