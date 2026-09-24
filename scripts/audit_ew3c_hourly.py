"""EW-3c 합성 채점: 카운트는 4h, 하위파 증거는 1h (33 §26 사전 고정). 시장·WA 를 읽지 않는다.

사용: ``python scripts/audit_ew3c_hourly.py dev`` (main·strict 둘 다) 또는
``python scripts/audit_ew3c_hourly.py holdout --setting main|strict --confirm-holdout`` (한 번만).
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import (  # noqa: E402
    WaveParser,
    as_grid_anchor,
    counted_at_four_hour,
    hourly_path_pivots,
)
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.synthetic import SyntheticPath, build_path  # noqa: E402

SEEDS = {"dev": 20260924, "holdout": 20260925}
DEGREES = {"D1": (0.5, 0.04), "D2": (1.0, 0.08), "D3": (2.0, 0.16)}
CELLS = ((0.001, 50), (0.0, 20))
SETTINGS = {"main": True, "strict": False}


def hour_bars(minutes: Sequence[Candle]) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            open_ms=g[0].open_ms,
            close_ms=g[-1].close_ms,
            open=g[0].open,
            high=max(c.high for c in g),
            low=min(c.low for c in g),
            close=g[-1].close,
            volume=sum((c.volume for c in g), start=Decimal(0)),
        )
        for g in (minutes[i : i + 60] for i in range(0, len(minutes), 60))
    )


def _one(path: SyntheticPath, settings: dict[str, bool]) -> dict[str, object]:
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
        return {"mapped": False}
    seg = segments[mapped[0][0]]  # type: ignore[index]
    idx = tuple(m[1] for m in mapped if m is not None)
    if not all(seg[k].four_hour_visible for k in idx):
        return {"mapped": False}
    cutoff = seg[idx[-1]].available_at_ms
    fine = [p for p in seg if p.available_at_ms <= cutoff]
    closed = [h for h in hours if h.close_ms <= cutoff]
    out: dict[str, object] = {"mapped": True}
    for name, lenient in settings.items():
        parser = WaveParser([as_grid_anchor(p) for p in fine], closed, lenient_three=lenient)
        imp = [x for x in parser.impulses(idx[0], idx[-1]) if counted_at_four_hour(x, fine)]
        zz = [x for x in parser.zigzags(idx[0], idx[-1]) if counted_at_four_hour(x, fine)]
        own, other = (imp, zz) if path.pattern == "IMPULSE" else (zz, imp)
        hit = next((x for x in own if x.bounds == idx), None)
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
        raise SystemExit("usage: audit_ew3c_hourly.py dev | holdout --setting S --confirm-holdout")
    split = argv[0]
    settings = SETTINGS
    if split == "holdout":
        if "--confirm-holdout" not in argv or "--setting" not in argv:
            raise SystemExit("확인 seed 는 --setting 과 --confirm-holdout 으로 한 번만 (33 §26)")
        chosen = argv[argv.index("--setting") + 1]
        settings = {chosen: SETTINGS[chosen]}
    rng = random.Random(SEEDS[split])
    cells: dict[str, object] = {}
    pooled: dict[str, dict[str, list[dict[str, object]]]] = {}
    for degree_id, (scale, threshold) in DEGREES.items():
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                rows = [
                    _one(build_path(rng, pattern, scale, threshold, noise), settings)
                    for _ in range(count)
                ]
                cells[f"{degree_id}|{pattern}|noise={noise}"] = {
                    name: _summary(rows, name) for name in settings
                }
                pooled.setdefault(f"noise={noise}", {}).setdefault(pattern, []).extend(rows)
    gate: dict[str, object] = {}
    for key, by_pattern in pooled.items():
        for name in settings:
            imp = _summary(by_pattern["IMPULSE"], name)
            zz = _summary(by_pattern["ZIGZAG"], name)
            recall = int(str(imp["recall"])) / max(1, int(str(imp["paths"])))  # NOT_MAPPED=실패
            false_imp = int(str(zz["other_pattern"])) / max(1, int(str(zz["mapped"])))
            gate[f"{key}|{name}"] = {
                "impulse": imp,
                "zigzag": zz,
                "impulse_recall": round(recall, 4),
                "false_impulse_on_zigzag": round(false_imp, 4),
                "gate_pass": recall >= 0.5 and false_imp <= 0.2,
                "recall_minus_false": round(recall - false_imp, 4),
            }
    result = {"split": split, "seed": SEEDS[split], "cells": cells, "pooled": gate}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
