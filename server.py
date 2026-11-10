"""Web server: the city UI, the Command Center API, and the autonomous scheduler.

    python server.py      # UI at http://localhost:8000, a cycle every AGENCY_INTERVAL_MIN

Hosted (HOST=0.0.0.0) it refuses to start without AGENCY_PASSWORD, and every page and
API call requires a signed-in session.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()

import urllib.parse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agency import auth, llm, org
from agency.paths import DATA_DIR, OUT_DIR
from agency.orchestrator import run_cycle
from agency.store import CommandCenter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
cc = CommandCenter()
app = FastAPI(title="AI Agency Command Center")
WEB = os.path.join(os.path.dirname(__file__), "web")
INTERVAL_MIN = float(os.environ.get("AGENCY_INTERVAL_MIN", 60))
_lock = threading.Lock()


def _cycle(only=None):
    if not _lock.acquire(blocking=False):
        return False
    try:
        run_cycle(cc, only)
    except Exception:
        logging.exception("cycle failed")
    finally:
        _lock.release()
    return True


def scheduler():
    while True:
        if not cc.get("paused", False):
            _cycle()
        time.sleep(INTERVAL_MIN * 60)


PUBLIC = {"/login", "/healthz"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path in PUBLIC or auth.valid(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    if request.url.path.startswith("/api/") or request.method != "GET":
        return JSONResponse({"detail": "Sign in required."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


@app.get("/login")
def login_page(error: str = ""):
    msg = {"bad": "Wrong password.", "locked": "Too many attempts. Try again in 15 minutes."}.get(error, "")
    return HTMLResponse(auth.LOGIN_PAGE.replace("__ERR__", f'<p class="err" role="alert">{msg}</p>' if msg else ""))


@app.post("/login")
async def login(request: Request):
    ip = _ip(request)
    if auth.locked_out(ip):
        return RedirectResponse("/login?error=locked", status_code=303)
    form = urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"))
    if not auth.check_password(ip, (form.get("password") or [""])[0]):
        return RedirectResponse("/login?error=bad", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.MAX_AGE, httponly=True,
                    secure=https, samesite="lax")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True, "cycle": cc.get("cycle", 0)}


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


def upload_names() -> set[str]:
    from agency.trading.risk import RiskLimits
    return {"leads.csv", "buyers.csv", "tiktok_products.csv"} | {f"{s}.csv" for s in RiskLimits().allowed_symbols}


@app.post("/api/upload/{name}")
async def upload(name: str, request: Request):
    """Replace one data file (lead list, buyer list, price history…) from any device."""
    if name not in upload_names():
        raise HTTPException(400, f"Allowed files: {', '.join(sorted(upload_names()))}")
    body = await request.body()
    if not body or len(body) > 20 * 1024 * 1024:
        raise HTTPException(400, "File is empty or larger than 20 MB.")
    first = body[:2000].decode("utf-8-sig", "replace").splitlines()[0] if body else ""
    if "," not in first:
        raise HTTPException(400, "That doesn't look like a CSV file (no header row with commas).")
    tmp = os.path.join(DATA_DIR, name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, os.path.join(DATA_DIR, name))
    rows = body.count(b"\n")
    cc.event("command", "command", "shared", "approved", f"Uploaded {name} ({rows} rows)")
    return {"saved": name, "rows": rows, "header": first[:200]}


@app.get("/api/state")
def state(after: int = 0):
    acct = cc.get("trading.account", {})
    return {
        "online": llm.online(), "model": llm.MODEL, "cycle": cc.get("cycle", 0),
        "running": bool(cc.get("running", False)), "paused": bool(cc.get("paused", False)),
        "last_cycle_at": cc.get("last_cycle_at"), "interval_min": INTERVAL_MIN,
        "agents": org.all_agents(),
        "businesses": [{"id": b.id, "name": b.name, "goal": b.goal, "color": b.color} for b in org.BUSINESSES],
        "paused_businesses": sorted(org.paused_businesses(cc)),
        "events": cc.events_since(after),
        "stats": cc.stats(),
        "trading": {"account": acct, "champion": cc.get("trading.champion"),
                    "live": os.environ.get("LIVE_TRADING", "").lower() == "true"},
        "briefing": (cc.history("shared", "briefing", limit=1) or [None])[0],
        "setup": setup_status(),
    }


def setup_status():
    env = os.environ.get
    from agency import mailer
    from agency.realestate import deals
    from agency.trading.brokers import get_broker
    broker = get_broker()
    return [
        {"name": "Claude agents", "ok": llm.online(), "fix": "Add ANTHROPIC_API_KEY to .env (or the host's environment settings)"},
        {"name": "HeroFX account", "ok": broker.name.startswith("herofx"),
         "fix": broker.reason or "Add HEROFX_* TradeLocker credentials to .env (demo first)"},
        {"name": "Live trading", "ok": broker.name == "herofx-live",
         "fix": "After demo results hold up: live TradeLocker URL + LIVE_TRADING=true"},
        {"name": "Real estate data", "ok": bool(env("ATTOM_API_KEY")) or os.path.exists(os.path.join(DATA_DIR, "leads.csv")),
         "fix": "Set ATTOM_API_KEY + WHOLESALE_ZIPS, or upload a PropStream/BatchLeads export as leads.csv below"},
        {"name": "Wholesale footprint", "ok": True,
         "fix": ", ".join(sorted(deals.states_cleared()))},
        {"name": "Gmail", "ok": mailer.configured() and bool(env("BUSINESS_ADDRESS")),
         "fix": "GMAIL_ADDRESS + GMAIL_APP_PASSWORD + BUSINESS_NAME/BUSINESS_ADDRESS in .env"},
    ]


@app.get("/api/history")
def history(business: str | None = None, kind: str | None = None, agent: str | None = None, limit: int = 50):
    return cc.history(business, kind, agent, min(limit, 500))


@app.post("/api/cycle")
def trigger(business: str | None = None):
    if cc.get("running", False) and _lock.locked():
        raise HTTPException(409, "A cycle is already running.")
    threading.Thread(target=_cycle, args=([business] if business else None,), daemon=True).start()
    return {"started": True}


@app.post("/api/pause")
def pause(paused: bool = True):
    cc.set("paused", paused)
    return {"paused": paused}


@app.post("/api/business/{bid}/pause")
def pause_business(bid: str, paused: bool = True):
    try:
        org.business(bid)
    except KeyError:
        raise HTTPException(404, f"Unknown business '{bid}'.")
    cur = org.paused_businesses(cc)
    cur = cur | {bid} if paused else cur - {bid}
    cc.set("paused_businesses", sorted(cur))
    cc.event("command", f"{bid}-hq", bid, "rejected" if paused else "approved",
             f"{bid} {'paused' if paused else 'resumed'}")
    return {"paused_businesses": sorted(cur)}


@app.post("/api/kill-switch")
def kill_switch(on: bool = True):
    acct = cc.get("trading.account", {}) or {}
    acct["kill_switch"] = on
    cc.set("trading.account", acct)
    cc.event("command", "trading-hq", "trading", "rejected" if on else "approved",
             "KILL SWITCH ON: trading halted" if on else "Kill switch released")
    return {"kill_switch": on}


app.mount("/output", StaticFiles(directory=OUT_DIR), name="output")

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and len(auth.password()) < 10:
        raise SystemExit("Refusing to start: the server is reachable from other machines, so set "
                         "AGENCY_PASSWORD to a password of at least 10 characters.")
    cc.set("running", False)
    if os.environ.get("AGENCY_AUTORUN", "1") == "1":
        threading.Thread(target=scheduler, daemon=True).start()
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", 8000)),
                proxy_headers=True, forwarded_allow_ips="*")
