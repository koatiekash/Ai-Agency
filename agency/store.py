"""Command Center: the shared, permanent record for every business and department.

Everything any agent produces lands here — research reports, executive decisions,
compliance reviews, executed actions, backtest results, marketing assets, CEO
briefings and lessons learned — so any department can read the full history.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

from .paths import DATA_DIR  # noqa: E402

DB_PATH = os.environ.get("AGENCY_DB", os.path.join(DATA_DIR, "command_center.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cycle INTEGER,
    business TEXT NOT NULL,      -- tiktok | wholesale | trading | shared
    dept TEXT NOT NULL,          -- research | hq | compliance | marketing | command | execution
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,          -- report | decision | review | action | backtest | asset | briefing | lesson | kpi
    title TEXT,
    status TEXT,                 -- e.g. approved / rejected / executed / simulated
    payload TEXT NOT NULL        -- JSON
);
CREATE INDEX IF NOT EXISTS idx_records_biz ON records(business, kind, ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    src TEXT NOT NULL,           -- agent id or building id
    dst TEXT NOT NULL,
    business TEXT NOT NULL,
    kind TEXT NOT NULL,          -- handoff | approved | rejected | executed | briefing
    text TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class CommandCenter:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---------------- writes ----------------
    def record(self, *, business: str, dept: str, agent: str, kind: str, payload: Any,
               title: str = "", status: str = "", cycle: int | None = None) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO records(ts,cycle,business,dept,agent,kind,title,status,payload) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (time.time(), cycle, business, dept, agent, kind, title, status,
                 json.dumps(payload, default=str)))
            self.db.commit()
            return cur.lastrowid

    def event(self, src: str, dst: str, business: str, kind: str, text: str = "") -> None:
        with self._lock:
            self.db.execute("INSERT INTO events(ts,src,dst,business,kind,text) VALUES(?,?,?,?,?,?)",
                            (time.time(), src, dst, business, kind, text[:300]))
            self.db.commit()

    def set(self, k: str, v: Any) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v, default=str)))
            self.db.commit()

    def get(self, k: str, default: Any = None) -> Any:
        with self._lock:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def next_cycle(self) -> int:
        n = int(self.get("cycle", 0)) + 1
        self.set("cycle", n)
        return n

    # ---------------- reads ----------------
    def history(self, business: str | None = None, kind: str | None = None,
                agent: str | None = None, limit: int = 50) -> list[dict]:
        q, args = "SELECT * FROM records WHERE 1=1", []
        if business:
            q += " AND business=?"; args.append(business)
        if kind:
            q += " AND kind=?"; args.append(kind)
        if agent:
            q += " AND agent=?"; args.append(agent)
        q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        with self._lock:
            rows = self.db.execute(q, args).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def events_since(self, after_id: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM events WHERE id>? ORDER BY id ASC LIMIT ?",
                                   (after_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def lessons(self, business: str, agent: str | None = None, limit: int = 8) -> list[str]:
        """Most recent lessons for a business (and optionally one agent) — fed back into prompts."""
        q = "SELECT payload FROM records WHERE kind='lesson' AND business IN (?, 'shared')"
        args: list[Any] = [business]
        if agent:
            q += " AND (agent=? OR agent='retrospective' OR agent LIKE '%compliance')"
            args.append(agent)
        q += " ORDER BY id DESC LIMIT ?"; args.append(limit * 6)
        seen: list[str] = []
        with self._lock:
            rows = self.db.execute(q, args).fetchall()
        for r in rows:
            text = json.loads(r["payload"]).get("lesson", "")
            if text and text not in seen:
                seen.append(text)
            if len(seen) >= limit:
                break
        return seen

    def stats(self) -> dict:
        out: dict[str, Any] = {}
        with self._lock:
            rows = self.db.execute(
                "SELECT business, kind, status, COUNT(*) n FROM records GROUP BY business, kind, status").fetchall()
        for r in rows:
            out.setdefault(r["business"], {}).setdefault(r["kind"], {})[r["status"] or "-"] = r["n"]
        return out
