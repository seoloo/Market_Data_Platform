from __future__ import annotations

from decimal import Decimal

from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.backtest.regimes import aggregate_regime_performance, label_regimes
from auto_trading.core.enums import CloseReason, PositionSide, Timeframe
from auto_trading.core.types import Candle, TimeframeSeries

D = Decimal


def _daily_series(count: int, *, future_crash: bool = False) -> TimeframeSeries:
    candles: list[Candle] = []
    price = D("100")
    for index in range(count):
        if future_crash and index >= count - 3:
            price -= D("20")
        else:
            price += D("1") + D(index % 4) / D("10")
        open_ms = index * Timeframe.D1.ms
        candles.append(
            Candle(
                open_ms=open_ms,
                close_ms=open_ms + Timeframe.D1.ms - 1,
                open=price - D("0.2"),
                high=price + D("1") + D(index % 7) / D("10"),
                low=price - D("1"),
                close=price,
                volume=D("10"),
            )
        )
    return TimeframeSeries(Timeframe.D1, tuple(candles))


def test_regime_labels_are_causal_when_future_is_appended() -> None:
    original = _daily_series(40)
    extended_candles = list(original.candles)
    price = original.candles[-1].close
    for _offset in range(3):
        index = len(extended_candles)
        price -= D("20")
        open_ms = index * Timeframe.D1.ms
        extended_candles.append(
            Candle(
                open_ms=open_ms,
                close_ms=open_ms + Timeframe.D1.ms - 1,
                open=price + D("1"),
                high=price + D("2"),
                low=price - D("2"),
                close=price,
                volume=D("10"),
            )
        )
    extended = TimeframeSeries(Timeframe.D1, tuple(extended_candles))

    first = label_regimes(
        original,
        ema_period=5,
        ema_slope_bars=2,
        atr_period=3,
        volatility_lookback=8,
    )
    second = label_regimes(
        extended,
        ema_period=5,
        ema_slope_bars=2,
        atr_period=3,
        volatility_lookback=8,
    )

    assert first
    assert second[: len(first)] == first
    assert all(label.trend == "trend_up" for label in first)
    assert {label.volatility for label in first} <= {"high_vol", "normal_vol", "low_vol"}


def test_regime_performance_uses_latest_closed_label() -> None:
    labels = label_regimes(
        _daily_series(20),
        ema_period=3,
        ema_slope_bars=1,
        atr_period=2,
        volatility_lookback=4,
    )
    entry_ms = labels[-1].as_of_ms + 1
    trade = BacktestTrade(
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        entry_ms=entry_ms,
        exit_ms=entry_ms + Timeframe.D1.ms,
        entry_price=D("100"),
        exit_price=D("110"),
        qty=D("1"),
        close_reason=CloseReason.TAKE_PROFIT,
        gross_pnl=D("10"),
        fees=D("1"),
        funding_paid=D("0"),
        planned_risk=D("5"),
        leverage=1,
        mae=D("2"),
        mfe=D("12"),
    )

    performance = aggregate_regime_performance([trade], labels)

    assert len(performance) == 1
    assert performance[0].regime == labels[-1].combined
    assert performance[0].n_trades == 1
    assert performance[0].total_net_pnl == D("9")
    assert performance[0].average_r == 1.8
    assert performance[0].warning == "n<50, 국면별 판단 보류"
