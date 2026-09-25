"""문서 60 — EW-4 1단계(플랫·수렴 삼각형) 합성 채점. 시장·수익 미조회.

사용::

    python scripts/audit_ew4.py dev                                   # 개발 seed 20260924, 두 버전
    python scripts/audit_ew4.py holdout --version ew3e --confirm-holdout
    # ↑ 확인 seed 20260929, 버전마다 1 회
    python scripts/audit_ew4.py holdout --version ew4 --confirm-holdout

충격파·지그재그 경로는 문서 39·58 과 같은 난수 흐름(``random.Random(seed)``)으로 만들고,
플랫·삼각형은 별도 난수(``random.Random(f"{seed}-FLAT")`` 등)로 만든다
— 기존 패턴 결과를 짝지어 비교하려고.
seed 20260927 은 JEV 비교용으로 예약 — 만들지 않는다.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_list,
)
from auto_trading.research.elliott.synthetic import SyntheticPath, build_path  # noqa: E402

SEEDS = {"dev": 20260924, "holdout": 20260929}
RESERVED_FOR_JEV = 20260927
KS = (1, 3, 10, 20, 50, 100)
GATE_N = 50
OLD, NEW = ("IMPULSE", "ZIGZAG"), ("FLAT", "TRIANGLE")


def score(path: SyntheticPath, *, ew4: bool) -> dict[str, object]:
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
            for trig in causal_triggers(replay, hours, four, all_new_pivots=True)
            if (trig.segment[-1].kind, trig.segment[-1].hour_open_ms) == truth[-1]
        ),
        None,
    )
    if hit is None:
        return {"triggered": False, "n": 0, "rank": None}
    where = {(p.kind, p.hour_open_ms): k for k, p in enumerate(hit.segment)}
    bounds = tuple(where.get(v, -1) for v in truth)
    pattern = Label[path.pattern]
    scen = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=ew4)
    truth_parse = next(
        (c for c, _ in scen.ranked if c.pattern is pattern and c.bounds == bounds), None
    )
    rank = None if truth_parse is None else scen.pessimistic_rank(truth_parse)
    top1 = scen.ranked[0][0].pattern.value if scen.ranked else None
    return {"triggered": True, "n": scen.total, "rank": rank, "top1_pattern": top1}


def summary(rows: list[dict[str, object]]) -> dict[str, object]:
    total = len(rows)
    ns = [int(str(r["n"])) for r in rows if r["triggered"]]
    inc = [r for r in rows if r["rank"] is not None]
    out: dict[str, object] = {
        "paths": total,
        "no_trigger": sum(not r["triggered"] for r in rows),
        "inclusion_full_list": round(len(inc) / total, 4),
        "candidates_median": median(ns) if ns else None,
    }
    for k in KS:
        out[f"top{k}"] = round(sum(1 for r in inc if int(str(r["rank"])) <= k) / total, 4)
    out[f"random_top{GATE_N}"] = round(
        sum(min(GATE_N, int(str(r["n"]))) / int(str(r["n"])) for r in inc) / total, 4
    )
    return out


def run(seed: int, *, ew4: bool) -> dict[str, object]:
    rng_old = random.Random(seed)
    rng_new = {p: random.Random(f"{seed}-{p}") for p in NEW}
    pooled: dict[str, dict[str, list[dict[str, object]]]] = {}
    cells: dict[str, object] = {}
    for degree_id, (scale, threshold) in DEGREES.items():
        for pattern in (*OLD, *NEW):
            rng = rng_old if pattern in OLD else rng_new[pattern]
            for noise, count in CELLS:
                rows = [
                    score(build_path(rng, pattern, scale, threshold, noise), ew4=ew4)
                    for _ in range(count)
                ]
                cells[f"{degree_id}|{pattern}|noise={noise}"] = summary(rows)
                pooled.setdefault(f"noise={noise}", {}).setdefault(pattern, []).extend(rows)
    return {
        "seed": seed,
        "version": "ew4" if ew4 else "ew3e",
        "reserved_for_jev_untouched": RESERVED_FOR_JEV,
        "pooled": {k: {p: summary(v) for p, v in by.items()} for k, by in pooled.items()},
        "cells": cells,
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit("usage: audit_ew4.py dev | holdout --version ew3e|ew4 --confirm-holdout")
    split = argv[0]
    if split == "holdout":
        if "--confirm-holdout" not in argv or "--version" not in argv:
            raise SystemExit("확인 seed 는 --version 과 --confirm-holdout 으로 버전마다 한 번만")
        version = argv[argv.index("--version") + 1]
        results = [run(SEEDS[split], ew4=version == "ew4")]
    else:
        results = [run(SEEDS[split], ew4=False), run(SEEDS[split], ew4=True)]
    sys.stdout.write(json.dumps(results, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
