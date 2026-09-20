"""지표 계산 — 순수 함수.

모든 함수는 **causal** 하다. 인덱스 ``i`` 의 값은 ``0..i`` 만 사용한다.
표본이 부족한 앞부분은 ``None`` 을 돌려준다 (0 을 돌려주면 정상값으로 오해된다).

**Decimal 을 쓰는 이유**: EMA·ATR 은 가격 차원의 양이다. 정규화 이전까지는
가격 정밀도를 유지한다 (AGENTS.md §4). 사칙연산만 쓰므로 Decimal 연산은
플랫폼과 무관하게 결정론적이다.

RSI 는 무차원이지만 같은 경로에서 계산되므로 Decimal 로 통일한다.
float 변환은 ``normalize.to_floats`` 경계에서만 일어난다.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from auto_trading.core.types import Candle

__all__ = [
    "atr_series",
    "ema_series",
    "ema_warmup_bars",
    "rsi_series",
    "sma_series",
    "true_range_series",
]

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)

#: EMA 가 초기 시드의 영향에서 벗어나는 데 필요한 배수.
#: EMA 는 모든 과거에 의존하므로 period 개만으로는 값이 시드에 좌우된다.
EMA_CONVERGENCE_MULTIPLE = 4


def ema_warmup_bars(period: int) -> int:
    """EMA 가 신뢰할 만해지는 최소 봉 수.

    ``period`` 개만 있으면 값은 나오지만 초기 시드(SMA)의 영향이 크게 남는다.
    전략이 쓰는 값은 수렴한 뒤여야 한다.
    """
    return period * EMA_CONVERGENCE_MULTIPLE


def _require_period(period: int, minimum: int = 1) -> None:
    if period < minimum:
        raise ValueError(f"period 는 {minimum} 이상이어야 한다: {period}")


def sma_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """단순이동평균. 누적합으로 O(n)."""
    _require_period(period)
    n = len(values)
    result: list[Decimal | None] = [None] * n
    if n < period:
        return result

    total = sum(values[:period], start=_ZERO)
    result[period - 1] = total / period
    for i in range(period, n):
        total += values[i] - values[i - period]
        result[i] = total / period
    return result


def ema_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """지수이동평균. 첫 값은 SMA(period) 로 시드한다.

    시드 방식은 구현마다 다르다(첫 값 그대로 쓰기도 한다). SMA 시드를 쓰는 이유는
    초기 왜곡이 가장 작기 때문이며, 어느 쪽이든 :func:`ema_warmup_bars` 만큼
    지나면 차이가 사라진다.
    """
    _require_period(period)
    n = len(values)
    result: list[Decimal | None] = [None] * n
    if n < period:
        return result

    multiplier = Decimal(2) / Decimal(period + 1)
    current = sum(values[:period], start=_ZERO) / period
    result[period - 1] = current
    for i in range(period, n):
        current = (values[i] - current) * multiplier + current
        result[i] = current
    return result


def true_range_series(candles: Sequence[Candle]) -> list[Decimal | None]:
    """True Range.

    ``max(high-low, |high - prev_close|, |low - prev_close|)``

    첫 봉은 직전 종가가 없으므로 ``high - low`` 를 쓴다.
    갭(전 봉 종가와 이번 봉 범위가 떨어진 경우)을 반영하는 것이 TR 의 목적이다.
    """
    result: list[Decimal | None] = []
    previous_close: Decimal | None = None
    for candle in candles:
        if previous_close is None:
            result.append(candle.high - candle.low)
        else:
            result.append(
                max(
                    candle.high - candle.low,
                    abs(candle.high - previous_close),
                    abs(candle.low - previous_close),
                )
            )
        previous_close = candle.close
    return result


def atr_series(candles: Sequence[Candle], period: int) -> list[Decimal | None]:
    """Average True Range (Wilder 평활).

    Wilder 평활: ``atr[i] = (atr[i-1] * (period-1) + tr[i]) / period``
    """
    _require_period(period)
    n = len(candles)
    result: list[Decimal | None] = [None] * n
    if n < period:
        return result

    true_ranges = true_range_series(candles)
    seed_values = [tr for tr in true_ranges[:period] if tr is not None]
    current = sum(seed_values, start=_ZERO) / period
    result[period - 1] = current

    for i in range(period, n):
        tr = true_ranges[i]
        if tr is None:  # pragma: no cover - true_range_series 는 None 을 내지 않는다
            continue
        current = (current * (period - 1) + tr) / period
        result[i] = current
    return result


def rsi_series(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """RSI (Wilder 평활). 0 ~ 100.

    경계:
        * 하락이 전혀 없으면 100
        * 상승이 전혀 없으면 0
        * 변화가 전혀 없으면 50 (중립) — 상승도 하락도 없으므로 과매수/과매도가 아니다
    """
    _require_period(period, minimum=2)
    n = len(values)
    result: list[Decimal | None] = [None] * n
    if n < period + 1:
        return result

    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for i in range(1, n):
        change = values[i] - values[i - 1]
        gains.append(change if change > _ZERO else _ZERO)
        losses.append(-change if change < _ZERO else _ZERO)

    avg_gain = sum(gains[:period], start=_ZERO) / period
    avg_loss = sum(losses[:period], start=_ZERO) / period
    result[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, n):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[i] = _rsi_from_averages(avg_gain, avg_loss)
    return result


def _rsi_from_averages(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    if avg_loss == _ZERO:
        return _HUNDRED if avg_gain > _ZERO else Decimal(50)
    if avg_gain == _ZERO:
        return _ZERO
    rs = avg_gain / avg_loss
    return _HUNDRED - (_HUNDRED / (Decimal(1) + rs))
