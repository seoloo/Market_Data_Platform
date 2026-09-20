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
    """

    timeframe: Timeframe = Timeframe.M15
    rsi_period: int = 14
    name: str = "t1_pullback"

    @property
    def warmup_bars(self) -> int:
        """Wilder 평활도 초기 시드 영향이 남으므로 여유를 둔다."""
        return self.rsi_period * 4 + 1

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

        return FeatureResult(
            name=self.name,
            timeframe=self.timeframe,
            available=True,
            raw=current,
            normalized=float((current - _RSI_MID) / _RSI_MID),
            detail={"rsi": current},
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
