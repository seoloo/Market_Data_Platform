"""Isolated shared-cash replay with frozen limits, no operational state writes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from scripts.tcn_chart_20261018 import data, execution

BOUNDS = ((1593561600000, 1704067200000), (1704067200000, 1735689600000), (1735689600000, data.END))


def replay(
    ds: dict[str, Any], pred: Any, period: int, assets: dict[str, Any], sensitivity: str = "base"
) -> dict[str, Any]:
    import numpy as np

    lo, hi = BOUNDS[period]
    n = (hi - lo) // 60000
    eq, exposure = np.full(n, 10000.0), np.zeros(n)
    cash = Decimal(10000)
    until, cursor, streak, losses_until = lo, 0, 0, lo
    halted = False
    peak = 10000.0
    blocked_days: set[int] = set()
    trades: list[dict[str, Any]] = []
    veto: Counter[str] = Counter()
    opts: dict[str, Any] = {}
    if sensitivity == "cost2":
        opts["cost"] = 2
    elif sensitivity == "delay5m":
        opts["delay"] = 6
    elif sensitivity == "half_volume":
        opts["participation"] = Decimal("0.0005")
    elif sensitivity == "worst_mark":
        opts["worst_mark"] = True
    elif sensitivity != "base":
        raise ValueError("unregistered sensitivity")
    rows = np.flatnonzero(ds["splits"] == period)
    for k in rows:
        t = int(ds["times"][k])
        at = (t - lo) // 60000
        if pred[k] == 0:
            veto["FLAT"] += 1
            continue
        if t < until:
            veto["POSITION_OR_60MIN"] += 1
            continue
        eq[cursor : at + 1] = float(cash)
        cursor = at + 1
        day = t // 86400000
        if halted or day in blocked_days or t < losses_until:
            veto["ACCOUNT_HALT" if halted else "DAILY_OR_STREAK"] += 1
            continue
        symbol = data.SYMBOLS[int(ds["symbols"][k])]
        side = 1 if pred[k] == 1 else -1
        d = assets[symbol]
        notional = cash * Decimal("0.1")
        cost = opts.get("cost", 1)
        # Even stressed round trip + adverse stop allowance remains within risk1%.
        risk_unit = Decimal("0.03") + Decimal("0.0039") * cost
        notional = min(notional, cash * Decimal("0.01") / risk_unit)
        if notional * Decimal("1.5") > cash:
            veto["MARGIN_BUFFER"] += 1
            continue
        e = execution.simulate(d["minutes"], d["funding"], t, side, symbol, notional, **opts)
        if e.qty == 0:
            veto[e.status] += 1
            continue
        pnl, notionals = execution.equity_path(e, d["minutes"], side)
        begin_ms = int(d["minutes"][e.start, 0])
        end_ms = int(d["minutes"][e.end, 0])
        start, end = (begin_ms - lo) // 60000, (end_ms - lo) // 60000
        if end >= n:
            raise ValueError("unpurged execution boundary")
        eq[cursor:start] = float(cash)
        path = float(cash) + pnl
        eq[start : end + 1] = path
        exposure[start : end + 1] = notionals / path
        cursor = end + 1
        peaks = np.maximum.accumulate(np.maximum(path, peak))
        if np.max(1 - path / peaks) >= 0.30:
            halted = True
        peak = max(peak, float(path.max()))
        for day_id in range(begin_ms // 86400000, end_ms // 86400000 + 1):
            day_start = max(0, (day_id * 86400000 - lo) // 60000)
            # UTC opening equity is prior minute close, not future intraday equity.
            day_equity = eq[day_start - 1] if day_start else 10000.0
            left, right = max(start, day_start), min(end + 1, day_start + 1440)
            if left < right and eq[left:right].min() <= day_equity * 0.98:
                blocked_days.add(day_id)
        if e.status == "UNRESOLVED":
            halted = True
            cash = Decimal(str(path[-1]))
        else:
            if abs(pnl[-1] - float(e.net)) > 1e-7:
                raise ValueError("cash/MTM reconciliation")
            cash += e.net
        until = end_ms + 3600000
        streak = streak + 1 if e.net < 0 else 0
        if streak >= 4:
            losses_until = end_ms + 4 * 3600000
            streak = 0
        trades.append(
            {
                "symbol": symbol,
                "side": "LONG" if side == 1 else "SHORT",
                "entry_ms": begin_ms,
                "exit_ms": end_ms,
                "net": float(e.net),
                "gross": float(e.gross),
                "fee": float(e.fees),
                "funding": float(e.funding),
                "turnover": float(e.turnover),
                "price_impact": float(e.price_impact),
                "qty": str(e.qty),
                "entry_notional": float(e.qty * e.entry),
                "partial": e.partial,
                "stopped": e.stopped,
                "pieces": e.pieces,
                "delay_min": e.exit_delay,
                "status": e.status,
                "remaining_qty": str(e.remaining),
            }
        )
    eq[cursor:] = float(cash)
    daily = eq[1439::1440]
    rets = daily / np.r_[10000, daily[:-1]] - 1
    peaks = np.maximum.accumulate(np.r_[10000, eq])[1:]
    monthly: dict[str, float] = {}
    prior = 10000.0
    for i, value in enumerate(daily):
        mon = datetime.fromtimestamp((lo + i * 86400000) / 1000, UTC).strftime("%Y-%m")
        monthly[mon] = float(value)
    for mon, value in monthly.copy().items():
        monthly[mon] = value / prior - 1
        prior = value
    wins, losses = (
        [t["net"] for t in trades if t["net"] > 0],
        [t["net"] for t in trades if t["net"] < 0],
    )
    return {
        "initial": 10000,
        "final": float(cash),
        "net": float(cash) - 10000,
        "cagr": (float(cash) / 10000) ** (365.25 / len(daily)) - 1,
        "sharpe": float(rets.mean() / rets.std(ddof=1) * np.sqrt(365)) if rets.std() > 0 else 0,
        "mdd": float(np.max(1 - eq / peaks)),
        "trades": trades,
        "n_trades": len(trades),
        "exposure_mean": float(exposure.mean()),
        "exposure_max": float(exposure.max()),
        "turnover_initial": sum(t["turnover"] for t in trades) / 10000,
        "fee": sum(t["fee"] for t in trades),
        "price_impact": sum(t["price_impact"] for t in trades),
        "funding": sum(t["funding"] for t in trades),
        "gross": sum(t["gross"] for t in trades),
        "mean_win": float(np.mean(wins)) if wins else 0,
        "mean_loss": float(np.mean(losses)) if losses else 0,
        "mean_trade_bps": float(np.mean([t["net"] / t["entry_notional"] * 10000 for t in trades]))
        if trades
        else 0,
        "partial_entries": sum(t["partial"] for t in trades),
        "delayed_exits": sum(t["delay_min"] > 0 for t in trades),
        "unresolved": sum(t["status"] != "CLOSED" for t in trades),
        "halted": halted,
        "veto": dict(veto),
        "monthly": monthly,
        "worst_month": min(monthly.values()),
        "daily_equity": daily.tolist(),
    }
