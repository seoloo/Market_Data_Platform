"""문서 38 — WA 추세 보유 vs 눌림 매수 · 상승/하락 다이버전스 (CROSS_VENUE_PROXY).

``precheck``: 시장만으로 가설별 노출 주·MDE (WA 보유 상태를 읽지 않는다).
``run``: 사전 점검을 다시 계산해 통과한 가설만 한 번 검정
(월 층화 차이, 주 블록 부트스트랩, Holm) + S_hold.
"""

from __future__ import annotations

import json
import math
import random
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew_edge_power import _aggregate, _chunks, _groups  # noqa: E402
from scripts.audit_wa_elliott_fib_join import spot_minutes  # noqa: E402
from scripts.audit_wa_position_state import _key, _stratified  # noqa: E402
from scripts.explore_wonyotti_trips import SOURCE, build_trips, load_xbtusd  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott.divergence import DivergenceKind  # noqa: E402
from auto_trading.research.elliott.exposure import (  # noqa: E402
    DivergenceIndex,
    ExposureIndex,
    UpState,
    up_state,
)
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    Trigger,
    TriggerResult,
    causal_triggers,
    evaluate_triggers,
)

SEED = 20260925
BOOT = 10_000
H1 = Timeframe.H1.ms
H4 = Timeframe.H4.ms
START = datetime(2018, 3, 1, tzinfo=UTC).timestamp() * 1000
END = datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000
# 가설: (결과 보유 방향)
TESTS = {"T1_long_in_up_run": 1, "T2_long_zone_vs_shallow": 1, "D1_long_after_bull_div": 1,
         "D2_short_after_bear_div": -1}  # fmt: skip

# t, 주, 달, 상승 상태, 강세, 약세, B 1h open
Row = tuple[float, str, str, UpState, bool, bool, int]


def _grid() -> tuple[list[Row], dict[str, int]]:
    four = _aggregate(spot_minutes(), H4)
    hours = _aggregate(spot_minutes(), H1)
    minutes = spot_minutes()
    triggers: list[Trigger] = []
    results: list[TriggerResult] = []
    chunks = _chunks(four)
    for chunk in chunks:
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        found = causal_triggers(replay, chunk_hours, chunk)
        triggers.extend(found)
        results.extend(evaluate_triggers(found, chunk_hours))
    index = ExposureIndex(triggers, results)
    divergence = DivergenceIndex(triggers, chunks)
    closes = [h.close_ms for h in hours]
    rows: list[Row] = []
    for h in hours:
        t = float(h.close_ms + 1)
        if not START <= t < END:
            continue
        k = bisect_left(closes, t) - 1
        if k < 0 or t - closes[k] > 2 * H1:
            continue
        leg = index.fib_leg(int(t))
        if leg is None:
            continue
        week, month = _key(t)
        rows.append(
            (
                t,
                week,
                month,
                up_state(leg, hours[k].close),
                divergence.recent(int(t), DivergenceKind.REGULAR_BULLISH),
                divergence.recent(int(t), DivergenceKind.REGULAR_BEARISH),
                leg.end_hour_open_ms,
            )
        )
    meta = {
        "divergence_events_bullish": sum(
            k is DivergenceKind.REGULAR_BULLISH for _, k in divergence.events
        ),
        "divergence_events_bearish": sum(
            k is DivergenceKind.REGULAR_BEARISH for _, k in divergence.events
        ),
        "grid_hours": len(rows),
    }
    return rows, meta


def _split(name: str, row: Row) -> bool | None:
    """True = 노출, False = 비교군, None = 이 가설에서 제외."""
    _, _, _, state, bull, bear, _ = row
    if name == "T1_long_in_up_run":
        return state is UpState.UP_RUN
    if name == "T2_long_zone_vs_shallow":
        if state is UpState.UP_ZONE:
            return True
        return False if state is UpState.UP_SHALLOW else None
    if name == "D1_long_after_bull_div":
        return bull
    return bear


def precheck(rows: list[Row] | None = None) -> dict[str, object]:
    meta: dict[str, int] = {}
    if rows is None:
        rows, meta = _grid()
    out: dict[str, object] = {"meta": meta}
    passed: list[str] = []
    for name in TESTS:
        exp_weeks = {r[1] for r in rows if _split(name, r) is True}
        cmp_weeks = {r[1] for r in rows if _split(name, r) is False}
        hours_exp = sum(1 for r in rows if _split(name, r) is True)
        mde = 2.8 * math.sqrt(0.25 / max(1, len(exp_weeks)) + 0.25 / max(1, len(cmp_weeks)))
        ok = len(exp_weeks) >= 30 and mde <= 0.15
        if ok:
            passed.append(name)
        out[name] = {
            "exposed_hours": hours_exp,
            "exposed_weeks": len(exp_weeks),
            "comparison_weeks": len(cmp_weeks),
            "approx_mde": round(mde, 4),
            "pass": ok,
        }
    zone_weeks = {r[1] for r in rows if r[3] is UpState.UP_ZONE}
    out["s_hold_computable"] = len(zone_weeks) >= 30
    out["passed_tests"] = passed
    return out


def run() -> dict[str, object]:
    rows, meta = _grid()
    check = precheck(rows)
    passed = list(check["passed_tests"])  # type: ignore[call-overload]
    fills, funding, _ = load_xbtusd(SOURCE)
    trips = sorted(build_trips(fills, funding), key=lambda t: t.opened)
    starts = [t.opened.timestamp() * 1000 for t in trips]

    def trip_at(at: float) -> tuple[int, float]:
        k = bisect_right(starts, at) - 1
        if k < 0:
            return 0, 0.0
        trip = trips[k]
        end = trip.closed.timestamp() * 1000 if trip.closed else float("inf")
        return (trip.side, starts[k]) if at < end else (0, 0.0)

    held = [trip_at(r[0]) for r in rows]
    rng = random.Random(SEED)
    report: dict[str, object] = {}
    pvalues: dict[str, float] = {}
    for name in passed:
        need = TESTS[name]
        by_week: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0] * 4))
        for row, (side, _) in zip(rows, held, strict=True):
            group = _split(name, row)
            if group is None:
                continue
            cell = by_week[row[1]][row[2]]
            if group:
                cell[0] += 1
                cell[1] += side == need
            else:
                cell[2] += 1
                cell[3] += side == need
        weeks = list(by_week.values())

        def flat(chosen: list[dict[str, list[int]]]) -> list[tuple[str, int, int, int, int]]:
            return [(m, v[0], v[1], v[2], v[3]) for wk in chosen for m, v in wk.items()]

        point = _stratified(flat(weeks))
        boots = sorted(
            d
            for d in (
                _stratified(flat([weeks[rng.randrange(len(weeks))] for _ in weeks]))
                for _ in range(BOOT)
            )
            if d is not None
        )
        p = sum(b <= 0 for b in boots) / len(boots)
        pvalues[name] = p
        tot = [sum(v[i] for wk in weeks for v in wk.values()) for i in range(4)]
        report[name] = {
            "exposed_hours": tot[0],
            "held_rate_exposed": round(tot[1] / tot[0], 4) if tot[0] else None,
            "held_rate_comparison": round(tot[3] / tot[2], 4) if tot[2] else None,
            "stratified_diff": None if point is None else round(point, 4),
            "ci95": [
                round(boots[int(0.025 * len(boots))], 4),
                round(boots[int(0.975 * len(boots)) - 1], 4),
            ],
            "p_one_sided": round(p, 4),
        }
    still = True
    for rank, name in enumerate(sorted(pvalues, key=lambda k: pvalues[k])):
        still = still and pvalues[name] <= 0.05 / (len(pvalues) - rank)
        entry = report[name]
        assert isinstance(entry, dict)
        entry["holm_significant"] = still and (entry["stratified_diff"] or 0) > 0

    # S_hold: UP_ZONE·롱 보유 시각 중 그 롱이 B 극값 1h 봉 이전에 열린 비율
    s_weeks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row, (side, opened) in zip(rows, held, strict=True):
        if row[3] is UpState.UP_ZONE and side == 1:
            s_weeks[row[1]][0] += opened < row[6]
            s_weeks[row[1]][1] += 1
    wk = list(s_weeks.values())
    s_point = sum(w[0] for w in wk) / sum(w[1] for w in wk) if wk else None
    s_boot = []
    for _ in range(BOOT):
        pick = [wk[rng.randrange(len(wk))] for _ in wk]
        s_boot.append(sum(w[0] for w in pick) / max(1, sum(w[1] for w in pick)))
    s_boot.sort()
    t2 = report.get("T2_long_zone_vs_shallow")
    t2_sig = isinstance(t2, dict) and bool(t2.get("holm_significant"))
    if "T2_long_zone_vs_shallow" not in passed or s_point is None:
        reading = "가르지 못함 (T2 미실행 또는 S_hold 없음)"
    elif t2_sig:
        reading = "눌림 매수 성분이 있다"
    elif s_point >= 0.5:
        reading = "주로 추세 보유"
    else:
        reading = "가르지 못함"
    return {
        "meta": meta,
        "precheck": check,
        "tests": report,
        "s_hold": {
            "zone_long_hours": sum(w[1] for w in wk),
            "share_opened_before_leg_top": None if s_point is None else round(s_point, 4),
            "ci95": [round(s_boot[int(0.025 * BOOT)], 4), round(s_boot[int(0.975 * BOOT) - 1], 4)],
        },
        "reading_rule_38_4": reading,
        "note": "문서 38 1회 실행. CROSS_VENUE_PROXY. T1·T2 준확증, D1·D2 첫 결합.",
    }


def main(argv: list[str]) -> int:
    modes = {"precheck": precheck, "run": run}
    if not argv or argv[0] not in modes:
        raise SystemExit("usage: audit_wa_trend_hold.py precheck|run")
    sys.stdout.write(json.dumps(modes[argv[0]](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
