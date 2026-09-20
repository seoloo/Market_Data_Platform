"""B2 — 1시간 ATR 변동성 국면. **거래 허가** 를 담당한다.

사람이 읽는 설명: docs/STRATEGY-CATALOG.md §2 B2

방향 신호가 아니다. "지금 거래해도 되는 국면인가" 를 판정한다.

* 너무 잠잠하면 (하위 5%) 수수료만 나간다
* 너무 심하면 (상위 2%) **손절이 제값에 체결되지 않는다** — 사이징 계산이
  "손절이 제값에 된다" 를 전제하므로 이 게이트가 그 전제를 지킨다 (05 §3.8)

ATR 을 가격으로 나눠 무차원화한 뒤 1년 백분위를 낸다.
BTC 가 3만일 때의 ATR 300 과 10만일 때의 ATR 300 은 다른 의미이기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import TimeframeSeries
from auto_trading.features.base import FeatureResult
from auto_trading.features.indicators import atr_series
from auto_trading.features.normalize import causal_percentile_rank

__all__ = ["VolatilityRegimeFeature"]

_ZERO = Decimal(0)
_HOURS_PER_DAY = 24


@dataclass(frozen=True, slots=True)
class VolatilityRegimeFeature:
    """ATR/가격의 장기 백분위. 0.0(최저 변동성) ~ 1.0(최고)."""

    timeframe: Timeframe = Timeframe.H1
    atr_period: int = 14
    lookback_days: int = 365
    name: str = "b2_volregime"

    @property
    def lookback_bars(self) -> int:
        """백분위 창 크기. 1시간봉 기준 하루 24봉."""
        bars_per_day = _HOURS_PER_DAY * 60 // self.timeframe.minutes
        return self.lookback_days * bars_per_day

    @property
    def warmup_bars(self) -> int:
        return self.atr_period + self.lookback_bars

    def compute(self, series: TimeframeSeries) -> FeatureResult:
        if len(series) < self.warmup_bars:
            return FeatureResult.unavailable(
                self.name,
                self.timeframe,
                f"warm-up 미충족: {len(series)}/{self.warmup_bars}봉 "
                f"({self.lookback_days}일 백분위 필요)",
            )

        atrs = atr_series(series.candles, self.atr_period)
        closes = series.closes

        ratios: list[Decimal] = [
            atr / closes[i] for i, atr in enumerate(atrs) if atr is not None and closes[i] != _ZERO
        ]
        if len(ratios) < self.lookback_bars:
            return FeatureResult.unavailable(
                self.name,
                self.timeframe,
                f"백분위 표본 부족: {len(ratios)}/{self.lookback_bars}",
            )

        percentile = causal_percentile_rank(ratios, self.lookback_bars)
        if percentile is None:  # pragma: no cover - 위에서 이미 검사
            return FeatureResult.unavailable(self.name, self.timeframe, "정규화 실패")

        return FeatureResult(
            name=self.name,
            timeframe=self.timeframe,
            available=True,
            raw=ratios[-1],
            normalized=percentile,
            detail={
                "atr": atrs[-1],
                "atr_over_price": ratios[-1],
                "lookback_bars": self.lookback_bars,
            },
        )
