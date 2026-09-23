"""시계열 추세(TSMOM) 특징 — 일봉 종가만 사용하는 순수 함수 (T/v2, docs/research/15·16).

연구 시뮬레이터(`backtest/trend.py`, float)와 같은 정의의 Decimal 구현. 일치는 테스트로 확인한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

__all__ = ["DAYS_PER_YEAR", "realized_vol_annual", "trend_direction"]

DAYS_PER_YEAR = 365
"""암호화폐는 매일 거래된다. 연환산에 365일을 쓴다."""

_ZERO = Decimal(0)


def _sign(x: Decimal) -> int:
    return (x > 0) - (x < 0)


def trend_direction(closes: Sequence[Decimal], lookbacks: Sequence[int]) -> Decimal | None:
    """마지막 종가 기준 lookback별 수익률 부호의 평균 ∈ [−1, 1]. 이력이 부족하면 None."""
    if not lookbacks or len(closes) < max(lookbacks) + 1:
        return None
    last = closes[-1]
    signs = [_sign(last / closes[-1 - k] - 1) for k in lookbacks]
    return Decimal(sum(signs)) / len(signs)


def realized_vol_annual(closes: Sequence[Decimal], days: int) -> Decimal | None:
    """직전 ``days``개 일간 로그수익률의 표본 SD(ddof=1) × √365. 이력이 부족하면 None."""
    if days < 2 or len(closes) < days + 1:
        return None
    steps = [(closes[-k] / closes[-k - 1]).ln() for k in range(1, days + 1)]
    mean = sum(steps, _ZERO) / days
    variance = sum(((x - mean) ** 2 for x in steps), _ZERO) / (days - 1)
    return variance.sqrt() * Decimal(DAYS_PER_YEAR).sqrt()
