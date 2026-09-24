"""EW-3d 합성 채점: 절대 규칙 후보 + 검증·가이드라인 순위 (33 §28 사전 고정). 시장·WA 미조회.

사용: ``python scripts/audit_ew3d_ranking.py dev`` 또는
``python scripts/audit_ew3d_ranking.py holdout --confirm-holdout`` (한 번만).
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path
from statistics import median, quantiles

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, SEEDS, hour_bars  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import (  # noqa: E402
    Label,
    WaveParser,
    as_grid_anchor,
    counted_at_four_hour,
    hourly_path_pivots,
)
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.ranking import pessimistic_rank  # noqa: E402
from auto_trading.research.elliott.synthetic import SyntheticPath, build_path  # noqa: E402

KS = (1, 3, 10)


def _one(path: SyntheticPath) -> dict[str, object]:
    bars = path.four_hour
    hours = hour_bars(path.minutes)
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(bars)))
    (replay,) = replay_minute_groups_ladder(bars, groups, bars[-1].close_ms, (EW0_THRESHOLD_PCT,))
    segments = hourly_path_pivots(replay, hours, bars)
    where = {
        (p.kind, p.hour_open_ms): (s, i)
        for s, seg in enumerate(segments)
        for i, p in enumerate(seg)
    }
    mapped = [
        where.get((kind, Timeframe.H1.open_ms_of(path.minutes[t].open_ms)))
        for t, kind in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
    ]
    if any(m is None for m in mapped) or len({m[0] for m in mapped if m}) != 1:
        return {"mapped": False, "n": 0, "rank": None}
    seg = segments[mapped[0][0]]  # type: ignore[index]
    idx = tuple(m[1] for m in mapped if m is not None)
    if not all(seg[k].four_hour_visible for k in idx):
        return {"mapped": False, "n": 0, "rank": None}
    cutoff = seg[idx[-1]].available_at_ms
    fine = [p for p in seg if p.available_at_ms <= cutoff]
    closed = [h for h in hours if h.close_ms <= cutoff]
    parser = WaveParser([as_grid_anchor(p) for p in fine], closed)
    candidates = [
        c for c in parser.top_candidates(idx[0], idx[-1]) if counted_at_four_hour(c, fine)
    ]
    pattern = Label.IMPULSE if path.pattern == "IMPULSE" else Label.ZIGZAG
    truth = next((c for c in candidates if c.pattern is pattern and c.bounds == idx), None)
    rank = None if truth is None else pessimistic_rank(truth, candidates, fine)
    verified = (
        None
        if truth is None
        else sum(lab in (Label.IMPULSE, Label.ZIGZAG) for lab in truth.leg_labels)
    )
    return {"mapped": True, "n": len(candidates), "rank": rank, "truth_verified_legs": verified}


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    total = len(rows)
    ranks = [r["rank"] for r in rows]
    ns = [int(str(r["n"])) for r in rows if r["mapped"]]
    included = [r for r in rows if r["rank"] is not None]
    out: dict[str, object] = {
        "paths": total,
        "mapped": sum(bool(r["mapped"]) for r in rows),
        "included": len(included),
        "inclusion": round(len(included) / total, 4) if total else None,
        "candidates_median": median(ns) if ns else None,
        "candidates_p90": quantiles(ns, n=10)[-1] if len(ns) >= 2 else None,
        "truth_verified_legs": dict(
            sorted(Counter(str(r["truth_verified_legs"]) for r in included).items())
        ),
    }
    for k in KS:
        hit = sum(1 for x in ranks if x is not None and int(str(x)) <= k)
        base = sum(min(k, int(str(r["n"]))) / int(str(r["n"])) for r in included) / total
        out[f"top{k}"] = round(hit / total, 4)
        out[f"random_top{k}"] = round(base, 4)
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit("usage: audit_ew3d_ranking.py dev | holdout --confirm-holdout")
    split = argv[0]
    if split == "holdout" and "--confirm-holdout" not in argv:
        raise SystemExit("확인 seed 는 --confirm-holdout 과 함께 한 번만 채점한다 (33 §28)")
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
        summaries = {pattern: _summary(rows) for pattern, rows in by_pattern.items()}
        passed = all(
            float(str(s["inclusion"])) >= 0.8
            and float(str(s["top3"])) >= 0.5
            and float(str(s["top3"])) >= 2 * float(str(s["random_top3"]))
            for s in summaries.values()
        )
        gate[key] = {**summaries, "gate_pass": passed}
    result = {"split": split, "seed": SEEDS[split], "cells": cells, "pooled": gate}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
