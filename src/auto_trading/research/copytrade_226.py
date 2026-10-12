"""문서 226 — 바이낸스 카피 트레이딩 상위 리더 거래 기록 패턴 (관찰, 순수 계산).

docs/research/226-binance-copytrade-top10-patterns.md.
받기 · 저장은 ``scripts/run_copytrade_226.py``.
"""

from __future__ import annotations

import bisect
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "HOUR_MS",
    "adds_against",
    "entry_move",
    "pick_top",
    "summarize_trader",
    "trend_on",
]

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
MIN_POSITIONS = 30
MAJORS = frozenset({"BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC"})


def pick_top(
    rows: Sequence[Mapping[str, Any]], counts: Mapping[str, int], n: int = 10
) -> list[Mapping[str, Any]]:
    """180 일 수익 내림차순 목록에서 ACTIVE · 포지션 30 건 이상만, 앞에서 n 명 (226 §1)."""
    out = []
    for r in rows:
        pid = str(r["leadPortfolioId"])
        if r.get("status", "ACTIVE") != "ACTIVE" or counts.get(pid, 0) < MIN_POSITIONS:
            continue
        out.append(r)
        if len(out) == n:
            break
    return out


def adds_against(
    pos: Mapping[str, Any], fills: Sequence[Mapping[str, Any]]
) -> tuple[int, bool] | None:
    """(여는 체결 수, 뒤 체결이 앞 체결보다 불리한 가격이었나). 체결이 없으면 None.

    여는 체결 = 그 포지션 기간(열림 − 1 초 ~ 닫힘) 안의 같은 종목 · 같은 방향 쪽 체결 중
    롱이면 BUY, 숏이면 SELL.
    """
    side = str(pos["side"]).upper()
    want = "BUY" if side == "LONG" else "SELL"
    lo, hi = int(pos["opened"]) - 1000, int(pos["closed"])
    opens = sorted(
        (
            f
            for f in fills
            if f["symbol"] == pos["symbol"]
            and str(f.get("positionSide", side)).upper() in (side, "BOTH")
            and f["side"] == want
            and lo <= int(f["time"]) <= hi
        ),
        key=lambda f: int(f["time"]),
    )
    if not opens:
        return None
    first = float(opens[0]["price"])
    worse = any(
        (float(f["price"]) < first) if side == "LONG" else (float(f["price"]) > first)
        for f in opens[1:]
    )
    return len(opens), worse


def entry_move(closes_1h: Sequence[tuple[int, float]], at_ms: int) -> float | None:
    """진입 직전 24 시간 수익 (진입 시각 전에 끝난 1 시간봉 종가 기준)."""
    times = [t for t, _ in closes_1h]
    i = bisect.bisect_right(times, at_ms - HOUR_MS) - 1  # 진입 전에 끝난 마지막 봉(열린 시각 기준)
    j = bisect.bisect_right(times, at_ms - HOUR_MS - DAY_MS) - 1
    if i < 0 or j < 0 or times[i] - times[j] < DAY_MS - HOUR_MS:
        return None
    return closes_1h[i][1] / closes_1h[j][1] - 1


def trend_on(daily: Sequence[tuple[int, float]], at_ms: int) -> bool | None:
    """진입일 전날까지 끝난 BTC 일봉으로 T/v2 부호 평균(20 · 60 · 120) > 0 인가."""
    closes = [c for t, c in daily if t + DAY_MS <= at_ms]
    if len(closes) < 121:
        return None
    last = closes[-1]
    signs = [(last > closes[-1 - k]) - (last < closes[-1 - k]) for k in (20, 60, 120)]
    return sum(signs) > 0


def _share(xs: Sequence[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def summarize_trader(
    positions: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    *,
    moves: Sequence[float | None],
    trends: Sequence[bool | None],
    since_ms: int,
    split_ms: int,
) -> dict[str, Any]:
    """226 §2 의 1 ~ 8. ``moves`` · ``trends`` 는 positions 와 같은 순서."""
    rows = [
        (p, m, t)
        for p, m, t in zip(positions, moves, trends, strict=True)
        if p.get("closed") and int(p["closed"]) >= since_ms
    ]
    ps = [p for p, _, _ in rows]
    if not ps:
        return {"positions": 0}
    pnl = [float(p["closingPnl"]) for p in ps]
    total = sum(pnl)
    sym = Counter(p["symbol"] for p in ps)
    sym_pnl: dict[str, float] = {}
    for p, x in zip(ps, pnl, strict=True):
        sym_pnl[p["symbol"]] = sym_pnl.get(p["symbol"], 0.0) + x
    hold_h = [(int(p["closed"]) - int(p["opened"])) / HOUR_MS for p in ps]
    lev = [float(p.get("leverage") or 0) for p in ps]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    longs = [p for p in ps if str(p["side"]).upper() == "LONG"]
    gains_sorted = sorted(wins, reverse=True)
    top_k = max(1, len(ps) // 20)
    adds = [adds_against(p, fills) for p in ps]
    adds_ok = [a for a in adds if a is not None]
    mv = [m for _, m, _ in rows if m is not None]
    tr = [t for _, _, t in rows if t is not None]
    first = sum(x for p, x in zip(ps, pnl, strict=True) if int(p["closed"]) < split_ms)
    second = total - first
    return {
        "positions": len(ps),
        "pnl": total,
        "top_symbols": [(s, c, round(sym_pnl[s], 2)) for s, c in sym.most_common(5)],
        "top5_share": sum(c for _, c in sym.most_common(5)) / len(ps),
        "major_share": sum(c for s, c in sym.items() if s in MAJORS) / len(ps),
        "symbols": len(sym),
        "long_share": len(longs) / len(ps),
        "long_pnl": sum(float(p["closingPnl"]) for p in longs),
        "hold_median_h": statistics.median(hold_h),
        "hold_buckets": {
            "<1h": _share([h < 1 for h in hold_h]),
            "1h-1d": _share([1 <= h < 24 for h in hold_h]),
            "1-7d": _share([24 <= h < 168 for h in hold_h]),
            ">7d": _share([h >= 168 for h in hold_h]),
        },
        "lev_median": statistics.median(lev),
        "lev_max": max(lev),
        "win_rate": len(wins) / len(ps),
        "avg_win": statistics.fmean(wins) if wins else None,
        "avg_loss": statistics.fmean(losses) if losses else None,
        "worst_over_avg_win": (min(losses) / statistics.fmean(wins)) if wins and losses else None,
        "top5pct_profit_share": (sum(gains_sorted[:top_k]) / total) if total > 0 else None,
        "adds_checked": len(adds_ok),
        "multi_fill_share": _share([a[0] > 1 for a in adds_ok]),
        "add_against_share": _share([a[1] for a in adds_ok]),
        "entry_24h_move_median": statistics.median(mv) if mv else None,
        "entry_after_drop_share": _share([m < 0 for m in mv]),
        "btc_trend_on_share": _share(tr),
        "halves_pnl": [first, second],
    }
