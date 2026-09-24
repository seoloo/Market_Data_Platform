"""문서 39 — 구간을 모를 때의 시나리오 묶음 검증 (합성, 시장·WA 미조회).

사용: ``python scripts/audit_ew_discovery.py dev`` 또는
``python scripts/audit_ew_discovery.py holdout --confirm-holdout`` (seed 20260926, 한 번만).
seed 20260927 은 JEV 비교용으로 예약 — 이 스크립트는 만들지 않는다.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import median, quantiles

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import causal_triggers, scenario_list  # noqa: E402
from auto_trading.research.elliott.synthetic import SyntheticPath, build_path  # noqa: E402

SEEDS = {"dev": 20260924, "holdout": 20260926}
RESERVED_FOR_JEV = 20260927
KS = (1, 3, 10)


def _one(path: SyntheticPath) -> dict[str, object]:
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
            for trig in causal_triggers(replay, hours, four)
            if (trig.segment[-1].kind, trig.segment[-1].hour_open_ms) == truth[-1]
        ),
        None,
    )
    if hit is None:
        return {"triggered": False, "n": 0, "rank": None, "span_rank": None}
    where = {(p.kind, p.hour_open_ms): k for k, p in enumerate(hit.segment)}
    bounds = tuple(where.get(v, -1) for v in truth)
    pattern = Label.IMPULSE if path.pattern == "IMPULSE" else Label.ZIGZAG
    scen = scenario_list(hit, hours)
    truth_parse = next(
        (c for c, _ in scen.ranked if c.pattern is pattern and c.bounds == bounds), None
    )
    rank = None if truth_parse is None else scen.pessimistic_rank(truth_parse)
    span = [
        scen.pessimistic_rank(c)
        for c, _ in scen.ranked
        if c.pattern is pattern and c.bounds[0] == bounds[0] and c.bounds[-1] == bounds[-1]
    ]
    return {
        "triggered": True,
        "n": scen.total,
        "rank": rank,
        "span_rank": min((r for r in span if r is not None), default=None),
    }


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    total = len(rows)
    ns = [int(str(r["n"])) for r in rows if r["triggered"]]
    included = [r for r in rows if r["rank"] is not None]
    out: dict[str, object] = {
        "paths": total,
        "no_trigger": sum(not r["triggered"] for r in rows),
        "inclusion_full_list": round(len(included) / total, 4),
        "candidates_median": median(ns) if ns else None,
        "candidates_p90": quantiles(ns, n=10)[-1] if len(ns) >= 2 else None,
        "span_match_top10": round(
            sum(1 for r in rows if r["span_rank"] is not None and int(str(r["span_rank"])) <= 10)
            / total,
            4,
        ),
    }
    for k in KS:
        out[f"top{k}"] = round(sum(1 for r in included if int(str(r["rank"])) <= k) / total, 4)
        out[f"random_top{k}"] = round(
            sum(min(k, int(str(r["n"]))) / int(str(r["n"])) for r in included) / total, 4
        )
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit("usage: audit_ew_discovery.py dev | holdout --confirm-holdout")
    split = argv[0]
    if split == "holdout" and "--confirm-holdout" not in argv:
        raise SystemExit("확인 seed 는 --confirm-holdout 과 함께 한 번만 채점한다 (문서 39 §4)")
    rng = random.Random(SEEDS[split])
    cells: dict[str, object] = {}
    pooled: dict[str, dict[str, list[dict[str, object]]]] = {}
    for degree_id, (scale, threshold) in DEGREES.items():
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                rows = [
                    _one(build_path(rng, pattern, scale, threshold, noise)) for _ in range(count)
                ]
                cells[f"{degree_id}|{pattern}|noise={noise}"] = _summary(rows)
                pooled.setdefault(f"noise={noise}", {}).setdefault(pattern, []).extend(rows)
    gate: dict[str, object] = {}
    for key, by_pattern in pooled.items():
        s = {pattern: _summary(rows) for pattern, rows in by_pattern.items()}
        gate[key] = {
            **s,
            "gate_pass": all(
                float(str(v["inclusion_full_list"])) >= 0.8
                and float(str(v["top10"])) >= 0.5
                and float(str(v["top10"])) >= 2 * float(str(v["random_top10"]))
                for v in s.values()
            ),
        }
    result = {
        "split": split,
        "seed": SEEDS[split],
        "reserved_for_jev_untouched": RESERVED_FOR_JEV,
        "cells": cells,
        "pooled": gate,
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
