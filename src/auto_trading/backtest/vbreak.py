"""V 변동성 돌파(당일 청산) 연구 계산 (docs/research/18 사전등록).

``breakout_trades``는 V/v1 원 규칙이다. 양방향 동시 돌파일을 **그날 전체 고저를 보고** 빼므로
미래 정보를 쓴다(18 §5 — 결과 무효). 판정에는 ``resolved_breakout_trades``(V/v1.1)를 쓴다.

일봉만으로 계산한다. 당일 시가 ± k×전날 고저폭을 돌파 가격으로 두고, 고가가 위쪽을 건드리면
그 가격에 롱, 저가가 아래쪽을 건드리면 그 가격에 숏, 당일 종가에 청산한다. 같은 날 양쪽이
모두 닿으면 어느 쪽이 먼저인지 일봉으로 알 수 없으므로 거래하지 않는다. 순수 함수다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

__all__ = [
    "BreakoutTrade",
    "DayBar",
    "HourBar",
    "Resolution",
    "breakout_trades",
    "resolved_breakout_trades",
]


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


@dataclass(frozen=True, slots=True)
class HourBar:
    open_ms: int
    high: float
    low: float


@dataclass(slots=True)
class Resolution:
    """V/v1.1 양방향 돌파일 처리 집계."""

    by_hour: int = 0
    """1시간봉으로 먼저 닿은 쪽을 가린 날."""
    worse_of: int = 0
    """순서를 가릴 수 없어(같은 1시간 양쪽 또는 1시간봉 결측) 나쁜 쪽 결과로 계산한 날."""


def resolved_breakout_trades(
    bars: Sequence[DayBar],
    hours: Mapping[int, Sequence[HourBar]],
    *,
    k: float,
    cost_bps: float,
) -> tuple[list[BreakoutTrade], Resolution]:
    """V/v1.1 — 같은 날 양쪽이 닿아도 거래한다. **미래 정보로 거래를 빼지 않는다.**

    먼저 닿은 쪽으로 진입해 당일 종가까지 보유한다(반대쪽 돌파로 뒤집지 않는다). 먼저 닿은 쪽은
    그날의 1시간봉(``hours[day_open_ms]``, 시간순)으로 가린다. 한 시간 안에 양쪽이 닿거나 먼저
    닿은 시각 이전에 1시간봉이 비면 순서를 알 수 없으므로 두 방향 중 **나쁜 결과**를 쓴다.
    """
    trades: list[BreakoutTrade] = []
    resolution = Resolution()
    for prev, bar in pairwise(bars):
        if bar.open_ms - prev.open_ms != 86_400_000:
            raise ValueError(f"일봉이 연속되지 않는다: {prev.open_ms} → {bar.open_ms}")
        width = k * (prev.high - prev.low)
        if width <= 0:
            continue
        up, down = bar.open + width, bar.open - width
        hit_up, hit_down = bar.high >= up, bar.low <= down
        long_gross = (bar.close / up - 1) * 1e4
        short_gross = -(bar.close / down - 1) * 1e4
        if hit_up and hit_down:
            side = _first_side(hours.get(bar.open_ms, ()), bar.open_ms, up, down)
            if side == 0:
                resolution.worse_of += 1
                side = 1 if long_gross <= short_gross else -1
            else:
                resolution.by_hour += 1
        elif hit_up:
            side = 1
        elif hit_down:
            side = -1
        else:
            continue
        gross = long_gross if side > 0 else short_gross
        trades.append(BreakoutTrade(bar.open_ms, side, gross, gross - cost_bps))
    return trades, resolution


def _first_side(hours: Sequence[HourBar], day_ms: int, up: float, down: float) -> int:
    """먼저 닿은 쪽(1 위, −1 아래). 가릴 수 없으면 0."""
    expected = day_ms
    for hour in sorted(hours, key=lambda h: h.open_ms):
        if hour.open_ms != expected:
            return 0  # 먼저 닿기 전에 빈 시간이 있다
        touched_up, touched_down = hour.high >= up, hour.low <= down
        if touched_up and touched_down:
            return 0
        if touched_up:
            return 1
        if touched_down:
            return -1
        expected += 3_600_000
    return 0
