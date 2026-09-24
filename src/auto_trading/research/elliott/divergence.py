"""EW-0: 확정 가격 피벗에 대응시킨 RSI 일반 다이버전스.

이 모듈은 피벗을 **검출하지 않는다**. 입력 피벗의 원천과 장중 고저 순서의
검증은 별도 단계다. 사건 시각만 출력하며, 활성 기간이나 주문 의미를 부여하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.indicators import rsi_series
from auto_trading.features.structure import Swing, SwingKind, SwingSeries
from auto_trading.research.elliott.pivots import (
    EW0_THRESHOLD_PCT,
    PivotDetection,
    closed_ew0_candles,
    detect_guarded_pivots,
)

EW0_RSI_PERIOD = 14


class DivergenceKind(StrEnum):
    REGULAR_BULLISH = "REGULAR_BULLISH"
    REGULAR_BEARISH = "REGULAR_BEARISH"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class UnknownReason(StrEnum):
    RSI_WARMUP = "RSI_WARMUP"
    AMBIGUOUS_INTRABAR = "AMBIGUOUS_INTRABAR"


@dataclass(frozen=True, slots=True)
class DivergenceEvent:
    """연속 확정 같은 종류 피벗 한 쌍의 결과. 과거 사건은 덮어쓰지 않는다."""

    kind: DivergenceKind
    first: Swing
    second: Swing
    first_rsi: Decimal | None
    second_rsi: Decimal | None
    detected_at_ms: int
    unknown_reason: UnknownReason | None = None


def classify_regular_divergence(
    first: Swing,
    second: Swing,
    first_rsi: Decimal,
    second_rsi: Decimal,
) -> DivergenceKind:
    """같은 종류의 두 가격 피벗에서 EW-0의 엄격 부등호를 평가한다."""
    if not isinstance(first.kind, SwingKind) or first.kind is not second.kind:
        raise ValueError("다른 종류의 피벗을 비교할 수 없다")
    if first.kind is SwingKind.LOW:
        if second.price < first.price and second_rsi > first_rsi:
            return DivergenceKind.REGULAR_BULLISH
    elif second.price > first.price and second_rsi < first_rsi:
        return DivergenceKind.REGULAR_BEARISH
    return DivergenceKind.NONE


def confirmed_divergences(
    candles: Sequence[Candle],
    swings: SwingSeries,
    as_of_ms: int,
) -> tuple[DivergenceEvent, ...]:
    """마감 4h 봉과 그 시점까지 확정된 피벗만으로 사건을 재생한다.

    `candles`의 시작 이전 RSI는 알 수 없다. 따라서 warmup 부족은 UNKNOWN이다.
    미래 봉이나 미확정 피벗은 아예 입력 prefix에서 제외한다.
    """
    if swings.threshold_pct != EW0_THRESHOLD_PCT:
        raise ValueError("EW-0 피벗 임계는 2%로 고정된다")

    closed = closed_ew0_candles(candles, as_of_ms)
    by_open = {c.open_ms: c for c in closed}
    by_close = {c.close_ms: c for c in closed}

    known = tuple(s for s in swings.swings if s.confirmed_at_ms <= as_of_ms)
    for index, swing in enumerate(known):
        if not isinstance(swing.kind, SwingKind):
            raise DataIntegrityError("피벗 종류가 HIGH/LOW가 아니다")
        pivot = by_open.get(swing.at_ms)
        confirmation = by_close.get(swing.confirmed_at_ms)
        if pivot is None or confirmation is None:
            raise DataIntegrityError("피벗 발생/확정 봉이 마감 4h 자료에 없다")
        if swing.price != (pivot.low if swing.kind is SwingKind.LOW else pivot.high):
            raise DataIntegrityError("피벗 가격이 발생 봉의 고가/저가와 다르다")
        if confirmation.open_ms < pivot.open_ms:
            raise DataIntegrityError("피벗 확정 봉이 발생 봉보다 빠르다")
        if index and (
            swing.at_ms <= known[index - 1].at_ms
            or swing.confirmed_at_ms <= known[index - 1].confirmed_at_ms
            or swing.kind is known[index - 1].kind
        ):
            raise DataIntegrityError("피벗은 발생·확정 시각 순이며 고저가 번갈아야 한다")

    if len(known) < 3:
        return ()
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        values = rsi_series([c.close for c in closed], EW0_RSI_PERIOD)
    rsi_by_open = dict(zip((c.open_ms for c in closed), values, strict=True))
    latest_by_kind: dict[SwingKind, Swing] = {}
    events: list[DivergenceEvent] = []
    ambiguous_since = False
    for swing in known:
        if swing.at_ms == by_close[swing.confirmed_at_ms].open_ms:
            ambiguous_since = True
        first = latest_by_kind.get(swing.kind)
        latest_by_kind[swing.kind] = swing
        if first is None:
            continue
        first_rsi = rsi_by_open[first.at_ms]
        second_rsi = rsi_by_open[swing.at_ms]
        reason: UnknownReason | None = None
        if ambiguous_since:
            kind = DivergenceKind.UNKNOWN
            reason = UnknownReason.AMBIGUOUS_INTRABAR
        elif first_rsi is None or second_rsi is None:
            kind = DivergenceKind.UNKNOWN
            reason = UnknownReason.RSI_WARMUP
        else:
            kind = classify_regular_divergence(first, swing, first_rsi, second_rsi)
        events.append(
            DivergenceEvent(
                kind=kind,
                first=first,
                second=swing,
                first_rsi=first_rsi,
                second_rsi=second_rsi,
                detected_at_ms=swing.confirmed_at_ms,
                unknown_reason=reason,
            )
        )
    return tuple(events)


def detect_ew0_divergences(
    candles: Sequence[Candle], as_of_ms: int
) -> tuple[PivotDetection, tuple[DivergenceEvent, ...]]:
    """가드된 피벗의 **같은 구간 안에서만** 다이버전스 사건을 계산한다."""
    detection = detect_guarded_pivots(candles, as_of_ms)
    events = tuple(
        event
        for segment in detection.segments
        for event in confirmed_divergences(candles, segment, as_of_ms)
    )
    return detection, events
