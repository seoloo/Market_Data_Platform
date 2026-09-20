"""T3 — 5분 거래량 확인. **곱셈 계수** 로 쓰인다.

사람이 읽는 설명: docs/STRATEGY-CATALOG.md §2 T3

거래량은 **방향이 없다.** 가중합에 더하면 "거래량이 많으니까 매수" 라는
말이 안 되는 기여가 생긴다 (06-scoring-engine.md §2.2).

그래서 T1/T2 가 만든 신호에 **곱하는 확인 계수** 로만 쓴다. 범위 [0.6, 1.0].
가격만 움직이고 거래량이 없으면 가짜 움직임일 수 있으므로 신호를 약화시킨다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import TimeframeSeries
from auto_trading.features.base import FeatureResult
from auto_trading.features.indicators import sma_series

__all__ = ["VolumeConfirmFeature"]

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class VolumeConfirmFeature:
    """현재 거래량 / 거래량 SMA. ``normalized`` 는 곱할 확인 계수다."""

    timeframe: Timeframe = Timeframe.M5
    sma_period: int = 20
    strong_ratio: Decimal = Decimal("1.2")
    weak_ratio: Decimal = Decimal("0.8")
    confirm_strong: float = 1.0
    confirm_normal: float = 0.8
    confirm_weak: float = 0.6
    name: str = "t3_volume"

    @property
    def warmup_bars(self) -> int:
        return self.sma_period

    def compute(self, series: TimeframeSeries) -> FeatureResult:
        if len(series) < self.warmup_bars:
            return FeatureResult.unavailable(
                self.name,
                self.timeframe,
                f"warm-up 미충족: {len(series)}/{self.warmup_bars}봉",
            )

        volumes = list(series.volumes)
        averages = sma_series(volumes, self.sma_period)
        average = averages[-1]
        if average is None:  # pragma: no cover - warm-up 검사로 도달 불가
            return FeatureResult.unavailable(self.name, self.timeframe, "SMA 계산 실패")
        if average == _ZERO:
            # 최근 구간에 거래가 전혀 없었다. 확인할 근거가 없으므로 가장 약하게 본다.
            return FeatureResult(
                name=self.name,
                timeframe=self.timeframe,
                available=True,
                raw=_ZERO,
                normalized=self.confirm_weak,
                detail={"reason": "거래량 SMA 가 0", "volume": volumes[-1]},
            )

        ratio = volumes[-1] / average
        if ratio >= self.strong_ratio:
            confirm = self.confirm_strong
        elif ratio >= self.weak_ratio:
            confirm = self.confirm_normal
        else:
            confirm = self.confirm_weak

        return FeatureResult(
            name=self.name,
            timeframe=self.timeframe,
            available=True,
            raw=ratio,
            normalized=confirm,
            detail={"volume": volumes[-1], "volume_sma": average, "ratio": ratio},
        )
