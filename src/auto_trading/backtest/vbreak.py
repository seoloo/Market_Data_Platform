"""V/v1 변동성 돌파(당일 청산) 연구 계산 (docs/research/18 사전등록).

일봉만으로 계산한다. 당일 시가 ± k×전날 고저폭을 돌파 가격으로 두고, 고가가 위쪽을 건드리면
그 가격에 롱, 저가가 아래쪽을 건드리면 그 가격에 숏, 당일 종가에 청산한다. 같은 날 양쪽이
모두 닿으면 어느 쪽이 먼저인지 일봉으로 알 수 없으므로 거래하지 않는다. 순수 함수다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

__all__ = ["BreakoutTrade", "DayBar", "breakout_trades"]


@dataclass(frozen=True, slots=True)
class DayBar:
    open_ms: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class BreakoutTrade:
    open_ms: int
    side: int
    gross_bps: float
    net_bps: float


def breakout_trades(
    bars: Sequence[DayBar], *, k: float, cost_bps: float
) -> tuple[list[BreakoutTrade], int]:
    """(거래 목록, 양방향 동시 돌파로 건너뛴 날 수). ``bars``는 연속 일봉이어야 한다."""
    trades: list[BreakoutTrade] = []
    ambiguous = 0
    for prev, bar in pairwise(bars):
        if bar.open_ms - prev.open_ms != 86_400_000:
            raise ValueError(f"일봉이 연속되지 않는다: {prev.open_ms} → {bar.open_ms}")
        width = k * (prev.high - prev.low)
        if width <= 0:
            continue
        up, down = bar.open + width, bar.open - width
        hit_up, hit_down = bar.high >= up, bar.low <= down
        if hit_up and hit_down:
            ambiguous += 1
            continue
        if hit_up:
            gross = (bar.close / up - 1) * 1e4
            trades.append(BreakoutTrade(bar.open_ms, 1, gross, gross - cost_bps))
        elif hit_down:
            gross = -(bar.close / down - 1) * 1e4
            trades.append(BreakoutTrade(bar.open_ms, -1, gross, gross - cost_bps))
    return trades, ambiguous
