"""피드 인과성 + 계좌 회계 테스트.

피드가 미래 봉을 한 개라도 내보내면 백테스트 성적 전체가 거짓이 되므로,
``TestFeedCausality`` 는 누출 탐지 표시를 달아 CI 에서 절대 건너뛰지 않게 한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.backtest.feed import CandleFeed, FeedWindow
from auto_trading.backtest.fills import Fill
from auto_trading.backtest.portfolio import BacktestPortfolio, BacktestRiskTracker, BacktestTrade
from auto_trading.config.schema import RiskConfig
from auto_trading.core.enums import (
    CloseReason,
    OrderPurpose,
    OrderType,
    PositionSide,
    Side,
    Timeframe,
)
from auto_trading.core.errors import InvariantViolationError
from auto_trading.core.types import Candle, FundingRate, Intent
from auto_trading.risk.gates import RiskState

D = Decimal
SYMBOL = "BTCUSDT"
MINUTE = 60_000


def candle(timeframe: Timeframe, index: int, *, close: str = "100", volume: str = "10") -> Candle:
    open_ms = index * timeframe.ms
    price = D(close)
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + timeframe.ms - 1,
        open=price,
        high=price + D(1),
        low=price - D(1),
        close=price,
        volume=D(volume),
        quote_volume=D(volume) * price,
    )


def feed_with(counts: dict[Timeframe, int], windows: dict[Timeframe, int]) -> CandleFeed:
    feed = CandleFeed(SYMBOL)
    for timeframe, count in counts.items():
        candles = tuple(candle(timeframe, i, close=str(100 + i)) for i in range(count))
        feed._install(timeframe, candles, windows[timeframe])
    return feed


# --------------------------------------------------------------------- 피드


@pytest.mark.leakage
class TestFeedCausality:
    def test_forming_bar_is_excluded(self) -> None:
        feed = feed_with({Timeframe.H1: 50}, {Timeframe.H1: 10})
        mid_bar = 20 * Timeframe.H1.ms + Timeframe.H1.ms // 2
        series = feed.series(Timeframe.H1, mid_bar)
        assert series.last is not None
        assert series.last.open_ms == 19 * Timeframe.H1.ms

    def test_bar_closing_exactly_at_as_of_is_included(self) -> None:
        feed = feed_with({Timeframe.H1: 50}, {Timeframe.H1: 10})
        as_of = 20 * Timeframe.H1.ms + Timeframe.H1.ms - 1
        series = feed.series(Timeframe.H1, as_of)
        assert series.last is not None
        assert series.last.open_ms == 20 * Timeframe.H1.ms

    def test_window_never_exceeds_requested_size(self) -> None:
        feed = feed_with({Timeframe.H1: 50}, {Timeframe.H1: 10})
        as_of = 49 * Timeframe.H1.ms + Timeframe.H1.ms - 1
        assert len(feed.series(Timeframe.H1, as_of)) == 10

    def test_overlapping_windows_hold_identical_bars(self) -> None:
        """창이 밀려도 겹치는 구간의 봉은 달라지지 않아야 한다 (repainting 금지)."""
        feed = feed_with({Timeframe.H1: 50}, {Timeframe.H1: 10})
        early = feed.series(Timeframe.H1, 30 * Timeframe.H1.ms - 1).candles
        late = {c.open_ms: c for c in feed.series(Timeframe.H1, 31 * Timeframe.H1.ms - 1).candles}
        assert all(late[c.open_ms] == c for c in early if c.open_ms in late)

    def test_warmup_is_false_until_the_window_is_full(self) -> None:
        feed = feed_with({Timeframe.H1: 50}, {Timeframe.H1: 10})
        assert not feed.has_warmup(Timeframe.H1, 5 * Timeframe.H1.ms - 1)
        assert feed.has_warmup(Timeframe.H1, 10 * Timeframe.H1.ms - 1)

    def test_same_candle_objects_are_reused_across_calls(self) -> None:
        """해시 캐시가 살아남아야 한다 (core/types.Candle.digest)."""
        feed = feed_with({Timeframe.H1: 50}, {Timeframe.H1: 10})
        as_of = 40 * Timeframe.H1.ms - 1
        first = feed.series(Timeframe.H1, as_of).candles[-1]
        second = feed.series(Timeframe.H1, as_of).candles[-1]
        assert first is second


class TestFeedWindow:
    def test_zero_bars_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="1 이상"):
            FeedWindow(timeframe=Timeframe.H1, bars=0)

    def test_timeframes_are_ordered_shortest_first(self) -> None:
        feed = feed_with(
            {Timeframe.H1: 5, Timeframe.M1: 5, Timeframe.M15: 5},
            {Timeframe.H1: 2, Timeframe.M1: 2, Timeframe.M15: 2},
        )
        assert feed.timeframes == (Timeframe.M1, Timeframe.M15, Timeframe.H1)

    def test_bars_between_is_inclusive(self) -> None:
        feed = feed_with({Timeframe.M1: 10}, {Timeframe.M1: 5})
        bars = feed.bars_between(Timeframe.M1, 2 * MINUTE, 4 * MINUTE)
        assert [b.open_ms for b in bars] == [2 * MINUTE, 3 * MINUTE, 4 * MINUTE]


# ------------------------------------------------------------------- 계좌


def entry_fill(price: str, qty: str = "1", at_ms: int = 0, fee: str = "0.5") -> Fill:
    return Fill(
        intent=Intent(OrderPurpose.ENTRY, Side.BUY, OrderType.MARKET, D(qty), "cid"),
        at_ms=at_ms,
        price=D(price),
        qty=D(qty),
        fee=D(fee),
        slippage_bps=D("1"),
    )


def exit_fill(price: str, qty: str = "1", at_ms: int = MINUTE, fee: str = "0.5") -> Fill:
    return Fill(
        intent=Intent(OrderPurpose.EXIT, Side.SELL, OrderType.MARKET, D(qty), "cid-x"),
        at_ms=at_ms,
        price=D(price),
        qty=D(qty),
        fee=D(fee),
        slippage_bps=D("1"),
    )


def opened(portfolio: BacktestPortfolio, *, entry: str = "100", stop: str = "98") -> None:
    portfolio.open(
        entry_fill(entry),
        side=PositionSide.LONG,
        stop_price=D(stop),
        take_profit_price=D("104"),
        planned_risk=D("2"),
        leverage=5,
        liquidation_price=D("82"),
    )


@pytest.fixture
def portfolio() -> BacktestPortfolio:
    return BacktestPortfolio(symbol=SYMBOL, initial_equity=D("1000"))


class TestPortfolioAccounting:
    def test_entry_fee_leaves_the_wallet_immediately(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        assert portfolio.wallet_balance == D("999.5")

    def test_winning_trade_nets_gross_minus_all_costs(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        trade = portfolio.close(exit_fill("104"), CloseReason.TAKE_PROFIT)
        assert trade.gross_pnl == D("4")
        assert trade.fees == D("1")  # 진입 0.5 + 청산 0.5
        assert trade.net_pnl == D("3")
        assert portfolio.wallet_balance == D("1003")

    def test_r_multiple_is_net_over_planned_risk(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        trade = portfolio.close(exit_fill("98"), CloseReason.STOP)
        assert trade.net_pnl == D("-3")
        assert trade.r_multiple == D("-1.5")

    def test_short_pnl_is_mirrored(self, portfolio: BacktestPortfolio) -> None:
        portfolio.open(
            entry_fill("100"),
            side=PositionSide.SHORT,
            stop_price=D("102"),
            take_profit_price=D("96"),
            planned_risk=D("2"),
            leverage=5,
            liquidation_price=D("118"),
        )
        trade = portfolio.close(exit_fill("96"), CloseReason.TAKE_PROFIT)
        assert trade.gross_pnl == D("4")

    def test_second_entry_while_holding_is_an_invariant_violation(
        self, portfolio: BacktestPortfolio
    ) -> None:
        opened(portfolio)
        with pytest.raises(InvariantViolationError, match="One-way"):
            opened(portfolio)

    def test_closing_without_a_position_is_rejected(self, portfolio: BacktestPortfolio) -> None:
        with pytest.raises(InvariantViolationError):
            portfolio.close(exit_fill("100"), CloseReason.SIGNAL)


class TestFunding:
    def test_long_pays_positive_funding(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        paid = portfolio.settle_funding(FundingRate(MINUTE, D("0.001"), 8))
        assert paid == D("0.1")  # 명목가 100 x 0.001
        assert portfolio.wallet_balance == D("999.4")

    def test_short_receives_positive_funding(self, portfolio: BacktestPortfolio) -> None:
        portfolio.open(
            entry_fill("100"),
            side=PositionSide.SHORT,
            stop_price=D("102"),
            take_profit_price=None,
            planned_risk=D("2"),
            leverage=5,
            liquidation_price=D("118"),
        )
        assert portfolio.settle_funding(FundingRate(MINUTE, D("0.001"), 8)) == D("-0.1")

    def test_same_settlement_is_never_applied_twice(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        rate = FundingRate(MINUTE, D("0.001"), 8)
        portfolio.settle_funding(rate)
        assert portfolio.settle_funding(rate) == D("0")

    def test_flat_account_pays_nothing(self, portfolio: BacktestPortfolio) -> None:
        assert portfolio.settle_funding(FundingRate(MINUTE, D("0.001"), 8)) == D("0")

    def test_funding_is_carried_into_the_trade_record(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        portfolio.settle_funding(FundingRate(MINUTE, D("0.001"), 8))
        trade = portfolio.close(exit_fill("100"), CloseReason.SIGNAL)
        assert trade.funding_paid == D("0.1")
        assert trade.net_pnl == D("-1.1")  # 수수료 1.0 + funding 0.1

    def test_funding_uses_settlement_mark_price(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        rate = FundingRate(MINUTE, D("0.001"), 8, mark_price=D("120"))
        assert portfolio.settle_funding(rate) == D("0.12")


class TestRiskTracker:
    def test_entry_updates_daily_and_same_direction_counts(self) -> None:
        tracker = BacktestRiskTracker.start(0, D("1000"))
        fill = entry_fill("100")
        tracker.record_entry(fill, PositionSide.LONG)
        tracker.record_entry(fill, PositionSide.LONG)
        state = tracker.to_state()
        assert state.day_trade_count == 2
        assert state.same_direction_entries == 2
        assert state.day_realized_pnl == D("-1.0")

    def test_utc_day_change_resets_only_daily_fields(self) -> None:
        tracker = BacktestRiskTracker.start(
            0,
            D("1000"),
            RiskState(consecutive_losses=2, day_trade_count=1, day_realized_pnl=D("-5")),
        )
        tracker.advance(86_400_000, D("990"))
        state = tracker.to_state()
        assert state.day_start_equity == D("990")
        assert state.day_trade_count == 0
        assert state.day_realized_pnl == D(0)
        assert state.consecutive_losses == 2

    def test_consecutive_loss_cooldown_expires_deterministically(self) -> None:
        cfg = RiskConfig()
        tracker = BacktestRiskTracker.start(0, D("1000"))
        for index in range(cfg.consecutive_loss_limit):
            trade = BacktestTrade(
                symbol=SYMBOL,
                side=PositionSide.LONG,
                entry_ms=index * MINUTE,
                exit_ms=(index + 1) * MINUTE,
                entry_price=D("100"),
                exit_price=D("98"),
                qty=D("1"),
                close_reason=CloseReason.STOP,
                gross_pnl=D("-2"),
                fees=D("1"),
                funding_paid=D(0),
                planned_risk=D("2"),
                leverage=5,
                mae=D("2"),
                mfe=D(0),
            )
            tracker.record_exit(trade, D("0.5"), cfg)
        assert tracker.to_state().consecutive_losses == cfg.consecutive_loss_limit
        assert tracker.cooldown_until_ms is not None
        tracker.advance(tracker.cooldown_until_ms, D("980"))
        assert tracker.to_state().consecutive_losses == 0
        assert tracker.to_state().cooldown_until_ms is None


class TestLiquidationAndExcursions:
    def test_liquidation_costs_the_whole_margin(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        trade = portfolio.liquidate(candle(Timeframe.M1, 3, close="82"))
        assert trade.close_reason is CloseReason.LIQUIDATION
        assert trade.gross_pnl == D("-20")  # 명목가 100 / 레버리지 5
        assert portfolio.is_flat

    def test_long_is_liquidated_when_low_reaches_the_price(
        self, portfolio: BacktestPortfolio
    ) -> None:
        opened(portfolio)
        assert portfolio.position is not None
        assert portfolio.position.is_liquidated(candle(Timeframe.M1, 1, close="82"))

    def test_zero_volume_bar_cannot_liquidate(self, portfolio: BacktestPortfolio) -> None:
        """채움봉 가격은 시장가가 아니다."""
        opened(portfolio)
        assert portfolio.position is not None
        assert not portfolio.position.is_liquidated(candle(Timeframe.M1, 1, close="50", volume="0"))

    def test_excursions_track_the_worst_and_best_reach(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        portfolio.mark(candle(Timeframe.M1, 1, close="95"))  # low 94
        portfolio.mark(candle(Timeframe.M1, 2, close="107"))  # high 108
        trade = portfolio.close(exit_fill("100"), CloseReason.SIGNAL)
        assert trade.mae == D("6")  # 100 - 94
        assert trade.mfe == D("8")  # 108 - 100

    def test_zero_volume_bars_do_not_move_excursions(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        portfolio.mark(candle(Timeframe.M1, 1, close="50", volume="0"))
        trade = portfolio.close(exit_fill("100"), CloseReason.SIGNAL)
        assert trade.mae == D("0")


class TestPortfolioState:
    def test_state_reports_the_position_to_the_strategy(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        state = portfolio.to_state(D("102"), MINUTE)
        assert state.position is not None
        assert state.position.side is PositionSide.LONG
        assert state.unrealized_pnl == D("2")
        assert state.equity == D("1001.5")  # 999.5 + 2

    def test_flat_state_has_no_position(self, portfolio: BacktestPortfolio) -> None:
        state = portfolio.to_state(D("100"), MINUTE)
        assert state.position is None
        assert state.equity == D("1000")

    def test_high_water_never_decreases(self, portfolio: BacktestPortfolio) -> None:
        opened(portfolio)
        portfolio.mark(candle(Timeframe.M1, 1, close="120"))
        peak = portfolio.equity_high_water
        portfolio.mark(candle(Timeframe.M1, 2, close="90"))
        assert portfolio.equity_high_water == peak
