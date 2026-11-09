"""Hard risk limits.

These checks are plain code, not model judgment: a trade that breaks any limit is
refused before it reaches a broker, whatever any agent says.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


def _env_list(k: str, default: str) -> tuple:
    return tuple(x.strip() for x in os.environ.get(k, default).split(",") if x.strip())


@dataclass
class RiskLimits:
    """Quantities are in contracts for futures and lots for CFDs (HeroFX), so they may be fractional."""
    max_contracts: float = float(os.environ.get("RISK_MAX_CONTRACTS", 2))
    min_qty: float = float(os.environ.get("TRADING_MIN_QTY", 0.01))
    max_daily_loss: float = float(os.environ.get("RISK_MAX_DAILY_LOSS", 300))      # USD
    max_drawdown: float = float(os.environ.get("RISK_MAX_DRAWDOWN", 1500))         # USD from peak
    max_risk_per_trade: float = float(os.environ.get("RISK_MAX_PER_TRADE", 150))   # USD at stop
    min_oos_trades: int = 20         # strategy must have this many out-of-sample trades
    min_oos_sharpe: float = 0.5      # and at least this out-of-sample Sharpe
    allowed_symbols: tuple = field(default_factory=lambda: _env_list(
        "TRADING_ALLOWED_SYMBOLS", "US500,NAS100,US30,XAUUSD,USOIL,MES,MNQ,MGC,MCL"))


def check_order(order: dict, account: dict, strategy_oos: dict, limits: RiskLimits,
                point_value: float) -> list[str]:
    """Return a list of violations (empty list = passes)."""
    v: list[str] = []
    if account.get("kill_switch"):
        v.append("Kill switch is ON: all trading halted.")
    action = order.get("action")
    if action in ("enter_long", "enter_short"):
        try:
            qty = float(order.get("contracts") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if order.get("symbol") not in limits.allowed_symbols:
            v.append(f"Symbol {order.get('symbol')} is not on the allowed list.")
        if qty < limits.min_qty or qty > limits.max_contracts:
            v.append(f"Size {qty} outside {limits.min_qty}..{limits.max_contracts}.")
        entry, stop = order.get("entry"), order.get("stop")
        if not stop:
            v.append("Every entry needs a stop loss.")
        elif entry and isinstance(entry, (int, float)) and isinstance(stop, (int, float)):
            risk = abs(float(entry) - float(stop)) * point_value * max(qty, limits.min_qty)
            if risk > limits.max_risk_per_trade:
                v.append(f"Risk at stop ${risk:,.0f} exceeds per-trade limit ${limits.max_risk_per_trade:,.0f}.")
            wrong_side = (action == "enter_long" and float(stop) >= float(entry)) or \
                         (action == "enter_short" and float(stop) <= float(entry))
            if wrong_side:
                v.append("Stop is on the wrong side of entry.")
        if account.get("daily_pnl", 0) <= -limits.max_daily_loss:
            v.append(f"Daily loss limit ${limits.max_daily_loss:,.0f} reached.")
        if account.get("peak_equity", 0) - account.get("equity", 0) >= limits.max_drawdown:
            v.append(f"Max drawdown ${limits.max_drawdown:,.0f} reached.")
        if strategy_oos.get("trades", 0) < limits.min_oos_trades:
            v.append(f"Strategy has only {strategy_oos.get('trades', 0)} out-of-sample trades "
                     f"(need {limits.min_oos_trades}).")
        if strategy_oos.get("avg_sharpe", 0) < limits.min_oos_sharpe:
            v.append(f"Out-of-sample Sharpe {strategy_oos.get('avg_sharpe', 0)} below "
                     f"{limits.min_oos_sharpe}.")
        if strategy_oos.get("data_source") == "synthetic":
            v.append("Strategy was validated on synthetic data; connect HeroFX (real price history) or load data/<SYMBOL>.csv.")
    return v
