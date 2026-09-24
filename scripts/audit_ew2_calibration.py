"""EW-2 판독기 보정: 교과서 합성 경로 혼동표 + 시장 기술 통계 (33 §22 사전 고정).

합성 경로는 시장 자료가 아니다. 시장 쪽은 DB 읽기 전용이며 WA 거래·방향·손익, D22, 전략 경로·원장을
조회하지 않는다. 결과는 stdout JSON 뿐이다.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.errors import DataIntegrityError  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.features.structure import SwingKind  # noqa: E402
from auto_trading.research.elliott.candidates import generate_bounded_candidates  # noqa: E402
from auto_trading.research.elliott.degrees import (  # noqa: E402
    HALF_OCTAVE_GRID,
    ChildLevel,
    ChildReadingReason,
    LegReading,
    read_leg,
    read_leg_descending,
)
from auto_trading.research.elliott.grammar import (  # noqa: E402
    EXPECTED_SUBDIVISIONS,
    LegSubdivision,
    WavePattern,
    impulse_rules,
    subdivision_matches,
)
from auto_trading.research.elliott.h4_evidence import (  # noqa: E402
    EvidenceStatus,
    FourHourEvidence,
    FourHourPivotEvidence,
    map_minute_path_to_four_hour_evidence,
)
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.synthetic import (  # noqa: E402
    MOTIVE,
    SEED,
    SyntheticPath,
    build_path,
)
from scripts import audit_ew2_descending as ew2  # noqa: E402

DEGREES = {"D1": (2, 0.5, 0.04), "D2": (4, 1.0, 0.08), "D3": (6, 2.0, 0.16)}  # 격자 색인·배율·임계
CELLS = ((0.001, 50), (0.0, 20))  # (잡음 표준편차, 표본 수)
STRICT_FIVE = {LegSubdivision.IMPULSE_FIVE, LegSubdivision.MOTIVE_FIVE}


def _outcome(r: LegReading) -> str:
    return r.shape.value if r.reason is None else r.reason.value


def _rule_failures(r: LegReading, bars: Sequence[Candle], as_of: int) -> list[str]:
    """보이는 다리 수가 5 인데 엄격한 5 가 아닌 읽기의 impulse 규칙 실패·보류 목록."""
    if r.child_leg_count != 5 or r.shape in STRICT_FIVE or len(r.child_anchors) != 6:
        return []
    closed = tuple(bar for bar in bars if bar.close_ms <= as_of)
    direction = 1 if r.child_anchors[0].kind is SwingKind.LOW else -1
    failures, pending = impulse_rules(list(r.child_anchors), closed, direction)
    return failures + pending


def _levels(grid: Sequence[Sequence[FourHourEvidence]], parent_index: int) -> list[ChildLevel]:
    return [
        ChildLevel(HALF_OCTAVE_GRID[j].degree_id, grid[j]) for j in range(parent_index - 1, -1, -1)
    ]


def _synthetic_one(path: SyntheticPath, degree_id: str, parent_index: int) -> dict[str, object]:
    bars = path.four_hour
    as_of = bars[-1].close_ms
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(bars)))
    thresholds = tuple(d.threshold_pct for d in HALF_OCTAVE_GRID[: parent_index + 1])
    replays = replay_minute_groups_ladder(bars, groups, as_of, thresholds)
    grid = [map_minute_path_to_four_hour_evidence(bars, r, as_of) for r in replays]
    parent = grid[parent_index]
    levels = _levels(grid, parent_index)

    # 부모 후보 순서와 구간(UNKNOWN 경계) 번호
    sequence: list[tuple[FourHourPivotEvidence, int]] = []
    segment = 0
    for item in parent:
        if item.status is EvidenceStatus.UNKNOWN:
            segment += 1
        elif item.candidate is not None:
            sequence.append((item.candidate, segment))
    position = {(c.kind, c.extreme_four_hour_open_ms): i for i, (c, _) in enumerate(sequence)}
    matched: list[int | None] = [
        position.get((kind, Timeframe.H4.open_ms_of(path.minutes[t].open_ms)))
        for t, kind in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
    ]
    detected = all(m is not None for m in matched)

    legs: list[dict[str, object]] = []
    for leg, truth in enumerate(path.leg_truth):
        a, b = matched[leg], matched[leg + 1]
        if a is None or b is None or b != a + 1 or sequence[a][1] != sequence[b][1]:
            legs.append({"truth": truth, "detected": False})
            continue
        r = read_leg_descending(bars, levels, sequence[a][0], sequence[b][0], as_of)
        legs.append(
            {
                "truth": truth,
                "detected": True,
                "outcome": _outcome(r),
                "rule_failures": _rule_failures(r, bars, as_of) if truth == MOTIVE else [],
            }
        )

    scenario: dict[str, object] = {"evaluated": False}
    last = matched[-1]
    if detected and last is not None:
        end = sequence[last][0]
        index = next(
            i for i, bar in enumerate(bars) if bar.close_ms == end.confirmed_four_hour_close_ms
        )
        cut = [ChildLevel(lv.degree_id, tuple(lv.evidence[: index + 1])) for lv in levels]
        try:
            snap = generate_bounded_candidates(
                bars[: index + 1], parent[: index + 1], end.confirmed_four_hour_close_ms,
                degree_id=degree_id, child_degree_id="EW2", child_levels=cut,
            )  # fmt: skip
            pattern = WavePattern(path.pattern)
            record = next(r for r in snap.records if r.scenario.pattern is pattern)
            expected = EXPECTED_SUBDIVISIONS[pattern]
            proofs = record.scenario.subdivisions
            ok = (
                sum(subdivision_matches(e, p.shape) for p, e in zip(proofs, expected, strict=True))
                if len(proofs) == len(expected)
                else None
            )
            scenario = {
                "evaluated": True,
                "status": record.assessment.status.value,
                "legs_consistent": ok,
            }
        except DataIntegrityError as exc:
            if "UNKNOWN 경계" not in str(exc):
                raise
            scenario = {"evaluated": True, "status": "BLOCKED_CROSS_UNKNOWN"}
    return {"detected": detected, "legs": legs, "scenario": scenario}


def synthetic() -> dict[str, object]:
    rng = random.Random(SEED)
    cells: dict[str, object] = {}
    for degree_id, (parent_index, scale, threshold) in DEGREES.items():
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                detected = 0
                confusion: Counter[str] = Counter()
                failures: Counter[str] = Counter()
                status: Counter[str] = Counter()
                consistent: Counter[str] = Counter()
                for _ in range(count):
                    path = build_path(rng, pattern, scale, threshold, noise)
                    one = _synthetic_one(path, degree_id, parent_index)
                    detected += bool(one["detected"])
                    for leg in one["legs"]:  # type: ignore[attr-defined]
                        if leg["detected"]:
                            confusion[f"{leg['truth']}|{leg['outcome']}"] += 1
                            failures.update(leg["rule_failures"])
                        else:
                            confusion[f"{leg['truth']}|NOT_DETECTED"] += 1
                    sc = one["scenario"]
                    if sc["evaluated"]:  # type: ignore[index]
                        status[str(sc["status"])] += 1  # type: ignore[index]
                        consistent[str(sc.get("legs_consistent"))] += 1  # type: ignore[attr-defined]
                cells[f"{degree_id}|{pattern}|noise={noise}"] = {
                    "paths": count,
                    "all_vertices_detected": detected,
                    "leg_confusion": dict(sorted(confusion.items())),
                    "motive_five_rule_failures": dict(sorted(failures.items())),
                    "scenario_status": dict(sorted(status.items())),
                    "scenario_legs_consistent": dict(sorted(consistent.items())),
                }
    return cells


def _p(cells: dict[str, object], noise: float) -> dict[str, object]:
    strict = total = false_five = corrective = 0
    for key, cell in cells.items():
        if not key.endswith(f"noise={noise}"):
            continue
        for label, n in cell["leg_confusion"].items():  # type: ignore[index]
            truth, outcome = label.split("|")
            if outcome == "NOT_DETECTED":
                continue
            if truth == MOTIVE:
                total += n
                strict += n if outcome in {s.value for s in STRICT_FIVE} else 0
            else:
                corrective += n
                false_five += n if outcome in {s.value for s in STRICT_FIVE} else 0
    return {
        "motive_legs_detected": total,
        "strict_five": strict,
        "p": round(strict / total, 4) if total else None,
        "corrective_legs_detected": corrective,
        "false_motive_rate": round(false_five / corrective, 4) if corrective else None,
    }


def market() -> dict[str, object]:
    bars, grid, _ = ew2.load_grid(ew2.market.DB_PATH)
    as_of = bars[-1].close_ms
    out: dict[str, object] = {}
    strict_all = legs_all = 0
    for degree_id, (parent_index, _, _) in DEGREES.items():
        levels = _levels(grid, parent_index)
        ids = [lv.degree_id for lv in levels]
        first_n: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        after_three: Counter[str] = Counter()
        previous: FourHourPivotEvidence | None = None
        legs = strict = 0
        for item in grid[parent_index]:
            if item.status is EvidenceStatus.UNKNOWN:
                previous = None
                continue
            if item.candidate is None:
                continue
            if previous is not None:
                legs += 1
                r = read_leg_descending(bars, levels, previous, item.candidate, as_of)
                strict += r.shape in STRICT_FIVE
                if r.child_leg_count is not None and r.child_leg_count >= 3:
                    first_n[str(r.child_leg_count)] += 1
                failures.update(_rule_failures(r, bars, as_of))
                if r.child_leg_count == 3:
                    if r.reason is ChildReadingReason.THREE_THEN_FIVE:
                        after_three["NESTED_STRICT_FIVE"] += 1
                    else:
                        k = ids.index(str(r.child_degree_id))
                        if k + 1 == len(levels):
                            after_three["FLOOR"] += 1
                        else:
                            f = read_leg(
                                bars, levels[k + 1].evidence, previous, item.candidate, as_of
                            )
                            after_three[
                                "UNREADABLE"
                                if f.child_leg_count is None
                                else "THREE"
                                if f.child_leg_count == 3
                                else "FIVE_NOT_NESTED_OR_NOT_STRICT"
                                if f.child_leg_count == 5
                                else f"N{f.child_leg_count}"
                            ] += 1
            previous = item.candidate
        strict_all += strict
        legs_all += legs
        out[degree_id] = {
            "legs": legs,
            "strict_five": strict,
            "first_visible_n": dict(sorted(first_n.items())),
            "five_rule_failures": dict(sorted(failures.items())),
            "after_three_next_level": dict(sorted(after_three.items())),
        }
    out["strict_five_rate_all_legs"] = round(strict_all / legs_all, 4) if legs_all else None
    return out


def main() -> int:
    cells = synthetic()
    result = {
        "synthetic": cells,
        "p_noise_0.001": _p(cells, 0.001),
        "p_noise_0.0": _p(cells, 0.0),
        "market": market(),
        "note": "33 §22 사전 고정. 합성은 교과서 가정(판독기 능력 상한). WA·성과 미조회.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
