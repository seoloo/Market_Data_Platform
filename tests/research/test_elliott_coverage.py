"""4h 후보 창 coverage는 가용성만 세고 UNKNOWN 경계를 보존한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.coverage import classify_cross_unknown, summarize_window_coverage
from auto_trading.research.elliott.h4_evidence import (
    EvidenceStatus,
    EvidenceUnknownReason,
    FourHourEvidence,
    FourHourPivotEvidence,
)

START_MS = 1_577_836_800_000
H4_MS = Timeframe.H4.ms


def _bar(index: int) -> Candle:
    open_ms = START_MS + index * H4_MS
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + H4_MS - 1,
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
    )


def _candidate(bars: list[Candle], index: int, extreme_index: int) -> FourHourEvidence:
    kind = SwingKind.HIGH if index % 2 else SwingKind.LOW
    extreme = bars[extreme_index]
    return FourHourEvidence(
        close_ms=bars[index].close_ms,
        status=EvidenceStatus.CANDIDATE,
        candidate=FourHourPivotEvidence(
            kind=kind,
            price=extreme.high if kind is SwingKind.HIGH else extreme.low,
            extreme_four_hour_open_ms=extreme.open_ms,
            confirmed_four_hour_close_ms=bars[index].close_ms,
        ),
    )


def _sample() -> tuple[list[Candle], list[FourHourEvidence]]:
    bars = [_bar(index) for index in range(11)]
    evidence = [FourHourEvidence(bar.close_ms, EvidenceStatus.NO_EVENT) for bar in bars]
    for index in (*range(1, 7), 9, 10):
        evidence[index] = _candidate(bars, index, index - 1)
    evidence[7] = FourHourEvidence(bars[7].close_ms, EvidenceStatus.UNKNOWN)
    return bars, evidence


def test_window_coverage_counts_bars_and_segments_separately() -> None:
    bars, evidence = _sample()
    result = summarize_window_coverage(bars, evidence)
    assert result.four_hour_bars == 11
    assert result.unknown_bars == 1
    assert result.non_unknown_segments == 2
    assert result.segment_anchor_count_median == 4
    assert result.segment_anchor_count_max == 6
    assert (result.segments_reaching_four, result.segments_reaching_six) == (1, 1)
    assert (
        result.bars_with_two_anchors,
        result.bars_with_four_anchors,
        result.bars_with_six_anchors,
    ) == (6, 3, 1)
    assert result.cross_unknown_candidates == 0
    assert result.by_calendar_year[0].year == 2020


@pytest.mark.leakage
def test_cross_unknown_anchor_blocks_only_new_segment_and_future_does_not_rewrite_prefix() -> None:
    bars, evidence = _sample()
    earlier = summarize_window_coverage(bars[:7], evidence[:7])
    assert earlier.bars_with_six_anchors == 1
    evidence[9] = _candidate(bars, 9, 6)
    later = summarize_window_coverage(bars, evidence)
    assert later.cross_unknown_candidates == 1
    assert later.bars_blocked_by_cross_unknown == 2
    assert later.bars_with_six_anchors == earlier.bars_with_six_anchors
    assert later.bars_with_two_anchors == earlier.bars_with_two_anchors


def test_coverage_rejects_missing_or_unconfirmed_four_hour_evidence() -> None:
    bars, evidence = _sample()
    with pytest.raises(DataIntegrityError):
        summarize_window_coverage(bars, evidence[1:])
    broken = list(evidence)
    candidate = broken[1].candidate
    assert candidate is not None
    broken[1] = replace(
        broken[1], candidate=replace(candidate, confirmed_four_hour_close_ms=bars[0].close_ms)
    )
    with pytest.raises(DataIntegrityError):
        summarize_window_coverage(bars, broken)


class TestCrossUnknownClassification:
    """33 §14: 교차-UNKNOWN 후보 기술 — 집계와 같은 건수, 경계 사유·극값 위치·확인 지연."""

    @staticmethod
    def _crossing() -> tuple[list[Candle], list[FourHourEvidence]]:
        bars = [_bar(index) for index in range(10)]
        evidence = [FourHourEvidence(bar.close_ms, EvidenceStatus.NO_EVENT) for bar in bars]
        evidence[1] = _candidate(bars, 1, 0)
        evidence[4] = FourHourEvidence(
            bars[4].close_ms, EvidenceStatus.UNKNOWN, reason=EvidenceUnknownReason.MULTIPLE_EVENTS
        )
        evidence[6] = _candidate(bars, 6, 3)  # 극값이 경계 봉(4) 보다 1봉 앞 · 첫 후보
        evidence[8] = _candidate(bars, 8, 7)  # 정상 후보
        return bars, evidence

    def test_same_count_as_coverage_and_fields(self) -> None:
        bars, evidence = self._crossing()
        cases = classify_cross_unknown(bars, evidence)
        assert len(cases) == summarize_window_coverage(bars, evidence).cross_unknown_candidates == 1
        (case,) = cases
        assert case.boundary_reason is EvidenceUnknownReason.MULTIPLE_EVENTS
        assert case.extreme_offset_bars == 1
        assert case.confirm_lag_bars == 3
        assert case.first_in_segment is True

    def test_extreme_in_boundary_bar_is_offset_zero(self) -> None:
        bars, evidence = self._crossing()
        evidence[6] = _candidate(bars, 6, 4)
        (case,) = classify_cross_unknown(bars, evidence)
        assert case.extreme_offset_bars == 0

    def test_later_candidate_in_segment_is_not_first(self) -> None:
        bars, evidence = self._crossing()
        evidence[6] = _candidate(bars, 6, 5)  # 정상 첫 후보
        evidence[8] = _candidate(bars, 8, 2)  # 두 번째 후보가 경계 이전 극값
        cases = classify_cross_unknown(bars, evidence)
        assert [c.first_in_segment for c in cases] == [False]

    def test_no_unknown_means_no_cases(self) -> None:
        bars, evidence = _sample()
        evidence[7] = FourHourEvidence(bars[7].close_ms, EvidenceStatus.NO_EVENT)
        assert classify_cross_unknown(bars, evidence) == ()
