"""문서 100 §6-3·4 — EW-5 합성 점검: 10 모양 × 3 degree × 두 설정(4h·1h), 혼동표. 시장·수익 미조회.

사용::

    python scripts/audit_ew100_synthetic.py dev [--count 10]   # 개발 seed 20261008 (조정용)
    python scripts/audit_ew100_synthetic.py gate --confirm-gate  # 관문 seed 20261005·20261006, 1 회

1 등 모양 = 1 등(동점 전체) 해석의 모양이 하나뿐일 때 그 모양, 둘 이상이면 ``TIE``, 진짜 끝에서
발동이 없으면 ``NO_TRIGGER``. 모양 X 의 맞게 = X 경로 중 1 등 모양이 X, 잘못 = 다른 모양 경로 중
1 등 모양이 X (34 §6: ≥ 50% · ≤ 30%, 두 seed 모두). 같은 경로를 EW-4n 으로도 읽어 비교한다.
seed 20260927 은 JEV 예약 — 만들지 않는다.
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

from scripts.audit_ew3c_hourly import DEGREES, hour_bars  # noqa: E402
from scripts.audit_ew93_hourly_degree import THRESHOLD_B, coarsen  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    ScenarioList,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.synthetic import (  # noqa: E402
    EW5_PATTERNS,
    SyntheticPath,
    build_path,
)

GATE_SEEDS = (20261005, 20261006)
DEV_SEED = 20261008
RESERVED_FOR_JEV = 20260927
NOISE, COUNT = 0.001, 50
PATTERNS = ("IMPULSE", "ZIGZAG", "FLAT", "TRIANGLE", *EW5_PATTERNS)
CLASS = {"ENDING_DIAGONAL": "DIAGONAL", "LEADING_DIAGONAL": "DIAGONAL"}
"""문서 100 §4.1: 종결·선행 대각선은 모양 규칙·비율 목표가 같고 하위 파동(3 vs 5)만 다르다 —
판독기는 갈래(대각선)까지만 가린다. 어느 쪽인지는 큰 추세 속 위치(5파·C 또는 1파·A)로."""
CLASSES = tuple(dict.fromkeys(CLASS.get(p, p) for p in PATTERNS))
MOTIVE = frozenset({"IMPULSE", "DIAGONAL"})
"""추진 갈래 — 93·97 의 오판(충격파 끝을 조정으로) 을 갈래 단위로 센다."""
TRUE_MIN, FALSE_MAX = 0.5, 0.3
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "100"


def _top_pattern(scen: ScenarioList) -> str:
    if not scen.ranked:
        return "EMPTY"
    first = scen.ranked[0][1]
    names = {CLASS.get(p.pattern.value, p.pattern.value) for p, key in scen.ranked if key == first}
    return names.pop() if len(names) == 1 else "TIE"


def _detected(scen: ScenarioList) -> list[str]:
    """검출기: 다리가 모두 검증된(검증 비율 1) 후보가 하나라도 있는 갈래 (1 등 여부와 무관)."""
    return sorted(
        {
            CLASS.get(p.pattern.value, p.pattern.value)
            for p, key in scen.ranked
            if key.neg_verified == -1
        }
    )


def _truth_rank(
    scen: ScenarioList, path: SyntheticPath, segment_where: dict[object, int]
) -> object:
    bounds = tuple(
        segment_where.get((k, Timeframe.H1.open_ms_of(path.minutes[t].open_ms)), -1)
        for t, k in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
    )
    label = Label[path.pattern]
    parse = next((c for c, _ in scen.ranked if c.pattern is label and c.bounds == bounds), None)
    return None if parse is None else scen.pessimistic_rank(parse)


def _family(name: str) -> str:
    if name in MOTIVE:
        return "MOTIVE"
    if name in ("TIE", "EMPTY", "NO_TRIGGER"):
        return name
    return "CORRECTIVE"


def _read(path: SyntheticPath, minutes: Sequence[Candle], thr: float) -> dict[str, object]:
    four = path.four_hour
    hours = hour_bars(minutes)
    groups = (minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (thr,))
    truth = (
        path.vertex_kinds[-1],
        Timeframe.H1.open_ms_of(path.minutes[path.vertex_minutes[-1]].open_ms),
    )
    hit = next(
        (
            t
            for t in causal_triggers(replay, hours, four, all_new_pivots=True)
            if (t.segment[-1].kind, t.segment[-1].hour_open_ms) == truth
        ),
        None,
    )
    if hit is None:
        return {"ew5": "NO_TRIGGER", "ew4n": "NO_TRIGGER", "n5": 0, "det5": [], "det4": []}
    common = {"lookback_bars": EW3E_LOOKBACK_BARS, "ew4": True, "nested": True}
    (s5,) = scenario_lists([hit], hours, ew5=True, **common)
    (s4,) = scenario_lists([hit], hours, **common)
    where: dict[object, int] = {(p.kind, p.hour_open_ms): k for k, p in enumerate(hit.segment)}
    return {
        "ew5": _top_pattern(s5),
        "ew4n": _top_pattern(s4),
        "n5": s5.total,
        "det5": _detected(s5),
        "det4": _detected(s4),
        "truth_rank5": _truth_rank(s5, path, where),
    }


def _run(seed: int, config: str, count: int, log: Callable[[str], None]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    original = synthetic_module.SPEED_PER_MINUTE
    hourly = config == "1h"
    if hourly:
        synthetic_module.SPEED_PER_MINUTE = original / 2  # 봉 수 유지 (93 §8)
    try:
        for pattern in PATTERNS:
            rng = random.Random(f"{seed}-{config}-{pattern}")
            for degree, (scale, threshold) in DEGREES.items():
                for _ in range(count):
                    if hourly:
                        path = build_path(rng, pattern, scale / 2, threshold / 2, NOISE)
                        minutes: Sequence[Candle] = coarsen(path.minutes)
                        thr = THRESHOLD_B
                    else:
                        path = build_path(rng, pattern, scale, threshold, NOISE)
                        minutes, thr = path.minutes, EW0_THRESHOLD_PCT
                    out = _read(path, minutes, thr)
                    rows.append({"pattern": CLASS.get(pattern, pattern), "degree": degree, **out})
            log(f"{seed} {config} {pattern} done")
    finally:
        synthetic_module.SPEED_PER_MINUTE = original
    return rows


def _summary(rows: list[dict[str, object]], version: str) -> dict[str, object]:
    by_truth: dict[str, Counter[str]] = {p: Counter() for p in CLASSES}
    for r in rows:
        by_truth[str(r["pattern"])][str(r[version])] += 1
    totals = {p: sum(c.values()) for p, c in by_truth.items()}
    every = sum(totals.values())
    rates: dict[str, dict[str, object]] = {}
    for p in CLASSES:
        true = by_truth[p][p] / max(1, totals[p])
        false = sum(by_truth[q][p] for q in CLASSES if q != p) / max(1, every - totals[p])
        rates[p] = {
            "true": round(true, 4),
            "false": round(false, 4),
            "pass": true >= TRUE_MIN and false <= FALSE_MAX,
        }
    fam: dict[str, Counter[str]] = {"MOTIVE": Counter(), "CORRECTIVE": Counter()}
    for r in rows:
        fam[_family(str(r["pattern"]))][_family(str(r[version]))] += 1
    det_key = "det5" if version == "ew5" else "det4"
    detector: dict[str, dict[str, object]] = {}
    for p in CLASSES:
        hit_true = sum(p in r[det_key] for r in rows if r["pattern"] == p)  # type: ignore[operator]
        hit_false = sum(p in r[det_key] for r in rows if r["pattern"] != p)  # type: ignore[operator]
        t = hit_true / max(1, totals[p])
        f = hit_false / max(1, every - totals[p])
        detector[p] = {
            "true": round(t, 4),
            "false": round(f, 4),
            "pass": t >= TRUE_MIN and f <= FALSE_MAX,
        }
    impulse = by_truth["IMPULSE"]
    imp_total = max(1, totals["IMPULSE"])
    return {
        "rates": rates,
        "detector_full_verified": detector,
        "confusion": {p: dict(sorted(c.items())) for p, c in by_truth.items()},
        "family_confusion": {k: dict(sorted(c.items())) for k, c in fam.items()},
        "impulse_read_corrective": round(
            sum(n for name, n in impulse.items() if _family(name) == "CORRECTIVE") / imp_total, 4
        ),
        "impulse_read_motive_family": round(
            sum(n for name, n in impulse.items() if _family(name) == "MOTIVE") / imp_total, 4
        ),
    }


def evaluate(seed: int, count: int, log: Callable[[str], None]) -> dict[str, object]:
    out: dict[str, object] = {"seed": seed, "count_per_degree": count, "noise": NOISE}
    for config in ("4h", "1h"):
        rows = _run(seed, config, count, log)
        out[config] = {v: _summary(rows, v) for v in ("ew5", "ew4n")}
        out[f"{config}_rows"] = rows
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("dev", "gate"):
        raise SystemExit("usage: audit_ew100_synthetic.py dev [--count N] | gate --confirm-gate")

    def log(msg: str) -> None:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    OUT.mkdir(parents=True, exist_ok=True)
    if argv[0] == "dev":
        count = int(argv[argv.index("--count") + 1]) if "--count" in argv else 10
        result = evaluate(DEV_SEED, count, log)
        target = OUT / f"synthetic_dev_{count}.json"
    else:
        if "--confirm-gate" not in argv:
            raise SystemExit("관문 seed 는 --confirm-gate 로 한 번만 (문서 100 §6)")
        target = OUT / "synthetic_gate.json"
        if target.exists():
            raise SystemExit(f"{target} 가 이미 있다 — 관문은 한 번만")
        seeds = {str(s): evaluate(s, COUNT, log) for s in GATE_SEEDS}
        passes: dict[str, dict[str, dict[str, bool]]] = {}
        for config in ("4h", "1h"):
            for metric in ("rates", "detector_full_verified"):
                passes.setdefault(config, {})[metric] = {
                    p: all(
                        bool(seeds[str(s)][config]["ew5"][metric][p]["pass"])  # type: ignore[index]
                        for s in GATE_SEEDS
                    )
                    for p in CLASSES
                }
        result = {"seeds": seeds, "pass_both_seeds": passes}
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
