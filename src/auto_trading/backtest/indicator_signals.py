"""지표 방향 신호 — 선물 방향 후보 비교 측정용 (docs/research/46). 순수 함수, I/O 없음.

모든 함수는 **causal** 하다. 인덱스 ``i`` 의 값은 ``0..i`` 봉만 쓴다.
표본이 부족한 앞부분은 ``None`` 이다 (0 으로 채우면 '중립 신호'로 오해된다).

방향은 +1 (롱) · 0 (쉼) · −1 (숏). 규칙과 기간은 교과서 기본값이며 수익을 보고 고르지 않았다
(46 §1). 가격 차원의 양이지만 연구 측정 전용이라 ``backtest/trend.py`` 와 같이 float 를 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "INDICATOR_RULES",
    "IndicatorRule",
    "direction_series",
    "ema",
    "ichimoku_cloud",
    "macd",
    "rsi",
    "sma",
]


def _require(period: int, minimum: int = 1) -> None:
    if period < minimum:
        raise ValueError(f"period 는 {minimum} 이상이어야 한다: {period}")


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """단순이동평균."""
    _require(period)
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1 : i + 1]) / period
    return out


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """지수이동평균. 첫 값은 SMA(period) 시드 (``features/indicators.ema_series`` 와 같은 정의)."""
    _require(period)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    cur = sum(values[:period]) / period
    out[period - 1] = cur
    for i in range(period, len(values)):
        cur = (values[i] - cur) * k + cur
        out[i] = cur
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """RSI (Wilder 평활), 0~100. 변화가 전혀 없으면 50."""
    _require(period, 2)
    out: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return out
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gain = sum(max(c, 0.0) for c in changes[:period]) / period
    loss = sum(max(-c, 0.0) for c in changes[:period]) / period
    out[period] = _rsi_value(gain, loss)
    for i in range(period + 1, len(values)):
        c = changes[i - 1]
        gain = (gain * (period - 1) + max(c, 0.0)) / period
        loss = (loss * (period - 1) + max(-c, 0.0)) / period
        out[i] = _rsi_value(gain, loss)
    return out


def _rsi_value(gain: float, loss: float) -> float:
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None]]:
    """(MACD 선 = EMA fast − EMA slow, 시그널 선 = MACD 선의 EMA signal)."""
    if fast >= slow:
        raise ValueError(f"fast < slow 여야 한다: {fast}, {slow}")
    ef, es = ema(values, fast), ema(values, slow)
    line: list[float | None] = [
        a - b if a is not None and b is not None else None for a, b in zip(ef, es, strict=True)
    ]
    start = slow - 1
    sig_tail = ema([x for x in line[start:] if x is not None], signal)
    sig: list[float | None] = [None] * start
    sig.extend(sig_tail)
    return line, sig


def _midpoint(highs: Sequence[float], lows: Sequence[float], i: int, period: int) -> float | None:
    if i < period - 1:
        return None
    return (max(highs[i - period + 1 : i + 1]) + min(lows[i - period + 1 : i + 1])) / 2


def ichimoku_cloud(
    highs: Sequence[float],
    lows: Sequence[float],
    tenkan: int = 9,
    kijun: int = 26,
    span_b: int = 52,
    shift: int = 26,
) -> list[tuple[float, float] | None]:
    """봉 ``i`` 에 그려지는 구름 (위, 아래).

    선행스팬 A·B 는 ``i − shift`` 봉까지로 계산한 값이다 — 차트가 앞으로 밀어 그리는 것을
    그 자리에서 읽으므로 미래 자료가 없다.
    """
    if len(highs) != len(lows):
        raise ValueError("고가·저가 길이가 다르다")
    for p in (tenkan, kijun, span_b, shift):
        _require(p)
    out: list[tuple[float, float] | None] = [None] * len(highs)
    for i in range(shift, len(highs)):
        j = i - shift
        t, k, b = (_midpoint(highs, lows, j, p) for p in (tenkan, kijun, span_b))
        if t is None or k is None or b is None:
            continue
        a = (t + k) / 2
        out[i] = (max(a, b), min(a, b))
    return out


# ---------------------------------------------------------------- 방향 규칙


def _rsi_trend(
    closes: Sequence[float], _h: Sequence[float], _l: Sequence[float]
) -> list[int | None]:
    return [None if r is None else _sign(r - 50) for r in rsi(closes, 14)]


def _rsi_reversion(
    closes: Sequence[float], _h: Sequence[float], _l: Sequence[float]
) -> list[int | None]:
    """RSI < 30 에서 롱, 50 이상으로 돌아오면 닫는다. > 70 에서 숏, 50 이하로 오면 닫는다."""
    out: list[int | None] = []
    state = 0
    for r in rsi(closes, 14):
        if r is None:
            out.append(None)
            continue
        if (state == 1 and r >= 50) or (state == -1 and r <= 50):
            state = 0
        if state == 0:
            state = 1 if r < 30 else -1 if r > 70 else 0
        out.append(state)
    return out


def _macd_hist(
    closes: Sequence[float], _h: Sequence[float], _l: Sequence[float]
) -> list[int | None]:
    line, sig = macd(closes)
    return [
        None if a is None or b is None else _sign(a - b) for a, b in zip(line, sig, strict=True)
    ]


def _cross(fast: int, slow: int, kind: str) -> Callable[..., list[int | None]]:
    avg = sma if kind == "sma" else ema

    def rule(closes: Sequence[float], _h: Sequence[float], _l: Sequence[float]) -> list[int | None]:
        f, s = avg(closes, fast), avg(closes, slow)
        return [None if a is None or b is None else _sign(a - b) for a, b in zip(f, s, strict=True)]

    return rule


def _ichimoku(
    closes: Sequence[float], highs: Sequence[float], lows: Sequence[float]
) -> list[int | None]:
    out: list[int | None] = []
    for c, cloud in zip(closes, ichimoku_cloud(highs, lows), strict=True):
        if cloud is None:
            out.append(None)
        else:
            top, bottom = cloud
            out.append(1 if c > top else -1 if c < bottom else 0)
    return out


@dataclass(frozen=True, slots=True)
class IndicatorRule:
    key: str
    family: str
    label: str
    rule: Callable[[Sequence[float], Sequence[float], Sequence[float]], list[int | None]]


INDICATOR_RULES: tuple[IndicatorRule, ...] = (
    IndicatorRule("I1", "RSI", "RSI(14) 50 기준 추세", _rsi_trend),
    IndicatorRule("I2", "RSI", "RSI(14) 30/70 역추세 (50 복귀 청산)", _rsi_reversion),
    IndicatorRule("I3", "MACD", "MACD(12,26,9) 히스토그램 부호", _macd_hist),
    IndicatorRule("I4", "MA", "SMA 50/200 교차", _cross(50, 200, "sma")),
    IndicatorRule("I5", "EMA", "EMA 20/50 교차", _cross(20, 50, "ema")),
    IndicatorRule("I6", "일목", "일목 구름(9,26,52) 위·아래, 안은 쉼", _ichimoku),
)
"""46 §1 에 고정한 후보. 가족마다 하나(RSI 만 해석이 둘로 갈려 둘)."""


def direction_series(
    rule: IndicatorRule, closes: Sequence[float], highs: Sequence[float], lows: Sequence[float]
) -> list[int | None]:
    if not len(closes) == len(highs) == len(lows):
        raise ValueError("종가·고가·저가 길이가 다르다")
    return rule.rule(closes, highs, lows)
