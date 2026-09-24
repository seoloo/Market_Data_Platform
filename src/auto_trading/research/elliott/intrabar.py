"""1분봉으로 닫힌 4시간봉의 최고·최저 시각 순서만 감사한다.

극값 순서가 확인돼도 ZigZag 피벗 경로 전체를 복원한 것은 아니다.
파동은 계속 4시간봉 이상에서만 판정하며, 이 모듈은 Shadow 연구용이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle

_FOUR_HOURS_MS = Timeframe.H4.ms
_ONE_MINUTE_MS = Timeframe.M1.ms
_MINUTES_PER_BAR = _FOUR_HOURS_MS // _ONE_MINUTE_MS


class ExtremumOrder(StrEnum):
    HIGH_BEFORE_LOW = "HIGH_BEFORE_LOW"
    LOW_BEFORE_HIGH = "LOW_BEFORE_HIGH"
    UNKNOWN = "UNKNOWN"


class IntrabarUnknownReason(StrEnum):
    FOUR_HOUR_NOT_CLOSED = "FOUR_HOUR_NOT_CLOSED"
    MINUTE_GRID_INCOMPLETE = "MINUTE_GRID_INCOMPLETE"
    ZERO_VOLUME_MINUTE = "ZERO_VOLUME_MINUTE"
    OHLC_MISMATCH = "OHLC_MISMATCH"
    EXTREMA_OVERLAP = "EXTREMA_OVERLAP"


@dataclass(frozen=True, slots=True)
class IntrabarAudit:
    """한 4h 봉의 전역 고저 발생 순서. 피벗 또는 거래 신호가 아니다."""

    order: ExtremumOrder
    reason: IntrabarUnknownReason | None
    four_hour_open_ms: int
    first_high_minute_ms: int | None = None
    last_high_minute_ms: int | None = None
    first_low_minute_ms: int | None = None
    last_low_minute_ms: int | None = None


def audit_intrabar_extrema(
    four_hour: Candle,
    minute_candles: Sequence[Candle],
    as_of_ms: int,
) -> IntrabarAudit:
    """완성된 240개 1m 봉과 4h OHLC가 정확히 맞을 때만 극값 순서를 반환한다.

    입력 배열에 미래 분봉이 더 있어도 대상 4h 밖 자료는 무시한다. 다만 4h가
    닫히기 전에는 내부 극값·부분 결과를 한 건도 노출하지 않는다.
    """
    if (
        four_hour.open_ms % _FOUR_HOURS_MS
        or four_hour.close_ms != four_hour.open_ms + _FOUR_HOURS_MS - 1
    ):
        raise DataIntegrityError("UTC 정렬 마감 4h 봉이 아니다")
    if as_of_ms < four_hour.close_ms:
        return _unknown(four_hour, IntrabarUnknownReason.FOUR_HOUR_NOT_CLOSED)

    minute_bars = tuple(
        bar
        for bar in minute_candles
        if four_hour.open_ms <= bar.open_ms < four_hour.open_ms + _FOUR_HOURS_MS
    )
    if len(minute_bars) != _MINUTES_PER_BAR:
        return _unknown(four_hour, IntrabarUnknownReason.MINUTE_GRID_INCOMPLETE)
    for index, bar in enumerate(minute_bars):
        expected_open_ms = four_hour.open_ms + index * _ONE_MINUTE_MS
        if bar.open_ms != expected_open_ms or bar.close_ms != expected_open_ms + _ONE_MINUTE_MS - 1:
            return _unknown(four_hour, IntrabarUnknownReason.MINUTE_GRID_INCOMPLETE)
    if any(bar.volume <= 0 for bar in minute_bars):
        return _unknown(four_hour, IntrabarUnknownReason.ZERO_VOLUME_MINUTE)
    if (
        minute_bars[0].open != four_hour.open
        or minute_bars[-1].close != four_hour.close
        or max(bar.high for bar in minute_bars) != four_hour.high
        or min(bar.low for bar in minute_bars) != four_hour.low
    ):
        return _unknown(four_hour, IntrabarUnknownReason.OHLC_MISMATCH)

    high_times = tuple(bar.open_ms for bar in minute_bars if bar.high == four_hour.high)
    low_times = tuple(bar.open_ms for bar in minute_bars if bar.low == four_hour.low)
    first_high, last_high = high_times[0], high_times[-1]
    first_low, last_low = low_times[0], low_times[-1]
    if last_high < first_low:
        order = ExtremumOrder.HIGH_BEFORE_LOW
        reason = None
    elif last_low < first_high:
        order = ExtremumOrder.LOW_BEFORE_HIGH
        reason = None
    else:
        order = ExtremumOrder.UNKNOWN
        reason = IntrabarUnknownReason.EXTREMA_OVERLAP
    return IntrabarAudit(
        order=order,
        reason=reason,
        four_hour_open_ms=four_hour.open_ms,
        first_high_minute_ms=first_high,
        last_high_minute_ms=last_high,
        first_low_minute_ms=first_low,
        last_low_minute_ms=last_low,
    )


def _unknown(four_hour: Candle, reason: IntrabarUnknownReason) -> IntrabarAudit:
    return IntrabarAudit(
        order=ExtremumOrder.UNKNOWN,
        reason=reason,
        four_hour_open_ms=four_hour.open_ms,
    )
