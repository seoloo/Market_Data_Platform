"""B1 — 4시간 EMA(50) 추세. **방향 허가** 를 담당한다.

사람이 읽는 설명: docs/STRATEGY-CATALOG.md §2 B1

가설 H1 의 "4시간 추세가 명확한 구간에서" 에 대응한다 (13-decisions.md D11).
이 값이 애매하면 (|값| < bias_threshold) **진입 자체를 하지 않는다.**

두 가지를 본다:
    1. 가격이 EMA(50) 에서 얼마나 떨어져 있는가 — 추세의 **위치**
    2. EMA(50) 자체가 오르고 있는가 — 추세의 **방향**

둘 다 **ATR 단위로 정규화**한다. 가격 단위(달러)로는 BTC 가 3만일 때와 10만일 때를
비교할 수 없고, 퍼센트로도 변동성 국면에 따라 의미가 달라지기 때문이다.

.. note:: **z-score 를 쓰지 않는 이유** (설계 변경, 2026-09-21)

   초안(06-scoring-engine.md)은 causal z-score 를 쓰기로 했으나 구현 중 결함을 발견했다.
   z-score 는 "최근 이력 대비 이례적인가" 를 재는 척도다. 꾸준히 상승하는 시장에서는
   ``(close-ema)/ema`` 가 일정해 분산이 0 이 되고 z→0 이 된다. 그러면 강한 추세장에서
   오히려 "추세 불명확" 으로 판정해 **영원히 진입하지 않는다.**

   z-score 는 T2(되돌림 종료 = 이례적 움직임 감지)에 맞는 척도이고,
   B1 처럼 **지속적 방향** 을 재야 하는 곳에는 ATR 정규화가 맞다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import TimeframeSeries
from auto_trading.features.base import FeatureResult
from auto_trading.features.indicators import atr_series, ema_series, ema_warmup_bars

__all__ = ["TrendFeature"]

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class TrendFeature:
    """ATR 단위로 잰 추세 강도. 양수면 상승, 음수면 하락.

    ``normalized`` 는 ATR 배수이므로 범위가 열려 있다. ``[-1,+1]`` 압축은
    방향을 아는 ``strategy/signals.py`` 가 한다.
    """

    timeframe: Timeframe = Timeframe.H4
    ema_period: int = 50
    atr_period: int = 14
    slope_bars: int = 10
    """기울기를 재는 구간. 1봉 차이는 노이즈가 커서 여러 봉에 걸쳐 본다."""
    name: str = "b1_trend"

    @property
    def warmup_bars(self) -> int:
        return max(ema_warmup_bars(self.ema_period), self.atr_period) + self.slope_bars

    def compute(self, series: TimeframeSeries) -> FeatureResult:
        if len(series) < self.warmup_bars:
            return FeatureResult.unavailable(
                self.name,
                self.timeframe,
                f"warm-up 미충족: {len(series)}/{self.warmup_bars}봉",
            )

        closes = series.closes
        emas = ema_series(list(closes), self.ema_period)
        atrs = atr_series(series.candles, self.atr_period)

        ema_now = emas[-1]
        ema_past = emas[-1 - self.slope_bars]
        atr_now = atrs[-1]
        if ema_now is None or ema_past is None or atr_now is None:  # pragma: no cover
            return FeatureResult.unavailable(self.name, self.timeframe, "지표 계산 실패")

        if atr_now == _ZERO:
            # 변동폭이 0 인 구간. 추세를 잴 기준이 없으므로 "추세 없음" 으로 본다.
            # bias 가 0 이 되어 진입하지 않는다 — 안전한 방향이다.
            return FeatureResult(
                name=self.name,
                timeframe=self.timeframe,
                available=True,
                raw=_ZERO,
                normalized=0.0,
                detail={"reason": "ATR 이 0", "ema": ema_now},
            )

        position_atr = (closes[-1] - ema_now) / atr_now
        slope_atr = (ema_now - ema_past) / atr_now
        strength = (position_atr + slope_atr) / 2

        return FeatureResult(
            name=self.name,
            timeframe=self.timeframe,
            available=True,
            raw=strength,
            normalized=float(strength),
            detail={
                "ema": ema_now,
                "atr": atr_now,
                "position_atr": position_atr,
                "slope_atr": slope_atr,
                "slope_bars": self.slope_bars,
            },
        )
