"""T/v2-long 현물 추세 결정 — Pure Core (docs/research/16).

하루에 한 번, 마감된 일봉(00:00 UTC 종가)으로 종목별 목표 비중을 정하고, 현재 보유와 비교해
주문 의도를 만든다. I/O·실시간 시계 없음. 결측·비연속·미마감은 해당 종목을 **판단 불가**로 두고
신규 매수를 하지 않는다(fail-closed). 판단 불가여도 매도(위험 축소)는 막지 않는다 — 다만 목표를
모르면 기존 보유를 유지한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from auto_trading.config.schema import SpotTrendConfig
from auto_trading.core.enums import Timeframe
from auto_trading.core.money import floor_to_step
from auto_trading.core.types import Candle
from auto_trading.features.tsmom import realized_vol_annual, trend_direction

__all__ = [
    "SpotOrderIntent",
    "SpotSide",
    "SpotTarget",
    "plan_spot_orders",
    "spot_trend_targets",
]

_ZERO = Decimal(0)
_DAY_MS = Timeframe.D1.ms


class SpotSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class SpotTarget:
    symbol: str
    weight: Decimal | None
    """자본 대비 목표 명목 비중 ∈ [0, max_weight]. None이면 판단 불가."""
    direction: Decimal | None = None
    vol_annual: Decimal | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SpotOrderIntent:
    symbol: str
    side: SpotSide
    quantity: Decimal
    target_weight: Decimal


def _closed_series(candles: Sequence[Candle], as_of_ms: int, need: int) -> list[Decimal] | None:
    closed = [c for c in candles if c.close_ms <= as_of_ms][-need:]
    if len(closed) < need or closed[-1].close_ms != as_of_ms:
        return None
    for prev, cur in pairwise(closed):
        if cur.open_ms != prev.open_ms + _DAY_MS or cur.close_ms != cur.open_ms + _DAY_MS - 1:
            return None
    return [c.close for c in closed]


def spot_trend_targets(
    daily: Mapping[str, Sequence[Candle]], *, as_of_ms: int, cfg: SpotTrendConfig
) -> dict[str, SpotTarget]:
    """``as_of_ms``(일봉 마감 시각 = 00:00 UTC − 1ms)에 종목별 목표 비중을 정한다.

    방향 = 20·60·120일 부호 평균, 음수면 0(현금). 크기 = min(15% / 60일 연변동성, 0.5).
    """
    need = max(max(cfg.lookbacks), cfg.vol_days) + 1
    out: dict[str, SpotTarget] = {}
    for symbol in cfg.symbols:
        if (as_of_ms + 1) % _DAY_MS:
            out[symbol] = SpotTarget(symbol, None, reason="NOT_DAILY_CLOSE")
            continue
        closes = _closed_series(daily.get(symbol, ()), as_of_ms, need)
        if closes is None:
            out[symbol] = SpotTarget(symbol, None, reason="DATA_UNAVAILABLE")
            continue
        direction = trend_direction(closes, cfg.lookbacks)
        vol = realized_vol_annual(closes, cfg.vol_days)
        if direction is None or vol is None:
            out[symbol] = SpotTarget(symbol, None, reason="DATA_UNAVAILABLE")
            continue
        size = cfg.max_weight if vol <= 0 else min(cfg.target_vol / vol, cfg.max_weight)
        weight = max(direction, _ZERO) * size
        out[symbol] = SpotTarget(symbol, weight, direction, vol)
    return out


def plan_spot_orders(
    targets: Mapping[str, SpotTarget],
    *,
    holdings: Mapping[str, Decimal],
    prices: Mapping[str, Decimal],
    equity: Decimal,
    cfg: SpotTrendConfig,
    step_size: Mapping[str, Decimal],
    min_notional: Mapping[str, Decimal],
) -> list[SpotOrderIntent]:
    """목표 비중과 보유 수량의 차이로 주문 의도를 만든다.

    |목표 − 현재| < 밴드이고 방향(보유 여부)이 같으면 거래하지 않는다. 수량은 step에 내림,
    최소 주문 금액 미만이면 건너뛴다. 매도가 먼저 오도록 정렬한다(현금 확보 뒤 매수).
    """
    if equity <= 0:
        return []
    intents: list[SpotOrderIntent] = []
    for symbol, target in targets.items():
        if target.weight is None or symbol not in prices:
            continue
        price = prices[symbol]
        held = holdings.get(symbol, _ZERO)
        current = held * price / equity
        want = target.weight
        same_state = (want > 0) == (current > 0)
        if abs(want - current) < cfg.band and same_state:
            continue
        delta_qty = floor_to_step(abs(want * equity / price - held), step_size[symbol])
        if want == 0:
            delta_qty = floor_to_step(held, step_size[symbol])
        if delta_qty <= 0 or delta_qty * price < min_notional[symbol]:
            continue
        side = SpotSide.BUY if want > current else SpotSide.SELL
        intents.append(SpotOrderIntent(symbol, side, delta_qty, want))
    return sorted(intents, key=lambda i: (i.side is SpotSide.BUY, i.symbol))
