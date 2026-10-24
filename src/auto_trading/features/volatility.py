"""B2 — 1시간 ATR 변동성 국면. **거래 허가** 를 담당한다.

사람이 읽는 설명: docs/archive/design/STRATEGY-CATALOG.md §2 B2

방향 신호가 아니다. "지금 거래해도 되는 국면인가" 를 판정한다.

* 너무 잠잠하면 (하위 5%) 수수료만 나간다
* 너무 심하면 (상위 2%) **손절이 제값에 체결되지 않는다** — 사이징 계산이
  "손절이 제값에 된다" 를 전제하므로 이 게이트가 그 전제를 지킨다 (05 §3.8)

ATR 을 가격으로 나눠 무차원화한 뒤 1년 백분위를 낸다.
BTC 가 3만일 때의 ATR 300 과 10만일 때의 ATR 300 은 다른 의미이기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    _cache: dict[str, FeatureResult] = field(
        default_factory=dict, init=False, compare=False, repr=False
    )
    """직전 계산 1건 캐시. 값의 동일성에는 관여하지 않는다 (compare=False).

    B2 는 1년치 ATR 백분위라 창이 8,774봉이고 계산이 ``decide()`` 시간의 73% 를
    차지한다 (2026-09-21 프로파일). 그런데 5분마다 판단하는 동안 1시간봉은 12번에
    한 번만 바뀌므로, 같은 창을 12번 다시 계산하고 있었다.

    키는 **시계열 전체의 해시**다. 창 경계만 쓰면 과거 봉이 정정됐을 때 낡은 값을
    돌려줄 수 있다. 봉별 해시가 캐시되어 있어 (Candle.digest) 키 계산은 싸다.
    """

    @property
    def lookback_bars(self) -> int:
        """백분위 창 크기. 1시간봉 기준 하루 24봉."""
        bars_per_day = _HOURS_PER_DAY * 60 // self.timeframe.minutes
        return self.lookback_days * bars_per_day

    @property
    def warmup_bars(self) -> int:
        return self.atr_period + self.lookback_bars

    def compute(self, series: TimeframeSeries) -> FeatureResult:
        key = series.digest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._compute(series)
        self._cache.clear()
        self._cache[key] = result
        return result

    def _compute(self, series: TimeframeSeries) -> FeatureResult:
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
