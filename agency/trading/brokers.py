"""Broker adapters.

HeroFX accounts run on TradeLocker (or MT5). This adapter uses TradeLocker's
official API, which works on any OS. Credentials come from .env only:

    HEROFX_TL_ENV=https://demo.tradelocker.com   # or https://live.tradelocker.com
    HEROFX_EMAIL=...        HEROFX_PASSWORD=...     HEROFX_SERVER=...   (shown on the TradeLocker login screen)
    LIVE_TRADING=true       # required in addition to a live environment URL before real money is used

With demo credentials the desk trades a real broker demo account (real quotes and fills,
no money at risk) — the recommended way to prove the strategy first.
With no credentials it falls back to the in-memory PaperBroker.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger("agency.broker")


@dataclass
class PaperBroker:
    """Simulated account. Fills at the requested entry price; stops/targets are checked each cycle."""
    name: str = "paper"
    reason: str = ""
    manages_exits: bool = False      # the orchestrator marks stops/targets for paper positions
    fills: list = field(default_factory=list)

    def place(self, order: dict) -> dict:
        fill = {**order, "ts": time.time(), "broker": self.name, "status": "filled"}
        self.fills.append(fill)
        return fill

    def close(self, symbol: str) -> dict:
        return {"symbol": symbol, "broker": self.name, "status": "closed"}

    def bars(self, symbol: str, n: int) -> pd.DataFrame | None:
        return None

    def account(self) -> dict | None:
        return None

    def instrument(self, symbol: str) -> dict:
        return {}


class TradeLockerBroker:
    """HeroFX via TradeLocker. Stops and targets are attached to every order at the broker."""
    manages_exits = True

    def __init__(self):
        from tradelocker import TLAPI
        env = os.environ.get("HEROFX_TL_ENV", "https://demo.tradelocker.com").rstrip("/")
        self.live = "live." in env
        if self.live and os.environ.get("LIVE_TRADING", "").lower() != "true":
            raise RuntimeError("Live TradeLocker URL configured but LIVE_TRADING is not 'true'.")
        for k in ("HEROFX_EMAIL", "HEROFX_PASSWORD", "HEROFX_SERVER"):
            if not os.environ.get(k):
                raise RuntimeError(f"{k} not set.")
        self.name = "herofx-live" if self.live else "herofx-demo"
        self.reason = ""
        self.api = TLAPI(environment=env, username=os.environ["HEROFX_EMAIL"],
                         password=os.environ["HEROFX_PASSWORD"], server=os.environ["HEROFX_SERVER"],
                         account_id=int(os.environ.get("HEROFX_ACCOUNT_ID", 0) or 0), log_level="warning")
        self._ids: dict[str, int] = {}

    def _id(self, symbol: str) -> int:
        if symbol not in self._ids:
            self._ids[symbol] = self.api.get_instrument_id_from_symbol_name(symbol)
        return self._ids[symbol]

    def instrument(self, symbol: str) -> dict:
        try:
            return self.api.get_instrument_details(self._id(symbol))
        except Exception as e:
            log.warning("instrument details for %s failed: %s", symbol, e)
            return {}

    def bars(self, symbol: str, n: int = 1500) -> pd.DataFrame | None:
        end = int(time.time() * 1000)
        start = end - int(n * 1.5 * 86400 * 1000)
        df = self.api.get_price_history(self._id(symbol), resolution="1D", start_timestamp=start, end_timestamp=end)
        if df is None or df.empty:
            return None
        df = df.rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df.index = pd.to_datetime(df["time"], unit="ms")
        return df[["open", "high", "low", "close", "volume"]].tail(n)

    def place(self, order: dict) -> dict:
        side = "buy" if order["action"] == "enter_long" else "sell"
        oid = self.api.create_order(self._id(order["symbol"]), quantity=float(order["contracts"]), side=side,
                                    type_="market", stop_loss=float(order["stop"]), stop_loss_type="absolute",
                                    take_profit=float(order["take_profit"]) if order.get("take_profit") else None,
                                    take_profit_type="absolute" if order.get("take_profit") else None)
        if not oid:
            raise RuntimeError("Broker rejected the order (no order id returned).")
        return {**order, "order_id": oid, "ts": time.time(), "broker": self.name, "status": "submitted"}

    def close(self, symbol: str) -> dict:
        pos = self.api.get_all_positions()
        iid, closed = self._id(symbol), []
        if pos is not None and not pos.empty:
            for _, p in pos[pos["tradableInstrumentId"] == iid].iterrows():
                self.api.close_position(position_id=int(p["id"]))
                closed.append(int(p["id"]))
        return {"symbol": symbol, "closed_positions": closed, "broker": self.name, "status": "closed"}

    def positions(self, symbol: str) -> list[dict]:
        pos = self.api.get_all_positions()
        if pos is None or pos.empty:
            return []
        return pos[pos["tradableInstrumentId"] == self._id(symbol)].to_dict("records")

    def account(self) -> dict | None:
        s = self.api.get_account_state()
        bal = float(s.get("balance", 0) or 0)
        eq = float(s.get("projectedBalance", 0) or 0) or bal + float(s.get("openGrossPnL", 0) or 0)
        return {"balance": bal, "equity": eq, "today_net": float(s.get("todayNet", 0) or 0), "raw": s}


_broker = None


def get_broker():
    """Singleton: TradeLocker when configured, else paper (with the reason recorded)."""
    global _broker
    if _broker is None:
        if os.environ.get("HEROFX_EMAIL"):
            try:
                _broker = TradeLockerBroker()
            except Exception as e:
                log.error("HeroFX/TradeLocker unavailable, paper trading instead: %s", e)
                _broker = PaperBroker(reason=str(e))
        else:
            _broker = PaperBroker(reason="No HEROFX_* credentials in .env")
    return _broker
