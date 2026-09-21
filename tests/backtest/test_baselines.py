from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.backtest.baselines import (
    SignalObservation,
    SingleFeatureSignals,
    collect_single_feature_signals,
    compute_baselines,
)
from auto_trading.backtest.engine import BacktestResult
from auto_trading.backtest.feed import CandleFeed
from auto_trading.backtest.fills import SlippageModel
from auto_trading.backtest.metrics import compute_metrics
from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.backtest.report import ReportMetadata, generate_report
from auto_trading.config.schema import CostConfig, RootConfig
from auto_trading.core.enums import CloseReason, PositionSide, Timeframe
from auto_trading.core.types import Candle

D = Decimal
MINUTE = Timeframe.M1.ms
DEFAULT_BASE = D("100")


def _bar(
    index: int, *, base: Decimal = DEFAULT_BASE, timeframe: Timeframe = Timeframe.M1
) -> Candle:
    drift = D(index) / D("50")
    cycle = D(index % 11 - 5) / D("10")
    price = base + drift + cycle
    open_ms = index * timeframe.ms
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + timeframe.ms - 1,
        open=price,
        high=price + D("1"),
        low=price - D("1"),
        close=price + D("0.1"),
        volume=D("10"),
        quote_volume=D("1000"),
    )


def _trade(
    entry_index: int,
    duration: int,
    side: PositionSide,
    gross: str,
) -> BacktestTrade:
    return BacktestTrade(
        symbol="BTCUSDT",
        side=side,
        entry_ms=entry_index * MINUTE,
        exit_ms=(entry_index + duration) * MINUTE,
        entry_price=D("100"),
        exit_price=D("101"),
        qty=D("50"),
        close_reason=CloseReason.TAKE_PROFIT if D(gross) > 0 else CloseReason.STOP,
        gross_pnl=D(gross),
        fees=D("2"),
        funding_paid=D("1"),
        planned_risk=D("100"),
        leverage=2,
        mae=D("1"),
        mfe=D("2"),
    )


def _result() -> BacktestResult:
    trades = [
        _trade(10, 5, PositionSide.LONG, "103"),
        _trade(80, 7, PositionSide.SHORT, "-47"),
    ]
    return BacktestResult(
        symbol="BTCUSDT",
        from_ms=0,
        to_ms=199 * MINUTE,
        initial_equity=D("10000"),
        final_equity=D("10050"),
        trades=trades,
        equity_curve=[
            (0, D("10000")),
            (10 * MINUTE, D("10000")),
            (15 * MINUTE, D("10100")),
            (80 * MINUTE, D("10100")),
            (87 * MINUTE, D("10050")),
            (199 * MINUTE, D("10050")),
        ],
        bars_total=200,
        bars_in_position=12,
    )


def _cost() -> CostConfig:
    return CostConfig(
        taker_fee_rate=D("0.001"),
        maker_fee_rate=D("0"),
        slippage_entry_rate=D("0"),
        expected_funding_rate=D("0"),
    )


def _slippage() -> SlippageModel:
    return SlippageModel(
        base_bps=D("0"),
        vol_coef_bps=D("0"),
        size_coef_bps=D("0"),
        cap_bps=D("0"),
    )


def test_buy_and_hold_applies_entry_and_exit_fees() -> None:
    result = _result()
    bars = tuple(_bar(index) for index in range(200))
    metrics = compute_metrics(result, bootstrap_samples=10)

    comparison = compute_baselines(
        result, metrics, bars, _cost(), random_runs=10, slippage=_slippage()
    )

    entry = bars[0].open
    exit_price = bars[-1].close
    qty = result.initial_equity / (entry * D("1.001"))
    expected = qty * exit_price * D("0.999") / result.initial_equity - 1
    assert comparison.buy_and_hold.total_return_pct == pytest.approx(float(expected))
    assert comparison.buy_and_hold.n_trades == 1
    assert comparison.equal_weight.total_return_pct == pytest.approx(0.005)


@pytest.mark.golden
def test_random_entry_distribution_is_deterministic() -> None:
    result = _result()
    bars = tuple(_bar(index) for index in range(200))
    metrics = compute_metrics(result, bootstrap_samples=10)

    first = compute_baselines(
        result,
        metrics,
        bars,
        _cost(),
        random_seed=12345,
        slippage=_slippage(),
    )
    second = compute_baselines(
        result,
        metrics,
        bars,
        _cost(),
        random_seed=12345,
        slippage=_slippage(),
    )

    assert first.to_dict() == second.to_dict()
    assert first.digest() == second.digest()
    assert first.random_entry.simulations == 1_000
    assert first.random_entry.strategy_percentile is not None
    assert 0.0 <= first.random_entry.strategy_percentile <= 1.0
    assert first.digest() == "b4ded0c644bb01150b0825261e745bb167aa28086d1c161071cf504e1bc04b56"


def test_single_feature_enters_only_after_signal_bar_closes() -> None:
    bars = tuple(_bar(index, base=D("100") + D(index * 10)) for index in range(5))
    actual = _trade(0, 1, PositionSide.LONG, "1")
    result = BacktestResult(
        symbol="BTCUSDT",
        from_ms=0,
        to_ms=4 * MINUTE,
        initial_equity=D("10000"),
        final_equity=D("9998"),
        trades=[actual],
        equity_curve=[(0, D("10000")), (MINUTE, D("9998"))],
    )
    metrics = compute_metrics(result, bootstrap_samples=1)
    signal = SingleFeatureSignals(
        "causal_test",
        (SignalObservation(bars[0].close_ms, 1),),
    )

    comparison = compute_baselines(
        result,
        metrics,
        bars,
        _cost(),
        single_features=(signal,),
        random_runs=1,
        slippage=_slippage(),
    )

    outcome = comparison.single_features[0]
    entry_price = bars[1].open
    exit_price = bars[2].open
    notional_ratio = actual.entry_price * actual.qty / result.initial_equity
    notional = result.initial_equity * notional_ratio
    qty = notional / entry_price
    expected_equity = result.initial_equity + (exit_price - entry_price) * qty
    expected_equity -= (entry_price * qty + exit_price * qty) * D("0.001")
    expected_equity -= notional * (actual.funding_paid / (actual.entry_price * actual.qty))
    assert outcome.n_trades == 1
    assert outcome.total_return_pct == pytest.approx(
        float(expected_equity / result.initial_equity - 1)
    )


def test_directionless_features_are_reported_as_not_applicable() -> None:
    result = _result()
    metrics = compute_metrics(result, bootstrap_samples=5)
    signals = (
        SingleFeatureSignals(
            "b2_volregime",
            directional=False,
            note="방향 없음",
        ),
        SingleFeatureSignals("t3_volume", directional=False, note="방향 없음"),
    )

    comparison = compute_baselines(
        result,
        metrics,
        tuple(_bar(index) for index in range(200)),
        _cost(),
        single_features=signals,
        random_runs=5,
        slippage=_slippage(),
    )

    assert [outcome.available for outcome in comparison.single_features] == [False, False]
    assert all(outcome.total_return_pct is None for outcome in comparison.single_features)


def test_zero_trade_random_baseline_is_explicitly_unavailable() -> None:
    result = BacktestResult(
        symbol="BTCUSDT",
        from_ms=0,
        to_ms=10 * MINUTE,
        initial_equity=D("10000"),
        final_equity=D("10000"),
    )
    metrics = compute_metrics(result, bootstrap_samples=1)

    comparison = compute_baselines(
        result,
        metrics,
        tuple(_bar(index) for index in range(11)),
        _cost(),
        random_runs=10,
        slippage=_slippage(),
    )

    assert comparison.random_entry.simulations == 0
    assert comparison.random_entry.strategy_percentile is None
    assert "0건" in comparison.random_entry.note


def test_report_embeds_machine_and_human_readable_baselines(tmp_path: Path) -> None:
    result = _result()
    metrics = compute_metrics(result, bootstrap_samples=5)
    comparison = compute_baselines(
        result,
        metrics,
        tuple(_bar(index) for index in range(200)),
        _cost(),
        random_runs=10,
        slippage=_slippage(),
    )

    artifacts = generate_report(
        result,
        metrics,
        ReportMetadata("baseline-report", "cfg", "sha", "train"),
        output_root=tmp_path,
        baselines=comparison,
    )

    summary = json.loads((artifacts.directory / "summary.json").read_text(encoding="utf-8"))
    markdown = (artifacts.directory / "baselines.md").read_text(encoding="utf-8")
    assert summary["baselines"]["random_entry"]["simulations"] == 10
    assert "전략 백분위" in markdown
    assert "Buy & Hold" in markdown


@pytest.mark.leakage
def test_collected_feature_signals_do_not_change_when_future_is_appended() -> None:
    cfg = _small_config()
    early = _feature_feed(extra_bars=0)
    extended = _feature_feed(extra_bars=20)
    from_ms = 60 * Timeframe.H1.ms
    to_ms = 75 * Timeframe.H1.ms - 1

    first = collect_single_feature_signals(early, cfg, from_ms=from_ms, to_ms=to_ms)
    second = collect_single_feature_signals(extended, cfg, from_ms=from_ms, to_ms=to_ms)

    assert first == second
    assert [signals.name for signals in first] == [
        "b1_trend",
        "b2_volregime",
        "t1_pullback",
        "t2_momentum",
        "t3_volume",
    ]
    assert first[1].directional is False
    assert first[4].directional is False
    assert first[0].observations
    assert first[2].observations
    assert first[3].observations


def _small_config() -> RootConfig:
    return RootConfig.model_validate(
        {
            "features": {
                "b1_trend": {
                    "timeframe": "4h",
                    "params": {"ema_period": 3, "atr_period": 3, "slope_bars": 2},
                },
                "b2_volregime": {
                    "timeframe": "1h",
                    "params": {"atr_period": 3, "lookback_days": 1},
                },
                "t1_pullback": {
                    "timeframe": "15m",
                    "params": {"rsi_period": 3, "setup_lookback_bars": 2},
                },
                "t2_momentum": {
                    "timeframe": "5m",
                    "params": {"return_bars": 2, "zscore_lookback": 6},
                },
                "t3_volume": {"timeframe": "5m", "params": {"sma_period": 3}},
            },
            "risk": {"volatility_gate": {"enabled": False}},
        }
    )


def _feature_feed(*, extra_bars: int) -> CandleFeed:
    feed = CandleFeed("BTCUSDT")
    counts = {
        Timeframe.H4: 25 + extra_bars,
        Timeframe.M15: 330 + extra_bars,
        Timeframe.M5: 950 + extra_bars,
    }
    windows = {Timeframe.H4: 14, Timeframe.M15: 18, Timeframe.M5: 8}
    for timeframe, count in counts.items():
        candles = tuple(_bar(index, timeframe=timeframe) for index in range(count))
        feed._install(timeframe, candles, windows[timeframe])
    return feed
