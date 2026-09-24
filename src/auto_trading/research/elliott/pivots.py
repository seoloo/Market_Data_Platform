"""EW-0 연구용 4h 피벗 검출: 봉 내부 순서 불명이면 구간을 절단한다.

OHLC만으로 같은 봉의 고가·저가 선후를 복원하지 않는다. 현재 봉이 새 극값과
2% 반전을 모두 포함하면 어느 쪽이 먼저였는지 불명이라 해당 봉을 버리고
다음 봉에서 새 구간을 시작한다. 과거 확정 피벗은 변경하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import Swing, SwingKind, SwingSeries

EW0_THRESHOLD_PCT = Decimal("0.02")
_STEP_MS = Timeframe.H4.ms


@dataclass(frozen=True, slots=True)
class PivotDetection:
    """모호한 봉으로 분리된 피벗 구간. 구간을 가로질러 쌍을 만들면 안 된다."""

    segments: tuple[SwingSeries, ...]
    ambiguous_open_ms: tuple[int, ...]
    as_of_ms: int


def closed_ew0_candles(candles: Sequence[Candle], as_of_ms: int) -> tuple[Candle, ...]:
    """판정 시점에 닫힌, 누락 없는 UTC 4h 봉만 반환한다."""
    closed = tuple(c for c in candles if c.close_ms <= as_of_ms)
    previous_open_ms: int | None = None
    for candle in closed:
        if candle.open_ms % _STEP_MS or candle.close_ms != candle.open_ms + _STEP_MS - 1:
            raise DataIntegrityError("EW-0에는 UTC 정렬 마감 4h 봉만 허용한다")
        if candle.volume <= 0:
            raise DataIntegrityError("체결량 0인 4h 봉에서는 피벗을 판정하지 않는다")
        if previous_open_ms is not None and candle.open_ms != previous_open_ms + _STEP_MS:
            raise DataIntegrityError("4h 봉이 누락·중복·역순이다")
        previous_open_ms = candle.open_ms
    return closed


def detect_guarded_pivots(candles: Sequence[Candle], as_of_ms: int) -> PivotDetection:
    """고저 선후를 가정하지 않는 보수적 2% ZigZag 피벗 검출.

    극값이 **이전 봉**에 있었고 현재 봉에서 반전이 확인될 때만 Swing을 낸다.
    하나의 모호한 봉 이후에는 이전 구간과 후보를 섞지 않는다. 이 선택은
    검출률을 낮출 수 있으며 기존 운영 `detect_swings`를 대체하지 않는다.
    """
    closed = closed_ew0_candles(candles, as_of_ms)
    segments, ambiguous_open_ms = replay_guarded_extrema(closed)
    return PivotDetection(
        segments=segments,
        ambiguous_open_ms=ambiguous_open_ms,
        as_of_ms=as_of_ms,
    )


def replay_guarded_extrema(
    validated_bars: Sequence[Candle],
) -> tuple[tuple[SwingSeries, ...], tuple[int, ...]]:
    """검증된 시간순 봉의 2% 반전 경로를 재생한다.

    입력의 시간대·격자·가용 시각은 **호출자 책임**이다. 연구 전용 4h/1m
    검증기만 이 함수를 호출하며, 운영 피벗 검출기에 연결하지 않는다.
    """
    state = GuardedExtremaState()
    for candle in validated_bars:
        state.feed(candle)
    return state.snapshot()


class GuardedExtremaState:
    """검증된 연속 봉을 한 번씩 읽는 연구용 상태 기계.

    호출자는 시간대·격자·거래량과 품질을 먼저 검증한다. `break_segment`는
    결측/모호한 상위 봉의 경계에만 사용하며, 과거 피벗은 수정하지 않는다.
    """

    def __init__(self, threshold_pct: Decimal = EW0_THRESHOLD_PCT) -> None:
        if not Decimal(0) < threshold_pct < Decimal(1):
            raise DataIntegrityError("반전 임계는 0과 1 사이여야 한다")
        self._threshold = threshold_pct
        self._segments: list[list[Swing]] = [[]]
        self._ambiguous_open_ms: list[int] = []
        self._direction = 0  # 0: 첫 피벗 미정, +1: 고점 탐색, -1: 저점 탐색
        self._high: tuple[Decimal, int] | None = None
        self._low: tuple[Decimal, int] | None = None

    def break_segment(self) -> None:
        self._segments.append([])
        self._direction = 0
        self._high = None
        self._low = None

    def feed(self, candle: Candle) -> None:
        """OHLC 내부 경로를 발명하지 않고 다음 봉 하나를 반영한다."""
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            self._feed(candle)

    def _feed(self, candle: Candle) -> None:
        high = self._high
        low = self._low
        direction = self._direction
        if high is None or low is None:
            t = self._threshold
            if _drops(candle.high, candle.low, t) or _rises(candle.low, candle.high, t):
                self._ambiguous_open_ms.append(candle.open_ms)
                self.break_segment()
                return
            self._high = (candle.high, candle.open_ms)
            self._low = (candle.low, candle.open_ms)
            return

        t = self._threshold
        new_high = candle.high > high[0]
        new_low = candle.low < low[0]
        high_then_low_possible = new_high and _drops(candle.high, candle.low, t)
        low_then_high_possible = new_low and _rises(candle.low, candle.high, t)
        confirms_high = _drops(high[0], candle.low, t)
        confirms_low = _rises(low[0], candle.high, t)

        if direction == 0:
            ambiguous = (
                high_then_low_possible or low_then_high_possible or (confirms_high and confirms_low)
            )
        elif direction > 0:
            ambiguous = high_then_low_possible
        else:
            ambiguous = low_then_high_possible

        if ambiguous:
            self._ambiguous_open_ms.append(candle.open_ms)
            self.break_segment()
            return

        if direction >= 0 and confirms_high:
            self._segments[-1].append(
                Swing(
                    kind=SwingKind.HIGH,
                    price=high[0],
                    at_ms=high[1],
                    confirmed_at_ms=candle.close_ms,
                )
            )
            self._direction = -1
            self._low = (candle.low, candle.open_ms)
            return
        if direction <= 0 and confirms_low:
            self._segments[-1].append(
                Swing(
                    kind=SwingKind.LOW,
                    price=low[0],
                    at_ms=low[1],
                    confirmed_at_ms=candle.close_ms,
                )
            )
            self._direction = 1
            self._high = (candle.high, candle.open_ms)
            return

        if direction >= 0 and new_high:
            self._high = (candle.high, candle.open_ms)
        if direction <= 0 and new_low:
            self._low = (candle.low, candle.open_ms)

    def snapshot(self) -> tuple[tuple[SwingSeries, ...], tuple[int, ...]]:
        """현재까지 확정된 사건만 반환한다. 미확정 극값은 포함하지 않는다."""
        return (
            tuple(
                SwingSeries(swings=tuple(swings), threshold_pct=self._threshold)
                for swings in self._segments
            ),
            tuple(self._ambiguous_open_ms),
        )


def _drops(high: Decimal, low: Decimal, threshold: Decimal = EW0_THRESHOLD_PCT) -> bool:
    return low <= high * (Decimal(1) - threshold)


def _rises(low: Decimal, high: Decimal, threshold: Decimal = EW0_THRESHOLD_PCT) -> bool:
    return high >= low * (Decimal(1) + threshold)
