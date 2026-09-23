"""A1/v1 causal 5분봉 신호. 연구 측정과 실거래 판단이 같은 정의를 사용한다.

성과 열람 전 정정: 레인지 돌파는 직전 6시간의 봉 고가/저가와 현재 종가를 비교한다.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.types import Candle

__all__ = [
    "BARS_PER_HOUR",
    "CONTRACTION_QUANTILE",
    "RANGE_BARS",
    "RANK_WINDOW_BARS",
    "TREND_BARS",
    "VOL_WINDOW_BARS",
    "A1Observation",
    "Trigger",
    "breakout_triggers",
    "contraction_flags",
    "latest_a1_observation",
    "rolling_vol",
    "scan_a1",
]

BARS_PER_HOUR = 12

VOL_WINDOW_BARS = 24 * BARS_PER_HOUR
"""실현변동성을 재는 창 (24시간). 더 짧으면 5분 잡음이 지배한다."""

RANK_WINDOW_BARS = 30 * 24 * BARS_PER_HOUR
"""변동성 순위를 매기는 causal 창 (30일).

절대 임계값을 쓰지 않는 이유: BTC 변동성 수준은 연도별로 수 배 차이가 난다.
2021 의 '조용함' 과 2024 의 '조용함' 은 다른 값이다.
"""

CONTRACTION_QUANTILE = 0.30
TREND_BARS = 4 * BARS_PER_HOUR * 50
"""HTF 방향 판단 구간 — 4시간봉 50개 상당."""
RANGE_BARS = 6 * BARS_PER_HOUR
"""돌파를 재는 직전 레인지 (6시간)."""


@dataclass(frozen=True)
class Trigger:
    """진입 시점과 의도 방향.

    ``direction`` 은 +1 롱 / −1 숏이다. **선행수익 SD 계산에는 쓰지 않는다** —
    부호를 곱해도 분산은 사실상 같고(평균이 작으므로), 방향별 수익을 들여다보면
    사전등록 전에 효과를 엿보는 것이 된다.
    """

    index: int
    direction: int


def rolling_vol(closes: list[float]) -> list[float | None]:
    """직전 ``VOL_WINDOW_BARS`` 5분 수익률의 표준편차. causal."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= VOL_WINDOW_BARS:
        return out
    steps = [0.0] * len(closes)
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        steps[i] = (closes[i] - prev) / prev if prev > 0.0 else 0.0
    n = VOL_WINDOW_BARS
    total = sum(steps[1 : n + 1])
    total_sq = sum(v * v for v in steps[1 : n + 1])
    for i in range(n, len(closes)):
        out[i] = max(total_sq / n - (total / n) ** 2, 0.0) ** 0.5
        if i + 1 < len(closes):
            leaving, entering = steps[i - n + 1], steps[i + 1]
            total += entering - leaving
            total_sq += entering * entering - leaving * leaving
    return out


def contraction_flags(vol: list[float | None]) -> list[bool]:
    """각 시점의 변동성이 직전 30일 분포의 하위 분위인가. causal."""
    flags = [False] * len(vol)
    history: deque[float] = deque()
    ordered: list[float] = []
    for i, value in enumerate(vol):
        if value is None:
            continue
        if len(history) >= RANK_WINDOW_BARS:
            below = bisect_left(ordered, value)
            flags[i] = below / len(ordered) <= CONTRACTION_QUANTILE
            expired = history.popleft()
            ordered.pop(bisect_left(ordered, expired))
        history.append(value)
        insort(ordered, value)
    return flags


def breakout_triggers(
    closes: list[float], highs: list[float], lows: list[float], flags: list[bool]
) -> list[Trigger]:
    """후보 A — 수축 국면에서 직전 6시간 고가/저가를 추세 방향으로 돌파.

    같은 돌파를 여러 번 세지 않도록 트리거 후 ``RANGE_BARS`` 동안 잠근다.

    추세는 **돌파 봉 직전**으로 판정한다. 돌파 봉 자신을 넣으면 큰 역방향 스파이크가
    추세 판정까지 뒤집어, "추세 방향 돌파" 가 아니라 "아무 급변" 을 잡게 된다.
    """
    if not (len(closes) == len(highs) == len(lows) == len(flags)):
        raise ValueError("종가·고가·저가·수축 플래그의 길이가 다르다")
    out: list[Trigger] = []
    blocked_until = 0
    for i in range(max(TREND_BARS, RANGE_BARS) + 1, len(closes)):
        if i < blocked_until or not flags[i]:
            continue
        up = closes[i - 1] > closes[i - 1 - TREND_BARS]
        prior_high = max(highs[i - RANGE_BARS : i])
        prior_low = min(lows[i - RANGE_BARS : i])
        if closes[i] > prior_high if up else closes[i] < prior_low:
            out.append(Trigger(index=i, direction=1 if up else -1))
            blocked_until = i + RANGE_BARS
    return out


@dataclass(frozen=True, slots=True)
class A1Observation:
    """이미 마감된 트리거 봉의 정보. 손익/미래 가격은 포함하지 않는다."""

    as_of_ms: int
    close: Decimal
    direction: int
    sigma_5m: float


def scan_a1(
    candles: Sequence[Candle], *, control_without_contraction: bool = False
) -> dict[int, A1Observation]:
    """전체 마감봉을 한번 훑어 트리거만 보관한다. 연구·라이브 정의가 같다."""
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    vol = rolling_vol(closes)
    if control_without_contraction:
        flags = [i >= VOL_WINDOW_BARS + RANK_WINDOW_BARS for i in range(len(vol))]
    else:
        flags = contraction_flags(vol)
    triggers = breakout_triggers(closes, highs, lows, flags)
    return {
        candles[t.index].close_ms: A1Observation(
            as_of_ms=candles[t.index].close_ms,
            close=candles[t.index].close,
            direction=t.direction,
            sigma_5m=float(vol[t.index] or 0.0),
        )
        for t in triggers
    }


def latest_a1_observation(
    candles: Sequence[Candle], *, control_without_contraction: bool = False
) -> A1Observation | None:
    """스냅샷의 마지막 마감봉만 판정한다. 미래 봉에 영향받지 않는다."""
    if not candles:
        return None
    return scan_a1(candles, control_without_contraction=control_without_contraction).get(
        candles[-1].close_ms
    )
