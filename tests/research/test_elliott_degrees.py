"""4h degree 사다리 하위파 읽기 (33 §16) — 합성 자료, 인과성·해상도 한계."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.research.elliott.candidates import (
    CandidateRecord,
    CandidateSnapshot,
    generate_bounded_candidates,
)
from auto_trading.research.elliott.degrees import (
    DEGREE_LADDER,
    ChildReadingReason,
    check_aligned,
    index_child_path,
    read_leg,
    read_leg_on_path,
)
from auto_trading.research.elliott.grammar import (
    GrammarStatus,
    LegSubdivision,
    ProofSource,
    WavePattern,
    validate_scenario,
)
from auto_trading.research.elliott.h4_evidence import (
    EvidenceStatus,
    EvidenceUnknownReason,
    FourHourEvidence,
    FourHourPivotEvidence,
)
from auto_trading.research.elliott.minute_path import (
    MinutePathPivot,
    MinutePathReplay,
    replay_minute_groups,
    replay_minute_groups_ladder,
)
from auto_trading.research.elliott.scenario_contract import CheckStatus, from_candidate_snapshot
from tests.research.test_elliott_grammar import synthetic_scenario
from tests.research.test_elliott_minute_path import group, outer, priced_path

H4 = Timeframe.H4.ms
M1 = Timeframe.M1.ms

# 부모 impulse 100→150→125→225→190→250, 각 다리의 자식 구조 5-3-5-3-5
LEG1 = ("100", "120", "110", "140", "130", "150")
LEG2 = ("150", "135", "145", "125")
LEG3 = ("125", "155", "140", "195", "180", "225")
LEG4 = ("225", "200", "215", "190")
LEG5 = ("190", "210", "200", "235", "225", "250")


def _join(*legs: tuple[str, ...]) -> tuple[tuple[str, ...], list[int]]:
    prices: list[str] = [legs[0][0]]
    parent = [0]
    for leg in legs:
        assert leg[0] == prices[-1]
        prices.extend(leg[1:])
        parent.append(len(prices) - 1)
    return tuple(prices), parent


def ladder_fixture(
    *legs: tuple[str, ...], direction: int = 1, parent_lag: int = 0
) -> tuple[list[Candle], list[FourHourEvidence], list[FourHourEvidence]]:
    """자식(D0) 꼭짓점마다 4h 두 봉, 부모(D1) 는 그 부분집합. 부모 확정은 ``parent_lag`` 봉 늦다."""
    prices, parent_index = _join(*legs)
    bars, child = synthetic_scenario(prices, WavePattern.IMPULSE, direction=direction)
    extra = [
        Candle(
            open_ms=bars[-1].open_ms + (k + 1) * (bars[1].open_ms - bars[0].open_ms),
            close_ms=bars[-1].close_ms + (k + 1) * (bars[1].open_ms - bars[0].open_ms),
            open=bars[-1].close,
            high=bars[-1].close + Decimal("0.2"),
            low=bars[-1].close - Decimal("0.2"),
            close=bars[-1].close,
            volume=Decimal(1),
        )
        for k in range(parent_lag)
    ]
    bars = bars + extra
    child_ev = list(child.evidence) + [
        FourHourEvidence(bar.close_ms, EvidenceStatus.NO_EVENT) for bar in extra
    ]
    parent_ev = [FourHourEvidence(bar.close_ms, EvidenceStatus.NO_EVENT) for bar in bars]
    for i in parent_index:
        confirm = 2 * i + 1 + parent_lag
        source = child_ev[2 * i + 1].candidate
        assert source is not None
        parent_ev[confirm] = FourHourEvidence(
            bars[confirm].close_ms,
            EvidenceStatus.CANDIDATE,
            candidate=replace(source, confirmed_four_hour_close_ms=bars[confirm].close_ms),
        )
    return bars, parent_ev, child_ev


def _anchors(evidence: list[FourHourEvidence]) -> list[FourHourPivotEvidence]:
    return [item.candidate for item in evidence if item.candidate is not None]


def _impulse(snapshot: CandidateSnapshot) -> CandidateRecord:
    return next(r for r in snapshot.records if r.scenario.pattern is WavePattern.IMPULSE)


def test_ladder_is_fixed_octave_starting_at_ew0() -> None:
    assert [d.threshold_pct for d in DEGREE_LADDER] == [
        Decimal("0.02"),
        Decimal("0.04"),
        Decimal("0.08"),
        Decimal("0.16"),
    ]


@pytest.mark.parametrize("direction", [1, -1])
def test_five_three_five_three_five_children_verify_parent_impulse(direction: int) -> None:
    bars, parent, child = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5, direction=direction)
    snapshot = generate_bounded_candidates(
        bars, parent, bars[-1].close_ms, degree_id="D1", child_evidence=child, child_degree_id="D0"
    )
    impulse = _impulse(snapshot)
    shapes = [p.shape for p in impulse.scenario.subdivisions]
    assert shapes == [
        LegSubdivision.IMPULSE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.IMPULSE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.IMPULSE_FIVE,
    ]
    assert [p.child_leg_count for p in impulse.scenario.subdivisions] == [5, 3, 5, 3, 5]
    assert all(p.source is ProofSource.H4_DEGREE_LADDER for p in impulse.scenario.subdivisions)
    assert impulse.assessment.status is GrammarStatus.PASS
    assert impulse.scenario.scenario_id.startswith("D1:IMPULSE:")

    s = from_candidate_snapshot(snapshot, venue="v", instrument="i")
    sc = next(x for x in s.scenarios if x.pattern == "IMPULSE")
    assert sc.degree_id == "H4:D1"
    assert sc.uncertainty.subdivision_status == "VERIFIED"
    legs = [c for c in sc.hard_checks if c.rule_id.startswith("LEG_")]
    assert len(legs) == 5 and all(c.status is CheckStatus.PASS for c in legs)
    counts = [e.observed for e in sc.evidence if e.family == "SUBDIVISION"]
    assert counts == [5.0, 3.0, 5.0, 3.0, 5.0]
    assert sc.rule_support.probability is None


def test_parent_confirming_later_than_children_is_still_causal() -> None:
    bars, parent, child = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5, parent_lag=1)
    snapshot = generate_bounded_candidates(
        bars, parent, bars[-1].close_ms, degree_id="D1", child_evidence=child, child_degree_id="D0"
    )
    impulse = _impulse(snapshot)
    assert impulse.assessment.status is GrammarStatus.PASS
    for proof, end in zip(impulse.scenario.subdivisions, _anchors(parent)[1:], strict=True):
        assert proof.available_at_ms == end.confirmed_four_hour_close_ms


def test_same_degree_child_is_single_leg_and_stays_unverified() -> None:
    bars, parent, child = ladder_fixture(("100", "110", "105", "130", "120", "140"))
    parent = child  # 같은 임계: 내부 파동이 보이지 않는다
    snapshot = generate_bounded_candidates(
        bars, parent, bars[-1].close_ms, child_evidence=child, child_degree_id="D0"
    )
    impulse = _impulse(snapshot)
    assert {p.reason for p in impulse.scenario.subdivisions} == {"CHILD_SINGLE_LEG"}
    assert impulse.assessment.status is GrammarStatus.PENDING
    assert "LEG_1_SUBDIVISION_UNVERIFIED" in impulse.assessment.pending


def test_visible_shape_conflict_is_pending_not_fail() -> None:
    # 1파 자리에 보이는 3다리 (motive 필요) → 해상도 한계로 CONFLICT 보류, FAIL 아님
    leg1_three = ("100", "130", "115", "150")
    bars, parent, child = ladder_fixture(leg1_three, LEG2, LEG3, LEG4, LEG5)
    snapshot = generate_bounded_candidates(
        bars, parent, bars[-1].close_ms, degree_id="D1", child_evidence=child, child_degree_id="D0"
    )
    impulse = _impulse(snapshot)
    assert impulse.assessment.status is GrammarStatus.PENDING
    assert "LEG_1_SUBDIVISION_CONFLICT" in impulse.assessment.pending
    assert not impulse.assessment.failures
    sc = next(
        x
        for x in from_candidate_snapshot(snapshot, venue="v", instrument="i").scenarios
        if x.pattern == "IMPULSE"
    )
    assert sc.uncertainty.subdivision_status == "PARTIAL"


def test_child_five_classification() -> None:
    diagonal = ("100", "140", "115", "150", "135", "155")  # 수렴 대각선, 4파가 1파와 겹침
    not_motive = ("100", "120", "110", "125", "115", "150")  # 3파 최단
    for leg, expected, reason in (
        (diagonal, LegSubdivision.MOTIVE_FIVE, None),
        (not_motive, LegSubdivision.UNKNOWN, ChildReadingReason.CHILD_FIVE_NOT_MOTIVE),
    ):
        bars, parent, child = ladder_fixture(leg)
        a, b = _anchors(parent)
        result = read_leg(bars, child, a, b, bars[-1].close_ms)
        assert (result.shape, result.child_leg_count, result.reason) == (expected, 5, reason)


def test_diagonal_child_conflicts_with_wave_three_impulse_requirement() -> None:
    leg3_diagonal = ("125", "185", "150", "205", "180", "225")  # 수렴·4파 겹침
    bars, parent, child = ladder_fixture(LEG1, LEG2, leg3_diagonal, LEG4, LEG5)
    snapshot = generate_bounded_candidates(
        bars, parent, bars[-1].close_ms, degree_id="D1", child_evidence=child, child_degree_id="D0"
    )
    impulse = _impulse(snapshot)
    assert impulse.scenario.subdivisions[2].shape is LegSubdivision.MOTIVE_FIVE
    assert "LEG_3_SUBDIVISION_CONFLICT" in impulse.assessment.pending


def test_seven_plus_children_are_not_forced_into_a_shape() -> None:
    leg = ("100", "120", "110", "130", "120", "140", "130", "150")
    bars, parent, child = ladder_fixture(leg)
    a, b = _anchors(parent)
    result = read_leg(bars, child, a, b, bars[-1].close_ms)
    assert (result.shape, result.child_leg_count) == (LegSubdivision.UNKNOWN, 7)
    assert result.reason is ChildReadingReason.CHILD_LEGS_7_PLUS


def test_alignment_and_continuity_failures_are_unknown() -> None:
    bars, parent, child = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    as_of = bars[-1].close_ms
    # 자식 쪽에 시작 꼭짓점이 없다
    no_start = list(child)
    no_start[1] = FourHourEvidence(no_start[1].close_ms, EvidenceStatus.NO_EVENT)
    assert read_leg(bars, no_start, a, b, as_of).reason is ChildReadingReason.START_NOT_ALIGNED
    # 자식 쪽 끝 꼭짓점 가격이 다르다
    moved = list(child)
    last = moved[-1].candidate
    assert last is not None
    moved[-1] = replace(moved[-1], candidate=replace(last, price=last.price + 1))
    assert read_leg(bars, moved, a, b, as_of).reason is ChildReadingReason.END_NOT_ALIGNED
    # 다리 안에 자식 UNKNOWN
    gap = list(child)
    gap[4] = FourHourEvidence(
        gap[4].close_ms, EvidenceStatus.UNKNOWN, reason=EvidenceUnknownReason.MULTIPLE_EVENTS
    )
    result = read_leg(bars, gap, a, b, as_of)
    assert result.reason is ChildReadingReason.CHILD_UNKNOWN_IN_LEG
    assert result.child_leg_count is None


@pytest.mark.leakage
def test_reading_never_uses_bars_after_as_of_and_future_append_preserves_past() -> None:
    bars, parent, child = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5)
    a, b = _anchors(parent)[:2]
    # 자식 끝 꼭짓점 확정 전 시점에서는 끝을 모른다 → 부모도 아직 없으므로 호출 자체를 거부
    with pytest.raises(DataIntegrityError):
        read_leg(bars, child, a, b, b.confirmed_four_hour_close_ms - 1)
    at_confirm = read_leg(bars, child, a, b, b.confirmed_four_hour_close_ms)
    later = read_leg(bars, child, a, b, bars[-1].close_ms)
    assert at_confirm == later
    past = generate_bounded_candidates(
        bars[:12], parent[:12], bars[11].close_ms, child_evidence=child[:12], child_degree_id="D0"
    )
    full_then_cut = generate_bounded_candidates(
        bars, parent, bars[11].close_ms, child_evidence=child, child_degree_id="D0"
    )
    assert past == full_then_cut


def test_child_evidence_must_be_aligned_and_paired_with_id() -> None:
    bars, parent, child = ladder_fixture(LEG1, LEG2)
    with pytest.raises(DataIntegrityError):
        check_aligned(bars, child[:-1])
    with pytest.raises(DataIntegrityError):
        generate_bounded_candidates(bars, parent, bars[-1].close_ms, child_evidence=child)


def test_existing_d0_output_is_unchanged_without_child_evidence() -> None:
    bars, parent, _ = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5)
    snapshot = generate_bounded_candidates(bars, parent, bars[-1].close_ms)
    assert all(r.scenario.subdivisions == () for r in snapshot.records)
    assert all(r.scenario.scenario_id.startswith("EW0:") for r in snapshot.records)
    assert snapshot.degree_id == "EW0" and snapshot.child_degree_id is None


def test_ladder_replay_keeps_ew0_bit_identical_and_shares_blocking() -> None:
    first, first_minutes = priced_path()
    second_minutes = group(240, ["97"] * 10 + ["101"] * 230)
    second = outer(second_minutes)
    four = [first, second]
    single = replay_minute_groups(four, iter((first_minutes, second_minutes)), second.close_ms)
    ladder = replay_minute_groups_ladder(
        four,
        iter((first_minutes, second_minutes)),
        second.close_ms,
        tuple(d.threshold_pct for d in DEGREE_LADDER),
    )
    assert ladder[0] == single
    assert all(r.blocked_four_hours == single.blocked_four_hours for r in ladder)
    n = [sum(len(s) for s in r.segments) for r in ladder]
    assert n == sorted(n, reverse=True)  # 임계가 클수록 반전 사건은 늘지 않는다 (이 경로에서)
    with pytest.raises(DataIntegrityError):
        replay_minute_groups_ladder(
            four, iter(()), second.close_ms, (Decimal("0.04"), Decimal("0.02"))
        )


def test_unknown_shape_needs_ladder_source_and_reason() -> None:
    bars, parent, child = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5)
    snapshot = generate_bounded_candidates(
        bars, parent, bars[-1].close_ms, degree_id="D1", child_evidence=child, child_degree_id="D0"
    )
    scenario = _impulse(snapshot).scenario
    first = scenario.subdivisions[0]
    unknown = replace(first, shape=LegSubdivision.UNKNOWN, reason="CHILD_SINGLE_LEG")
    as_of = bars[-1].close_ms
    for invalid in (
        replace(unknown, reason=None),
        replace(unknown, source=ProofSource.SYNTHETIC_FIXTURE),
    ):
        with pytest.raises(DataIntegrityError):
            validate_scenario(
                bars, replace(scenario, subdivisions=(invalid, *scenario.subdivisions[1:])), as_of
            )
    ok = validate_scenario(
        bars, replace(scenario, subdivisions=(unknown, *scenario.subdivisions[1:])), as_of
    )
    assert ok.status is GrammarStatus.PENDING
    assert "LEG_1_SUBDIVISION_UNVERIFIED" in ok.pending


# ---------------------------------------------------------------- EW-1 (33 §18)


def path_from(
    child: list[FourHourEvidence], *, same_bar: frozenset[int] = frozenset()
) -> list[MinutePathPivot]:
    """자식 4h 후보를 1m 경로 사건 한 구간으로 옮긴다.

    ``same_bar`` 꼭짓점은 극값 봉 안에서 확인된 것으로 둔다.
    """
    events: list[MinutePathPivot] = []
    for n, item in enumerate(i for i in child if i.candidate is not None):
        c = item.candidate
        assert c is not None
        confirm_bar_open = c.extreme_four_hour_open_ms if n in same_bar else item.close_ms + 1 - H4
        events.append(
            MinutePathPivot(
                kind=c.kind,
                price=c.price,
                extreme_minute_ms=c.extreme_four_hour_open_ms,
                confirmation_minute_close_ms=confirm_bar_open + 2 * M1 - 1,
                available_at_ms=confirm_bar_open + H4 - 1,
            )
        )
    return events


def _replay(*segments: list[MinutePathPivot]) -> MinutePathReplay:
    return MinutePathReplay(tuple(tuple(s) for s in segments), (), (), 0)


def _unknown(item: FourHourEvidence) -> FourHourEvidence:
    return FourHourEvidence(
        item.close_ms, EvidenceStatus.UNKNOWN, reason=EvidenceUnknownReason.SAME_FOUR_HOUR
    )


def test_ew1_matches_ew0_when_every_child_vertex_is_a_candidate() -> None:
    bars, parent, child = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5)
    path = index_child_path(_replay(path_from(child)))
    ew0 = _impulse(
        generate_bounded_candidates(
            bars, parent, bars[-1].close_ms, child_evidence=child, child_degree_id="D0"
        )
    )
    ew1 = _impulse(
        generate_bounded_candidates(
            bars, parent, bars[-1].close_ms, child_evidence=child, child_degree_id="D0",
            child_path=path,
        )
    )  # fmt: skip
    assert [p.shape for p in ew1.scenario.subdivisions] == [
        p.shape for p in ew0.scenario.subdivisions
    ]
    assert ew1.assessment.status is GrammarStatus.PASS
    assert ew1.scenario.subdivisions[0].source_id.startswith("D0/EW1:")


def test_ew1_finds_vertex_that_four_hour_mapping_left_unknown() -> None:
    bars, parent, child = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    child = list(child)
    child[1] = _unknown(child[1])  # 시작 꼭짓점: 극값과 2% 확인이 같은 4h
    events = path_from(ladder_fixture(LEG1)[2], same_bar=frozenset({0}))
    path = index_child_path(_replay(events))
    as_of = bars[-1].close_ms
    assert read_leg(bars, child, a, b, as_of).reason is ChildReadingReason.START_NOT_ALIGNED
    ew1 = read_leg_on_path(bars, child, path, a, b, as_of)
    assert (ew1.shape, ew1.child_leg_count) == (LegSubdivision.IMPULSE_FIVE, 5)
    assert ew1.child_anchors[0].confirmed_four_hour_close_ms == bars[0].close_ms


def test_ew1_interior_stays_strict() -> None:
    bars, parent, child = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    path = index_child_path(_replay(path_from(child)))
    child = list(child)
    child[3] = _unknown(child[3])  # 내부 자식 꼭짓점이 4h 후보가 아니다
    result = read_leg_on_path(bars, child, path, a, b, bars[-1].close_ms)
    assert result.reason is ChildReadingReason.CHILD_INTERIOR_UNRESOLVED


def test_ew1_rejects_sub_four_hour_reversal_inside_vertex_bar() -> None:
    bars, parent, child = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    events = path_from(child)
    wiggle = [
        replace(events[1], extreme_minute_ms=10 * M1, price=Decimal("103")),
        replace(events[0], extreme_minute_ms=20 * M1, price=Decimal("101")),
    ]
    path = index_child_path(_replay([events[0], *wiggle, *events[1:]]))
    result = read_leg_on_path(bars, child, path, a, b, bars[-1].close_ms)
    assert result.reason is ChildReadingReason.CHILD_SUB_4H_AT_VERTEX


def test_ew1_needs_one_unbroken_path_segment_and_both_vertices() -> None:
    bars, parent, child = ladder_fixture(LEG1)
    a, b = _anchors(parent)
    events = path_from(child)
    as_of = bars[-1].close_ms
    broken = index_child_path(_replay(events[:3], events[3:]))
    assert (
        read_leg_on_path(bars, child, broken, a, b, as_of).reason
        is ChildReadingReason.PATH_BROKEN_IN_LEG
    )
    no_start = index_child_path(_replay(events[1:]))
    assert (
        read_leg_on_path(bars, child, no_start, a, b, as_of).reason
        is ChildReadingReason.START_NOT_ON_PATH
    )


@pytest.mark.leakage
def test_ew1_ignores_path_events_not_yet_available() -> None:
    bars, parent, child = ladder_fixture(LEG1, LEG2, LEG3, LEG4, LEG5, parent_lag=0)
    events = path_from(child)
    a, b = _anchors(parent)[:2]
    late_end = replace(events[5], available_at_ms=events[5].available_at_ms + 2 * H4)
    path = index_child_path(_replay([*events[:5], late_end, *events[6:]]))
    at_parent = read_leg_on_path(bars, child, path, a, b, b.confirmed_four_hour_close_ms)
    assert at_parent.reason is ChildReadingReason.END_NOT_ON_PATH
    as_of = bars[20].close_ms
    full = generate_bounded_candidates(
        bars, parent, as_of, child_evidence=child, child_degree_id="D0",
        child_path=index_child_path(_replay(events)),
    )  # fmt: skip
    visible = [e for e in events if e.available_at_ms <= as_of]
    cut = generate_bounded_candidates(
        bars[:21], parent[:21], as_of, child_evidence=child[:21], child_degree_id="D0",
        child_path=index_child_path(_replay(visible)),
    )  # fmt: skip
    assert full == cut
