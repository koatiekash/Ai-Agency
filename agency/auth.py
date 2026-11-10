"""Password login for the hosted dashboard.

Set AGENCY_PASSWORD (required whenever the server is reachable from other machines).
A successful login sets a signed, HttpOnly cookie valid for 30 days, so a phone or
laptop stays signed in. Repeated wrong passwords from one address are locked out.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

COOKIE = "agency_session"
MAX_AGE = 30 * 86400
_fails: dict[str, list[float]] = {}


def password() -> str:
    return os.environ.get("AGENCY_PASSWORD", "")


def _secret() -> bytes:
    # AGENCY_SECRET signs cookies; falls back to a hash of the password so changing the
    # password signs everyone out.
    s = os.environ.get("AGENCY_SECRET") or ("pw:" + password())
    return hashlib.sha256(s.encode()).digest()


def make_token() -> str:
    exp = str(int(time.time()) + MAX_AGE)
    sig = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid(token: str | None) -> bool:
    if not password():
        return True                      # local use with no password configured
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    good = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and exp.isdigit() and int(exp) > time.time()


def locked_out(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 900]
    _fails[ip] = recent
    return len(recent) >= 10


def check_password(ip: str, attempt: str) -> bool:
    ok = bool(password()) and secrets.compare_digest(attempt.encode(), password().encode())
    if not ok:
        _fails.setdefault(ip, []).append(time.time())
        time.sleep(1)                    # slow down guessing
    return ok


LOGIN_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#070521"><title>Agency City · Sign in</title>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#070521;--panel:#13104a;--line:#2d2a86;--tx:#e9eaff;--mut:#a3a5e6;--cy:#22d3ee;--rd:#ff6b78;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--tx);
font:16px/1.45 "Chakra Petch",system-ui,sans-serif;padding:24px max(16px,env(safe-area-inset-left,0px))}
form{width:100%;max-width:360px;background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px;display:grid;gap:12px}
h1{margin:0;font-size:24px;letter-spacing:3px}p{margin:0;color:var(--mut);font-size:14px}
label{font-size:13px;color:var(--mut)}input{font:inherit;color:var(--tx);background:#0c0934;border:1px solid var(--line);border-radius:10px;padding:11px 12px;width:100%}
input:focus-visible,button:focus-visible{outline:2px solid var(--cy);outline-offset:2px}
button{font:inherit;font-weight:700;color:#070521;background:var(--cy);border:0;border-radius:10px;padding:11px;cursor:pointer}
.err{color:var(--rd);font-size:14px}
</style></head><body>
<form method="post" action="/login"><h1>AGENCY CITY</h1><p>Sign in to the Command Center.</p>
__ERR__<label for="pw">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<button type="submit">Sign in</button></form></body></html>"""
