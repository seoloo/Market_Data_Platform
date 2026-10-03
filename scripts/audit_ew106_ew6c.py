"""문서 106 — 검출기 좁히기: EW-6c(가장 뚜렷한 조합 하나) vs EW-6b, 패턴 없는 경로의 헛검출률 포함.

사용::

    python scripts/audit_ew106_ew6c.py dev [--count 10]      # 개발 seed 20261009
    python scripts/audit_ew106_ew6c.py gate --confirm-gate   # 관문 seed 20261012 · 20261013, 1 회

패턴 없는 경로 = 실제 BTCUSDT 1m 수익을 **무작위로 뒤섞어**(구조 제거) k 배로 이은 무작위 걸음.
수익 시험이 아니다.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew3c_hourly import DEGREES  # noqa: E402
from scripts.audit_ew103_market_synthetic import STEP_1H, load_returns  # noqa: E402
from scripts.audit_ew104_ew6 import CLASS, CLASSES, NOISE, PATTERNS, TOLERANCE_BARS  # noqa: E402

from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.features.structure import SwingKind  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.geometry_ew6 import (  # noqa: E402
    Ew6Event,
    ew6b_events,
    ew6c_events,
)
from auto_trading.research.elliott.synthetic import MarketNoise, build_path  # noqa: E402

DEV_SEED = 20261009
GATE_SEEDS = (20261012, 20261013)
MARKET_K = 2.0
COUNT_GATE = 50
NOISE_PATHS_DEV, NOISE_PATHS_GATE = 60, 300
NOISE_MINUTES = 48_000
TRUE_MIN, FALSE_MAX, ALARM_MAX = 0.5, 0.3, 0.10
READERS: dict[str, Callable[[Sequence[Candle]], list[Ew6Event]]] = {
    "ew6b": lambda bars: ew6b_events(bars, 2.0),
    "ew6c": lambda bars: ew6c_events(bars, 2.0),
}
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "106"


def _labels(events: Sequence[Ew6Event]) -> set[str]:
    return {x for e in events for x in (*e.five, *e.three)}


def pattern_rows(
    seed: int, count: int, returns: Sequence[float]
) -> dict[str, list[dict[str, object]]]:
    original = synthetic_module.SPEED_PER_MINUTE
    synthetic_module.SPEED_PER_MINUTE = original / 2  # 1h 설정 (93 §8)
    market = MarketNoise(returns, MARKET_K, STEP_1H, 2)
    rows: dict[str, list[dict[str, object]]] = {name: [] for name in READERS}
    try:
        for pattern in PATTERNS:
            rng = random.Random(f"{seed}-ew6c-{pattern}")
            for _degree, (scale, threshold) in DEGREES.items():
                for _ in range(count):
                    path = build_path(rng, pattern, scale / 2, threshold / 2, NOISE, market)
                    kind = +1 if path.vertex_kinds[-1] is SwingKind.HIGH else -1
                    truth_bar = path.vertex_minutes[-1] // 240
                    for name, reader in READERS.items():
                        events = reader(path.four_hour)
                        matched = [
                            e
                            for e in events
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


def noise_bars(rng: random.Random, returns: Sequence[float]) -> list[Candle]:
    """뒤섞은 실제 1m 수익 × k 의 무작위 걸음 (1h 설정: 실제 1 분 = 합성 4 분), 240 분 봉."""
    level = math.log(30_000.0)
    bars: list[Candle] = []
    step_ret = 0.0
    for t, start in enumerate(range(0, NOISE_MINUTES, 240), start=1):
        hi = lo = op = level
        for minute in range(start, start + 240):
            if minute % STEP_1H == 0:
                step_ret = MARKET_K * returns[rng.randrange(len(returns))] / STEP_1H
            level += step_ret
            hi, lo = max(hi, level), min(lo, level)
        bars.append(
            Candle(
                open_ms=t * 14_400_000,
                close_ms=(t + 1) * 14_400_000 - 1,
                open=Decimal(str(round(math.exp(op), 2))),
                high=Decimal(str(round(math.exp(hi), 2))),
                low=Decimal(str(round(math.exp(lo), 2))),
                close=Decimal(str(round(math.exp(level), 2))),
                volume=Decimal(1),
            )
        )
    return bars


def noise_alarm(seed: int, paths: int, returns: Sequence[float]) -> dict[str, dict[str, object]]:
    rng = random.Random(f"{seed}-noise")
    counts = {name: Counter() for name in READERS}
    totals = Counter()
    for _ in range(paths):
        bars = noise_bars(rng, returns)
        for name, reader in READERS.items():
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
        for name in READERS
    }


def summarise(rows: Sequence[dict[str, object]], alarm: dict[str, float]) -> dict[str, object]:
    totals = Counter(str(r["pattern"]) for r in rows)
    every = len(rows)
    out: dict[str, object] = {
        "paths": every,
        "end_found": round(sum(bool(r["found"]) for r in rows) / every, 4),
    }
    for c in CLASSES:
        t = sum(c in r["detected"] for r in rows if r["pattern"] == c) / max(1, totals[c])  # type: ignore[operator]
        f = sum(c in r["detected"] for r in rows if r["pattern"] != c) / max(1, every - totals[c])  # type: ignore[operator]
        a = alarm[c]
        out[c] = {
            "true": round(t, 4),
            "false": round(f, 4),
            "noise_alarm": a,
            "pass": t >= TRUE_MIN and f <= FALSE_MAX and a <= ALARM_MAX,
        }
    return out


def evaluate(
    seed: int, count: int, noise_paths: int, returns: Sequence[float]
) -> dict[str, object]:
    rows = pattern_rows(seed, count, returns)
    alarm = noise_alarm(seed, noise_paths, returns)
    return {
        name: {
            "noise_pivots": alarm[name]["pivots"],
            **summarise(rows[name], alarm[name]["alarm"]),  # type: ignore[arg-type]
        }
        for name in READERS
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("dev", "gate"):
        raise SystemExit("usage: audit_ew106_ew6c.py dev [--count N] | gate --confirm-gate")
    returns = load_returns()
    OUT.mkdir(parents=True, exist_ok=True)
    if argv[0] == "dev":
        count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 10
        result: dict[str, object] = evaluate(DEV_SEED, count, NOISE_PATHS_DEV, returns)
        target = OUT / f"dev_{count}.json"
    else:
        if "--confirm-gate" not in argv:
            raise SystemExit("관문 seed 는 --confirm-gate 로 한 번만 (문서 106)")
        target = OUT / "gate.json"
        if target.exists():
            raise SystemExit(f"{target} 가 이미 있다 — 관문은 한 번만")
        seeds = {str(s): evaluate(s, COUNT_GATE, NOISE_PATHS_GATE, returns) for s in GATE_SEEDS}
        passes = {
            name: {
                c: all(bool(seeds[str(s)][name][c]["pass"]) for s in GATE_SEEDS)  # type: ignore[index]
                for c in CLASSES
            }
            for name in READERS
        }
        result = {"seeds": seeds, "pass_both_seeds": passes}
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
