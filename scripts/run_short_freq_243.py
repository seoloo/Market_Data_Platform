"""문서 243 — Altair 숏이 과거에 얼마나 자주 나왔나 (점검, 판정 없음).

사용 예::

    python -m scripts.run_short_freq_243

232 엔진이 고정해 둔 자료(2020-06-01 ~ 2026-08-31)만 다시 읽는다 — 새 계산 · 새 자료 없음.

* 매일 롱 목표(T/v2): ``reports/wallet_grid_20261013/features.json`` 의 ``targets``.
* 4 시간 숏 신호(RSI7 70 꺾임 · 일봉 ADX > 25 · −DI > +DI, 롱 목표와 무관): 같은 파일의 ``signals``.
* 실제 숏: 232 결과의 ``OWN:AG4:SHORT_MAX:BASE`` 거래 기록(롱 목표 0 일 때만 · 48 시간 · +3% 손절).
  지금 Altair(235 AG4 + 숏 2 배)와 진입 규칙이 같다(235 의 숏 횟수 147 vs 232 148).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
FEATURES = REPO / "reports" / "wallet_grid_20261013" / "features.json"
R232 = REPO / "reports" / "ag_short_own_gate_232" / "result.json"
CASE = "OWN:AG4:SHORT_MAX:BASE"
OUT = REPO / "reports" / "short_freq_243"
COINS = ("BTCUSDT", "ETHUSDT")
DAY_MS = 86_400_000


def day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def runs(flags: Sequence[bool]) -> list[tuple[int, int]]:
    """True 가 이어진 구간들 → (시작 색인, 길이)."""
    out: list[tuple[int, int]] = []
    start = None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - start))
            start = None
    if start is not None:
        out.append((start, len(flags) - start))
    return out


def gaps_days(entries: Sequence[int], lo: int, hi: int) -> list[tuple[int, int, float]]:
    """숏 진입 사이 빈 기간(앞뒤 끝 포함) → (시작 ms, 끝 ms, 일)."""
    pts = [lo, *sorted(entries), hi]
    return [(a, b, (b - a) / DAY_MS) for a, b in pairwise(pts)]


def run_summary(lengths: Sequence[int]) -> dict[str, Any]:
    if not lengths:
        return {"count": 0}
    return {
        "count": len(lengths),
        "median_days": statistics.median(lengths),
        "max_days": max(lengths),
        "over_60_days": sum(1 for x in lengths if x > 60),
    }


def analyse(features: Sequence[dict[str, Any]], trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    daily = [f for f in features if f["targets"] is not None]
    days = [day(f["at_ms"]) for f in daily]
    goal = {c: [float(f["targets"][c]) for f in daily] for c in COINS}
    zero = {c: [g == 0 for g in goal[c]] for c in COINS}
    both_on = [not zero["BTCUSDT"][i] and not zero["ETHUSDT"][i] for i in range(len(days))]
    goal_out: dict[str, Any] = {}
    for c in COINS:
        rs = runs(zero[c])
        goal_out[c] = {
            "zero_share": round(sum(zero[c]) / len(days), 3),
            "zero_runs": run_summary([n for _, n in rs]),
            "longest_zero": [(days[s], n) for s, n in sorted(rs, key=lambda r: -r[1])[:3]],
        }
    on_runs = runs(both_on)
    # 4 시간 신호: 롱 목표가 있어 막힌 것 / 0 이라 숏 후보였던 것
    goal_at: dict[str, dict[str, float]] = {}
    for f in daily:
        goal_at[day(f["at_ms"])] = {c: float(f["targets"][c]) for c in COINS}
    sig: Counter[str] = Counter()
    sig_year: Counter[str] = Counter()
    for f in features:
        for c in f["signals"]:
            g = goal_at.get(day(f["at_ms"]), {}).get(c)
            kind = "vetoed_by_long" if g is not None and g > 0 else "eligible"
            sig[f"{c}:{kind}"] += 1
            sig_year[f"{day(f['at_ms'])[:4]}:{kind}"] += 1
    shorts = [t for t in trades if t["side"] == "SHORT"]
    lo, hi = features[0]["at_ms"], features[-1]["at_ms"] + 4 * 3_600_000
    gaps = gaps_days([t["entry_ms"] for t in shorts], lo, hi)
    years = (hi - lo) / DAY_MS / 365.25
    per_year: dict[str, dict[str, Any]] = {}
    for t in shorts:
        y = day(t["entry_ms"])[:4]
        row = per_year.setdefault(y, {"count": 0, "BTC": 0, "ETH": 0, "wins": 0, "net": 0.0})
        row["count"] += 1
        row["BTC" if t["symbol"] == "BTCUSDT" else "ETH"] += 1
        row["wins"] += float(t["net"]) > 0
        row["net"] = round(row["net"] + float(t["net"]), 1)
    months = {day(t["entry_ms"])[:7] for t in shorts}
    n_months = round((hi - lo) / DAY_MS / 30.44)
    hold_h = [(t["exit_ms"] - t["entry_ms"]) / 3_600_000 for t in shorts]
    return {
        "period": [day(lo), day(hi - 1)],
        "daily_goal": goal_out,
        "both_long_on": {
            "share": round(sum(both_on) / len(days), 3),
            "runs": run_summary([n for _, n in on_runs]),
            "longest": [(days[s], n) for s, n in sorted(on_runs, key=lambda r: -r[1])[:5]],
        },
        "signals": dict(sig),
        "signals_by_year": dict(sorted(sig_year.items())),
        "shorts": {
            "count": len(shorts),
            "per_year": round(len(shorts) / years, 1),
            "by_year": dict(sorted(per_year.items())),
            "months_with_short": f"{len(months)} / {n_months}",
            "reasons": dict(Counter(t["reason"] for t in shorts)),
            "win_rate": round(sum(float(t["net"]) > 0 for t in shorts) / len(shorts), 3),
            "median_hold_hours": statistics.median(hold_h),
            "first": day(shorts[0]["entry_ms"]) if shorts else None,
            "last": day(shorts[-1]["entry_ms"]) if shorts else None,
        },
        "gaps": {
            "median_days": round(statistics.median(g for *_r, g in gaps), 1),
            "over_30_days": sum(1 for *_r, g in gaps if g > 30),
            "over_90_days": sum(1 for *_r, g in gaps if g > 90),
            "longest": [
                (day(a), day(b), round(g)) for a, b, g in sorted(gaps, key=lambda x: -x[2])[:6]
            ],
        },
    }


def main() -> int:
    features = json.loads(FEATURES.read_text(encoding="utf8"))
    trades = json.loads(R232.read_text(encoding="utf8"))["trades"][CASE]
    out = analyse(features, trades)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
