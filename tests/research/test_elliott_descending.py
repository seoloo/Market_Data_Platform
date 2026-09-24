"""EW-2 반옥타브 하강 읽기 (33 §20) — 합성 자료."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.elliott.candidates import generate_bounded_candidates
from auto_trading.research.elliott.degrees import (
    DEGREE_LADDER,
    HALF_OCTAVE_GRID,
    ChildLevel,
    ChildReadingReason,
    read_leg_descending,
)
from auto_trading.research.elliott.grammar import GrammarStatus, LegSubdivision
from auto_trading.research.elliott.h4_evidence import (
    EvidenceStatus,
    EvidenceUnknownReason,
    FourHourEvidence,
)
from tests.research.test_elliott_degrees import (
    LEG1,
    LEG2,
    LEG3,
    LEG4,
    LEG5,
    _anchors,
    _impulse,
    ladder_fixture,
)


def keep(child: list[FourHourEvidence], vertices: set[int]) -> list[FourHourEvidence]:
    """자식 꼭짓점 중 ``vertices`` 번째만 후보로 남긴 (더 굵은 단계를 흉내 낸) 증거."""
    out: list[FourHourEvidence] = []
    n = -1
    for item in child:
        if item.candidate is None:
            out.append(item)
            continue
        n += 1
        out.append(
            item if n in vertices else FourHourEvidence(item.close_ms, EvidenceStatus.NO_EVENT)
        )
    return out


def test_grid_is_fixed_half_octave_and_contains_the_ladder() -> None:
    assert [d.threshold_pct for d in HALF_OCTAVE_GRID] == [
        Decimal(x) for x in ("0.02", "0.028284", "0.04", "0.056569", "0.08", "0.113137", "0.16")
    ]
    grid = {d.threshold_pct for d in HALF_OCTAVE_GRID}
    assert {d.threshold_pct for d in DEGREE_LADDER} <= grid


def test_descends_past_invisible_level_to_first_visible_structure() -> None:
    bars, parent, fine = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    coarse = keep(fine, {0, 5})  # 굵은 단계: 부모 양 끝만 → n=1
    r = read_leg_descending(
        bars, [ChildLevel("A", coarse), ChildLevel("B", fine)], a, b, bars[-1].close_ms
    )
    assert (r.shape, r.child_leg_count, r.child_degree_id) == (
        LegSubdivision.IMPULSE_FIVE,
        5,
        "B",
    )


def test_three_that_becomes_nested_five_one_level_down_is_unknown() -> None:
    bars, parent, fine = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    coarse = keep(fine, {0, 3, 4, 5})  # 100→140→130→150: 2파가 안 보이는 3
    r = read_leg_descending(
        bars, [ChildLevel("A", coarse), ChildLevel("B", fine)], a, b, bars[-1].close_ms
    )
    assert (r.shape, r.reason, r.child_degree_id) == (
        LegSubdivision.UNKNOWN,
        ChildReadingReason.THREE_THEN_FIVE,
        "A",
    )


def test_three_is_corrective_at_floor_or_when_finer_is_not_a_nested_five() -> None:
    bars, parent, fine = ladder_fixture(LEG2)
    a, b = _anchors(parent)
    as_of = bars[-1].close_ms
    floor = read_leg_descending(bars, [ChildLevel("B", fine)], a, b, as_of)
    assert floor.shape is LegSubdivision.CORRECTIVE_THREE
    both = read_leg_descending(bars, [ChildLevel("A", fine), ChildLevel("B", fine)], a, b, as_of)
    assert (both.shape, both.child_degree_id) == (LegSubdivision.CORRECTIVE_THREE, "A")


def test_unreadable_level_stops_descent_instead_of_skipping() -> None:
    bars, parent, fine = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    blocked = list(keep(fine, {0, 5}))
    blocked[4] = FourHourEvidence(
        blocked[4].close_ms, EvidenceStatus.UNKNOWN, reason=EvidenceUnknownReason.MULTIPLE_EVENTS
    )
    r = read_leg_descending(
        bars, [ChildLevel("A", blocked), ChildLevel("B", fine)], a, b, bars[-1].close_ms
    )
    assert (r.reason, r.child_degree_id) == (ChildReadingReason.DESCENT_BLOCKED, "A")


def test_all_levels_single_leg_is_invisible_to_floor() -> None:
    bars, parent, fine = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    only_ends = keep(fine, {0, 5})
    r = read_leg_descending(
        bars,
        [ChildLevel("A", only_ends), ChildLevel("B", only_ends)],
        a,
        b,
        bars[-1].close_ms,
    )
    assert (r.reason, r.child_leg_count) == (ChildReadingReason.INVISIBLE_TO_FLOOR, 1)
    with pytest.raises(DataIntegrityError):
        read_leg_descending(bars, [], a, b, bars[-1].close_ms)


def _levels(fine: list[FourHourEvidence], coarse_vertices: set[int]) -> list[ChildLevel]:
    return [ChildLevel("A", keep(fine, coarse_vertices)), ChildLevel("B", fine)]


def test_generator_uses_descent_and_labels_the_level_per_leg() -> None:
    bars, parent, fine = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5)
    parent_vertices = {0, 5, 8, 13, 16, 21}
    snapshot = generate_bounded_candidates(
        bars, parent, bars[-1].close_ms, degree_id="D1", child_degree_id="EW2",
        child_levels=_levels(fine, parent_vertices),
    )  # fmt: skip
    impulse = _impulse(snapshot)
    assert [p.shape for p in impulse.scenario.subdivisions] == [
        LegSubdivision.IMPULSE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.IMPULSE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.IMPULSE_FIVE,
    ]
    assert all(p.source_id.startswith("B/EW2:") for p in impulse.scenario.subdivisions)
    assert impulse.assessment.status is GrammarStatus.PASS
    with pytest.raises(DataIntegrityError):
        generate_bounded_candidates(
            bars, parent, bars[-1].close_ms, child_levels=_levels(fine, parent_vertices)
        )


@pytest.mark.leakage
def test_descent_never_reads_the_future() -> None:
    bars, parent, fine = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5)
    levels = _levels(fine, {0, 5, 8, 13, 16, 21})
    as_of = bars[20].close_ms
    full = generate_bounded_candidates(
        bars, parent, as_of, child_degree_id="EW2", child_levels=levels
    )
    cut = generate_bounded_candidates(
        bars[:21], parent[:21], as_of, child_degree_id="EW2",
        child_levels=[ChildLevel(lv.degree_id, list(lv.evidence)[:21]) for lv in levels],
    )  # fmt: skip
    assert full == cut
