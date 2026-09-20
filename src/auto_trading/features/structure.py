"""가격 구조 — ZigZag 스윙, HH/HL/LH/LL, 피보나치 되돌림.

사람이 읽는 설명: docs/STRATEGY-CATALOG.md §2 S (손절 위치 규칙)

**이 모듈의 존재 이유는 repainting 차단이다.**

일반적인 ZigZag 구현은 새 극값이 나오면 이전 스윙을 **덮어쓴다**. 그러면 같은 시점에 대해
어제 본 스윙과 오늘 본 스윙이 달라지고, 백테스트가 "그때는 알 수 없던 스윙"을 쓰게 된다.
이것이 12-critique.md §3 에서 Elliott 파동 카운팅을 MVP 에서 뺀 이유이며,
남긴 부분(스윙 구조)에도 똑같이 적용되는 문제다.

우리 규칙:

1. 스윙은 **확정되면 절대 바뀌지 않는다** (덮어쓰기 없음)
2. 모든 스윙은 ``confirmed_at_ms`` 를 가진다 — 극값이 발생한 시각이 아니라
   **되돌림 임계를 넘어 그것이 극값이었음을 알게 된 시각**
3. ``as_of`` 시점의 전략은 ``confirmed_at_ms <= as_of`` 인 스윙만 본다

.. warning::
   극값 시각(``at_ms``)과 확정 시각(``confirmed_at_ms``)은 **항상 다르다.**
   확정은 본질적으로 지연된다 — 고점인지 알려면 가격이 임계만큼 내려와야 한다.
   이 지연을 모델링하지 않으면 미래를 보는 것이다.

스윙 용도는 **손절 위치 계산**이다 (05-risk-engine.md §3.1). 점수 요소로 쓰지 않는다 —
되돌림 깊이는 이미 T1(RSI)이 재고 있어 중복 계산이 되기 때문이다 (06 §2.3 상관 제약).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from auto_trading.core.types import Candle

__all__ = [
    "StructureLabel",
    "Swing",
    "SwingKind",
    "SwingSeries",
    "detect_swings",
]

_ZERO = Decimal(0)


class SwingKind(StrEnum):
    HIGH = "high"
    LOW = "low"

    @property
    def opposite(self) -> SwingKind:
        return SwingKind.LOW if self is SwingKind.HIGH else SwingKind.HIGH


class StructureLabel(StrEnum):
    """직전 두 스윙 쌍으로 판정한 구조."""

    HIGHER_HIGH = "HH"
    HIGHER_LOW = "HL"
    LOWER_HIGH = "LH"
    LOWER_LOW = "LL"


@dataclass(frozen=True, slots=True)
class Swing:
    """확정된 스윙 하나. 확정 후에는 값이 바뀌지 않는다."""

    kind: SwingKind
    price: Decimal
    at_ms: int
    """극값이 발생한 봉의 시작 시각."""
    confirmed_at_ms: int
    """그것이 극값이었음을 알게 된 봉의 마감 시각. 항상 ``at_ms`` 보다 늦다."""

    def __post_init__(self) -> None:
        if self.confirmed_at_ms < self.at_ms:
            raise ValueError(
                f"확정 시각이 극값 시각보다 빠르다: {self.confirmed_at_ms} < {self.at_ms}. "
                "확정은 본질적으로 지연된다."
            )
        if self.price <= _ZERO:
            raise ValueError(f"스윙 가격이 0 이하: {self.price}")

    @property
    def confirmation_lag_ms(self) -> int:
        """극값에서 확정까지 걸린 시간. 이만큼은 그 스윙을 쓸 수 없었다."""
        return self.confirmed_at_ms - self.at_ms


@dataclass(frozen=True, slots=True)
class SwingSeries:
    """확정 시각 순으로 정렬된 스윙 목록."""

    swings: tuple[Swing, ...]
    threshold_pct: Decimal

    def __len__(self) -> int:
        return len(self.swings)

    def confirmed_as_of(self, as_of_ms: int) -> SwingSeries:
        """``as_of_ms`` 시점에 **알 수 있었던** 스윙만 남긴다.

        전략은 반드시 이것을 거쳐 스윙에 접근한다. 원본 목록을 그대로 쓰면
        미래에 확정될 스윙을 보게 된다.
        """
        return SwingSeries(
            swings=tuple(s for s in self.swings if s.confirmed_at_ms <= as_of_ms),
            threshold_pct=self.threshold_pct,
        )

    def of_kind(self, kind: SwingKind) -> tuple[Swing, ...]:
        return tuple(s for s in self.swings if s.kind is kind)

    def last(self, kind: SwingKind | None = None) -> Swing | None:
        """가장 최근 확정 스윙. ``kind`` 를 주면 그 종류 중 최근."""
        candidates = self.swings if kind is None else self.of_kind(kind)
        return candidates[-1] if candidates else None

    def labels(self) -> tuple[StructureLabel, ...]:
        """직전 두 고점·두 저점을 비교한 구조 라벨.

        상승 구조는 ``(HH, HL)``, 하락 구조는 ``(LH, LL)`` 이 나온다.
        한쪽만 나오거나 섞이면 구조가 불명확하다는 뜻이다.
        """
        result: list[StructureLabel] = []
        highs = self.of_kind(SwingKind.HIGH)
        lows = self.of_kind(SwingKind.LOW)
        if len(highs) >= 2:
            result.append(
                StructureLabel.HIGHER_HIGH
                if highs[-1].price > highs[-2].price
                else StructureLabel.LOWER_HIGH
            )
        if len(lows) >= 2:
            result.append(
                StructureLabel.HIGHER_LOW
                if lows[-1].price > lows[-2].price
                else StructureLabel.LOWER_LOW
            )
        return tuple(result)

    def fib_retracement(self, price: Decimal) -> float | None:
        """직전 스윙 구간에서 현재 가격의 되돌림 비율 (0.0 ~ 1.0).

        0.0 = 추세 방향 끝(되돌림 없음), 1.0 = 구간 시작점까지 되돌림.
        고점·저점이 하나씩 없거나 구간 폭이 0 이면 ``None``.
        """
        high = self.last(SwingKind.HIGH)
        low = self.last(SwingKind.LOW)
        if high is None or low is None:
            return None
        span = high.price - low.price
        if span <= _ZERO:
            return None
        # 저점이 먼저면 상승 구간 — 고점에서 얼마나 내려왔는가
        if low.at_ms < high.at_ms:
            return float((high.price - price) / span)
        # 고점이 먼저면 하락 구간 — 저점에서 얼마나 올라왔는가
        return float((price - low.price) / span)


def detect_swings(
    candles: tuple[Candle, ...] | list[Candle],
    threshold_pct: Decimal,
) -> SwingSeries:
    """ZigZag 스윙을 검출한다. **확정된 것만** 돌려준다.

    고점은 가격이 그 고점에서 ``threshold_pct`` 만큼 내려온 봉에서 확정된다.
    저점은 반대다.

    확정된 스윙은 **절대 덮어쓰지 않는다.** 일반적인 ZigZag 는 새 극값이 나오면
    이전 스윙을 갱신해 repainting 을 일으킨다. 그 결과는 백테스트에서 쓸 수 없다.
    대신 아직 확정되지 않은 진행 중 극값은 **아예 내보내지 않는다** — 그것이
    극값인지 지금은 알 수 없기 때문이다.

    Args:
        threshold_pct: 되돌림 임계 (0.02 = 2%).

    Raises:
        ValueError: 임계값이 0 이하이거나 1 이상일 때.
    """
    if threshold_pct <= _ZERO or threshold_pct >= Decimal(1):
        raise ValueError(f"threshold_pct 는 0 과 1 사이여야 한다: {threshold_pct}")

    ordered = tuple(candles)
    if not ordered:
        return SwingSeries(swings=(), threshold_pct=threshold_pct)

    swings: list[Swing] = []
    #: 0 = 방향 미정, +1 = 고점 탐색 중, -1 = 저점 탐색 중
    direction = 0
    high_price, high_ms = ordered[0].high, ordered[0].open_ms
    low_price, low_ms = ordered[0].low, ordered[0].open_ms

    for candle in ordered:
        if candle.high > high_price:
            high_price, high_ms = candle.high, candle.open_ms
        if candle.low < low_price:
            low_price, low_ms = candle.low, candle.open_ms

        # 고점 확정: 추적 중인 고점에서 임계만큼 내려왔는가
        if direction >= 0 and high_price > _ZERO:
            drop = (high_price - candle.low) / high_price
            if drop >= threshold_pct:
                swings.append(
                    Swing(
                        kind=SwingKind.HIGH,
                        price=high_price,
                        at_ms=high_ms,
                        confirmed_at_ms=candle.close_ms,
                    )
                )
                direction = -1
                high_price, high_ms = candle.high, candle.open_ms
                low_price, low_ms = candle.low, candle.open_ms
                continue

        # 저점 확정: 추적 중인 저점에서 임계만큼 올라왔는가
        if direction <= 0 and low_price > _ZERO:
            rise = (candle.high - low_price) / low_price
            if rise >= threshold_pct:
                swings.append(
                    Swing(
                        kind=SwingKind.LOW,
                        price=low_price,
                        at_ms=low_ms,
                        confirmed_at_ms=candle.close_ms,
                    )
                )
                direction = 1
                high_price, high_ms = candle.high, candle.open_ms
                low_price, low_ms = candle.low, candle.open_ms

    return SwingSeries(swings=tuple(swings), threshold_pct=threshold_pct)
