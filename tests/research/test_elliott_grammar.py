"""4h 이상 Elliott 엄격 문법의 합성 반례와 미래 무관성."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.candidates import (
    HypothesisStatus,
    generate_bounded_candidates,
)
from auto_trading.research.elliott.grammar import (
    GrammarScenario,
    GrammarStatus,
    LegSubdivision,
    LegSubdivisionProof,
    ParentRole,
    ProofSource,
    WavePattern,
    validate_scenario,
    validate_scenarios,
)
from auto_trading.research.elliott.h4_evidence import (
    EvidenceStatus,
    FourHourEvidence,
    FourHourPivotEvidence,
)

D = Decimal
H4 = Timeframe.H4.ms
IMPULSE_PRICES = ("100", "110", "105", "130", "120", "140")
IMPULSE_SUBDIVISIONS = (
    LegSubdivision.MOTIVE_FIVE,
    LegSubdivision.CORRECTIVE_THREE,
    LegSubdivision.IMPULSE_FIVE,
    LegSubdivision.CORRECTIVE_THREE,
    LegSubdivision.MOTIVE_FIVE,
)


def _bar(index: int, price: Decimal, kind: SwingKind | None) -> Candle:
    if kind is SwingKind.HIGH:
        low, high, close = price - 1, price, price - D("0.5")
    elif kind is SwingKind.LOW:
        low, high, close = price, price + 1, price + D("0.5")
    else:
        low, high, close = price - D("0.5"), price + D("0.5"), price
    return Candle(
        open_ms=index * H4,
        close_ms=(index + 1) * H4 - 1,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=D(1),
    )


def synthetic_scenario(
    prices: tuple[str, ...],
    pattern: WavePattern,
    subdivisions: tuple[LegSubdivision, ...] = (),
    *,
    direction: int = 1,
    parent_role: ParentRole = ParentRole.UNKNOWN,
) -> tuple[list[Candle], GrammarScenario]:
    values = tuple(D(price) if direction > 0 else D(300) - D(price) for price in prices)
    bars: list[Candle] = []
    evidence: list[FourHourEvidence] = []
    for index in range(2 * len(values)):
        if index % 2 == 0:
            anchor_index = index // 2
            kind = SwingKind.LOW if (anchor_index % 2 == 0) == (direction > 0) else SwingKind.HIGH
            bars.append(_bar(index, values[anchor_index], kind))
            evidence.append(FourHourEvidence(bars[-1].close_ms, EvidenceStatus.NO_EVENT))
        else:
            anchor_index = index // 2
            price = (
                (values[anchor_index] + values[anchor_index + 1]) / 2
                if anchor_index + 1 < len(values)
                else values[anchor_index]
            )
            bars.append(_bar(index, price, None))
            kind = SwingKind.LOW if (anchor_index % 2 == 0) == (direction > 0) else SwingKind.HIGH
            anchor = FourHourPivotEvidence(
                kind=kind,
                price=values[anchor_index],
                extreme_four_hour_open_ms=(index - 1) * H4,
                confirmed_four_hour_close_ms=bars[-1].close_ms,
            )
            evidence.append(
                FourHourEvidence(bars[-1].close_ms, EvidenceStatus.CANDIDATE, candidate=anchor)
            )
    return bars, GrammarScenario(
        scenario_id=f"synthetic-{pattern.value}",
        pattern=pattern,
        direction=direction,
        evidence=tuple(evidence),
        subdivisions=tuple(
            LegSubdivisionProof(
                shape=shape,
                start_four_hour_open_ms=2 * index * H4,
                end_four_hour_open_ms=2 * (index + 1) * H4,
                available_at_ms=bars[-1].close_ms,
                timeframe=Timeframe.H4,
                source=ProofSource.SYNTHETIC_FIXTURE,
                source_id=f"synthetic-leg-{index + 1}",
            )
            for index, shape in enumerate(subdivisions)
        ),
        parent_role=parent_role,
    )


@pytest.mark.parametrize("direction", [1, -1])
def test_strict_impulse_geometry_and_declared_subdivisions_are_symmetric(direction: int) -> None:
    bars, scenario = synthetic_scenario(
        IMPULSE_PRICES, WavePattern.IMPULSE, IMPULSE_SUBDIVISIONS, direction=direction
    )
    result = validate_scenario(bars, scenario, bars[-1].close_ms)
    assert result.status is GrammarStatus.PASS
    assert result.geometry_status is GrammarStatus.PASS
    assert result.failures == ()


def test_impulse_without_lower_degree_verification_remains_pending() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE)
    result = validate_scenario(bars, scenario, bars[-1].close_ms)
    assert result.geometry_status is GrammarStatus.PASS
    assert result.status is GrammarStatus.PENDING
    assert "LEG_1_SUBDIVISION_UNVERIFIED" in result.pending


def test_impulse_third_leg_requires_impulse_not_generic_motive() -> None:
    shapes = (
        LegSubdivision.MOTIVE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.MOTIVE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.MOTIVE_FIVE,
    )
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE, shapes)
    result = validate_scenario(bars, scenario, bars[-1].close_ms)
    assert result.status is GrammarStatus.FAIL
    assert "LEG_3_SUBDIVISION_MISMATCH" in result.failures


def test_impulse_wave_two_boundary_and_wave_three_shortest_fail() -> None:
    boundary_bars, boundary = synthetic_scenario(
        ("100", "110", "100", "130", "120", "140"),
        WavePattern.IMPULSE,
        IMPULSE_SUBDIVISIONS,
    )
    boundary_result = validate_scenario(boundary_bars, boundary, boundary_bars[-1].close_ms)
    assert "IMPULSE_W2_RETRACE" in boundary_result.failures

    short_bars, short = synthetic_scenario(
        ("100", "110", "105", "113", "111", "140"),
        WavePattern.IMPULSE,
        IMPULSE_SUBDIVISIONS,
    )
    short_result = validate_scenario(short_bars, short, short_bars[-1].close_ms)
    assert "IMPULSE_W3_SHORTEST" in short_result.failures


def test_wave_four_intermediate_wick_overlap_fails_but_start_bar_is_pending() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE, IMPULSE_SUBDIVISIONS)
    breached = list(bars)
    breached[7] = replace(bars[7], low=D(109))
    result = validate_scenario(breached, scenario, bars[-1].close_ms)
    assert "IMPULSE_W4_WICK_OVERLAP" in result.failures

    boundary = list(bars)
    boundary[6] = replace(bars[6], low=D(109))
    boundary_result = validate_scenario(boundary, scenario, bars[-1].close_ms)
    assert boundary_result.status is GrammarStatus.PENDING
    assert "IMPULSE_W4_START_BAR_ORDER_UNKNOWN" in boundary_result.pending


@pytest.mark.parametrize("direction", [1, -1])
def test_normal_zigzag_and_b_boundary(direction: int) -> None:
    shapes = (
        LegSubdivision.MOTIVE_FIVE,
        LegSubdivision.CORRECTIVE_THREE,
        LegSubdivision.MOTIVE_FIVE,
    )
    bars, scenario = synthetic_scenario(
        ("100", "120", "110", "135"), WavePattern.ZIGZAG, shapes, direction=direction
    )
    assert validate_scenario(bars, scenario, bars[-1].close_ms).status is GrammarStatus.PASS

    invalid_bars, invalid = synthetic_scenario(
        ("100", "120", "100", "135"), WavePattern.ZIGZAG, shapes, direction=direction
    )
    result = validate_scenario(invalid_bars, invalid, invalid_bars[-1].close_ms)
    assert "ZIGZAG_B_STRICT_RETRACE" in result.failures


def test_ending_diagonal_needs_parent_and_contracting_lines() -> None:
    shapes = (LegSubdivision.CORRECTIVE_THREE,) * 5
    bars, scenario = synthetic_scenario(
        ("100", "120", "110", "126", "118", "130"),
        WavePattern.ENDING_DIAGONAL,
        shapes,
        parent_role=ParentRole.WAVE5,
    )
    result = validate_scenario(bars, scenario, bars[-1].close_ms)
    assert result.status is GrammarStatus.PASS
    assert (
        validate_scenario(
            bars, replace(scenario, parent_role=ParentRole.UNKNOWN), bars[-1].close_ms
        ).status
        is GrammarStatus.PENDING
    )

    overlap_impulse = replace(scenario, scenario_id="strict-impulse", pattern=WavePattern.IMPULSE)
    outcomes = validate_scenarios(bars, [scenario, overlap_impulse], bars[-1].close_ms)
    assert [item.status for item in outcomes] == [GrammarStatus.PASS, GrammarStatus.FAIL]
    assert "IMPULSE_W4_ENDPOINT_NONOVERLAP" in outcomes[1].failures


def test_ending_diagonal_leg_lengths_alone_do_not_prove_trendline_contraction() -> None:
    positions = (0, 2, 4, 6, 10, 12)
    prices = (D(100), D(120), D(110), D(126), D(118), D(130))
    bars = [_bar(index, D(115), None) for index in range(14)]
    evidence = [FourHourEvidence(bar.close_ms, EvidenceStatus.NO_EVENT) for bar in bars]
    for index, (position, price) in enumerate(zip(positions, prices, strict=True)):
        kind = SwingKind.LOW if index % 2 == 0 else SwingKind.HIGH
        bars[position] = _bar(position, price, kind)
        evidence[position + 1] = FourHourEvidence(
            bars[position + 1].close_ms,
            EvidenceStatus.CANDIDATE,
            candidate=FourHourPivotEvidence(
                kind=kind,
                price=price,
                extreme_four_hour_open_ms=bars[position].open_ms,
                confirmed_four_hour_close_ms=bars[position + 1].close_ms,
            ),
        )
    scenario = GrammarScenario(
        scenario_id="non-converging-lines",
        pattern=WavePattern.ENDING_DIAGONAL,
        direction=1,
        evidence=tuple(evidence),
        parent_role=ParentRole.WAVE5,
    )
    result = validate_scenario(bars, scenario, bars[-1].close_ms)
    assert result.status is GrammarStatus.FAIL
    assert "ENDING_TRENDLINE_NOT_CONTRACTING" in result.failures


def test_extra_intervening_candidate_cannot_be_skipped_to_fit_impulse() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE, IMPULSE_SUBDIVISIONS)
    evidence = list(scenario.evidence)
    evidence[2] = FourHourEvidence(
        close_ms=bars[2].close_ms,
        status=EvidenceStatus.CANDIDATE,
        candidate=FourHourPivotEvidence(
            kind=SwingKind.LOW,
            price=D(100),
            extreme_four_hour_open_ms=bars[0].open_ms,
            confirmed_four_hour_close_ms=bars[2].close_ms,
        ),
    )
    result = validate_scenario(
        bars, replace(scenario, evidence=tuple(evidence), subdivisions=()), bars[-1].close_ms
    )
    assert result.status is GrammarStatus.FAIL
    assert "EXTRA_ANCHORS_IN_WINDOW" in result.failures


def test_unsupported_pattern_is_not_silently_treated_as_ending_diagonal() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE)
    with pytest.raises(DataIntegrityError):
        validate_scenario(
            bars,
            replace(scenario, pattern=cast(WavePattern, "FLAT")),
            bars[-1].close_ms,
        )


def test_subdivision_proof_requires_causal_h4_provenance() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE, IMPULSE_SUBDIVISIONS)
    proof = scenario.subdivisions[0]
    for invalid in (
        replace(proof, available_at_ms=bars[-1].close_ms + H4),
        replace(proof, available_at_ms=bars[1].close_ms),
        replace(proof, end_four_hour_open_ms=proof.end_four_hour_open_ms + H4),
        replace(proof, timeframe=Timeframe.H1),
        replace(proof, source_id=""),
    ):
        with pytest.raises(DataIntegrityError):
            validate_scenario(
                bars,
                replace(scenario, subdivisions=(invalid, *scenario.subdivisions[1:])),
                bars[-1].close_ms,
            )


def test_generator_limits_to_three_unverified_h4_scenarios() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE)
    snapshot = generate_bounded_candidates(bars, scenario.evidence, bars[-1].close_ms)
    assert len(snapshot.records) == 3
    assert snapshot.usable_anchor_count == 6
    assert snapshot.confidence == "UNCALIBRATED_RULE_SUPPORT_ONLY"
    assert snapshot.probability is None
    assert all(record.scenario.subdivisions == () for record in snapshot.records)
    assert all(record.assessment.status is not GrammarStatus.PASS for record in snapshot.records)
    assert all(
        record.invalidation is None or record.invalidation.purpose == "ANALYSIS_ONLY"
        for record in snapshot.records
    )


@pytest.mark.leakage
def test_generator_unknown_bar_resets_segment_and_future_append_preserves_prefix() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE)
    prefix = generate_bounded_candidates(bars[:10], scenario.evidence[:10], bars[9].close_ms)
    assert prefix == generate_bounded_candidates(bars, scenario.evidence, bars[9].close_ms)

    interrupted = list(scenario.evidence)
    interrupted[7] = FourHourEvidence(bars[7].close_ms, EvidenceStatus.UNKNOWN)
    snapshot = generate_bounded_candidates(bars, interrupted, bars[-1].close_ms)
    assert snapshot.last_unknown_close_ms == bars[7].close_ms
    assert snapshot.usable_anchor_count == 2
    assert all(record.hypothesis_status is HypothesisStatus.FORMING for record in snapshot.records)
    assert all(len(record.scenario.evidence) <= 3 for record in snapshot.records)

    interrupted[8] = FourHourEvidence(bars[8].close_ms, EvidenceStatus.UNKNOWN)
    with pytest.raises(DataIntegrityError):
        generate_bounded_candidates(bars, interrupted, bars[-1].close_ms)


def test_generator_records_endpoint_reextension_without_trading_stop() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE)
    extension = _bar(len(bars), D(145), None)
    evidence = (*scenario.evidence, FourHourEvidence(extension.close_ms, EvidenceStatus.NO_EVENT))
    snapshot = generate_bounded_candidates([*bars, extension], evidence, extension.close_ms)
    impulse = snapshot.records[0]
    assert impulse.assessment.geometry_status is GrammarStatus.PASS
    assert impulse.assessment.status is GrammarStatus.PENDING
    assert impulse.hypothesis_status is HypothesisStatus.ENDING_INVALIDATED
    assert impulse.invalidation is not None
    assert impulse.invalidation.endpoint_price == D(140)
    assert impulse.invalidation.invalidated_at_ms == extension.close_ms


def test_generator_rejects_missing_evidence_bar() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE)
    with pytest.raises(DataIntegrityError):
        generate_bounded_candidates(bars, scenario.evidence[1:], bars[-1].close_ms)


@pytest.mark.leakage
def test_unknown_bar_and_future_append_do_not_rewrite_prior_snapshot() -> None:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE, IMPULSE_SUBDIVISIONS)
    earlier = replace(scenario, evidence=scenario.evidence[:10], subdivisions=())
    before = validate_scenario(bars[:10], earlier, bars[9].close_ms)
    appended = validate_scenario(bars, earlier, bars[9].close_ms)
    assert before == appended
    assert before.status is GrammarStatus.PENDING

    unknown = list(scenario.evidence)
    unknown[7] = FourHourEvidence(bars[7].close_ms, EvidenceStatus.UNKNOWN)
    result = validate_scenario(bars, replace(scenario, evidence=tuple(unknown)), bars[-1].close_ms)
    assert result.status is GrammarStatus.UNKNOWN
    assert result.failures == ()
