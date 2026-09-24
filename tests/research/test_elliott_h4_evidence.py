"""1m 경로 사건은 단일·완성 4h 증거 조건을 통과할 때만 후보가 된다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.h4_evidence import (
    EvidenceStatus,
    EvidenceUnknownReason,
    map_minute_path_to_four_hour_evidence,
)
from auto_trading.research.elliott.intrabar import IntrabarUnknownReason
from auto_trading.research.elliott.minute_path import (
    BlockedFourHour,
    MinutePathPivot,
    MinutePathReplay,
)

H4 = Timeframe.H4.ms
M1 = Timeframe.M1.ms


def bar(index: int) -> Candle:
    return Candle(
        open_ms=index * H4,
        close_ms=(index + 1) * H4 - 1,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("95"),
        close=Decimal("100"),
        volume=Decimal("240"),
    )


def event(
    kind: SwingKind,
    price: str,
    extreme_bar: int,
    confirmation_bar: int,
) -> MinutePathPivot:
    return MinutePathPivot(
        kind=kind,
        price=Decimal(price),
        extreme_minute_ms=extreme_bar * H4 + 10 * M1,
        confirmation_minute_close_ms=confirmation_bar * H4 + 21 * M1 - 1,
        available_at_ms=(confirmation_bar + 1) * H4 - 1,
    )


def path(
    as_of_ms: int,
    *events: MinutePathPivot,
    blocked: tuple[BlockedFourHour, ...] = (),
    ambiguous: tuple[int, ...] = (),
) -> MinutePathReplay:
    return MinutePathReplay(
        segments=(events,),
        ambiguous_minute_open_ms=ambiguous,
        blocked_four_hours=blocked,
        as_of_ms=as_of_ms,
    )


@pytest.mark.leakage
def test_only_prior_four_hour_extremum_becomes_candidate_after_confirmation_close() -> None:
    first, second = bar(0), bar(1)
    low = event(SwingKind.LOW, "95", 0, 1)
    before = map_minute_path_to_four_hour_evidence(
        [first, second], path(first.close_ms), first.close_ms
    )
    assert len(before) == 1
    assert before[0].status is EvidenceStatus.NO_EVENT

    after = map_minute_path_to_four_hour_evidence(
        [first, second], path(second.close_ms, low), second.close_ms
    )
    assert after[0] == before[0]
    assert after[1].status is EvidenceStatus.CANDIDATE
    assert after[1].candidate is not None
    assert after[1].candidate.extreme_four_hour_open_ms == first.open_ms
    assert after[1].candidate.confirmed_four_hour_close_ms == second.close_ms


def test_same_four_hour_event_is_not_an_elliott_timeframe_candidate() -> None:
    first = bar(0)
    same_bar = event(SwingKind.HIGH, "105", 0, 0)
    result = map_minute_path_to_four_hour_evidence(
        [first], path(first.close_ms, same_bar), first.close_ms
    )
    assert result[0].status is EvidenceStatus.UNKNOWN
    assert result[0].reason is EvidenceUnknownReason.SAME_FOUR_HOUR


def test_multiple_confirmations_in_one_four_hour_are_not_collapsed() -> None:
    first, second = bar(0), bar(1)
    low = event(SwingKind.LOW, "95", 0, 1)
    high = event(SwingKind.HIGH, "105", 0, 1)
    result = map_minute_path_to_four_hour_evidence(
        [first, second], path(second.close_ms, low, high), second.close_ms
    )
    assert result[1].status is EvidenceStatus.UNKNOWN
    assert result[1].reason is EvidenceUnknownReason.MULTIPLE_EVENTS


def test_local_minute_extremum_is_not_four_hour_bar_extremum() -> None:
    first, second = bar(0), bar(1)
    local_low = event(SwingKind.LOW, "96", 0, 1)
    result = map_minute_path_to_four_hour_evidence(
        [first, second], path(second.close_ms, local_low), second.close_ms
    )
    assert result[1].reason is EvidenceUnknownReason.NOT_FOUR_HOUR_EXTREMUM


def test_blocked_or_ambiguous_extreme_bar_interrupts_mapping() -> None:
    first, second = bar(0), bar(1)
    low = event(SwingKind.LOW, "95", 0, 1)
    blocked = path(
        second.close_ms,
        low,
        blocked=(BlockedFourHour(first.open_ms, IntrabarUnknownReason.ZERO_VOLUME_MINUTE),),
    )
    blocked_result = map_minute_path_to_four_hour_evidence(
        [first, second], blocked, second.close_ms
    )
    assert blocked_result[0].reason is EvidenceUnknownReason.BLOCKED_FOUR_HOUR
    assert blocked_result[1].reason is EvidenceUnknownReason.INTERRUPTED_PATH

    ambiguous = path(second.close_ms, low, ambiguous=(30 * M1,))
    ambiguous_result = map_minute_path_to_four_hour_evidence(
        [first, second], ambiguous, second.close_ms
    )
    assert ambiguous_result[0].reason is EvidenceUnknownReason.AMBIGUOUS_MINUTE
    assert ambiguous_result[1].reason is EvidenceUnknownReason.INTERRUPTED_PATH


@pytest.mark.leakage
def test_later_non_monotonic_candidate_does_not_rewrite_earlier_candidate() -> None:
    first, second, third = bar(0), bar(1), bar(2)
    low = event(SwingKind.LOW, "95", 0, 1)
    high = event(SwingKind.HIGH, "105", 0, 2)
    prefix = map_minute_path_to_four_hour_evidence(
        [first, second], path(second.close_ms, low), second.close_ms
    )
    extended = map_minute_path_to_four_hour_evidence(
        [first, second, third], path(third.close_ms, low, high), third.close_ms
    )
    assert extended[:2] == prefix
    assert extended[2].reason is EvidenceUnknownReason.NON_MONOTONIC_CANDIDATE


def test_alternating_later_four_hour_extrema_remain_separate_candidates() -> None:
    bars = [bar(index) for index in range(4)]
    low = event(SwingKind.LOW, "95", 0, 1)
    high = event(SwingKind.HIGH, "105", 2, 3)
    result = map_minute_path_to_four_hour_evidence(
        bars, path(bars[-1].close_ms, low, high), bars[-1].close_ms
    )
    assert [item.status for item in result] == [
        EvidenceStatus.NO_EVENT,
        EvidenceStatus.CANDIDATE,
        EvidenceStatus.NO_EVENT,
        EvidenceStatus.CANDIDATE,
    ]
    assert result[3].candidate is not None
    assert result[3].candidate.extreme_four_hour_open_ms == bars[2].open_ms


def test_mismatched_snapshot_is_rejected() -> None:
    first = bar(0)
    with pytest.raises(DataIntegrityError):
        map_minute_path_to_four_hour_evidence([first], path(first.close_ms - 1), first.close_ms)
