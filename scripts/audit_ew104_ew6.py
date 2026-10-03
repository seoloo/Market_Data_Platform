"""문서 104 — EW-6 (변동성 맞춤 꼭짓점 + 최상위 기하) 합성 점검. 시장형 합성(103, k = 2.0)이 기준.

사용::

    python scripts/audit_ew104_ew6.py dev [--count 10]      # 개발 seed 20261009
    python scripts/audit_ew104_ew6.py gate --confirm-gate   # 관문 seed 20261010 · 20261011, 1 회

수익 시험이 아니다.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew3c_hourly import DEGREES  # noqa: E402
from scripts.audit_ew103_market_synthetic import STEP_1H, load_returns  # noqa: E402

from auto_trading.features.structure import SwingKind  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.geometry_ew6 import (  # noqa: E402
    FIVE_LEG,
    THREE_LEG,
    ew6_events,
    ew6b_events,
    primary_label,
)
from auto_trading.research.elliott.synthetic import (  # noqa: E402
    EW5_PATTERNS,
    MarketNoise,
    build_path,
)

DEV_SEED = 20261009
GATE_SEEDS = (20261010, 20261011)
MARKET_K = 2.0
NOISE = 0.001
COUNT_GATE = 50
TOLERANCE_BARS = 2
PATTERNS = ("IMPULSE", "ZIGZAG", "FLAT", "TRIANGLE", *EW5_PATTERNS)
CLASS = {
    "ENDING_DIAGONAL": "DIAGONAL",
    "LEADING_DIAGONAL": "DIAGONAL",
    "DOUBLE_ZIGZAG": "ZIGZAG",
    "DOUBLE_THREE": "FLAT",
}
"""문서 104 §3: 하위 파동을 안 보므로 이중 지그재그 → 지그재그, 이중 3 → 플랫 갈래로 채점."""
CLASSES = (*FIVE_LEG, *THREE_LEG)
TRUE_MIN, FALSE_MAX = 0.5, 0.3
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "104"


READER = {"ew6": ew6_events, "ew6b": ew6b_events}
"""ew6: 단계 사다리의 마지막 꼭짓점 (§2) · ew6b: 촘촘한 단계 위 극단 다리 조합 (§6)."""
_reader = "ew6"
_base_m = 2.0


def read_path(path: synthetic_module.SyntheticPath) -> dict[str, object]:
    bars = path.four_hour
    events = ew6b_events(bars, _base_m) if _reader == "ew6b" else ew6_events(bars)
    kind = +1 if path.vertex_kinds[-1] is SwingKind.HIGH else -1
    truth_bar = path.vertex_minutes[-1] // 240
    matched = [
        e
        for e in events
        if e.pivot.kind == kind and abs(e.pivot.index - truth_bar) <= TOLERANCE_BARS
    ]
    detected = sorted({x for e in matched for x in (*e.five, *e.three)})
    return {
        "found": bool(matched),
        "detected": detected,
        "primary": primary_label(matched) if matched else "NO_PIVOT",
        "events": len(events),
        "events_any_five": sum(1 for e in events if e.five),
    }


def run(seed: int, k: float, count: int, returns: Sequence[float]) -> list[dict[str, object]]:
    original = synthetic_module.SPEED_PER_MINUTE
    synthetic_module.SPEED_PER_MINUTE = original / 2  # 1h 설정 (93 §8)
    market = MarketNoise(returns, k, STEP_1H, 2) if k > 0 else None
    rows: list[dict[str, object]] = []
    try:
        for pattern in PATTERNS:
            rng = random.Random(f"{seed}-ew6-{k}-{pattern}")
            for degree, (scale, threshold) in DEGREES.items():
                for _ in range(count):
                    path = build_path(rng, pattern, scale / 2, threshold / 2, NOISE, market)
                    rows.append(
                        {
                            "pattern": CLASS.get(pattern, pattern),
                            "degree": degree,
                            **read_path(path),
                        }
                    )
    finally:
        synthetic_module.SPEED_PER_MINUTE = original
    return rows


def summary(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    totals = Counter(str(r["pattern"]) for r in rows)
    every = len(rows)
    det: dict[str, object] = {}
    pri: dict[str, object] = {}
    for c in CLASSES:
        n_true, n_other = totals[c], every - totals[c]
        dt = sum(c in r["detected"] for r in rows if r["pattern"] == c) / max(1, n_true)  # type: ignore[operator]
        df = sum(c in r["detected"] for r in rows if r["pattern"] != c) / max(1, n_other)  # type: ignore[operator]
        pt = sum(r["primary"] == c for r in rows if r["pattern"] == c) / max(1, n_true)
        pf = sum(r["primary"] == c for r in rows if r["pattern"] != c) / max(1, n_other)
        det[c] = {
            "true": round(dt, 4),
            "false": round(df, 4),
            "pass": dt >= TRUE_MIN and df <= FALSE_MAX,
        }
        pri[c] = {
            "true": round(pt, 4),
            "false": round(pf, 4),
            "pass": pt >= TRUE_MIN and pf <= FALSE_MAX,
        }
    confusion: dict[str, dict[str, int]] = {}
    for r in rows:
        confusion.setdefault(str(r["pattern"]), Counter())[str(r["primary"])] += 1  # type: ignore[index]
    events = sum(int(str(r["events"])) for r in rows)
    return {
        "paths": every,
        "end_pivot_found": round(sum(bool(r["found"]) for r in rows) / every, 4),
        "detector": det,
        "primary": pri,
        "confusion": {k: dict(sorted(v.items())) for k, v in confusion.items()},
        "events_any_five_frac": round(
            sum(int(str(r["events_any_five"])) for r in rows) / max(1, events), 4
        ),
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("dev", "gate"):
        raise SystemExit("usage: audit_ew104_ew6.py dev [--count N] | gate --confirm-gate")
    global _reader, _base_m
    if "--reader" in argv:
        _reader = argv[argv.index("--reader") + 1]
    if "--base-m" in argv:
        _base_m = float(argv[argv.index("--base-m") + 1])
    returns = load_returns()
    OUT.mkdir(parents=True, exist_ok=True)
    if argv[0] == "dev":
        count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 10
        result: dict[str, object] = {
            f"k={k}": summary(run(DEV_SEED, k, count, returns)) for k in (MARKET_K, 0.0)
        }
        target = OUT / f"dev_{_reader}_m{_base_m}_{count}.json"
    else:
        if "--confirm-gate" not in argv:
            raise SystemExit("관문 seed 는 --confirm-gate 로 한 번만 (문서 104 §4)")
        target = OUT / f"gate_{_reader}_m{_base_m}.json"
        if target.exists():
            raise SystemExit(f"{target} 가 이미 있다 — 관문은 한 번만")
        seeds = {
            str(s): {f"k={k}": summary(run(s, k, COUNT_GATE, returns)) for k in (MARKET_K, 0.0)}
            for s in GATE_SEEDS
        }
        passes = {
            metric: {
                c: all(
                    bool(seeds[str(s)][f"k={MARKET_K}"][metric][c]["pass"])  # type: ignore[index]
                    for s in GATE_SEEDS
                )
                for c in CLASSES
            }
            for metric in ("detector", "primary")
        }
        result = {"seeds": seeds, "pass_both_seeds_market_k": passes}
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
