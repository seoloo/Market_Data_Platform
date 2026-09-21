"""T1 / T2 — 진입 타이밍.

사람이 읽는 설명: docs/STRATEGY-CATALOG.md §2 T1, T2

가설 H1 의 "15분 되돌림 이후 추세 방향으로 재개" 에 대응한다.

* **T1 (15분 RSI)** — 되돌림이 얼마나 깊은가
* **T2 (5분 모멘텀)** — 되돌림이 **끝났는가**

T1 은 방향에 따라 해석이 정반대다 (롱 편향에선 RSI 가 낮아야 좋고, 숏 편향에선 높아야 좋다).
그래서 여기서는 RSI 값만 내고, 방향을 아는 ``strategy/signals.py`` 가 해석한다
(features/base.py 참조).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import TimeframeSeries
from auto_trading.features.base import FeatureResult
from auto_trading.features.indicators import rsi_series
from auto_trading.features.normalize import causal_zscore

__all__ = ["MomentumFeature", "PullbackFeature"]

_ZERO = Decimal(0)
_RSI_MID = Decimal(50)


@dataclass(frozen=True, slots=True)
class PullbackFeature:
    """T1 — 15분 RSI(14). 추세 중 되돌림의 깊이.

    ``normalized`` 는 중립(50) 기준 편차를 ``[-1, +1]`` 로 옮긴 값이다
    (RSI 0 → -1, 50 → 0, 100 → +1). 방향 해석은 하지 않는다.

    ``detail["history"]`` 에 최근 ``history_bars`` 개의 RSI 를 **오름차순**으로 담는다.
    T1 은 "지금 눌려 있는가" 가 아니라 **"최근에 눌렸다가 풀렸는가"** 라는 시간적
    조건이므로 (D14), 해석하는 쪽이 현재값 하나가 아니라 창을 봐야 한다.
    측정과 해석의 분리는 그대로다 — 여기서는 창을 **제공만** 한다.
    """

    timeframe: Timeframe = Timeframe.M15
    rsi_period: int = 14
    history_bars: int = 16
    name: str = "t1_pullback"

    def __post_init__(self) -> None:
        if self.history_bars < 1:
            raise ValueError(f"history_bars 는 1 이상이어야 한다: {self.history_bars}")

    @property
    def warmup_bars(self) -> int:
        """Wilder 평활도 초기 시드 영향이 남으므로 여유를 둔다.

        setup 판정에 쓸 창(``history_bars``)까지 **전부 RSI 가 채워져야** 한다.
        창이 부족한 상태로 판정하면 "되돌림이 없었다" 와 "아직 모른다" 가 섞인다.
        """
        return self.rsi_period * 4 + self.history_bars

    def compute(self, series: TimeframeSeries) -> FeatureResult:
        if len(series) < self.warmup_bars:
            return FeatureResult.unavailable(
                self.name,
                self.timeframe,
                f"warm-up 미충족: {len(series)}/{self.warmup_bars}봉",
            )

        rsis = rsi_series(list(series.closes), self.rsi_period)
        current = rsis[-1]
        if current is None:  # pragma: no cover - warm-up 검사로 도달 불가
            return FeatureResult.unavailable(self.name, self.timeframe, "RSI 계산 실패")

        window = rsis[-self.history_bars :]
        if any(value is None for value in window):  # pragma: no cover - warm-up 으로 차단
            return FeatureResult.unavailable(self.name, self.timeframe, "RSI 창 미충족")

        return FeatureResult(
            name=self.name,
            timeframe=self.timeframe,
            available=True,
            raw=current,
            normalized=float((current - _RSI_MID) / _RSI_MID),
            detail={"rsi": current, "history": tuple(cast("list[Decimal]", window))},
        )


@dataclass(frozen=True, slots=True)
class MomentumFeature:
    """T2 — 최근 N봉 수익률의 causal z-score. 되돌림 종료 확인.

    수익률 자체를 쓰지 않고 z-score 를 쓰는 이유: 같은 0.3% 움직임도 조용한 장과
    변동성 큰 장에서 의미가 다르다.
    """

    timeframe: Timeframe = Timeframe.M5
    return_bars: int = 6
    zscore_lookback: int = 288
    name: str = "t2_momentum"

    @property
    def warmup_bars(self) -> int:
        return self.return_bars + self.zscore_lookback

    def compute(self, series: TimeframeSeries) -> FeatureResult:
        if len(series) < self.warmup_bars:
            return FeatureResult.unavailable(
                self.name,
                self.timeframe,
                f"warm-up 미충족: {len(series)}/{self.warmup_bars}봉",
            )

        closes = series.closes
        returns: list[Decimal] = []
        for i in range(self.return_bars, len(closes)):
            base = closes[i - self.return_bars]
            if base == _ZERO:
                continue
            returns.append((closes[i] - base) / base)

        if len(returns) < self.zscore_lookback:
            return FeatureResult.unavailable(
                self.name,
                self.timeframe,
                f"z-score 표본 부족: {len(returns)}/{self.zscore_lookback}",
            )

        z = causal_zscore(returns, self.zscore_lookback)
        if z is None:  # pragma: no cover - 위에서 이미 검사
            return FeatureResult.unavailable(self.name, self.timeframe, "정규화 실패")

        return FeatureResult(
            name=self.name,
            timeframe=self.timeframe,
            available=True,
            raw=returns[-1],
            normalized=z,
            detail={
                "return_pct": returns[-1],
                "return_bars": self.return_bars,
                "zscore": z,
            },
        )
