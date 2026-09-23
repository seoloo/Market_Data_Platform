"""A1의 공통 decide → 다음 1분 체결 → 2시간 청산 경로."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.backtest.calendar import CalendarCoverage, CoverageGap, MacroCalendar
from auto_trading.backtest.engine import BacktestEngine
from auto_trading.backtest.feed import CandleFeed
from auto_trading.config.loader import load_config
from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import CloseReason, Timeframe
from auto_trading.core.types import Candle, FundingRate
from auto_trading.features import a1
from auto_trading.strategy.decision import DecisionContext

D = Decimal


def _config() -> RootConfig:
    loaded = load_config("backtest", strategy="a1_v1", apply_env=False).config
    risk = loaded.risk.model_copy(
        update={
            "volatility_gate": loaded.risk.volatility_gate.model_copy(update={"enabled": False})
        }
    )
    # 합성 경로 테스트에서만 장기 G5 이력을 생략한다. 운영 설정은 변경하지 않는다.
    assert loaded.a1 is not None
    return loaded.model_copy(
        update={
            "risk": risk,
            "a1": loaded.a1.model_copy(update={"control_without_contraction": True}),
        }
    )


def _feed() -> CandleFeed:
    feed = CandleFeed("BTCUSDT")
    minute_bars: list[Candle] = []
    five_bars: list[Candle] = []
    for block in range(80):
        price = D("100") + D(block) / 100
        for offset in range(5):
            index = 5 * block + offset
            minute_bars.append(
                Candle(
                    open_ms=index * Timeframe.M1.ms,
                    close_ms=(index + 1) * Timeframe.M1.ms - 1,
                    open=price,
                    high=price + D("0.001"),
                    low=price - D("0.001"),
                    close=price,
                    volume=D("100"),
                    quote_volume=D("10000"),
                )
            )
        five_bars.append(
            Candle(
                open_ms=block * Timeframe.M5.ms,
                close_ms=(block + 1) * Timeframe.M5.ms - 1,
                open=price,
                high=price + D("0.001"),
                low=price - D("0.001"),
                close=price,
                volume=D("500"),
                quote_volume=D("50000"),
            )
        )
    feed._install(Timeframe.M1, tuple(minute_bars), 54)
    feed._install(Timeframe.M5, tuple(five_bars), 20)
    return feed


def _audited_calendar(cfg: RootConfig) -> MacroCalendar:
    # 합성 데이터 전용으로 감사된 무이벤트 구간. 운영 데이터에는 재사용하지 않는다.
    return MacroCalendar(
        coverage=CalendarCoverage(
            from_ms=Timeframe.M1.ms,
            to_ms=400 * Timeframe.M1.ms,
            indicators=frozenset(cfg.risk.blackout),
            source_digest="synthetic-fixture",
        )
    )


def test_a1_engine_uses_next_minute_fill_and_two_hour_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(a1, "VOL_WINDOW_BARS", 4)
    monkeypatch.setattr(a1, "RANK_WINDOW_BARS", 5)
    monkeypatch.setattr(a1, "TREND_BARS", 8)
    monkeypatch.setattr(a1, "RANGE_BARS", 6)
    feed = _feed()
    cfg = _config()
    funding = (FundingRate(funding_ms=Timeframe.M1.ms, rate=D("0")),)
    engine = BacktestEngine(
        cfg,
        feed,
        funding=funding,
        macro_calendar=_audited_calendar(cfg),
    )
    assert engine._a1_events
    result = engine.run(
        from_ms=11 * Timeframe.M5.ms,
        to_ms=399 * Timeframe.M1.ms,
        initial_equity=D("10000"),
    )
    assert result.trades
    first = result.trades[0]
    assert first.entry_ms - 1 in engine._a1_events
    assert first.close_reason is CloseReason.SIGNAL
    assert 120 * Timeframe.M1.ms <= first.holding_ms <= 122 * Timeframe.M1.ms
    assert first.pre_cost_pnl is not None
    assert first.pre_cost_pnl > first.gross_pnl
    assert first.pre_cost_bps is not None
    assert first.net_bps is not None
    assert first.pre_cost_bps > first.net_bps
    assert result.liquidations == 0


def test_a1_engine_fails_closed_without_funding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a1, "VOL_WINDOW_BARS", 4)
    monkeypatch.setattr(a1, "RANK_WINDOW_BARS", 5)
    monkeypatch.setattr(a1, "TREND_BARS", 8)
    monkeypatch.setattr(a1, "RANGE_BARS", 6)
    cfg = _config()
    engine = BacktestEngine(
        cfg,
        _feed(),
        funding=(),
        macro_calendar=_audited_calendar(cfg),
    )
    result = engine.run(
        from_ms=11 * Timeframe.M5.ms,
        to_ms=399 * Timeframe.M1.ms,
        initial_equity=D("10000"),
    )
    assert result.entries_signalled > 0
    assert not result.trades
    assert result.veto_counts.get("DATA_QUALITY", 0) > 0


def test_a1_engine_fails_closed_without_macro_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a1, "VOL_WINDOW_BARS", 4)
    monkeypatch.setattr(a1, "RANK_WINDOW_BARS", 5)
    monkeypatch.setattr(a1, "TREND_BARS", 8)
    monkeypatch.setattr(a1, "RANGE_BARS", 6)
    engine = BacktestEngine(
        _config(),
        _feed(),
        funding=(FundingRate(funding_ms=Timeframe.M1.ms, rate=D("0")),),
        context=DecisionContext(event_calendar_available=True),
    )
    result = engine.run(
        from_ms=11 * Timeframe.M5.ms,
        to_ms=399 * Timeframe.M1.ms,
        initial_equity=D("10000"),
    )
    assert result.entries_signalled > 0
    assert not result.trades
    assert result.veto_counts.get("EVENT_BLACKOUT", 0) > 0


def test_a1_engine_fails_closed_inside_calendar_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(a1, "VOL_WINDOW_BARS", 4)
    monkeypatch.setattr(a1, "RANK_WINDOW_BARS", 5)
    monkeypatch.setattr(a1, "TREND_BARS", 8)
    monkeypatch.setattr(a1, "RANGE_BARS", 6)
    cfg = _config()
    gap = CoverageGap(
        from_ms=Timeframe.M1.ms,
        to_ms=400 * Timeframe.M1.ms,
        indicators=frozenset({"POWELL"}),
        reason="합성: 기자회견 시각의 사전 판본 없음",
    )
    calendar = MacroCalendar(
        coverage=CalendarCoverage(
            from_ms=Timeframe.M1.ms,
            to_ms=400 * Timeframe.M1.ms,
            indicators=frozenset(cfg.risk.blackout),
            source_digest="synthetic-fixture",
            gaps=(gap,),
        )
    )
    engine = BacktestEngine(
        cfg,
        _feed(),
        funding=(FundingRate(funding_ms=Timeframe.M1.ms, rate=D("0")),),
        macro_calendar=calendar,
    )
    result = engine.run(
        from_ms=11 * Timeframe.M5.ms,
        to_ms=399 * Timeframe.M1.ms,
        initial_equity=D("10000"),
    )
    assert result.entries_signalled > 0
    assert not result.trades
    assert result.veto_counts.get("EVENT_BLACKOUT", 0) > 0
