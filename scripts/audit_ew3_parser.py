"""EW-3 문법 파서의 합성 채점 (33 §24 사전 고정). 시장 자료·WA 를 읽지 않는다.

사용: ``python scripts/audit_ew3_parser.py dev`` (개발 seed) 또는
``python scripts/audit_ew3_parser.py holdout --confirm-holdout`` (확인 seed, 한 번만).
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott.h4_evidence import (  # noqa: E402
    EvidenceStatus,
    FourHourPivotEvidence,
    map_minute_path_to_four_hour_evidence,
)
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Parse, WaveParser  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.synthetic import SyntheticPath, build_path  # noqa: E402

SEEDS = {"dev": 20260924, "holdout": 20260925}
DEGREES = {"D1": (0.5, 0.04), "D2": (1.0, 0.08), "D3": (2.0, 0.16)}  # 배율·부모 임계
CELLS = ((0.001, 50), (0.0, 20))


def _one(path: SyntheticPath) -> dict[str, object]:
    bars = path.four_hour
    as_of = bars[-1].close_ms
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(bars)))
    (replay,) = replay_minute_groups_ladder(bars, groups, as_of, (EW0_THRESHOLD_PCT,))
    evidence = map_minute_path_to_four_hour_evidence(bars, replay, as_of)
    segments: list[list[FourHourPivotEvidence]] = [[]]
    for item in evidence:
        if item.status is EvidenceStatus.UNKNOWN:
            segments.append([])
        elif item.candidate is not None:
            segments[-1].append(item.candidate)
    where = {
        (c.kind, c.extreme_four_hour_open_ms): (s, i)
        for s, seg in enumerate(segments)
        for i, c in enumerate(seg)
    }
    mapped = [
        where.get((kind, Timeframe.H4.open_ms_of(path.minutes[t].open_ms)))
        for t, kind in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
    ]
    if any(m is None for m in mapped) or len({m[0] for m in mapped if m}) != 1:
        return {"mapped": False}
    seg_index = mapped[0][0]  # type: ignore[index]
    idx = [m[1] for m in mapped if m is not None]
    start, end = idx[0], idx[-1]
    segment = segments[seg_index]
    cutoff = segment[end].confirmed_four_hour_close_ms
    pivots = [c for c in segment if c.confirmed_four_hour_close_ms <= cutoff]
    closed = [bar for bar in bars if bar.close_ms <= cutoff]
    truth = tuple(idx)
    out: dict[str, object] = {"mapped": True}
    for name, lenient in (("main", True), ("strict", False)):
        parser = WaveParser(pivots, closed, lenient_three=lenient)
        own: tuple[Parse, ...]
        if path.pattern == "IMPULSE":
            own, other = parser.impulses(start, end), parser.zigzags(start, end)
        else:
            own, other = parser.zigzags(start, end), parser.impulses(start, end)
        hit = next((p for p in own if p.bounds == truth), None)
        out[name] = {
            "recall": hit is not None,
            "verified": bool(hit and hit.verified_one_level),
            "ambiguity": len(own),
            "other_pattern": bool(other),
            "leg_labels": [lab.value for lab in hit.leg_labels] if hit else None,
        }
    return out


def _summary(rows: list[dict[str, object]], name: str) -> dict[str, object]:
    mapped = [r for r in rows if r["mapped"]]
    got = [r[name] for r in mapped]
    amb = [int(g["ambiguity"]) for g in got]  # type: ignore[index]
    labels: Counter[str] = Counter()
    for g in got:
        for lab in g["leg_labels"] or []:  # type: ignore[index]
            labels[str(lab)] += 1
    return {
        "paths": len(rows),
        "mapped": len(mapped),
        "recall": sum(bool(g["recall"]) for g in got),  # type: ignore[index]
        "verified": sum(bool(g["verified"]) for g in got),  # type: ignore[index]
        "ambiguity_median": median(amb) if amb else None,
        "ambiguity_max": max(amb) if amb else None,
        "other_pattern": sum(bool(g["other_pattern"]) for g in got),  # type: ignore[index]
        "true_parse_leg_labels": dict(sorted(labels.items())),
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SEEDS:
        raise SystemExit("usage: audit_ew3_parser.py dev|holdout [--confirm-holdout]")
    split = argv[0]
    if split == "holdout" and "--confirm-holdout" not in argv:
        raise SystemExit("확인 seed 는 --confirm-holdout 과 함께 한 번만 채점한다 (33 §24)")
    rng = random.Random(SEEDS[split])
    cells: dict[str, object] = {}
    pooled: dict[str, dict[str, list[dict[str, object]]]] = {}
    for degree_id, (scale, threshold) in DEGREES.items():
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                rows = [
                    _one(build_path(rng, pattern, scale, threshold, noise)) for _ in range(count)
                ]
                cells[f"{degree_id}|{pattern}|noise={noise}"] = {
                    "main": _summary(rows, "main"),
                    "strict": _summary(rows, "strict"),
                }
                pooled.setdefault(f"noise={noise}", {}).setdefault(pattern, []).extend(rows)
    gate: dict[str, object] = {}
    for key, by_pattern in pooled.items():
        for name in ("main", "strict"):
            imp = _summary(by_pattern["IMPULSE"], name)
            zz = _summary(by_pattern["ZIGZAG"], name)
            imp_recall = int(str(imp["recall"])) / max(1, int(str(imp["paths"])))  # NOT_MAPPED=실패
            false_imp = int(str(zz["other_pattern"])) / max(1, int(str(zz["mapped"])))
            gate[f"{key}|{name}"] = {
                "impulse": imp,
                "zigzag": zz,
                "impulse_recall": round(imp_recall, 4),
                "false_impulse_on_zigzag": round(false_imp, 4),
                "gate_pass": imp_recall >= 0.5 and false_imp <= 0.2,
            }
    result = {
        "split": split,
        "seed": SEEDS[split],
        "cells": cells,
        "pooled": gate,
        "note": "33 §24. 관문은 확인 seed·잡음 0.1%·main 설정만. 합성 교과서 가정.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
