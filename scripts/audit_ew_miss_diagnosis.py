"""문서 58 — 충격파 후보 생성 실패 원인 분류 (**개발 seed 20260924 만**, 합성, 시장 미조회).

문서 39 의 recall@50 에서 진짜 해석이 빠진 경로를 단계별로 나눈다.
순위 규칙(ranking.py, 동결)은 바꾸지 않는다.

단계 (앞에서 걸린 것 하나로 센다):

1. ``no_trigger``        진짜 끝 꼭짓점에서 발동이 없다
2. ``missing_vertex``    진짜 경계 꼭짓점 중 하나가 1h 꼭짓점 열에 없다 (피벗 탐지)
3. ``start_not_4h``      진짜 시작 꼭짓점이 4h 가시가 아니다
4. ``bound_not_4h``      진짜 내부 경계 중 4h 가시가 아니거나 이웃과 같은 4h 봉인 것이 있다
                         (4h 카운트 조건)
5. ``start_lookback``    진짜 시작이 360 개 4h 봉(60 일)보다 멀다
6. ``not_relaxed_span``  시작 극단 조건(``relaxed_span``) 불만족
7. ``parse_missing``     위를 다 통과했는데 ``top_candidates`` 에 진짜 경계의 충격파가 없다
                         (다리 해석·절대 규칙)
8. ``rank_over_50``      목록에 있으나 비관적 순위 > 50
9. ``ok``                상위 50 안

사용: ``python scripts/audit_ew_miss_diagnosis.py [--lookback N]`` — 개발 seed 만.
확인 seed·예약 seed 는 거부한다. ``--lookback`` 은 시작 탐색 창(4h 봉 수) 실험용.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott import scanner  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import (  # noqa: E402
    Label,
    WaveParser,
    as_grid_anchor,
    counted_at_four_hour,
)
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    LOOKBACK_BARS,
    causal_triggers,
    scenario_list,
)
from auto_trading.research.elliott.synthetic import SyntheticPath, build_path  # noqa: E402

DEV_SEED = 20260924
GATE_N = 50
LOOKBACK = LOOKBACK_BARS
ALL_NEW = False
STAGES = (
    "no_trigger",
    "missing_vertex",
    "start_not_4h",
    "bound_not_4h",
    "start_lookback",
    "not_relaxed_span",
    "parse_missing",
    "rank_over_50",
    "ok",
)


def diagnose(path: SyntheticPath) -> dict[str, object]:
    four = path.four_hour
    hours = hour_bars(path.minutes)
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,))
    truth = [
        (kind, Timeframe.H1.open_ms_of(path.minutes[t].open_ms))
        for t, kind in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
    ]
    hit = next(
        (
            trig
            for trig in causal_triggers(replay, hours, four, all_new_pivots=ALL_NEW)
            if (trig.segment[-1].kind, trig.segment[-1].hour_open_ms) == truth[-1]
        ),
        None,
    )
    if hit is None:
        return {"stage": "no_trigger"}
    fine = hit.segment
    where = {(p.kind, p.hour_open_ms): k for k, p in enumerate(fine)}
    bounds = tuple(where.get(v, -1) for v in truth)
    missing = [k for k, b in enumerate(bounds) if b < 0]
    if missing:
        return {"stage": "missing_vertex", "missing_labels": missing}
    j = len(fine) - 1
    b0 = bounds[0]
    if not fine[b0].four_hour_visible:
        return {"stage": "start_not_4h"}
    pivots = [fine[b] for b in bounds]
    same_bar = [
        k
        for k in range(1, len(pivots))
        if pivots[k].four_hour_open_ms == pivots[k - 1].four_hour_open_ms
    ]
    hidden = [k for k, p in enumerate(pivots) if not p.four_hour_visible]
    if hidden or same_bar:
        return {"stage": "bound_not_4h", "hidden": hidden, "same_4h_bar": same_bar}
    if (fine[j].four_hour_open_ms - fine[b0].four_hour_open_ms) // Timeframe.H4.ms > LOOKBACK:
        return {"stage": "start_lookback"}
    closed = [bar for bar in hours if bar.close_ms <= hit.close_ms]
    parser = WaveParser([as_grid_anchor(p) for p in fine], closed)
    if not parser.relaxed_span(b0, j):
        return {"stage": "not_relaxed_span"}
    pattern = Label.IMPULSE if path.pattern == "IMPULSE" else Label.ZIGZAG
    cands = [c for c in parser.top_candidates(b0, j, relaxed=True) if counted_at_four_hour(c, fine)]
    same_span = [c for c in cands if c.pattern is pattern]
    if not any(c.bounds == bounds for c in same_span):
        return {
            "stage": "parse_missing",
            "same_span_same_pattern": len(same_span),
            "bounds_len": len(bounds),
            "segment_len": len(fine),
        }
    scen = scenario_list(hit, hours)
    truth_parse = next(c for c, _ in scen.ranked if c.pattern is pattern and c.bounds == bounds)
    rank = scen.pessimistic_rank(truth_parse)
    if rank is None or rank > GATE_N:
        return {"stage": "rank_over_50", "rank": rank, "n": scen.total}
    return {"stage": "ok", "rank": rank, "n": scen.total}


def main(argv: list[str]) -> int:
    global LOOKBACK, ALL_NEW
    args = list(argv)
    ALL_NEW = "--all-new-pivots" in args
    if ALL_NEW:
        args.remove("--all-new-pivots")
    lookback = LOOKBACK_BARS
    if args:
        if len(args) != 2 or args[0] != "--lookback":
            raise SystemExit(
                "usage: audit_ew_miss_diagnosis.py [--lookback N] [--all-new-pivots] (개발 seed 만)"
            )
        lookback = int(args[1])
        scanner.LOOKBACK_BARS = lookback  # 실험: trigger_candidates 가 호출 때 읽는다
    LOOKBACK = lookback
    rng = random.Random(DEV_SEED)
    table: dict[str, Counter[str]] = {}
    examples: dict[str, list[dict[str, object]]] = {}
    for degree_id, (scale, threshold) in DEGREES.items():
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                for n in range(count):
                    row = diagnose(build_path(rng, pattern, scale, threshold, noise))
                    key = f"{pattern}|noise={noise}"
                    table.setdefault(key, Counter())[str(row["stage"])] += 1
                    table.setdefault(f"{key}|{degree_id}", Counter())[str(row["stage"])] += 1
                    if row["stage"] not in ("ok",) and len(examples.setdefault(key, [])) < 40:
                        examples[key].append({"degree": degree_id, "i": n, **row})
    out = {
        "seed": DEV_SEED,
        "lookback_bars": lookback,
        "all_new_pivots": ALL_NEW,
        "stages": STAGES,
        "table": {k: {s: v.get(s, 0) for s in STAGES} for k, v in sorted(table.items())},
        "examples": examples,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
