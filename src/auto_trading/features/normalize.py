"""정규화 — look-ahead 누출의 실제 발생 지점.

지표 계산보다 **정규화가 미래 정보 누출이 실제로 일어나는 곳**이다 (02-architecture.md §4).
전체 표본으로 평균·표준편차를 구하면 백테스트 전 구간이 미래를 보게 된다.

이 모듈의 모든 함수는 **주어진 창의 마지막 원소를 '현재'로 보고 그 이전만 사용**한다.
미래 값을 뒤에 덧붙여도 앞 시점의 결과가 바뀌지 않는다 (테스트로 고정).

금지 사항:
    * ``MinMaxScaler`` / ``StandardScaler`` 를 전체 데이터에 fit
    * 전략 파라미터를 전체 기간 통계로 결정
    * 미래 봉을 참조하는 중심화 이동평균

**float 사용에 대하여**: z-score·백분위는 금액이나 수량이 아니라 무차원 통계량이므로
``Decimal`` 대신 ``float`` 를 쓴다 (AGENTS.md §4 의 Decimal 규칙은 금액·수량 대상).
다만 결정론을 지키기 위해 두 가지를 강제한다.

1. ``tanh`` 같은 초월함수를 쓰지 않는다 — libm 구현에 따라 최하위 비트가 달라질 수 있고,
   개발(Windows)과 운영(Linux)이 다른 값을 내면 Parity Test 가 무의미해진다
2. 출력은 :data:`SIGNAL_DECIMALS` 자리로 양자화한다
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from decimal import Decimal

__all__ = [
    "SIGNAL_DECIMALS",
    "causal_mean",
    "causal_percentile_rank",
    "causal_percentile_rank_series",
    "causal_stdev",
    "causal_zscore",
    "causal_zscore_series",
    "clip",
    "squash",
    "to_floats",
]

#: 신호 출력 양자화 자릿수.
#: 부동소수점 연산의 플랫폼 간 미세 차이가 하위 계층으로 전파되지 않게 한다.
SIGNAL_DECIMALS = 9


def to_floats(values: Sequence[float] | Sequence[Decimal]) -> list[float]:
    """``Decimal`` 또는 ``float`` 시퀀스를 ``float`` 리스트로 바꾼다.

    여기가 Decimal → float 경계다. 이 지점 이후는 통계 계산이므로 float 이 맞고,
    이전(가격·수량)은 Decimal 이어야 한다.
    """
    return [float(v) for v in values]


def _quantize(value: float) -> float:
    return round(value, SIGNAL_DECIMALS)


def clip(value: float, low: float, high: float) -> float:
    if low > high:
        raise ValueError(f"clip 범위가 뒤집혔다: low={low} high={high}")
    return max(low, min(high, value))


def squash(value: float, k: float = 1.5) -> float:
    """무한 범위 값을 ``[-1, +1]`` 로 압축한다.

    ``x / (k + |x|)`` 를 쓴다. ``tanh(x/k)`` 와 형태는 비슷하나 **사칙연산만 사용**하므로
    IEEE 754 상 모든 플랫폼에서 비트 단위로 같은 결과가 나온다.
    초월함수는 libm 구현에 따라 최하위 비트가 달라질 수 있어 쓰지 않는다.

    이상치가 신호를 지배하는 것도 함께 막는다 — 입력이 아무리 커도 출력은 1 을 넘지 않는다.

    Args:
        k: 클수록 완만해진다. 입력이 ``k`` 일 때 출력은 정확히 0.5 다.
    """
    if k <= 0:
        raise ValueError(f"k 는 0보다 커야 한다: {k}")
    return _quantize(value / (k + abs(value)))


# --------------------------------------------------------------- 단일 시점


def causal_mean(values: Sequence[float] | Sequence[Decimal], lookback: int) -> float | None:
    """마지막 ``lookback`` 개의 평균. 표본이 부족하면 ``None``.

    부족할 때 ``None`` 을 돌려주는 이유: 0 이나 부분 표본 값을 돌려주면 그것이
    "정상 신호" 로 오해되어 진입을 유발할 수 있다. warm-up 은 fail-closed 여야 한다.
    """
    _require_lookback(lookback)
    if len(values) < lookback:
        return None
    window = to_floats(values[-lookback:])
    return _quantize(sum(window) / lookback)


def causal_stdev(values: Sequence[float] | Sequence[Decimal], lookback: int) -> float | None:
    """마지막 ``lookback`` 개의 표본 표준편차 (ddof=1)."""
    _require_lookback(lookback, minimum=2)
    if len(values) < lookback:
        return None
    window = to_floats(values[-lookback:])
    mean = sum(window) / lookback
    variance = sum((x - mean) ** 2 for x in window) / (lookback - 1)
    return _quantize(variance**0.5)


def causal_zscore(values: Sequence[float] | Sequence[Decimal], lookback: int) -> float | None:
    """현재 값이 최근 ``lookback`` 구간 평균에서 몇 표준편차 떨어져 있는가.

    분산이 0 이면 (모든 값이 같으면) ``0.0`` 을 돌려준다.
    "변화가 없다" 는 "신호가 없다" 와 같으므로 0 이 올바른 값이다.
    """
    _require_lookback(lookback, minimum=2)
    if len(values) < lookback:
        return None
    window = to_floats(values[-lookback:])
    mean = sum(window) / lookback
    variance = sum((x - mean) ** 2 for x in window) / (lookback - 1)
    if variance <= 0.0:
        return 0.0
    return _quantize((window[-1] - mean) / variance**0.5)


def causal_percentile_rank(
    values: Sequence[float] | Sequence[Decimal], lookback: int
) -> float | None:
    """현재 값이 최근 ``lookback`` 구간에서 차지하는 백분위 (0.0 ~ 1.0).

    동점은 중간 순위(midrank)로 처리한다. 모든 값이 같으면 0.5 다.
    ATR 변동성 국면 판정(B2)에 쓰인다.
    """
    _require_lookback(lookback)
    if len(values) < lookback:
        return None
    window = to_floats(values[-lookback:])
    current = window[-1]
    below = sum(1 for x in window if x < current)
    equal = sum(1 for x in window if x == current)
    return _quantize((below + 0.5 * equal) / lookback)


# --------------------------------------------------------------- 시계열


def causal_zscore_series(
    values: Sequence[float] | Sequence[Decimal], lookback: int
) -> list[float | None]:
    """각 시점의 causal z-score. 표본이 부족한 앞부분은 ``None``.

    feature 상관행렬(06 §2.3)과 백테스트 분석에 쓴다.
    누적합으로 O(n) 에 계산한다 — 3.5백만 봉을 매번 O(n·lookback) 으로 돌면 끝나지 않는다.
    """
    _require_lookback(lookback, minimum=2)
    data = to_floats(values)
    n = len(data)
    result: list[float | None] = [None] * n
    if n < lookback:
        return result

    running_sum = sum(data[:lookback])
    running_sq = sum(x * x for x in data[:lookback])

    for i in range(lookback - 1, n):
        if i >= lookback:
            entering = data[i]
            leaving = data[i - lookback]
            running_sum += entering - leaving
            running_sq += entering * entering - leaving * leaving
        mean = running_sum / lookback
        # 누적합 방식은 부동소수점 상쇄 오차가 생길 수 있으므로 음수 분산을 0 으로 막는다
        variance = max(0.0, (running_sq - lookback * mean * mean) / (lookback - 1))
        result[i] = 0.0 if variance <= 0.0 else _quantize((data[i] - mean) / variance**0.5)
    return result


def causal_percentile_rank_series(
    values: Sequence[float] | Sequence[Decimal], lookback: int
) -> list[float | None]:
    """각 시점의 causal 백분위. 정렬된 창을 유지해 O(n log n) 에 계산한다.

    단순 구현은 시점마다 창 전체를 훑어 O(n·lookback) 이 된다.
    1년 룩백(8,760)에 6만 봉이면 5억 회 비교라 백테스트가 실용적이지 않다.
    """
    _require_lookback(lookback)
    data = to_floats(values)
    n = len(data)
    result: list[float | None] = [None] * n
    if n < lookback:
        return result

    window: list[float] = sorted(data[:lookback])
    for i in range(lookback - 1, n):
        if i >= lookback:
            leaving = data[i - lookback]
            del window[bisect.bisect_left(window, leaving)]
            bisect.insort(window, data[i])
        current = data[i]
        below = bisect.bisect_left(window, current)
        equal = bisect.bisect_right(window, current) - below
        result[i] = _quantize((below + 0.5 * equal) / lookback)
    return result


def _require_lookback(lookback: int, *, minimum: int = 1) -> None:
    if lookback < minimum:
        raise ValueError(f"lookback 은 {minimum} 이상이어야 한다: {lookback}")
