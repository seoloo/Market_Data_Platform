"""순수 decide 경로와 결정론 테스트."""

from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

from auto_trading.config.schema import EdgeTableConfig, RootConfig
from auto_trading.core.enums import Action, Mode, PositionSide, PositionState, Timeframe, VetoReason
from auto_trading.core.hashing import sha256_hex
from auto_trading.core.types import (
    Candle,
    DataQuality,
    MarketSnapshot,
    PortfolioState,
    PositionSnapshot,
    TimeframeSeries,
)
from auto_trading.features.base import FeatureResult
from auto_trading.strategy import DecisionContext, decide, evaluate_scores

D = Decimal


def config() -> RootConfig:
    return RootConfig.model_validate(
        {
            "features": {
                "b1_trend": {
                    "timeframe": "4h",
                    "params": {"ema_period": 5, "atr_period": 3, "slope_bars": 2},
                },
                "b2_volregime": {
                    "timeframe": "1h",
                    "params": {"atr_period": 3, "lookback_days": 1},
                },
                "t1_pullback": {
                    "timeframe": "15m",
                    "weight": 0.5,
                    "params": {
                        "rsi_period": 3,
                        "long_zone": [35.0, 50.0],
                        "short_zone": [50.0, 65.0],
                        "invalidate_below": 30.0,
                        "invalidate_above": 70.0,
                    },
                },
                "t2_momentum": {
                    "timeframe": "5m",
                    "weight": 0.5,
                    "params": {
                        "return_bars": 2,
                        "zscore_lookback": 6,
                        "min_abs_z": 0.3,
                        "squash_k": 1.5,
                    },
                },
                "t3_volume": {
                    "timeframe": "5m",
                    "weight": 0.0,
                    "params": {"sma_period": 3},
                },
            }
        }
    )


def series(timeframe: Timeframe, count: int, *, rising: bool = False) -> TimeframeSeries:
    candles = []
    for index in range(count):
        drift = D(index) if rising else D(index % 4) - D("1.5")
        price = D("100") + drift
        open_ms = index * timeframe.ms
        candles.append(
            Candle(
                open_ms=open_ms,
                close_ms=open_ms + timeframe.ms - 1,
                open=price,
                high=price + D("1"),
                low=price - D("1"),
                close=price,
                volume=D("10") + D(index % 3),
            )
        )
    return TimeframeSeries(timeframe=timeframe, candles=tuple(candles))


def snapshot(*, quality: DataQuality | None = None) -> MarketSnapshot:
    all_series = (
        series(Timeframe.M1, 10),
        series(Timeframe.M5, 20),
        series(Timeframe.M15, 20),
        series(Timeframe.H1, 30),
        series(Timeframe.H4, 30, rising=True),
        series(Timeframe.D1, 2),
    )
    as_of_ms = max(item.candles[-1].close_ms for item in all_series)
    return MarketSnapshot(
        symbol="BTCUSDT",
        as_of_ms=as_of_ms,
        mode=Mode.BACKTEST,
        series=all_series,
        mark_price=D("120"),
        funding_rate=D("0.0001"),
        next_funding_ms=as_of_ms + 600_000,
        spread_bps=D("1"),
        data_quality=quality or DataQuality(ok=True),
    )


def portfolio() -> PortfolioState:
    return PortfolioState(
        equity=D("1000"),
        wallet_balance=D("1000"),
        available_balance=D("1000"),
        equity_high_water=D("1000"),
    )


def test_decide_is_deterministic_for_one_thousand_repetitions() -> None:
    snap = snapshot()
    cfg = config()
    expected = decide(snap, portfolio(), cfg).digest()
    assert {decide(snap, portfolio(), cfg).digest() for _ in range(1000)} == {expected}


def test_score_only_path_matches_full_decision() -> None:
    snap = snapshot()
    cfg = config()
    assert evaluate_scores(snap, cfg).scores == decide(snap, portfolio(), cfg).scores


def test_score_only_path_fails_closed_on_bad_data() -> None:
    result = evaluate_scores(snapshot(quality=DataQuality(ok=False)), config())
    assert not result.available
    assert result.unavailable_names == ("data_quality",)
    assert result.scores == result.scores.neutral()


def test_bad_data_fails_closed_before_feature_computation() -> None:
    result = decide(snapshot(quality=DataQuality(ok=False)), portfolio(), config())
    assert result.veto_reasons == (VetoReason.DATA_QUALITY,)
    assert not result.will_trade


def signal_features(direction: int = 1) -> tuple[FeatureResult, ...]:
    """setup 이 걸린 상태 + 방아쇠가 당겨진 상태 (D14).

    T1 은 창을 제공하고 setup 판정은 strategy 가 한다. 롱이면 최근에 RSI 가
    눌림 구간(35~50)에 들어왔다가 나온 이력을 준다.
    """
    rsi = D("38") if direction > 0 else D("62")
    history = tuple([D("55") if direction > 0 else D("45")] * 8 + [rsi] * 4)
    return (
        FeatureResult("b1_trend", Timeframe.H4, True, D(direction), float(direction)),
        FeatureResult("b2_volregime", Timeframe.H1, True, D("0.02"), 0.5),
        FeatureResult(
            "t1_pullback",
            Timeframe.M15,
            True,
            rsi,
            -0.24 * direction,
            detail={"rsi": rsi, "history": history},
        ),
        FeatureResult("t2_momentum", Timeframe.M5, True, D("0.01"), 1.2 * direction),
        FeatureResult("t3_volume", Timeframe.M5, True, D("1.3"), 1.0),
    )


def patch_features(monkeypatch: pytest.MonkeyPatch, direction: int = 1) -> None:
    module = importlib.import_module("auto_trading.strategy.decision")
    monkeypatch.setattr(
        module, "_compute_features", lambda _snapshot, _cfg: signal_features(direction)
    )


def entry_context() -> DecisionContext:
    return DecisionContext(
        expected_edge_bps=D("30"),
        edge_sample_count=30,
        qty_step=D("0.001"),
        price_tick=D("0.1"),
        min_qty=D("0.001"),
        min_notional=D("5"),
        maintenance_margin_rate=D("0.005"),
        event_calendar_available=True,
    )


def table_config(*, edge_by_horizon: dict[int, str]) -> RootConfig:
    cfg = config()
    buckets = [
        {
            "side": "LONG",
            "regime": "normal",
            "horizon_bars": horizon,
            "score_from": 70.0,
            "score_to": 80.0,
            "sample_count": 40,
            "observed_mean_bps": edge,
            "positive_rate": "0.6",
            "min_samples": 30,
            "available": True,
            "edge_bps": edge,
        }
        for horizon, edge in edge_by_horizon.items()
    ]
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "source_config_hash": "a" * 64,
        "train_from_ms": 1,
        "train_to_ms": 2,
        "decision_timeframe": "5m",
        "score_boundaries": [70.0, 80.0, 90.0, 100.0],
        "horizons_bars": [24, 72, 144, 288],
        "min_samples": 30,
        "observations": 160,
        "buckets": buckets,
    }
    payload["digest"] = sha256_hex(payload)
    payload["aggregation"] = "all_horizons_min"
    return cfg.model_copy(update={"edge_table": EdgeTableConfig.model_validate(payload)})


def exchange_context() -> DecisionContext:
    return DecisionContext(
        qty_step=D("0.001"),
        price_tick=D("0.1"),
        min_qty=D("0.001"),
        min_notional=D("5"),
        maintenance_margin_rate=D("0.005"),
        event_calendar_available=True,
    )


def test_approved_entry_builds_entry_stop_and_take_profit_intents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_features(monkeypatch)
    result = decide(snapshot(), portfolio(), config(), entry_context())
    assert result.action is Action.OPEN_LONG
    assert not result.veto_reasons
    assert result.sizing is not None
    assert tuple(intent.purpose.value for intent in result.intents) == ("E", "S", "T")
    # D14: direction = squash(1.2, 1.5) * T3(1.0) = 1.2/2.7. 국면 계수는 곱하지 않는다.
    assert result.scores.long_score == pytest.approx(50.0 * (1 + 1.2 / 2.7))


def test_configured_multi_horizon_edge_uses_worst_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_features(monkeypatch)
    cfg = table_config(edge_by_horizon={24: "35", 72: "30", 144: "25", 288: "28"})
    result = decide(snapshot(), portfolio(), cfg, exchange_context())
    assert result.action is Action.OPEN_LONG
    assert result.expected_edge_bps == 25.0
    assert VetoReason.INSUFFICIENT_EDGE not in result.veto_reasons


def test_one_weak_horizon_vetoes_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_features(monkeypatch)
    cfg = table_config(edge_by_horizon={24: "35", 72: "30", 144: "10", 288: "28"})
    result = decide(snapshot(), portfolio(), cfg, exchange_context())
    assert result.expected_edge_bps == 10.0
    assert VetoReason.INSUFFICIENT_EDGE in result.veto_reasons


def test_manual_edge_override_has_priority_over_table(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_features(monkeypatch)
    cfg = table_config(edge_by_horizon={24: "1", 72: "1", 144: "1", 288: "1"})
    result = decide(snapshot(), portfolio(), cfg, entry_context())
    assert result.expected_edge_bps == 30.0
    assert VetoReason.INSUFFICIENT_EDGE not in result.veto_reasons


def test_missing_runtime_entry_inputs_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_features(monkeypatch)
    result = decide(snapshot(), portfolio(), config())
    assert result.action is Action.OPEN_LONG
    assert VetoReason.DATA_QUALITY in result.veto_reasons
    assert VetoReason.INSUFFICIENT_EDGE in result.veto_reasons
    assert VetoReason.EVENT_BLACKOUT in result.veto_reasons
    assert not result.intents


def test_score_reversal_builds_reduce_only_exit_even_without_entry_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_features(monkeypatch, direction=-1)
    open_long = PositionSnapshot(
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        state=PositionState.OPEN,
        qty=D("0.01"),
        entry_price=D("100"),
    )
    state = PortfolioState(
        equity=D("1000"),
        wallet_balance=D("1000"),
        available_balance=D("900"),
        equity_high_water=D("1000"),
        position=open_long,
    )
    result = decide(snapshot(), state, config())
    assert result.action is Action.CLOSE
    assert len(result.intents) == 1
    assert result.intents[0].reduce_only
    assert result.intents[0].qty == D("0.01")
