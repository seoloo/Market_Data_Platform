"""백테스트 엔진 통합 테스트.

합성 데이터로 **경로**를 검증한다. 수익성이 아니라 다음을 본다.

* 신호 봉이 아니라 **다음 1분봉**에서 체결되는가 (R-1)
* 같은 입력이면 같은 결과가 나오는가 (결정론)
* 비용·funding 이 빠짐없이 반영되는가
* 강제청산이 기록되는가
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from auto_trading.backtest.engine import BacktestEngine, ExchangeFilters
from auto_trading.backtest.feed import CandleFeed
from auto_trading.backtest.fills import SlippageModel
from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle, FundingRate
from auto_trading.strategy.decision import DecisionContext

D = Decimal
SYMBOL = "BTCUSDT"
MINUTE = Timeframe.M1.ms
WARMUP_DAYS = 9
"""재생 시작 전에 채워둘 일수. 스윙 창(7.5일)보다 커야 한다."""

DAYS = 20
"""합성 구간 길이. 스윙 창(15m 720봉 = 7.5일) + 여유를 덮는다."""


def config() -> RootConfig:
    """warm-up 이 얕은 설정. 엔진 경로가 검증 대상이지 지표 품질이 아니다."""
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
                    "params": {"rsi_period": 14, "setup_lookback_bars": 8},
                },
                "t2_momentum": {
                    "timeframe": "5m",
                    "params": {"return_bars": 2, "zscore_lookback": 12},
                },
                "t3_volume": {"timeframe": "5m", "params": {"sma_period": 3}},
            },
            "data": {"warmup_safety_bars": 0},
            "risk": {
                # 이 회귀 fixture는 명시적으로 제한을 켠 경우를 검증한다.
                "daily_trade_limit": 2,
                "same_direction_limit": 2,
                # 손절 TF 를 15m 으로 낮춘다. 1h 이면 스윙 창 720봉(30일)이 필요해
                # 합성 구간이 한 달을 넘어야 하고 테스트가 분 단위로 느려진다.
                "stop": {"atr_timeframe": "15m", "atr_period": 3},
                "volatility_gate": {"enabled": False},
            },
        }
    )


_DRIFT_BPS = D("0.10")
"""분당 추세 (bps). 하루 약 +1.4% — 4시간봉 B1 이 열릴 만큼은 되는 완만한 상승장."""

_NOISE_BPS = D("9")
"""분당 잡음 진폭 (bps). 되돌림과 RSI 진동을 만든다."""


def _walk(minutes: int) -> list[Decimal]:
    """결정론적 유사난수 보행 + 추세.

    구간 선형(톱니) 데이터로는 진입 경로를 태울 수 없다. 주기가 봉 경계와 정렬되어
    RSI 와 z 가 같은 값만 반복하고, 되돌림이 항상 같은 깊이라 setup 이 걸리지 않는다
    (실제로 두 번 겪었다). 난수를 쓰되 **시드 고정 LCG** 로 재현성을 유지한다.
    """
    prices = [D(30000)]
    state = 12345
    for _ in range(minutes - 1):
        state = (1103515245 * state + 12345) % (2**31)
        noise = (D(state % 2001) - D(1000)) / D(1000) * _NOISE_BPS
        prices.append(prices[-1] * (D(1) + (_DRIFT_BPS + noise) / D(10000)))
    return prices


def build_feed(*, minutes: int = DAYS * 1440, volume: str = "10") -> CandleFeed:
    """1분봉을 만들고 상위 TF 를 집계해 채운다.

    창 크기는 **엔진이 쓰는 것과 같은** ``feed_windows`` 에서 가져온다. 여기서
    임의로 정하면 feature warm-up 과 어긋나 거래가 조용히 사라진다 (실제로 겪었다).
    """
    windows = {w.timeframe: w.bars for w in BacktestEngine.feed_windows(config())}
    feed = CandleFeed(SYMBOL)
    prices = _walk(minutes)
    minute_bars = [_candle(Timeframe.M1, i, prices[i], volume) for i in range(minutes)]
    feed._install(Timeframe.M1, tuple(minute_bars), windows[Timeframe.M1])
    for timeframe in (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4):
        step = timeframe.ms // MINUTE
        bars = tuple(
            _aggregate(timeframe, minute_bars[i : i + step])
            for i in range(0, minutes - step + 1, step)
        )
        feed._install(timeframe, bars, windows[timeframe])
    return feed


def _candle(timeframe: Timeframe, index: int, price: Decimal, volume: str) -> Candle:
    open_ms = index * timeframe.ms
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + timeframe.ms - 1,
        open=price,
        high=price * D("1.0006"),
        low=price * D("0.9994"),
        close=price,
        volume=D(volume),
        quote_volume=D(volume) * price,
    )


def _aggregate(timeframe: Timeframe, minutes: list[Candle]) -> Candle:
    first = minutes[0]
    return Candle(
        open_ms=timeframe.open_ms_of(first.open_ms),
        close_ms=timeframe.open_ms_of(first.open_ms) + timeframe.ms - 1,
        open=first.open,
        high=max(c.high for c in minutes),
        low=min(c.low for c in minutes),
        close=minutes[-1].close,
        volume=sum((c.volume for c in minutes), D(0)),
        quote_volume=sum((c.quote_volume for c in minutes), D(0)),
    )


def default_funding(minutes: int, rate: str = "0.0001") -> tuple[FundingRate, ...]:
    """8시간 주기 정산. 리스크 게이트가 ``next_funding_ms`` 를 요구하므로 필수다."""
    step = 8 * 60
    return tuple(
        FundingRate(funding_ms=i * 60_000, rate=D(rate), interval_hours=8)
        for i in range(step, minutes, step)
    )


def run(
    feed: CandleFeed | None = None,
    *,
    funding: tuple[FundingRate, ...] | None = None,
    slippage: SlippageModel | None = None,
    minutes: int = DAYS * 1440,
):
    engine = BacktestEngine(
        config(),
        feed or build_feed(minutes=minutes),
        funding=default_funding(minutes) if funding is None else funding,
        filters=ExchangeFilters(),
        slippage=slippage,
        context=DecisionContext(
            expected_edge_bps=D("60"), edge_sample_count=100, event_calendar_available=True
        ),
    )
    return engine.run(
        from_ms=WARMUP_DAYS * 1440 * MINUTE,
        to_ms=(minutes - 1) * MINUTE,
        initial_equity=D("10000"),
    )


@pytest.fixture(scope="module")
def result():
    return run()


class TestEngineRuns:
    def test_it_evaluates_and_produces_trades(self, result) -> None:
        assert result.decisions_evaluated > 0
        assert result.trades, "합성 톱니 데이터에서 거래가 한 건도 없으면 경로가 막힌 것이다"

    def test_no_liquidations_under_the_configured_risk(self, result) -> None:
        """청산이 1회라도 나오면 리스크 설계 실패다 (07 §3.5)."""
        assert result.liquidations == 0

    def test_exposure_is_a_fraction_not_always_in_market(self, result) -> None:
        assert 0.0 < result.exposure < 1.0

    def test_every_trade_pays_fees(self, result) -> None:
        assert all(t.fees > 0 for t in result.trades)

    def test_equity_curve_covers_every_minute(self, result) -> None:
        assert len(result.equity_curve) == result.bars_total

    def test_final_equity_matches_the_curve_tail(self, result) -> None:
        assert result.final_equity == result.equity_curve[-1][1]

    def test_daily_entry_limit_is_enforced_in_replay(self, result) -> None:
        entries_per_utc_day = Counter(trade.entry_ms // 86_400_000 for trade in result.trades)
        limit = config().risk.daily_trade_limit
        assert limit is not None
        assert all(count <= limit for count in entries_per_utc_day.values())


class TestDeterminism:
    def test_same_inputs_give_the_same_trades(self) -> None:
        """라이브 일치성의 전제. 같은 입력에 다른 결과가 나오면 재현이 불가능하다."""
        first, second = run(minutes=DAYS * 1440), run(minutes=DAYS * 1440)
        assert [(t.entry_ms, t.exit_ms, t.entry_price, t.exit_price) for t in first.trades] == [
            (t.entry_ms, t.exit_ms, t.entry_price, t.exit_price) for t in second.trades
        ]
        assert first.final_equity == second.final_equity


@pytest.mark.leakage
class TestNoLookAhead:
    def test_entry_never_fills_before_its_decision_bar(self, result) -> None:
        """체결 시각은 항상 판단 시각 **이후**여야 한다 (R-1)."""
        decision_tf = Timeframe.M5
        for trade in result.trades:
            decided_at = decision_tf.open_ms_of(trade.entry_ms) - 1
            assert trade.entry_ms > decided_at

    def test_exit_never_precedes_entry(self, result) -> None:
        assert all(t.exit_ms >= t.entry_ms for t in result.trades)


class TestCosts:
    def test_higher_slippage_lowers_the_result(self) -> None:
        """비용이 늘면 성과가 나빠져야 한다. 아니면 비용이 반영되지 않는 것이다."""
        cheap = run(minutes=DAYS * 1440, slippage=SlippageModel(base_bps=D("0.1")))
        pricey = run(
            minutes=20 * 1440,
            slippage=SlippageModel(base_bps=D("25"), cap_bps=D("60")),
        )
        assert pricey.final_equity < cheap.final_equity

    def test_funding_reaches_the_trade_record(self) -> None:
        outcome = run()
        assert any(t.funding_paid != D(0) for t in outcome.trades), (
            "보유 중 정산을 지났는데 funding 이 전부 0 이면 반영되지 않은 것이다"
        )

    def test_costlier_funding_lowers_the_result(self) -> None:
        """요율을 올리면 성과가 나빠져야 한다.

        비용 게이트가 막지 않는 범위에서 비교한다 — 요율이 너무 높으면 진입 자체가
        거부되어(INSUFFICIENT_EDGE) 비교가 성립하지 않는다.
        """
        cheap = run(funding=default_funding(DAYS * 1440, "0.0001"))
        expensive = run(funding=default_funding(DAYS * 1440, "0.002"))
        assert expensive.final_equity < cheap.final_equity

    def test_funding_so_expensive_that_the_edge_vanishes_blocks_entry(self) -> None:
        """비용이 기대 엣지를 넘으면 진입하지 않는다 (05 G6)."""
        outcome = run(funding=default_funding(DAYS * 1440, "0.006"))
        assert outcome.trades == []
        assert outcome.veto_counts


class TestFailClosed:
    def test_without_funding_data_every_entry_is_vetoed(self) -> None:
        """funding 이 없으면 게이트가 진입을 막아야 한다 (AGENTS.md §3.6).

        추정값으로 메우면 실거래에서 알 수 없는 값을 백테스트가 알고 있게 된다.
        """
        outcome = run(funding=())
        assert outcome.entries_signalled > 0  # 신호 자체는 나왔고
        assert outcome.trades == []  # 체결은 없다
        assert outcome.veto_counts.get("DATA_QUALITY", 0) > 0

    def test_stale_funding_gap_becomes_unavailable(self) -> None:
        minutes = DAYS * 1440
        only_one = (FundingRate(8 * 60 * MINUTE, D("0.0001"), 8),)
        engine = BacktestEngine(config(), build_feed(minutes=minutes), funding=only_one)
        assert engine._funding_as_of(16 * 60 * MINUTE + 2 * MINUTE) is None


class TestZeroVolumeGaps:
    def test_flat_zero_volume_stretch_does_not_invent_fills(self) -> None:
        """거래량 0 구간은 체결도 청산도 만들지 않는다."""
        minutes = DAYS * 1440
        feed = build_feed(minutes=minutes, volume="0")
        outcome = run(feed=feed, minutes=minutes)
        assert outcome.trades == []


class TestSummary:
    def test_summary_is_encodable_on_a_korean_console(self, result) -> None:
        """Windows 기본 콘솔은 cp949 다. 리포트 출력이 죽으면 안 된다."""
        result.summary().encode("cp949")

    def test_small_sample_is_flagged(self, result) -> None:
        if len(result.trades) < 100:
            assert "n<100" in result.summary()
