"""Market data, strategy, backtester and walk-forward optimizer.

Self-improvement works like this: on each cycle the optimizer searches strategy
parameters on a *training* window, then scores the winner on the *next, unseen*
window (walk-forward). A new parameter set is promoted only if its out-of-sample
results beat the current champion's on the same data. That is what keeps
"improving the success rate" from turning into curve-fitting.
"""
from __future__ import annotations

import itertools
import math
import os
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..paths import DATA_DIR  # noqa: E402

# Instrument specs: point value in USD per 1 contract/lot, commission+slippage per round trip.
# CFD point values differ between brokers, so for HeroFX the broker's own contract size is
# used when available (see spec()); set TRADING_POINT_VALUE to override.
CONTRACTS = {
    "US500": {"point_value": 1.0, "cost_rt": 0.6, "name": "S&P 500 CFD", "cfd": True},
    "NAS100": {"point_value": 1.0, "cost_rt": 1.5, "name": "Nasdaq-100 CFD", "cfd": True},
    "US30": {"point_value": 1.0, "cost_rt": 2.5, "name": "Dow 30 CFD", "cfd": True},
    "XAUUSD": {"point_value": 100.0, "cost_rt": 25.0, "name": "Gold CFD (100 oz/lot)", "cfd": True},
    "USOIL": {"point_value": 1000.0, "cost_rt": 30.0, "name": "WTI Crude CFD (1000 bbl/lot)", "cfd": True},
    "MES": {"point_value": 5.0, "cost_rt": 2.5, "name": "Micro E-mini S&P 500"},
    "MNQ": {"point_value": 2.0, "cost_rt": 2.5, "name": "Micro E-mini Nasdaq-100"},
    "MGC": {"point_value": 10.0, "cost_rt": 3.0, "name": "Micro Gold"},
    "MCL": {"point_value": 100.0, "cost_rt": 3.0, "name": "Micro WTI Crude"},
}


_SPEC_CACHE: dict[str, dict] = {}


def spec(symbol: str) -> dict:
    """Point value / costs for a symbol: env override > broker contract size > built-in default."""
    if symbol in _SPEC_CACHE:
        return _SPEC_CACHE[symbol]
    s = dict(CONTRACTS.get(symbol, {"point_value": 1.0, "cost_rt": 0.0, "name": symbol, "cfd": True}))
    s["point_value_source"] = "default"
    try:
        from .brokers import TradeLockerBroker, get_broker
        b = get_broker()
        if isinstance(b, TradeLockerBroker):
            d = b.instrument(symbol)
            cs = d.get("contractSize") or d.get("lotSize")
            if cs:
                s["point_value"], s["point_value_source"] = float(cs), "broker"
    except Exception:
        pass
    if os.environ.get("TRADING_POINT_VALUE"):
        s["point_value"], s["point_value_source"] = float(os.environ["TRADING_POINT_VALUE"]), "env"
    if os.environ.get("TRADING_COST_RT"):
        s["cost_rt"] = float(os.environ["TRADING_COST_RT"])
    _SPEC_CACHE[symbol] = s
    return s


# ------------------------------- data -------------------------------------- #
def load_bars(symbol: str, n: int = 1500) -> tuple[pd.DataFrame, str]:
    """Daily OHLCV bars. Priority: HeroFX/TradeLocker price history when connected,
    then data/<SYMBOL>.csv, then synthetic data (clearly labelled; compliance won't trade on it)."""
    try:
        from .brokers import get_broker
        df = get_broker().bars(symbol, n)
        if df is not None and len(df) > 300:
            return df, "broker"
    except Exception:
        pass
    path = os.path.join(DATA_DIR, f"{symbol}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=[0], index_col=0)
        df.columns = [c.lower() for c in df.columns]
        return df[["open", "high", "low", "close", "volume"]].dropna().tail(n), "csv"
    return synthetic_bars(symbol, n), "synthetic"


def synthetic_bars(symbol: str, n: int) -> pd.DataFrame:
    """Regime-switching random walk. For wiring and demos only — never trade on it."""
    rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
    base = {"MES": 5000, "MNQ": 18000, "MGC": 2300, "MCL": 75, "US500": 5000, "NAS100": 18000,
            "US30": 39000, "XAUUSD": 2300, "USOIL": 75}.get(symbol, 1000)
    drift = np.repeat(rng.normal(0.0003, 0.0008, n // 100 + 1), 100)[:n]
    vol = np.repeat(rng.uniform(0.006, 0.018, n // 100 + 1), 100)[:n]
    rets = rng.normal(drift, vol)
    close = base * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.001, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2)))
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.integers(50_000, 300_000, n)}, index=idx)


# ----------------------------- indicators ---------------------------------- #
def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def snapshot(df: pd.DataFrame) -> dict:
    """Compact market state handed to the chart/quant agents."""
    c = df["close"]
    last = float(c.iloc[-1])
    return {
        "last": round(last, 2),
        "chg_1d_pct": round(100 * (c.iloc[-1] / c.iloc[-2] - 1), 2),
        "chg_20d_pct": round(100 * (c.iloc[-1] / c.iloc[-21] - 1), 2),
        "sma20": round(float(c.rolling(20).mean().iloc[-1]), 2),
        "sma50": round(float(c.rolling(50).mean().iloc[-1]), 2),
        "sma200": round(float(c.rolling(200).mean().iloc[-1]), 2),
        "atr14": round(float(atr(df).iloc[-1]), 2),
        "rsi14": round(float(rsi(c).iloc[-1]), 1),
        "high_20d": round(float(df["high"].tail(20).max()), 2),
        "low_20d": round(float(df["low"].tail(20).min()), 2),
        "high_252d": round(float(df["high"].tail(252).max()), 2),
        "low_252d": round(float(df["low"].tail(252).min()), 2),
        "realized_vol_20d_ann_pct": round(float(c.pct_change().tail(20).std() * math.sqrt(252) * 100), 1),
    }


# ------------------------------ strategy ----------------------------------- #
@dataclass(frozen=True)
class Params:
    fast: int = 20          # trend filter fast MA
    slow: int = 100         # trend filter slow MA
    breakout: int = 20      # Donchian breakout lookback
    stop_atr: float = 2.0   # initial stop distance in ATRs
    target_atr: float = 4.0  # profit target in ATRs


def signals(df: pd.DataFrame, p: Params) -> pd.Series:
    """+1 long / -1 short / 0 flat entry signal on each bar's close (trend-filtered breakout)."""
    c = df["close"]
    up = c.rolling(p.fast).mean() > c.rolling(p.slow).mean()
    hi = df["high"].rolling(p.breakout).max().shift()
    lo = df["low"].rolling(p.breakout).min().shift()
    sig = pd.Series(0, index=df.index)
    sig[(c > hi) & up] = 1
    sig[(c < lo) & ~up] = -1
    return sig


def backtest(df: pd.DataFrame, p: Params, symbol: str = "MES", contracts: int = 1) -> dict:
    """Bar-by-bar backtest: enter next open, ATR stop/target, costs included."""
    sp = spec(symbol)
    pv, cost = sp["point_value"], sp["cost_rt"]
    sig = signals(df, p).values
    a = atr(df).values
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    trades: list[float] = []
    pos, entry, stop, target = 0, 0.0, 0.0, 0.0
    equity = [0.0]
    for i in range(1, len(df)):
        pnl_bar = 0.0
        if pos == 0 and sig[i - 1] != 0 and not np.isnan(a[i - 1]):
            pos, entry = int(sig[i - 1]), o[i]
            stop = entry - pos * p.stop_atr * a[i - 1]
            target = entry + pos * p.target_atr * a[i - 1]
        if pos != 0:
            hit_stop = l[i] <= stop if pos > 0 else h[i] >= stop
            hit_tgt = h[i] >= target if pos > 0 else l[i] <= target
            exit_px = stop if hit_stop else (target if hit_tgt else None)   # stop first: conservative
            if exit_px is not None:
                t = pos * (exit_px - entry) * pv * contracts - cost * contracts
                trades.append(t); pnl_bar = t; pos = 0
        equity.append(equity[-1] + pnl_bar)
    return metrics(trades, np.array(equity))


def metrics(trades: list[float], equity: np.ndarray) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0, "net_pnl": 0, "profit_factor": 0, "expectancy": 0,
                "max_drawdown": 0, "sharpe": 0}
    t = np.array(trades)
    wins, losses = t[t > 0], t[t <= 0]
    dd = float((np.maximum.accumulate(equity) - equity).max())
    daily = np.diff(equity)
    sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 3),
        "net_pnl": round(float(t.sum()), 2),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else 99.0,
        "expectancy": round(float(t.mean()), 2),
        "max_drawdown": round(dd, 2),
        "sharpe": round(sharpe, 2),
    }


def score(m: dict) -> float:
    """Objective used for ranking: risk-adjusted, penalizes too few trades and deep drawdown."""
    if m["trades"] < 8:
        return -1e9
    return m["sharpe"] * math.sqrt(min(m["trades"], 60) / 60) - 0.0001 * m["max_drawdown"]


GRID = {"fast": [10, 20, 50], "slow": [100, 150, 200], "breakout": [10, 20, 55],
        "stop_atr": [1.5, 2.0, 3.0], "target_atr": [2.0, 3.0, 4.0, 6.0]}


def walk_forward(df: pd.DataFrame, symbol: str, champion: Params, folds: int = 4) -> dict:
    """Optimize on each training window, score on the following unseen window.

    Returns the champion's and challenger's stitched out-of-sample results and whether
    the challenger should be promoted.
    """
    n = len(df)
    test_len = n // (folds + 2)
    oos_champ, oos_chal, chosen = [], [], []
    for k in range(folds):
        train = df.iloc[: n - (folds - k) * test_len]
        test = df.iloc[n - (folds - k) * test_len - 250: n - (folds - k - 1) * test_len]  # 250 bars warm-up
        best, best_s = champion, -1e18
        for combo in itertools.product(*GRID.values()):
            p = Params(*combo)
            if p.fast >= p.slow:
                continue
            s = score(backtest(train.tail(750), p, symbol))
            if s > best_s:
                best, best_s = p, s
        chosen.append(best)
        oos_chal.append(backtest(test, best, symbol))
        oos_champ.append(backtest(test, champion, symbol))

    def agg(ms: list[dict]) -> dict:
        tr = sum(m["trades"] for m in ms)
        return {"trades": tr,
                "net_pnl": round(sum(m["net_pnl"] for m in ms), 2),
                "win_rate": round(sum(m["win_rate"] * m["trades"] for m in ms) / tr, 3) if tr else 0,
                "avg_sharpe": round(float(np.mean([m["sharpe"] for m in ms])), 2),
                "worst_drawdown": max(m["max_drawdown"] for m in ms),
                "positive_folds": sum(m["net_pnl"] > 0 for m in ms)}

    champ, chal = agg(oos_champ), agg(oos_chal)
    challenger = chosen[-1]  # most recent training window's optimum
    promote = (challenger != champion and chal["avg_sharpe"] > champ["avg_sharpe"] + 0.1
               and chal["net_pnl"] > champ["net_pnl"] and chal["positive_folds"] >= folds // 2 + 1)
    return {"champion": asdict(champion), "challenger": asdict(challenger),
            "oos_champion": champ, "oos_challenger": chal, "promote": promote, "folds": folds}
