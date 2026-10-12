"""문서 228 — 롱 T/v2 × 2 · 3 · 4 배 + 짧은 숏 1 배, 후향 파일럿 1 회. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_brief_short_228    # reports/brief_short_228/result.json · 화면
"""

from __future__ import annotations

import bisect
import json
import math
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_o_overlay import FROM_MS, TO_MS
from scripts.run_o_overlay import load as load_overlay

from auto_trading.backtest.overlay import OverlayConfig, simulate_overlay
from auto_trading.research.brief_short_228 import (
    combine,
    random_short_totals,
    short_ok,
    simulate_shorts,
    total,
)
from auto_trading.research.laya_short_227 import DAY_MS, Bar, features

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
OUT = REPO / "reports" / "brief_short_228"
COINS = ("BTCUSDT", "ETHUSDT")
SPLIT = int(datetime(2023, 6, 1, tzinfo=UTC).timestamp() * 1000)
INITIAL = 10_000.0


def load_short() -> tuple[
    dict[str, dict[int, float]], dict[str, dict[int, Bar]], dict[str, list[tuple[int, float]]]
]:
    spot: dict[str, dict[int, float]] = {}
    perp: dict[str, dict[int, Bar]] = {}
    fund: dict[str, list[tuple[int, float]]] = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as c:
        for s in COINS:
            spot[s] = {
                int(ms): float(cl)
                for ms, cl in c.execute(
                    "SELECT open_ms, close FROM candles WHERE exchange='binance_spot'"
                    " AND symbol=? AND timeframe='1d'",
                    (s,),
                )
            }
            perp[s] = {
                int(ms): Bar(float(o), float(h), float(lo), float(cl))
                for ms, o, h, lo, cl in c.execute(
                    "SELECT open_ms, open, high, low, close FROM candles"
                    " WHERE exchange='binance_usdm' AND symbol=? AND timeframe='1d'",
                    (s,),
                )
            }
            fund[s] = [
                (int(ms), float(r))
                for ms, r in c.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange='binance_usdm'"
                    " AND symbol=? ORDER BY funding_ms",
                    (s,),
                )
            ]
    return spot, perp, fund


def signals(
    spot: dict[str, dict[int, float]], fund: dict[str, list[tuple[int, float]]]
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """(조건 맞는 날, 평가 가능한 모든 날) — 코인마다."""
    sig: dict[str, list[int]] = {}
    alld: dict[str, list[int]] = {}
    for s in COINS:
        days = sorted(spot[s])
        closes = [spot[s][d] for d in days]
        ft = [t for t, _ in fund[s]]
        sig[s], alld[s] = [], []
        for i, d in enumerate(days):
            if d < FROM_MS or d > TO_MS:
                continue
            end = d + DAY_MS
            rates = [
                r
                for _, r in fund[s][
                    bisect.bisect_left(ft, end - 7 * DAY_MS) : bisect.bisect_left(ft, end)
                ]
            ]
            f = features(closes[: i + 1], rates)
            if f is None:
                continue
            alld[s].append(d)
            if short_ok(f):
                sig[s].append(d)
    return sig, alld


def stats(returns: list[float]) -> dict[str, float]:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    years = len(returns) / 365
    sd = statistics.stdev(returns)
    return {
        "cagr": eq ** (1 / years) - 1 if eq > 0 else -1.0,
        "sharpe": statistics.fmean(returns) / sd * math.sqrt(365) if sd > 0 else 0.0,
        "mdd": mdd,
        "final_x": eq,
    }


def short_summary(trades: list[Any]) -> dict[str, Any]:
    rets = [t.ret for t in trades]
    first = [t for t in trades if t.signal_day < SPLIT]
    second = [t for t in trades if t.signal_day >= SPLIT]
    return {
        "trades": len(trades),
        "total": total(trades),
        "halves": [total(first), total(second)],
        "win_rate": sum(r > 0 for r in rets) / len(rets) if rets else None,
        "stops": sum(t.stopped for t in trades),
        "worst": min(rets) if rets else None,
        "by_coin": {c: total([t for t in trades if t.coin == c]) for c in COINS},
    }


def main() -> int:
    o_spot, o_perp, o_fund = load_overlay()
    spot, perp, fund = load_short()
    sig, alld = signals(spot, fund)
    shorts = {h: simulate_shorts(sig, h, perp, fund) for h in (1, 3)}
    res: dict[str, Any] = {"signal_days": {c: len(v) for c, v in sig.items()}}
    for h, tr in shorts.items():
        s = short_summary(tr)
        rnd = random_short_totals(alld, len(tr), h, perp, fund) if tr else []
        s["random_median"] = statistics.median(rnd) if rnd else None
        s["random_share_below"] = sum(x < s["total"] for x in rnd) / len(rnd) if rnd else None
        res[f"S_{h}d"] = s
    s1 = res["S_1d"]
    if s1["trades"] == 0 or s1["total"] <= 0:
        verdict = "불리"
    elif all(x > 0 for x in s1["halves"]) and (s1["random_share_below"] or 0) >= 0.95:
        verdict = "지지"
    else:
        verdict = "불명"
    res["verdict(S_1d)"] = verdict
    table: dict[str, Any] = {}
    for k in (2, 3, 4):
        cfg = OverlayConfig(
            exposure=float(k),
            catastrophe_stop=0.20,
            halt_drawdown=1.0,
            max_futures_leverage=max(3.0, float(k)),
        )
        r = simulate_overlay(
            o_spot, o_perp, o_fund, from_ms=FROM_MS, to_ms=TO_MS, initial_equity=INITIAL, cfg=cfg
        )
        row: dict[str, Any] = {}
        for name, tr in (("long_only", []), ("long+S1d", shorts[1]), ("long+S3d", shorts[3])):
            rets, halted = combine(r.days, r.returns, tr)
            st = stats(rets)
            st["halted"] = (
                datetime.fromtimestamp(halted / 1000, UTC).date().isoformat() if halted else None
            )
            row[name] = st
        table[f"{k}x"] = row
    res["table"] = table
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
