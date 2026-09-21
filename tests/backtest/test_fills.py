"""체결 시뮬레이션 테스트.

여기서 검증하는 세 규칙이 백테스트 성적의 진위를 결정한다.

* 신호 봉 종가로 체결하지 않는다 (R-1)
* 거래량 0 인 채움봉에서는 체결하지 않는다
* 손절과 익절이 같은 봉에 들어오면 손절이 먼저다 (비관적)

낙관적인 백테스트는 쓸모가 없는 정도가 아니라 **해롭다** — 통과하면 안 될 전략을
실거래로 보내기 때문이다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.backtest.fills import FillSimulator, SlippageModel
from auto_trading.config.schema import CostConfig
from auto_trading.core.enums import OrderPurpose, OrderType, Side
from auto_trading.core.types import Candle, Intent

D = Decimal
MINUTE = 60_000
COST = CostConfig()


def bar(
    *,
    open_: str = "100",
    high: str = "101",
    low: str = "99",
    close: str = "100",
    volume: str = "10",
    open_ms: int = 0,
) -> Candle:
    # OHLC 관계는 Candle 이 강제한다. 테스트 의도는 고가/저가 값 자체가 아니라
    # 체결 판정이므로, 넘긴 값이 관계를 깨면 여기서 넓혀준다.
    body_high = max(D(high), D(open_), D(close))
    body_low = min(D(low), D(open_), D(close))
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + MINUTE - 1,
        open=D(open_),
        high=body_high,
        low=body_low,
        close=D(close),
        volume=D(volume),
        quote_volume=D(volume) * D(open_),
    )


def market(side: Side, qty: str = "1") -> Intent:
    return Intent(OrderPurpose.ENTRY, side, OrderType.MARKET, D(qty), "cid-e")


def stop_order(side: Side, price: str) -> Intent:
    return Intent(
        OrderPurpose.STOP,
        side,
        OrderType.STOP_MARKET,
        D(0),
        "cid-s",
        stop_price=D(price),
        close_position=True,
    )


def take_profit(side: Side, price: str) -> Intent:
    return Intent(
        OrderPurpose.TAKE_PROFIT,
        side,
        OrderType.TAKE_PROFIT_MARKET,
        D(0),
        "cid-t",
        stop_price=D(price),
        close_position=True,
    )


@pytest.fixture
def sim() -> FillSimulator:
    return FillSimulator(COST, SlippageModel())


class TestMarketFill:
    def test_fills_at_next_bar_open_not_signal_close(self, sim: FillSimulator) -> None:
        """R-1. 봉 종가로 체결하면 신호를 만든 정보로 그 가격에 거래하는 셈이다."""
        fill = sim.fill_market(market(Side.BUY), bar(open_="100", close="105"))
        assert fill is not None
        assert fill.price > D("100")  # 시가 + 슬리피지
        assert fill.price < D("100.5")  # 종가(105) 와는 무관하다

    def test_zero_volume_bar_does_not_fill(self, sim: FillSimulator) -> None:
        """Binance 1m 아카이브는 체결 없는 분을 직전가로 채운다 (07 §2 실측)."""
        assert sim.fill_market(market(Side.BUY), bar(volume="0")) is None

    def test_buy_slippage_is_adverse(self, sim: FillSimulator) -> None:
        fill = sim.fill_market(market(Side.BUY), bar(open_="100"))
        assert fill is not None
        assert fill.price > D("100")

    def test_sell_slippage_is_adverse(self, sim: FillSimulator) -> None:
        fill = sim.fill_market(market(Side.SELL), bar(open_="100"))
        assert fill is not None
        assert fill.price < D("100")

    def test_fee_is_taker_on_market_orders(self, sim: FillSimulator) -> None:
        fill = sim.fill_market(market(Side.BUY, "2"), bar(open_="100"))
        assert fill is not None
        assert fill.fee == fill.price * D("2") * COST.taker_fee_rate

    def test_fill_timestamp_is_the_execution_bar(self, sim: FillSimulator) -> None:
        fill = sim.fill_market(market(Side.BUY), bar(open_ms=5 * MINUTE))
        assert fill is not None
        assert fill.at_ms == 5 * MINUTE


class TestProtectiveTrigger:
    def test_long_stop_triggers_when_low_breaks_through(self, sim: FillSimulator) -> None:
        assert sim.triggered(stop_order(Side.SELL, "98"), bar(low="97"))

    def test_long_stop_does_not_trigger_when_untouched(self, sim: FillSimulator) -> None:
        assert not sim.triggered(stop_order(Side.SELL, "98"), bar(low="98.5"))

    def test_short_stop_triggers_when_high_breaks_through(self, sim: FillSimulator) -> None:
        assert sim.triggered(stop_order(Side.BUY, "102"), bar(high="103"))

    def test_zero_volume_bar_never_triggers(self, sim: FillSimulator) -> None:
        """유동성이 없던 구간이다. 그 가격에 체결될 수 없다."""
        assert not sim.triggered(stop_order(Side.SELL, "98"), bar(low="90", volume="0"))

    def test_take_profit_triggers_on_favourable_extreme(self, sim: FillSimulator) -> None:
        assert sim.triggered(take_profit(Side.SELL, "105"), bar(high="106"))


class TestProtectiveResolution:
    def test_stop_wins_when_both_hit_in_one_bar(self, sim: FillSimulator) -> None:
        """1분봉만으로는 순서를 알 수 없다. 모르면 불리한 쪽이다."""
        both = bar(low="90", high="110")
        fired = sim.resolve_protective(
            (take_profit(Side.SELL, "105"), stop_order(Side.SELL, "95")), both
        )
        assert fired is not None
        assert fired.purpose is OrderPurpose.STOP

    def test_take_profit_alone_is_taken(self, sim: FillSimulator) -> None:
        fired = sim.resolve_protective(
            (take_profit(Side.SELL, "105"), stop_order(Side.SELL, "95")),
            bar(low="99", high="106"),
        )
        assert fired is not None
        assert fired.purpose is OrderPurpose.TAKE_PROFIT

    def test_nothing_fires_when_range_is_quiet(self, sim: FillSimulator) -> None:
        assert (
            sim.resolve_protective(
                (take_profit(Side.SELL, "105"), stop_order(Side.SELL, "95")), bar()
            )
            is None
        )


class TestProtectiveFill:
    def test_gap_through_stop_fills_at_bar_open_not_stop_price(self, sim: FillSimulator) -> None:
        """갭에서 발동가에 체결됐다고 하면 손실을 체계적으로 과소평가한다."""
        gapped = bar(open_="90", high="91", low="89", close="90")
        fill = sim.fill_protective(stop_order(Side.SELL, "98"), gapped, D("1"))
        assert fill is not None
        assert fill.price < D("90")  # 시가 90 에서 추가 슬리피지

    def test_normal_stop_fills_near_stop_price(self, sim: FillSimulator) -> None:
        fill = sim.fill_protective(stop_order(Side.SELL, "98"), bar(low="97.5"), D("1"))
        assert fill is not None
        assert D("97") < fill.price <= D("98")

    def test_stop_slippage_exceeds_market_slippage(self, sim: FillSimulator) -> None:
        """스톱은 연쇄 청산 구간에서 체결되므로 더 불리하다."""
        stop_fill = sim.fill_protective(stop_order(Side.SELL, "98"), bar(low="97.5"), D("1"))
        market_fill = sim.fill_market(market(Side.SELL), bar(open_="98"))
        assert stop_fill is not None
        assert market_fill is not None
        assert stop_fill.slippage_bps > market_fill.slippage_bps

    def test_zero_volume_bar_does_not_fill_protective(self, sim: FillSimulator) -> None:
        assert sim.fill_protective(stop_order(Side.SELL, "98"), bar(volume="0"), D("1")) is None


class TestSlippageModel:
    def test_unknown_market_impact_assumes_the_worst(self) -> None:
        model = SlippageModel()
        blind = model.estimate(is_stop=False)
        informed = model.estimate(
            is_stop=False, atr_percentile=0.1, notional=D("100"), bar_quote_volume=D("1000000")
        )
        assert blind > informed

    def test_cap_limits_extreme_estimates(self) -> None:
        model = SlippageModel()
        bps = model.estimate(
            is_stop=False, atr_percentile=1.0, notional=D("1000000"), bar_quote_volume=D("1")
        )
        assert bps <= model.cap_bps

    def test_stop_multiplier_applies(self) -> None:
        model = SlippageModel()
        assert model.estimate(is_stop=True, atr_percentile=0.0) > model.estimate(
            is_stop=False, atr_percentile=0.0
        )
