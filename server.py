"""Web server: the 3D city UI, the Command Center API, and the autonomous scheduler.

    python server.py                    # UI at http://localhost:8000, cycles every AGENCY_INTERVAL_MIN
"""
from __future__ import annotations

import logging
import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agency import llm, org
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


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


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
        {"name": "Claude agents", "ok": llm.online(), "fix": "Add ANTHROPIC_API_KEY to .env"},
        {"name": "HeroFX account", "ok": broker.name.startswith("herofx"),
         "fix": broker.reason or "Add HEROFX_* TradeLocker credentials to .env (demo first)"},
        {"name": "Live trading", "ok": broker.name == "herofx-live",
         "fix": "After demo results hold up: live TradeLocker URL + LIVE_TRADING=true"},
        {"name": "Real estate data", "ok": bool(env("ATTOM_API_KEY")) or os.path.exists("data/leads.csv"),
         "fix": "Set ATTOM_API_KEY + WHOLESALE_ZIPS, or drop a PropStream/BatchLeads export at data/leads.csv"},
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
    org.business(bid)  # 404s on unknown ids via KeyError
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


app.mount("/output", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "output")), name="output")

if __name__ == "__main__":
    cc.set("running", False)
    if os.environ.get("AGENCY_AUTORUN", "1") == "1":
        threading.Thread(target=scheduler, daemon=True).start()
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
