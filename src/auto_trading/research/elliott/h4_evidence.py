"""1m 내부 경로를 마감 4h의 보수적 피벗 *증거*로만 투영한다.

파동 카운트, 진입 신호, confidence, 실거래 판단을 생성하지 않는다.
모호함과 다대일 충돌은 UNKNOWN으로 보존하고 과거 출력을 소급 수정하지 않는다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.minute_path import MinutePathPivot, MinutePathReplay
from auto_trading.research.elliott.pivots import closed_ew0_candles


class EvidenceStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    NO_EVENT = "NO_EVENT"
    UNKNOWN = "UNKNOWN"


class EvidenceUnknownReason(StrEnum):
    BLOCKED_FOUR_HOUR = "BLOCKED_FOUR_HOUR"
    AMBIGUOUS_MINUTE = "AMBIGUOUS_MINUTE"
    MULTIPLE_EVENTS = "MULTIPLE_EVENTS"
    SAME_FOUR_HOUR = "SAME_FOUR_HOUR"
    INTERRUPTED_PATH = "INTERRUPTED_PATH"
    NOT_FOUR_HOUR_EXTREMUM = "NOT_FOUR_HOUR_EXTREMUM"
    NON_MONOTONIC_CANDIDATE = "NON_MONOTONIC_CANDIDATE"


@dataclass(frozen=True, slots=True)
class FourHourPivotEvidence:
    kind: SwingKind
    price: Decimal
    extreme_four_hour_open_ms: int
    confirmed_four_hour_close_ms: int


@dataclass(frozen=True, slots=True)
class FourHourEvidence:
    """한 4h 마감 시점에 새로 알 수 있는 경로 증거. 파동 라벨 아님."""

    close_ms: int
    status: EvidenceStatus
    candidate: FourHourPivotEvidence | None = None
    reason: EvidenceUnknownReason | None = None


def map_minute_path_to_four_hour_evidence(
    four_hour_candles: Sequence[Candle],
    replay: MinutePathReplay,
    as_of_ms: int,
) -> tuple[FourHourEvidence, ...]:
    """결과를 보지 않고 고정한 다대일·충돌·UNKNOWN 규칙을 적용한다.

    하나의 4h에 확인 사건이 정확히 하나이고, 극값이 **이전 4h 봉**의
    실제 고저이며 극값~확정 구간에 단절이 없을 때만 후보 증거다.
    1m 시간대로 Elliott count를 만들지 않는다.
    """
    if replay.as_of_ms != as_of_ms:
        raise DataIntegrityError("4h 증거와 1m 경로의 as_of_ms가 다르다")
    closed = closed_ew0_candles(four_hour_candles, as_of_ms)
    bars_by_open = {bar.open_ms: bar for bar in closed}
    close_set = {bar.close_ms for bar in closed}
    blocked_open = {item.open_ms for item in replay.blocked_four_hours}
    if (
        len(blocked_open) != len(replay.blocked_four_hours)
        or not blocked_open <= bars_by_open.keys()
    ):
        raise DataIntegrityError("1m 경로의 차단 4h 식별자가 유효하지 않다")
    ambiguous_open = set(replay.ambiguous_minute_open_ms)
    if len(ambiguous_open) != len(replay.ambiguous_minute_open_ms):
        raise DataIntegrityError("중복된 모호 1m 식별자")
    for minute_open_ms in ambiguous_open:
        if (
            minute_open_ms % Timeframe.M1.ms
            or Timeframe.H4.open_ms_of(minute_open_ms) not in bars_by_open
        ):
            raise DataIntegrityError("1m 경로의 모호 시각이 유효한 4h 범위 밖이다")
    ambiguous_four_hours = {Timeframe.H4.open_ms_of(minute) for minute in ambiguous_open}

    by_close: dict[int, list[MinutePathPivot]] = defaultdict(list)
    for segment in replay.segments:
        for event in segment:
            if (
                event.available_at_ms not in close_set
                or event.available_at_ms
                != Timeframe.H4.close_ms_of(event.confirmation_minute_close_ms)
                or event.extreme_minute_ms % Timeframe.M1.ms
                or Timeframe.H4.open_ms_of(event.extreme_minute_ms) not in bars_by_open
                or event.confirmation_minute_close_ms % Timeframe.M1.ms != Timeframe.M1.ms - 1
                or event.extreme_minute_ms >= event.confirmation_minute_close_ms
            ):
                raise DataIntegrityError("1m 경로 사건의 시각 경계가 유효하지 않다")
            by_close[event.available_at_ms].append(event)

    result: list[FourHourEvidence] = []
    previous: FourHourPivotEvidence | None = None
    for bar in closed:
        events = by_close.get(bar.close_ms, [])
        reason: EvidenceUnknownReason | None = None
        if bar.open_ms in blocked_open:
            reason = EvidenceUnknownReason.BLOCKED_FOUR_HOUR
        elif bar.open_ms in ambiguous_four_hours:
            reason = EvidenceUnknownReason.AMBIGUOUS_MINUTE
        elif len(events) > 1:
            reason = EvidenceUnknownReason.MULTIPLE_EVENTS
        elif not events:
            result.append(FourHourEvidence(bar.close_ms, EvidenceStatus.NO_EVENT))
            continue
        else:
            event = events[0]
            extreme_open = Timeframe.H4.open_ms_of(event.extreme_minute_ms)
            extreme_bar = bars_by_open.get(extreme_open)
            if extreme_bar is None:
                raise DataIntegrityError("1m 극값의 4h 봉이 없다")
            if extreme_open >= bar.open_ms:
                reason = EvidenceUnknownReason.SAME_FOUR_HOUR
            elif _interrupted(extreme_open, bar.open_ms, blocked_open, ambiguous_four_hours):
                reason = EvidenceUnknownReason.INTERRUPTED_PATH
            elif event.price != (
                extreme_bar.high if event.kind is SwingKind.HIGH else extreme_bar.low
            ):
                reason = EvidenceUnknownReason.NOT_FOUR_HOUR_EXTREMUM
            elif previous is not None and (
                extreme_open <= previous.extreme_four_hour_open_ms or event.kind is previous.kind
            ):
                reason = EvidenceUnknownReason.NON_MONOTONIC_CANDIDATE
            else:
                previous = FourHourPivotEvidence(
                    kind=event.kind,
                    price=event.price,
                    extreme_four_hour_open_ms=extreme_open,
                    confirmed_four_hour_close_ms=bar.close_ms,
                )
                result.append(
                    FourHourEvidence(bar.close_ms, EvidenceStatus.CANDIDATE, candidate=previous)
                )
                continue

        result.append(FourHourEvidence(bar.close_ms, EvidenceStatus.UNKNOWN, reason=reason))
        previous = None
    return tuple(result)


def _interrupted(
    extreme_open_ms: int,
    confirmation_open_ms: int,
    blocked_open_ms: set[int],
    ambiguous_four_hour_open_ms: set[int],
) -> bool:
    if any(extreme_open_ms <= blocked <= confirmation_open_ms for blocked in blocked_open_ms):
        return True
    return any(
        extreme_open_ms <= ambiguous <= confirmation_open_ms
        for ambiguous in ambiguous_four_hour_open_ms
    )
