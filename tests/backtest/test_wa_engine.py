"""WA 1h 평가→1m 체결→6h 만기 및 인과성 통합 검사."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from auto_trading.backtest.calendar import CalendarCoverage, MacroCalendar
from auto_trading.backtest.engine import BacktestEngine, BacktestResult
from auto_trading.backtest.feed import CandleFeed
from auto_trading.config.loader import load_config
from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import CloseReason, Timeframe
from auto_trading.core.types import Candle, FundingRate
from auto_trading.features.wa import WAState
from auto_trading.strategy.decision import DecisionContext
from tests.strategy.test_wa_decision import config

D = Decimal
H = Timeframe.H1.ms
M = Timeframe.M1.ms
START = 168 * H - M
END = 199 * H


def cfg(direction: str = "long", control: bool = True) -> RootConfig:
    data = config(direction).model_dump()
    data["data"]["warmup_safety_bars"] = 0
    data["wa"]["control_without_state"] = control
    return RootConfig.model_validate(data)


def feed(configuration: RootConfig) -> CandleFeed:
    result = CandleFeed("BTCUSDT")
    hours: list[Candle] = []
    minutes: list[Candle] = []
    for i in range(201):
        price = D(100) - D(i) / 100 + (D("0.05") if i % 2 == 0 else D("-0.05"))
        hourly = Candle(
            open_ms=i * H,
            close_ms=(i + 1) * H - 1,
            open=price,
            high=price + D("0.001"),
            low=price - D("0.001"),
            close=price,
            volume=D(6000),
            quote_volume=D(600000000),
        )
        hours.append(hourly)
        if i >= 166:
            minutes.extend(
                replace(
                    hourly,
                    open_ms=i * H + j * M,
                    close_ms=i * H + (j + 1) * M - 1,
                    volume=D(100),
                    quote_volume=D(10000000),
                )
                for j in range(60)
            )
    for window in BacktestEngine.feed_windows(configuration):
        bars = minutes if window.timeframe is Timeframe.M1 else hours
        result._install(window.timeframe, tuple(bars), window.bars)
    return result


def calendar(configuration: RootConfig) -> MacroCalendar:
    return MacroCalendar(
        coverage=CalendarCoverage(
            from_ms=1,
            to_ms=300 * H,
            indicators=frozenset(configuration.risk.blackout),
            source_digest="synthetic-wa-no-events",
        )
    )


def funding() -> tuple[FundingRate, ...]:
    return tuple(FundingRate(funding_ms=i * H, rate=D(0)) for i in range(160, 209, 8))


def engine(
    configuration: RootConfig,
    source: CandleFeed | None = None,
    context: DecisionContext | None = None,
) -> BacktestEngine:
    return BacktestEngine(
        configuration,
        source or feed(configuration),
        funding=funding(),
        macro_calendar=calendar(configuration),
        context=context,
    )


def run(instance: BacktestEngine, *, end: int = END) -> BacktestResult:
    return instance.run(from_ms=START, to_ms=end, initial_equity=D(10000))


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("control", [False, True])
def test_hourly_entries_exact_six_hour_exit_and_deterministic_replay(
    direction: str, control: bool
) -> None:
    instance = engine(cfg(direction, control))
    first = run(instance)
    assert first == run(instance)
    assert first.trades
    for trade in first.trades:
        assert trade.entry_ms % H == 0
        assert trade.holding_ms == 6 * H
        assert trade.close_reason is CloseReason.SIGNAL
        assert (
            trade.fees
            == (trade.entry_price + trade.exit_price) * trade.qty * cfg().risk.cost.taker_fee_rate
        )
        assert (
            trade.gross_pnl == (trade.exit_price - trade.entry_price) * trade.qty * trade.side.sign
        )
    assert first.final_equity == D(10000) + sum((t.net_pnl for t in first.trades), D(0))
    assert first.wa_state is not None


def test_requirements_only_use_wa_and_risk_inputs() -> None:
    windows = {w.timeframe: w.bars for w in BacktestEngine.feed_windows(cfg())}
    assert windows[Timeframe.H1] == 168
    assert set(windows) == {Timeframe.M1, Timeframe.H1}


@pytest.mark.parametrize("missing", ["calendar", "funding", "hourly"])
def test_missing_inputs_fail_closed_but_trigger_state_advances(missing: str) -> None:
    configuration = cfg()
    source = feed(configuration)
    if missing == "hourly":
        source = CandleFeed("BTCUSDT")
        other = feed(configuration)
        source._install(
            Timeframe.M1, other.all_candles(Timeframe.M1), other.required_bars(Timeframe.M1)
        )
    instance = BacktestEngine(
        configuration,
        source,
        funding=() if missing == "funding" else funding(),
        macro_calendar=None if missing == "calendar" else calendar(configuration),
        context=DecisionContext(event_calendar_available=True),
    )
    result = run(instance)
    assert not result.trades
    assert result.veto_counts
    if missing != "hourly":
        assert result.wa_state is not None and result.wa_state.last_trigger_ms is not None


def alter_minute(source: CandleFeed, at_ms: int, **changes: Any) -> None:
    source._install(
        Timeframe.M1,
        tuple(
            replace(c, **changes) if c.open_ms == at_ms else c
            for c in source.all_candles(Timeframe.M1)
        ),
        source.required_bars(Timeframe.M1),
    )


@pytest.mark.parametrize("gap, reason", [(False, CloseReason.SIGNAL), (True, CloseReason.STOP)])
def test_deadline_uses_open_not_future_intrabar_low(gap: bool, reason: CloseReason) -> None:
    configuration = cfg()
    source = feed(configuration)
    changes = {"low": D(97)}
    if gap:
        changes["open"] = D(97)
    alter_minute(source, 174 * H, **changes)
    result = run(engine(configuration, source), end=175 * H)
    assert result.trades[0].holding_ms == 6 * H
    assert result.trades[0].close_reason is reason


def test_zero_volume_delays_fill_and_timer_starts_at_actual_entry() -> None:
    configuration = cfg()
    source = feed(configuration)
    alter_minute(source, 168 * H, volume=D(0), quote_volume=D(0))
    result = run(engine(configuration, source), end=175 * H)
    assert result.trades[0].entry_ms == 168 * H + M
    assert result.trades[0].holding_ms == 6 * H


def test_zero_volume_at_deadline_retries_next_minute() -> None:
    configuration = cfg()
    source = feed(configuration)
    alter_minute(source, 174 * H, volume=D(0), quote_volume=D(0))
    result = run(engine(configuration, source), end=175 * H)
    assert result.trades[0].holding_ms == 6 * H + M


@pytest.mark.leakage
def test_appending_future_does_not_change_completed_trades_or_state() -> None:
    configuration = cfg()
    source = feed(configuration)
    short = CandleFeed("BTCUSDT")
    for tf in source.timeframes:
        short._install(
            tf,
            tuple(c for c in source.all_candles(tf) if c.open_ms <= 175 * H),
            source.required_bars(tf),
        )
    assert run(engine(configuration, source), end=175 * H) == run(
        engine(configuration, short), end=175 * H
    )


def test_restored_trigger_lock_and_future_state_rejection() -> None:
    state = WAState(167 * H - 1, 167 * H - 1)
    result = run(engine(cfg(), context=DecisionContext(wa_state=state)))
    assert result.trades[0].entry_ms == 179 * H
    with pytest.raises(ValueError, match="복구 상태"):
        run(engine(cfg(), context=DecisionContext(wa_state=WAState(200 * H, 200 * H))))


def test_research_edge_mode_trades_without_calendar_and_is_backtest_only() -> None:
    configuration = cfg()
    source = feed(configuration)
    halted = BacktestEngine(configuration, source, funding=funding(), macro_calendar=None)
    assert not run(halted).trades  # 달력 없음 → G6 fail-closed
    research = BacktestEngine(
        configuration, source, funding=funding(), macro_calendar=None, research_edge_mode=True
    )
    assert run(research).trades
    paper = load_config("paper", apply_env=False).config
    with pytest.raises(ValueError, match="BACKTEST 전용"):
        BacktestEngine(paper, CandleFeed("BTCUSDT"), research_edge_mode=True)
