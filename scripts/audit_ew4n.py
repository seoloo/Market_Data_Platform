"""문서 61 — EW-4 2단계(4파·B파 안 삼각형·확장 플랫) 합성 채점. 시장·수익 미조회.

사용::

    python scripts/audit_ew4n.py dev                                   # 개발 seed 20260924, 두 버전
    python scripts/audit_ew4n.py holdout --version ew4 --confirm-holdout
    # ↑ 확인 seed 20260930, 버전마다 1 회
    python scripts/audit_ew4n.py holdout --version ew4n --confirm-holdout

기존 충격파·지그재그는 ``random.Random(seed)``, 플랫·삼각형은 ``"<seed>-FLAT"`` 등(문서 60 흐름),
새 패턴은 ``"<seed>-<이름>"`` 난수로 만든다. seed 20260927 은 JEV 비교용으로 예약 — 만들지 않는다.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402
from scripts.audit_ew4 import KS, summary  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_list,
)
from auto_trading.research.elliott.synthetic import (  # noqa: E402
    NESTED_PATTERNS,
    SyntheticPath,
    build_path,
)

SEEDS = {"dev": 20260924, "holdout": 20260930}
RESERVED_FOR_JEV = 20260927
OLD, STAGE1 = ("IMPULSE", "ZIGZAG"), ("FLAT", "TRIANGLE")
NESTED = tuple(NESTED_PATTERNS)
_FORM_LABEL = {"TRIANGLE": Label.TRIANGLE, "EXPFLAT": Label.FLAT}


def score(path: SyntheticPath, *, nested: bool) -> dict[str, object]:
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
    top_name, leg, form = NESTED_PATTERNS.get(path.pattern, (path.pattern, -1, ""))
    pattern = Label[top_name]
    scen = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=nested)
    truth_parse = next(
        (c for c, _ in scen.ranked if c.pattern is pattern and c.bounds == bounds), None
    )
    rank = None if truth_parse is None else scen.pessimistic_rank(truth_parse)
    out: dict[str, object] = {"triggered": True, "n": scen.total, "rank": rank}
    if truth_parse is not None and leg >= 0:
        out["leg_reading"] = truth_parse.leg_labels[leg].value
        out["reading_ok"] = truth_parse.leg_labels[leg] is _FORM_LABEL[form]
    return out


def nested_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    out = summary(rows)
    read = [r for r in rows if "reading_ok" in r]
    out["reading_accuracy"] = (
        round(sum(bool(r["reading_ok"]) for r in read) / len(read), 4) if read else None
    )
    counts: dict[str, int] = {}
    for r in read:
        counts[str(r["leg_reading"])] = counts.get(str(r["leg_reading"]), 0) + 1
    out["leg_readings"] = counts
    return out


def run(seed: int, *, nested: bool) -> dict[str, object]:
    rng_old = random.Random(seed)
    rng_new = {p: random.Random(f"{seed}-{p}") for p in (*STAGE1, *NESTED)}
    pooled: dict[str, dict[str, list[dict[str, object]]]] = {}
    cells: dict[str, object] = {}
    for degree_id, (scale, threshold) in DEGREES.items():
        for pattern in (*OLD, *STAGE1, *NESTED):
            rng = rng_old if pattern in OLD else rng_new[pattern]
            for noise, count in CELLS:
                rows = [
                    score(build_path(rng, pattern, scale, threshold, noise), nested=nested)
                    for _ in range(count)
                ]
                cells[f"{degree_id}|{pattern}|noise={noise}"] = nested_summary(rows)
                pooled.setdefault(f"noise={noise}", {}).setdefault(pattern, []).extend(rows)
    return {
        "seed": seed,
        "version": "ew4n" if nested else "ew4",
        "reserved_for_jev_untouched": RESERVED_FOR_JEV,
        "ks": list(KS),
        "pooled": {k: {p: nested_summary(v) for p, v in by.items()} for k, by in pooled.items()},
        "pooled_nested": {
            k: nested_summary([r for p in NESTED for r in by[p]]) for k, by in pooled.items()
        },
        "cells": cells,
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS or "--version" not in argv:
        raise SystemExit("usage: audit_ew4n.py dev|holdout --version ew4|ew4n [--confirm-holdout]")
    split = argv[0]
    if split == "holdout" and "--confirm-holdout" not in argv:
        raise SystemExit("확인 seed 는 --confirm-holdout 으로 버전마다 한 번만")
    version = argv[argv.index("--version") + 1]
    if version not in ("ew4", "ew4n"):
        raise SystemExit("--version ew4 | ew4n")
    result = run(SEEDS[split], nested=version == "ew4n")
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
