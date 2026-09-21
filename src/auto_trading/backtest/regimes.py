"""사후 정보 없이 시장 국면을 라벨링하고 국면별 성과를 집계한다."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.core.enums import Timeframe
from auto_trading.core.money import ZERO
from auto_trading.core.types import TimeframeSeries
from auto_trading.features.indicators import atr_series, ema_series
from auto_trading.features.normalize import causal_percentile_rank_series

__all__ = [
    "RegimeLabel",
    "RegimePerformance",
    "aggregate_regime_performance",
    "label_regimes",
]

TrendRegime = Literal["trend_up", "trend_down", "range"]
VolatilityRegime = Literal["high_vol", "normal_vol", "low_vol"]


@dataclass(frozen=True, slots=True)
class RegimeLabel:
    """해당 일봉 마감 시점까지의 정보만으로 확정된 국면."""

    as_of_ms: int
    trend: TrendRegime
    volatility: VolatilityRegime
    atr_percentile: float

    @property
    def combined(self) -> str:
        return f"{self.trend}/{self.volatility}"


@dataclass(frozen=True, slots=True)
class RegimePerformance:
    """한 결합 국면의 거래 성과."""

    regime: str
    n_trades: int
    total_net_pnl: Decimal
    average_r: float
    win_rate_pct: float
    warning: str | None

    def to_dict(self) -> dict[str, str | int | float | None]:
        return {
            "regime": self.regime,
            "n_trades": self.n_trades,
            "total_net_pnl": str(self.total_net_pnl),
            "average_r": self.average_r,
            "win_rate_pct": self.win_rate_pct,
            "warning": self.warning,
        }


def label_regimes(
    daily: TimeframeSeries,
    *,
    ema_period: int = 200,
    ema_slope_bars: int = 20,
    atr_period: int = 14,
    volatility_lookback: int = 365,
) -> tuple[RegimeLabel, ...]:
    """일봉을 causal trend/volatility 국면으로 변환한다.

    추세는 200일 EMA 위치와 과거 EMA 기울기, 변동성은 현재까지의 ATR 백분위만
    사용한다. 필요한 표본이 모이기 전에는 라벨을 만들지 않아 중립으로 오인하지 않는다.
    """
    if daily.timeframe is not Timeframe.D1:
        raise ValueError(f"regime 입력은 1d 여야 한다: {daily.timeframe.value}")
    if ema_period < 2:
        raise ValueError(f"ema_period 는 2 이상이어야 한다: {ema_period}")
    if ema_slope_bars < 1:
        raise ValueError(f"ema_slope_bars 는 1 이상이어야 한다: {ema_slope_bars}")
    if atr_period < 1:
        raise ValueError(f"atr_period 는 1 이상이어야 한다: {atr_period}")
    if volatility_lookback < 2:
        raise ValueError(f"volatility_lookback 은 2 이상이어야 한다: {volatility_lookback}")

    candles = daily.candles
    emas = ema_series(daily.closes, ema_period)
    atrs = atr_series(candles, atr_period)
    valid_atrs = [value for value in atrs if value is not None]
    valid_percentiles = causal_percentile_rank_series(valid_atrs, volatility_lookback)
    percentiles: list[float | None] = []
    valid_index = 0
    for atr in atrs:
        if atr is None:
            percentiles.append(None)
        else:
            percentiles.append(valid_percentiles[valid_index])
            valid_index += 1
    labels: list[RegimeLabel] = []

    for index, candle in enumerate(candles):
        older_index = index - ema_slope_bars
        ema = emas[index]
        older_ema = emas[older_index] if older_index >= 0 else None
        atr = atrs[index]
        percentile = percentiles[index]
        if ema is None or older_ema is None or atr is None or percentile is None:
            continue
        if candle.close > ema and ema > older_ema:
            trend: TrendRegime = "trend_up"
        elif candle.close < ema and ema < older_ema:
            trend = "trend_down"
        else:
            trend = "range"

        if percentile > 0.70:
            volatility: VolatilityRegime = "high_vol"
        elif percentile < 0.30:
            volatility = "low_vol"
        else:
            volatility = "normal_vol"
        labels.append(
            RegimeLabel(
                as_of_ms=candle.close_ms,
                trend=trend,
                volatility=volatility,
                atr_percentile=percentile,
            )
        )
    return tuple(labels)


def aggregate_regime_performance(
    trades: list[BacktestTrade], labels: tuple[RegimeLabel, ...]
) -> tuple[RegimePerformance, ...]:
    """진입 시점에 이미 확정되어 있던 최신 국면으로 거래를 묶는다."""
    label_times = [label.as_of_ms for label in labels]
    groups: dict[str, list[BacktestTrade]] = {}
    for trade in trades:
        index = bisect.bisect_right(label_times, trade.entry_ms) - 1
        regime = labels[index].combined if index >= 0 else "unclassified"
        groups.setdefault(regime, []).append(trade)

    output: list[RegimePerformance] = []
    for regime, regime_trades in sorted(groups.items()):
        total_net = sum((trade.net_pnl for trade in regime_trades), start=ZERO)
        average_r = sum(float(trade.r_multiple) for trade in regime_trades) / len(regime_trades)
        wins = sum(1 for trade in regime_trades if trade.net_pnl > ZERO)
        output.append(
            RegimePerformance(
                regime=regime,
                n_trades=len(regime_trades),
                total_net_pnl=total_net,
                average_r=average_r,
                win_rate_pct=wins / len(regime_trades),
                warning=("n<50, 국면별 판단 보류" if len(regime_trades) < 50 else None),
            )
        )
    return tuple(output)
