"""문서 107 — 여러 시간 단위 판독기 EW-7 (삼각형 + 큰 단위 추세 맥락) 합성 점검.

사용::

    python scripts/audit_ew107_multidegree.py dev [--count 10]   # 개발 seed 20261009
    python scripts/audit_ew107_multidegree.py gate --confirm-gate   # 관문 seed 20261014·15, 1 회

시장형 합성(103, k = 2.0) + 진입 다리 = 첫 다리 × 2 (교과서 맥락: 큰 추세 다리 뒤 패턴).
패턴 없는 경로는 106 과 같은 뒤섞은 실제 1m 무작위 걸음. 수익 시험이 아니다.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew3c_hourly import DEGREES  # noqa: E402
from scripts.audit_ew103_market_synthetic import STEP_1H, load_returns  # noqa: E402
from scripts.audit_ew104_ew6 import CLASS, CLASSES, NOISE, PATTERNS, TOLERANCE_BARS  # noqa: E402
from scripts.audit_ew106_ew6c import _labels, noise_bars, summarise  # noqa: E402

from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.features.structure import SwingKind  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.geometry_ew6 import (  # noqa: E402
    Ew6Event,
    ew6b_events,
    ew7_events,
)
from auto_trading.research.elliott.synthetic import MarketNoise, build_path  # noqa: E402

DEV_SEED = 20261009
GATE_SEEDS = (20261014, 20261015)
MARKET_K = 2.0
LEAD_W1 = 2.0
COUNT_GATE = 50
NOISE_PATHS_DEV, NOISE_PATHS_GATE = 60, 300
TRIANGLE_CLASSES = ("TRIANGLE", "RUNNING_TRIANGLE", "EXPANDING_TRIANGLE")
DEV_READERS: dict[str, Callable[[Sequence[Candle]], list[Ew6Event]]] = {
    "ew6b": lambda bars: ew6b_events(bars, 2.0),
    "ew7_h3": lambda bars: ew7_events(bars, 2.0, 3.0),
    "ew7_h4": lambda bars: ew7_events(bars, 2.0, 4.0),
    "ew7_h6": lambda bars: ew7_events(bars, 2.0, 6.0),
}
GATE_READERS: dict[str, Callable[[Sequence[Candle]], list[Ew6Event]]] = {
    "ew6b": DEV_READERS["ew6b"],
    "ew7": DEV_READERS["ew7_h4"],
}
RETRY_SEEDS = (20261016, 20261017, 20261018)
"""문서 107 §8: 재도전(마지막) — m_high 3, 새 seed 세 개 모두 통과해야 한다."""
RETRY_READERS: dict[str, Callable[[Sequence[Candle]], list[Ew6Event]]] = {
    "ew7_h3": DEV_READERS["ew7_h3"],
}
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "107"


def pattern_rows(
    seed: int,
    count: int,
    returns: Sequence[float],
    readers: dict[str, Callable[[Sequence[Candle]], list[Ew6Event]]],
) -> dict[str, list[dict[str, object]]]:
    original = synthetic_module.SPEED_PER_MINUTE
    synthetic_module.SPEED_PER_MINUTE = original / 2  # 1h 설정 (93 §8)
    market = MarketNoise(returns, MARKET_K, STEP_1H, 2)
    rows: dict[str, list[dict[str, object]]] = {name: [] for name in readers}
    try:
        for pattern in PATTERNS:
            rng = random.Random(f"{seed}-ew7-{pattern}")
            for _degree, (scale, threshold) in DEGREES.items():
                for _ in range(count):
                    path = build_path(
                        rng, pattern, scale / 2, threshold / 2, NOISE, market, LEAD_W1
                    )
                    kind = +1 if path.vertex_kinds[-1] is SwingKind.HIGH else -1
                    truth_bar = path.vertex_minutes[-1] // 240
                    for name, reader in readers.items():
                        matched = [
                            e
                            for e in reader(path.four_hour)
                            if e.pivot.kind == kind
                            and abs(e.pivot.index - truth_bar) <= TOLERANCE_BARS
                        ]
                        rows[name].append(
                            {
                                "pattern": CLASS.get(pattern, pattern),
                                "found": bool(matched),
                                "detected": sorted(_labels(matched)),
                            }
                        )
    finally:
        synthetic_module.SPEED_PER_MINUTE = original
    return rows


def noise_alarm(
    seed: int,
    paths: int,
    returns: Sequence[float],
    readers: dict[str, Callable[[Sequence[Candle]], list[Ew6Event]]],
) -> dict[str, dict[str, object]]:
    rng = random.Random(f"{seed}-noise7")
    counts = {name: Counter() for name in readers}
    totals: Counter[str] = Counter()
    for _ in range(paths):
        bars = noise_bars(rng, returns)
        for name, reader in readers.items():
            events = reader(bars)
            totals[name] += len(events)
            for e in events:
                for label in {*e.five, *e.three}:
                    counts[name][label] += 1
    return {
        name: {
            "pivots": totals[name],
            "alarm": {c: round(counts[name][c] / max(1, totals[name]), 4) for c in CLASSES},
        }
        for name in readers
    }


def evaluate(
    seed: int,
    count: int,
    noise_paths: int,
    returns: Sequence[float],
    readers: dict[str, Callable[[Sequence[Candle]], list[Ew6Event]]],
) -> dict[str, object]:
    rows = pattern_rows(seed, count, returns, readers)
    alarm = noise_alarm(seed, noise_paths, returns, readers)
    return {
        name: {
            "noise_pivots": alarm[name]["pivots"],
            **summarise(rows[name], alarm[name]["alarm"]),  # type: ignore[arg-type]
        }
        for name in readers
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("dev", "gate", "retry"):
        raise SystemExit(
            "usage: audit_ew107_multidegree.py dev [--count N] | gate --confirm-gate"
            " | retry --confirm-final"
        )
    returns = load_returns()
    OUT.mkdir(parents=True, exist_ok=True)
    if argv[0] == "retry":
        if "--confirm-final" not in argv:
            raise SystemExit("재도전은 --confirm-final 로 한 번만 (문서 107 §8)")
        target = OUT / "retry.json"
        if target.exists():
            raise SystemExit(f"{target} 가 이미 있다 — 재도전은 한 번만")
        seeds = {
            str(s): evaluate(s, COUNT_GATE, NOISE_PATHS_GATE, returns, RETRY_READERS)
            for s in RETRY_SEEDS
        }
        passes = {
            c: all(bool(seeds[str(s)]["ew7_h3"][c]["pass"]) for s in RETRY_SEEDS)  # type: ignore[index]
            for c in TRIANGLE_CLASSES
        }
        result = {"seeds": seeds, "pass_all_three_seeds": passes}
        target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        sys.stdout.write(json.dumps(result, indent=1) + "\n")
        return 0
    if argv[0] == "dev":
        count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 10
        result: dict[str, object] = evaluate(DEV_SEED, count, NOISE_PATHS_DEV, returns, DEV_READERS)
        target = OUT / f"dev_{count}.json"
    else:
        if "--confirm-gate" not in argv:
            raise SystemExit("관문 seed 는 --confirm-gate 로 한 번만 (문서 107)")
        target = OUT / "gate.json"
        if target.exists():
            raise SystemExit(f"{target} 가 이미 있다 — 관문은 한 번만")
        seeds = {
            str(s): evaluate(s, COUNT_GATE, NOISE_PATHS_GATE, returns, GATE_READERS)
            for s in GATE_SEEDS
        }
        passes = {
            name: {
                c: all(bool(seeds[str(s)][name][c]["pass"]) for s in GATE_SEEDS)  # type: ignore[index]
                for c in TRIANGLE_CLASSES
            }
            for name in GATE_READERS
        }
        result = {"seeds": seeds, "pass_both_seeds": passes}
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
