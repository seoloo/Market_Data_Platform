"""T/v2-long Pure Core: 연구 시뮬레이터 일치·결정 재현·미래 무관성·fail-closed·주문 계획."""

from __future__ import annotations

import math
import random
from decimal import Decimal
from typing import ClassVar

import pytest
from pydantic import ValidationError

from auto_trading.backtest.trend import TrendConfig, target_weight
from auto_trading.config.schema import SpotTrendConfig
from auto_trading.core.types import Candle
from auto_trading.strategy.spot_trend import (
    SpotOrderIntent,
    SpotSide,
    SpotTarget,
    plan_spot_orders,
    spot_trend_targets,
)

D = 86_400_000
START = 1_600_000_000_000 - 1_600_000_000_000 % D
CFG = SpotTrendConfig()


def walk(n: int, seed: int, drift: float = 0.0) -> list[Decimal]:
    rng = random.Random(seed)
    price = 100.0
    out = []
    for _ in range(n):
        price *= math.exp(drift + rng.gauss(0, 0.03))
        out.append(Decimal(repr(round(price, 6))))
    return out


def candles(closes: list[Decimal], start: int = START) -> list[Candle]:
    return [
        Candle(
            open_ms=start + k * D,
            close_ms=start + (k + 1) * D - 1,
            open=c,
            high=c,
            low=c,
            close=c,
            volume=Decimal(1),
        )
        for k, c in enumerate(closes)
    ]


def as_of(index: int) -> int:
    return START + (index + 1) * D - 1


class TestResearchParity:
    @pytest.mark.parametrize(("seed", "drift"), [(1, 0.0), (2, 0.003), (3, -0.003), (4, 0.001)])
    def test_matches_research_long_only_weight(self, seed: int, drift: float) -> None:
        closes = walk(400, seed, drift)
        bars = candles(closes)
        research = TrendConfig(long_only=True)
        for i in range(130, 400, 7):
            core = spot_trend_targets({"BTCUSDT": bars}, as_of_ms=as_of(i), cfg=CFG)["BTCUSDT"]
            expected = target_weight([float(c) for c in closes[: i + 1]], research)
            assert core.weight is not None and expected is not None
            assert float(core.weight) == pytest.approx(expected, abs=1e-9)


def test_same_input_same_decision_1000_times() -> None:
    bars = candles(walk(200, 7, 0.002))
    first = spot_trend_targets({"BTCUSDT": bars}, as_of_ms=as_of(199), cfg=CFG)
    for _ in range(1000):
        assert spot_trend_targets({"BTCUSDT": bars}, as_of_ms=as_of(199), cfg=CFG) == first


@pytest.mark.leakage
def test_future_candles_do_not_change_decision() -> None:
    closes = walk(300, 9, 0.001)
    past = candles(closes[:200])
    with_future = candles(closes)
    a = spot_trend_targets({"BTCUSDT": past}, as_of_ms=as_of(199), cfg=CFG)
    b = spot_trend_targets({"BTCUSDT": with_future}, as_of_ms=as_of(199), cfg=CFG)
    assert a == b


class TestFailClosed:
    def test_missing_day_is_unavailable(self) -> None:
        bars = candles(walk(200, 5))
        del bars[150]
        target = spot_trend_targets({"BTCUSDT": bars}, as_of_ms=as_of(199), cfg=CFG)["BTCUSDT"]
        assert target.weight is None and target.reason == "DATA_UNAVAILABLE"

    def test_not_at_daily_close_is_unavailable(self) -> None:
        bars = candles(walk(200, 5))
        target = spot_trend_targets({"BTCUSDT": bars}, as_of_ms=as_of(199) - 1, cfg=CFG)
        assert target["BTCUSDT"].reason == "NOT_DAILY_CLOSE"

    def test_short_history_and_unknown_symbol(self) -> None:
        targets = spot_trend_targets(
            {"BTCUSDT": candles(walk(100, 5))}, as_of_ms=as_of(99), cfg=CFG
        )
        assert targets["BTCUSDT"].weight is None
        assert targets["ETHUSDT"].weight is None

    def test_downtrend_is_cash(self) -> None:
        bars = candles(walk(200, 11, -0.01))
        target = spot_trend_targets({"BTCUSDT": bars}, as_of_ms=as_of(199), cfg=CFG)["BTCUSDT"]
        assert target.weight == 0


def test_config_values_are_frozen() -> None:
    with pytest.raises(ValidationError):
        SpotTrendConfig(target_vol=Decimal("0.3"))
    with pytest.raises(ValidationError):
        SpotTrendConfig(lookbacks=(10, 20, 30))


class TestPlanOrders:
    STEP: ClassVar[dict[str, Decimal]] = {
        "BTCUSDT": Decimal("0.00001"),
        "ETHUSDT": Decimal("0.0001"),
    }
    MIN: ClassVar[dict[str, Decimal]] = {"BTCUSDT": Decimal(5), "ETHUSDT": Decimal(5)}
    PRICES: ClassVar[dict[str, Decimal]] = {"BTCUSDT": Decimal(50000), "ETHUSDT": Decimal(2000)}

    def plan(
        self, targets: dict[str, SpotTarget], holdings: dict[str, Decimal]
    ) -> list[SpotOrderIntent]:
        return plan_spot_orders(
            targets,
            holdings=holdings,
            prices=self.PRICES,
            equity=Decimal(10000),
            cfg=CFG,
            step_size=self.STEP,
            min_notional=self.MIN,
        )

    def test_buy_to_target_floored_to_step(self) -> None:
        (intent,) = self.plan({"BTCUSDT": SpotTarget("BTCUSDT", Decimal("0.3"))}, {})
        assert intent.side is SpotSide.BUY
        assert intent.quantity == Decimal("0.06")

    def test_small_change_within_band_is_skipped(self) -> None:
        held = {"BTCUSDT": Decimal("0.06")}  # 30%
        assert self.plan({"BTCUSDT": SpotTarget("BTCUSDT", Decimal("0.33"))}, held) == []

    def test_zero_target_sells_everything_even_within_band(self) -> None:
        held = {"BTCUSDT": Decimal("0.004")}  # 2%
        (intent,) = self.plan({"BTCUSDT": SpotTarget("BTCUSDT", Decimal(0))}, held)
        assert intent.side is SpotSide.SELL and intent.quantity == Decimal("0.004")

    def test_sells_come_before_buys_and_unknown_targets_hold(self) -> None:
        held = {"BTCUSDT": Decimal("0.06"), "ETHUSDT": Decimal(0)}
        intents = self.plan(
            {
                "BTCUSDT": SpotTarget("BTCUSDT", Decimal(0)),
                "ETHUSDT": SpotTarget("ETHUSDT", Decimal("0.4")),
            },
            held,
        )
        assert [i.side for i in intents] == [SpotSide.SELL, SpotSide.BUY]
        assert self.plan({"BTCUSDT": SpotTarget("BTCUSDT", None)}, held) == []

    def test_below_min_notional_is_skipped(self) -> None:
        tiny = plan_spot_orders(
            {"BTCUSDT": SpotTarget("BTCUSDT", Decimal("0.3"))},
            holdings={},
            prices=self.PRICES,
            equity=Decimal(10),
            cfg=CFG,
            step_size=self.STEP,
            min_notional=self.MIN,
        )
        assert tiny == []
