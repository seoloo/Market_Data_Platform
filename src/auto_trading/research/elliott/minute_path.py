"""1m 경로로 2% 반전을 검증하되 4h 마감 전에는 노출하지 않는다.

이 출력은 분 단위 경로 사건이며 Elliott 파동/4h 피벗 라벨이 아니다.
한 4h에 여러 반전이 있거나 1m 내부 순서가 불명이면 후속 파동 매핑은 보류한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.intrabar import (
    ExtremumOrder,
    IntrabarUnknownReason,
    audit_intrabar_extrema,
)
from auto_trading.research.elliott.pivots import (
    GuardedExtremaState,
    closed_ew0_candles,
)


@dataclass(frozen=True, slots=True)
class MinutePathPivot:
    """분 단위 반전 사건. `available_at_ms` 전에는 연구 snapshot에 없다."""

    kind: SwingKind
    price: Decimal
    extreme_minute_ms: int
    confirmation_minute_close_ms: int
    available_at_ms: int


@dataclass(frozen=True, slots=True)
class BlockedFourHour:
    open_ms: int
    reason: IntrabarUnknownReason


@dataclass(frozen=True, slots=True)
class MinutePathReplay:
    """불명 4h/1m에서 끊긴 경로 구간. 서로 다른 구간의 피벗은 잇지 않는다."""

    segments: tuple[tuple[MinutePathPivot, ...], ...]
    ambiguous_minute_open_ms: tuple[int, ...]
    blocked_four_hours: tuple[BlockedFourHour, ...]
    as_of_ms: int


def replay_four_hour_minute_path(
    four_hour_candles: Sequence[Candle],
    minutes_by_four_hour_open_ms: Mapping[int, Sequence[Candle]],
    as_of_ms: int,
) -> MinutePathReplay:
    """4h 마감봉 prefix의 1m 경로만 재생한다.

    1m은 가격 사건의 순서 확인용이다. 파동 시간대는 여전히 4h 이상이며
    분봉의 조기 신호를 생성하지 않는다. 품질 실패 봉과 모호한 1m에서 절단한다.
    """
    return replay_minute_groups(
        four_hour_candles,
        (minutes_by_four_hour_open_ms.get(outer.open_ms, ()) for outer in four_hour_candles),
        as_of_ms,
    )


def replay_minute_groups(
    four_hour_candles: Sequence[Candle],
    minute_groups: Iterable[Sequence[Candle]],
    as_of_ms: int,
) -> MinutePathReplay:
    """완성 4h당 1m 묶음 하나만 메모리에 두는 동등한 재생 경로.

    모든 Elliott 라벨은 4h 이상으로 제한된다. 이 반환값은 분봉의
    경로·품질 감사 사건일 뿐 1m 파동 판단이나 실거래 입력이 아니다.
    """
    closed = closed_ew0_candles(four_hour_candles, as_of_ms)
    supplied_groups = iter(minute_groups)
    state = GuardedExtremaState()
    blocked: list[BlockedFourHour] = []
    for outer in closed:
        supplied = next(supplied_groups, ())
        audit = audit_intrabar_extrema(outer, supplied, outer.close_ms)
        if audit.order is ExtremumOrder.UNKNOWN:
            assert audit.reason is not None
            blocked.append(BlockedFourHour(outer.open_ms, audit.reason))
            state.break_segment()
            continue
        for minute in supplied:
            if outer.open_ms <= minute.open_ms < outer.open_ms + Timeframe.H4.ms:
                state.feed(minute)

    swing_segments, ambiguous_minutes = state.snapshot()
    segments = tuple(
        tuple(
            MinutePathPivot(
                kind=swing.kind,
                price=swing.price,
                extreme_minute_ms=swing.at_ms,
                confirmation_minute_close_ms=swing.confirmed_at_ms,
                available_at_ms=Timeframe.H4.close_ms_of(swing.confirmed_at_ms),
            )
            for swing in swing_segment.swings
        )
        for swing_segment in swing_segments
    )
    return MinutePathReplay(
        segments=segments,
        ambiguous_minute_open_ms=ambiguous_minutes,
        blocked_four_hours=tuple(blocked),
        as_of_ms=as_of_ms,
    )
