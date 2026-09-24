"""EW-2 반옥타브 하강 읽기의 시장 기술 통계 (33 §20 에서 사전 고정). 읽기 전용.

WA 거래·방향·손익, D22, 전략 경로·원장을 조회하지 않는다. 결과는 stdout JSON 뿐이다.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.errors import DataIntegrityError  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.candidates import generate_bounded_candidates  # noqa: E402
from auto_trading.research.elliott.degrees import (  # noqa: E402
    HALF_OCTAVE_GRID,
    ChildLevel,
    ChildReadingReason,
    index_child_path,
    read_leg_descending,
)
from auto_trading.research.elliott.grammar import (  # noqa: E402
    EXPECTED_SUBDIVISIONS,
    LegSubdivision,
    subdivision_matches,
)
from auto_trading.research.elliott.h4_evidence import (  # noqa: E402
    EvidenceStatus,
    FourHourEvidence,
    FourHourPivotEvidence,
    map_minute_path_to_four_hour_evidence,
)
from auto_trading.research.elliott.minute_path import (  # noqa: E402
    MinutePathReplay,
    replay_minute_groups_ladder,
)
from scripts import audit_ew0_degree_ladder as ladder  # noqa: E402

market = ladder.market
REGRESSION = {  # 33 §17 의 D0~D3 digest
    "G0": "034a8ed68e5d67b343d65049ffcdd0274a58bcc6dc4894af55c9e406dd5ffe37",
    "G2": "f28194fd47189a54f7ed7f23cd70c7a9212d23d396894f6817d4e57b94f715b2",
    "G4": "906fc693eb40ac4850ac9976bc125b9a9531a1a69b10c7fdec98b1d9df7784a5",
    "G6": "298f5c5faafcf6c0409d7d40603c0cd2ded8835f397c617b926f45145fe1070f",
}
PARENTS = {"D1": 2, "D2": 4, "D3": 6}  # 격자 색인
BASELINE_DEFINITE = {  # 33 §17/§19 의 모양 확정 다리 수 (§20 채택 기준 iii)
    "EW0": {"D1": 7, "D2": 21, "D3": 7},
    "EW1": {"D1": 18, "D2": 31, "D3": 11},
}
DEFINITE = {
    LegSubdivision.CORRECTIVE_THREE,
    LegSubdivision.IMPULSE_FIVE,
    LegSubdivision.MOTIVE_FIVE,
}


def load_grid(
    db_path: Path,
) -> tuple[tuple[Candle, ...], list[tuple[FourHourEvidence, ...]], tuple[MinutePathReplay, ...]]:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        bars = tuple(
            market._candle(row)
            for row in connection.execute(
                market._QUERY, ("binance_usdm", "BTCUSDT", "4h", market.FROM_MS, market.TO_MS)
            )
        )
        if not bars:
            raise ValueError("4h 시장자료가 없다")
        as_of = bars[-1].close_ms
        replays = replay_minute_groups_ladder(
            bars,
            market._minute_groups(connection, bars),
            as_of,
            tuple(d.threshold_pct for d in HALF_OCTAVE_GRID),
        )
    finally:
        connection.close()
    evidence = [map_minute_path_to_four_hour_evidence(bars, r, as_of) for r in replays]
    return bars, evidence, replays


def _legs(
    bars: tuple[Candle, ...], parent: Sequence[FourHourEvidence], levels: Sequence[ChildLevel]
) -> dict[str, object]:
    as_of = bars[-1].close_ms
    outcome: Counter[str] = Counter()
    first_visible: Counter[str] = Counter()
    blocked_at: Counter[str] = Counter()
    previous: FourHourPivotEvidence | None = None
    for item in parent:
        if item.status is EvidenceStatus.UNKNOWN:
            previous = None
            continue
        if item.candidate is None:
            continue
        if previous is not None:
            r = read_leg_descending(bars, levels, previous, item.candidate, as_of)
            outcome[r.shape.value if r.reason is None else r.reason.value] += 1
            if r.child_leg_count is not None and r.child_leg_count >= 3:
                first_visible[str(r.child_degree_id)] += 1
            if r.reason is ChildReadingReason.DESCENT_BLOCKED:
                blocked_at[str(r.child_degree_id)] += 1
        previous = item.candidate
    return {
        "legs": sum(outcome.values()),
        "outcome": dict(sorted(outcome.items())),
        "definite": sum(outcome[s.value] for s in DEFINITE),
        "first_visible_level": dict(sorted(first_visible.items())),
        "blocked_at_level": dict(sorted(blocked_at.items())),
    }


def _scenarios(
    bars: tuple[Candle, ...],
    parent: Sequence[FourHourEvidence],
    levels: Sequence[ChildLevel],
    degree_id: str,
    label: str,
) -> dict[str, object]:
    status: Counter[str] = Counter()
    consistent: Counter[str] = Counter()
    pass_windows: dict[str, set[int]] = {}
    blocked = 0
    for index in range(len(bars)):
        cut = [ChildLevel(lv.degree_id, tuple(lv.evidence[: index + 1]), lv.path) for lv in levels]
        try:
            snap = generate_bounded_candidates(
                bars[: index + 1],
                parent[: index + 1],
                bars[index].close_ms,
                degree_id=degree_id,
                child_degree_id=label,
                child_levels=cut,
            )
        except DataIntegrityError as exc:
            if "UNKNOWN 경계" not in str(exc):
                raise
            blocked += 1
            continue
        for record in snap.records:
            pattern = record.scenario.pattern
            status[f"{pattern.value}|{record.assessment.status.value}"] += 1
            expected = EXPECTED_SUBDIVISIONS[pattern]
            proofs = record.scenario.subdivisions
            if len(proofs) != len(expected):
                continue
            ok = sum(subdivision_matches(e, p.shape) for p, e in zip(proofs, expected, strict=True))
            consistent[f"{pattern.value}|{ok}/{len(expected)}"] += 1
            if record.assessment.status.value == "PASS":
                pass_windows.setdefault(pattern.value, set()).add(
                    record.scenario.evidence[0].close_ms
                )
    return {
        "blocked_cross_unknown_closes": blocked,
        "grammar_status_closes": dict(sorted(status.items())),
        "complete_window_legs_consistent_closes": dict(sorted(consistent.items())),
        "distinct_pass_windows": {k: len(v) for k, v in sorted(pass_windows.items())},
        "pass_window_first_close_ms": {k: sorted(v) for k, v in sorted(pass_windows.items())},
    }


def main() -> int:
    bars, grid, replays = load_grid(market.DB_PATH)
    digests = {
        spec.degree_id: ladder._digest(ev) for spec, ev in zip(HALF_OCTAVE_GRID, grid, strict=True)
    }
    for level_id, expected in REGRESSION.items():
        if digests[level_id] != expected:
            raise SystemExit(f"{level_id} digest 가 §17 과 다르다: {digests[level_id]}")
    grid_stats = {
        spec.degree_id: {
            "threshold_pct": str(spec.threshold_pct),
            "mapped_evidence_sha256": digests[spec.degree_id],
            "status": dict(sorted(Counter(i.status.value for i in ev).items())),
        }
        for spec, ev in zip(HALF_OCTAVE_GRID, grid, strict=True)
    }
    paths = [index_child_path(r) for r in replays]
    readers: dict[str, dict[str, object]] = {"EW0": {}, "EW1": {}}
    adoption: dict[str, bool] = {}
    for reader in ("EW0", "EW1"):
        ok = True
        for degree_id, parent_index in PARENTS.items():
            levels = [
                ChildLevel(
                    HALF_OCTAVE_GRID[j].degree_id,
                    grid[j],
                    paths[j] if reader == "EW1" else None,
                )
                for j in range(parent_index - 1, -1, -1)
            ]
            legs = _legs(bars, grid[parent_index], levels)
            ok = ok and int(str(legs["definite"])) >= BASELINE_DEFINITE[reader][degree_id]
            readers[reader][degree_id] = {
                "child_levels": [lv.degree_id for lv in levels],
                "legs": legs,
                "scenarios": _scenarios(
                    bars, grid[parent_index], levels, degree_id, f"EW2-{reader}"
                ),
            }
        adoption[f"iii_definite_not_lower_{reader}"] = ok
    result = {
        "period_utc": "2020-01-01/2022-01-01",
        "source": "binance_usdm:BTCUSDT",
        "grid": grid_stats,
        "readers": readers,
        "adoption_checks": adoption,
        "note": "33 §20 사전 고정 기술 통계. 반복 마감은 독립 표본 아님. WA·성과 미조회.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
