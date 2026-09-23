"""WA 마감 1h 상태와 명시적 트리거 잠금. I/O 및 숨은 가변 상태 없음."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from auto_trading.config.schema import WAConfig
from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle


@dataclass(frozen=True, slots=True)
class WAState:
    """호출자가 저장·복구할 상태. 진입 거부 여부와 무관하게 트리거를 소비한다."""

    last_evaluated_ms: int | None = None
    last_trigger_ms: int | None = None


@dataclass(frozen=True, slots=True)
class WAResult:
    state: WAState
    available: bool
    triggered: bool = False
    return_24h_pct: Decimal | None = None
    range_position_pct: Decimal | None = None
    sigma_1h: Decimal | None = None


def evaluate_wa(
    candles: Sequence[Candle], *, as_of_ms: int, cfg: WAConfig, state: WAState
) -> WAResult:
    """현재 마감 시각만 평가. 중복/역순은 상태 불변, 결측 창은 fail-closed.

    기존 설계 측정과 같은 24개 단순 수익률의 표본 SD(ddof=1)를 쓴다. 봉 창을 잘라도 잠금은
    WAState에 남으므로 신호 위상이 달라지지 않는다. 미래 봉은 읽지 않는다.
    """
    if state.last_evaluated_ms is not None and as_of_ms <= state.last_evaluated_ms:
        return WAResult(state, available=True)
    if (as_of_ms + 1) % Timeframe.H1.ms:
        return WAResult(state, available=False)
    closed = [c for c in candles if c.close_ms <= as_of_ms][-cfg.range_hours :]
    if len(closed) < cfg.range_hours or closed[-1].close_ms != as_of_ms:
        return WAResult(state, available=False)
    if any(
        c.open_ms % Timeframe.H1.ms
        or c.close_ms != c.open_ms + Timeframe.H1.ms - 1
        or (i > 0 and c.open_ms != closed[i - 1].open_ms + Timeframe.H1.ms)
        for i, c in enumerate(closed)
    ):
        return WAResult(state, available=False)
    if state.last_trigger_ms is not None and state.last_trigger_ms > as_of_ms:
        return WAResult(state, available=False)
    latest = closed[-1].close
    ret = latest / closed[-1 - cfg.return_hours].close - 1
    low, high = min(c.low for c in closed), max(c.high for c in closed)
    pos = (latest - low) / (high - low) if high > low else Decimal("0.5")
    vol_window = closed[-cfg.return_hours - 1 :]
    returns = [b.close / a.close - 1 for a, b in pairwise(vol_window)]
    mean = sum(returns, Decimal(0)) / len(returns)
    sigma = (sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (len(returns) - 1)).sqrt()
    unlocked = (
        state.last_trigger_ms is None
        or as_of_ms - state.last_trigger_ms >= cfg.trigger_lock_minutes * Timeframe.M1.ms
    )
    triggered = unlocked and (
        cfg.control_without_state or (ret < 0 and pos <= cfg.range_position_pct)
    )
    next_state = WAState(as_of_ms, as_of_ms if triggered else state.last_trigger_ms)
    return WAResult(next_state, True, triggered, ret, pos, sigma)
