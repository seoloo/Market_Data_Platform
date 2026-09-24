"""WA 결합용 진입 시점 노출 상태 (문서 35, 순수 함수) — 거래 방향·손익을 읽지 않는다.

시각 t 의 상태는 t **이전에** 마감한 4h 발동만으로 정한다(동시·이후 발동 제외).

* Elliott: t 직전 ``K`` 봉 안에 1순위 IMPULSE 사건이 있었나, 있었다면 끝난 impulse 의 방향.
* 피보나치: t 직전 발동의 꼭짓점 열에서 가장 최근 완성 다리 A→B (둘 다 4h 가시, 종류 반대)를 잡고,
  가격 P 의 되돌림 r = (B − P) / (B − A). 0.382 ≤ r ≤ 0.618 이면 되돌림 구간.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.scanner import Trigger, TriggerResult

__all__ = ["EW_STALENESS_BARS", "FIB_ZONE", "ExposureIndex", "FibLeg"]

EW_STALENESS_BARS = 12
"""Elliott 종료 사건을 '최근' 으로 보는 기간: 12 개 4h 봉(2일). 문서 35 에서 고정."""
FIB_ZONE = (Decimal("0.382"), Decimal("0.618"))


@dataclass(frozen=True, slots=True)
class FibLeg:
    direction: int
    """+1: A(저점)→B(고점) 상승 다리, −1: 하락 다리."""
    a: Decimal
    b: Decimal

    def retracement(self, price: Decimal) -> Decimal:
        return (self.b - price) / (self.b - self.a)


class ExposureIndex:
    """발동·결과 목록을 시간순으로 색인해 임의 시각의 상태를 인과적으로 조회한다."""

    def __init__(self, triggers: Sequence[Trigger], results: Sequence[TriggerResult]) -> None:
        if len(triggers) != len(results):
            raise ValueError("발동과 결과 수가 다르다")
        order = sorted(range(len(triggers)), key=lambda k: triggers[k].close_ms)
        self._closes = [triggers[k].close_ms for k in order]
        self._triggers = [triggers[k] for k in order]
        self._results = [results[k] for k in order]
        self._event_closes = [r.close_ms for r in self._results if r.is_event]
        self._event_dirs = [r.direction for r in self._results if r.is_event]

    def elliott_end(self, at_ms: int) -> int | None:
        """``at_ms`` 이전 12 봉 안의 가장 최근 impulse 종료 사건의 방향 (없으면 None)."""
        k = bisect_left(self._event_closes, at_ms) - 1
        if k < 0 or at_ms - self._event_closes[k] > EW_STALENESS_BARS * Timeframe.H4.ms:
            return None
        return self._event_dirs[k]

    def fib_leg(self, at_ms: int) -> FibLeg | None:
        """``at_ms`` 이전 마지막 발동 시점의 가장 최근 완성 다리."""
        k = bisect_left(self._closes, at_ms) - 1
        if k < 0:
            return None
        segment = self._triggers[k].segment
        end = segment[-1]
        start = next(
            (p for p in reversed(segment[:-1]) if p.four_hour_visible and p.kind is not end.kind),
            None,
        )
        if start is None or start.price == end.price:
            return None
        return FibLeg(1 if end.kind is SwingKind.HIGH else -1, start.price, end.price)

    def in_fib_zone(self, at_ms: int, price: Decimal, direction: int) -> bool:
        """``direction`` 방향 다리의 되돌림 구간에 가격이 있는가 (그 방향으로 재진입하는 자리)."""
        leg = self.fib_leg(at_ms)
        if leg is None or leg.direction != direction:
            return False
        r = leg.retracement(price)
        return FIB_ZONE[0] <= r <= FIB_ZONE[1]
