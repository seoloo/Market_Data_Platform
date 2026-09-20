"""core.types 테스트 — 특히 데이터 무결성 검증과 causal 보장."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import (
    Action,
    Mode,
    OrderPurpose,
    OrderState,
    OrderType,
    PositionSide,
    PositionState,
    Side,
    Timeframe,
    VetoReason,
)
from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.core.types import (
    Candle,
    Component,
    DataQuality,
    Decision,
    Intent,
    MarketSnapshot,
    OrderSnapshot,
    PortfolioState,
    PositionSnapshot,
    RiskVerdict,
    ScoreSet,
    TimeframeSeries,
)

D = Decimal
TF = Timeframe.M1


def make_candle(open_ms: int = 0, close: str = "100", tf: Timeframe = TF) -> Candle:
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + tf.ms - 1,
        open=D("100"),
        high=D("101"),
        low=D("99"),
        close=D(close),
        volume=D("10"),
    )


def make_series(count: int = 3, tf: Timeframe = TF) -> TimeframeSeries:
    return TimeframeSeries(
        timeframe=tf,
        candles=tuple(make_candle(i * tf.ms, tf=tf) for i in range(count)),
    )


def make_snapshot(as_of_ms: int | None = None, tf: Timeframe = TF) -> MarketSnapshot:
    series = make_series(3, tf)
    last = series.last
    assert last is not None
    return MarketSnapshot(
        symbol="BTCUSDT",
        as_of_ms=as_of_ms if as_of_ms is not None else last.close_ms,
        mode=Mode.BACKTEST,
        series=(series,),
        mark_price=D("100"),
    )


class TestCandle:
    def test_valid_candle(self):
        c = make_candle()
        assert c.range == D("2")

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"high": D("99")}, "OHLC"),  # high < close
            ({"low": D("101")}, "OHLC"),  # low > open
            ({"open": D("0")}, "0 이하"),
            ({"close": D("-1")}, "0 이하"),
            ({"volume": D("-1")}, "음수"),
        ],
    )
    def test_rejects_bad_ohlc(self, kwargs, match):
        """잘못된 시장 데이터가 타입 경계를 넘지 못한다 (F9)."""
        base = {
            "open_ms": 0,
            "close_ms": 59_999,
            "open": D("100"),
            "high": D("101"),
            "low": D("99"),
            "close": D("100"),
            "volume": D("10"),
        }
        with pytest.raises(DataIntegrityError, match=match):
            Candle(**{**base, **kwargs})

    def test_rejects_inverted_time(self):
        with pytest.raises(DataIntegrityError, match="close_ms"):
            Candle(
                open_ms=1000,
                close_ms=500,
                open=D("1"),
                high=D("1"),
                low=D("1"),
                close=D("1"),
                volume=D("0"),
            )

    def test_is_immutable(self):
        c = make_candle()
        with pytest.raises((AttributeError, TypeError)):
            c.close = D("200")  # type: ignore[misc]


class TestTimeframeSeries:
    def test_valid_series(self):
        s = make_series(5)
        assert len(s) == 5
        assert s.last is not None
        assert len(s.closes) == 5

    def test_rejects_duplicate_or_unordered(self):
        c = make_candle(0)
        with pytest.raises(DataIntegrityError, match=r"오름차순|중복"):
            TimeframeSeries(timeframe=TF, candles=(c, c))

    def test_rejects_unaligned_open_ms(self):
        bad = Candle(
            open_ms=30_000,  # 1m 경계가 아님
            close_ms=89_999,
            open=D("1"),
            high=D("1"),
            low=D("1"),
            close=D("1"),
            volume=D("0"),
        )
        with pytest.raises(DataIntegrityError, match="정렬"):
            TimeframeSeries(timeframe=TF, candles=(bad,))

    def test_detects_gaps(self):
        assert make_series(3).has_gaps() is False
        gapped = TimeframeSeries(
            timeframe=TF,
            candles=(make_candle(0), make_candle(2 * TF.ms)),  # 1번 봉 결측
        )
        assert gapped.has_gaps() is True

    def test_empty_series(self):
        s = TimeframeSeries(timeframe=TF, candles=())
        assert s.last is None
        assert len(s) == 0


class TestMarketSnapshot:
    def test_valid_snapshot(self):
        snap = make_snapshot()
        assert snap.get(TF) is not None
        assert snap.get(Timeframe.H4) is None

    def test_rejects_future_candle(self):
        """as_of 이후에 마감된 봉이 들어오면 즉시 실패한다 (look-ahead 차단)."""
        series = make_series(3)
        last = series.last
        assert last is not None
        with pytest.raises(CausalityError, match="as_of 이후"):
            MarketSnapshot(
                symbol="BTCUSDT",
                as_of_ms=last.close_ms - 1,
                mode=Mode.BACKTEST,
                series=(series,),
                mark_price=D("100"),
            )

    def test_rejects_duplicate_timeframe(self):
        s = make_series(2)
        with pytest.raises(DataIntegrityError, match="중복"):
            MarketSnapshot(
                symbol="BTCUSDT",
                as_of_ms=10**12,
                mode=Mode.BACKTEST,
                series=(s, s),
                mark_price=D("100"),
            )

    def test_require_raises_for_missing(self):
        with pytest.raises(DataIntegrityError, match="4h"):
            make_snapshot().require(Timeframe.H4)

    def test_digest_is_deterministic(self):
        """같은 입력 → 같은 해시. 재현성의 기반이다."""
        assert make_snapshot().digest() == make_snapshot().digest()

    def test_digest_changes_with_data(self):
        a = make_snapshot()
        b = MarketSnapshot(
            symbol="BTCUSDT",
            as_of_ms=a.as_of_ms,
            mode=Mode.BACKTEST,
            series=a.series,
            mark_price=D("101"),  # 다름
        )
        assert a.digest() != b.digest()

    def test_digest_ignores_mode(self):
        """백테스트와 라이브가 같은 데이터에서 같은 해시를 내야 parity 검증이 가능하다."""
        a = make_snapshot()
        b = MarketSnapshot(
            symbol=a.symbol,
            as_of_ms=a.as_of_ms,
            mode=Mode.LIVE,
            series=a.series,
            mark_price=a.mark_price,
        )
        assert a.digest() == b.digest()


class TestPositionSnapshot:
    def test_rejects_negative_qty(self):
        with pytest.raises(DataIntegrityError, match="음수"):
            PositionSnapshot(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                state=PositionState.OPEN,
                qty=D("-1"),
            )

    def test_flat_must_have_zero_qty(self):
        with pytest.raises(DataIntegrityError, match="FLAT"):
            PositionSnapshot(
                symbol="BTCUSDT",
                side=PositionSide.FLAT,
                state=PositionState.FLAT,
                qty=D("1"),
            )

    def test_holding_ms(self):
        p = PositionSnapshot(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            state=PositionState.OPEN,
            qty=D("1"),
            opened_ms=1000,
        )
        assert p.holding_ms(5000) == 4000
        assert p.holding_ms(500) == 0  # 음수 방지

    def test_requires_protection(self):
        open_pos = PositionSnapshot(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            state=PositionState.OPEN,
            qty=D("1"),
        )
        assert open_pos.requires_protection() is True

        opening = PositionSnapshot(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            state=PositionState.OPENING,
            qty=D("1"),
        )
        assert opening.requires_protection() is False


class TestPortfolioState:
    def test_drawdown(self):
        pf = PortfolioState(
            equity=D("9000"),
            wallet_balance=D("9000"),
            available_balance=D("9000"),
            equity_high_water=D("10000"),
        )
        assert pf.drawdown_pct == D("0.1")

    def test_drawdown_never_negative(self):
        """신고점 갱신 중이면 DD 는 0이다."""
        pf = PortfolioState(
            equity=D("11000"),
            wallet_balance=D("11000"),
            available_balance=D("11000"),
            equity_high_water=D("10000"),
        )
        assert pf.drawdown_pct == D("0")

    def test_unknown_order_blocks_entry(self):
        order = OrderSnapshot(
            client_order_id="x",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            purpose=OrderPurpose.ENTRY,
            qty=D("1"),
            state=OrderState.UNKNOWN,
        )
        pf = PortfolioState(
            equity=D("10000"),
            wallet_balance=D("10000"),
            available_balance=D("10000"),
            equity_high_water=D("10000"),
            open_orders=(order,),
        )
        assert pf.has_unknown_orders is True


class TestOrderSnapshot:
    def test_rejects_overfill(self):
        with pytest.raises(DataIntegrityError, match="범위"):
            OrderSnapshot(
                client_order_id="x",
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                purpose=OrderPurpose.ENTRY,
                qty=D("1"),
                filled_qty=D("2"),
                state=OrderState.PARTIALLY_FILLED,
            )

    def test_protective_requires_reduce_only(self):
        plain_stop = OrderSnapshot(
            client_order_id="x",
            symbol="BTCUSDT",
            side=Side.SELL,
            order_type=OrderType.STOP_MARKET,
            purpose=OrderPurpose.STOP,
            qty=D("1"),
            state=OrderState.NEW,
        )
        assert plain_stop.is_protective is False


class TestScoreSet:
    def test_scores_derive_from_direction(self):
        s = ScoreSet(
            direction=0.5,
            confidence_score=70.0,
            risk_score=30.0,
            bias_direction=1,
            bias_multiplier=1.0,
            agreement=0.8,
        )
        assert s.long_score == 75.0
        assert s.short_score == 25.0
        assert s.long_score + s.short_score == 100.0

    def test_neutral(self):
        s = ScoreSet.neutral()
        assert s.long_score == 50.0
        assert s.short_score == 50.0

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"direction": 1.5}, "direction"),
            ({"bias_direction": 2}, "bias_direction"),
            ({"confidence_score": 101.0}, "confidence_score"),
            ({"agreement": -0.1}, "agreement"),
        ],
    )
    def test_range_validation(self, kwargs, match):
        base = {
            "direction": 0.0,
            "confidence_score": 0.0,
            "risk_score": 0.0,
            "bias_direction": 0,
            "bias_multiplier": 0.0,
            "agreement": 0.0,
        }
        with pytest.raises(ValueError, match=match):
            ScoreSet(**{**base, **kwargs})


class TestIntent:
    def test_stop_must_be_reduce_only(self):
        """INV-5: 보호주문이 반대 포지션을 열면 안 된다."""
        with pytest.raises(ValueError, match="reduce_only"):
            Intent(
                purpose=OrderPurpose.STOP,
                side=Side.SELL,
                order_type=OrderType.STOP_MARKET,
                qty=D("1"),
                client_order_id="x",
                stop_price=D("90"),
            )

    def test_valid_stop(self):
        i = Intent(
            purpose=OrderPurpose.STOP,
            side=Side.SELL,
            order_type=OrderType.STOP_MARKET,
            qty=D("1"),
            client_order_id="x",
            stop_price=D("90"),
            close_position=True,
        )
        assert i.close_position is True

    def test_stop_requires_stop_price(self):
        with pytest.raises(ValueError, match="stop_price"):
            Intent(
                purpose=OrderPurpose.STOP,
                side=Side.SELL,
                order_type=OrderType.STOP_MARKET,
                qty=D("1"),
                client_order_id="x",
                reduce_only=True,
            )

    def test_limit_requires_price(self):
        with pytest.raises(ValueError, match="price"):
            Intent(
                purpose=OrderPurpose.ENTRY,
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                qty=D("1"),
                client_order_id="x",
            )


class TestRiskVerdict:
    def test_approve_and_veto(self):
        assert RiskVerdict.approve().approved is True
        v = RiskVerdict.veto(VetoReason.DAILY_LOSS_LIMIT, VetoReason.COOLDOWN)
        assert v.approved is False
        assert len(v.reasons) == 2

    def test_veto_requires_reason(self):
        with pytest.raises(ValueError, match="사유가 없다"):
            RiskVerdict(approved=False)

    def test_approve_cannot_have_reasons(self):
        with pytest.raises(ValueError, match="거부 사유"):
            RiskVerdict(approved=True, reasons=(VetoReason.COOLDOWN,))


class TestComponent:
    def test_contribution(self):
        c = Component(name="b1", layer="bias", raw=1.2, normalized=1.2, signal=0.6, weight=0.5)
        assert c.contribution == pytest.approx(0.3)

    def test_signal_range(self):
        with pytest.raises(ValueError, match="signal"):
            Component(name="x", layer="bias", raw=0.0, normalized=0.0, signal=2.0, weight=1.0)

    def test_layer_validation(self):
        with pytest.raises(ValueError, match="layer"):
            Component(name="x", layer="other", raw=0.0, normalized=0.0, signal=0.0, weight=1.0)


class TestDecision:
    def _decision(self, **kwargs) -> Decision:
        base = {
            "as_of_ms": 1000,
            "symbol": "BTCUSDT",
            "action": Action.NONE,
            "scores": ScoreSet.neutral(),
            "data_quality": DataQuality(ok=True),
            "snapshot_digest": "abc",
        }
        return Decision(**{**base, **kwargs})

    def test_veto_cannot_have_intents(self):
        intent = Intent(
            purpose=OrderPurpose.ENTRY,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=D("1"),
            client_order_id="x",
        )
        with pytest.raises(ValueError, match="거부된 결정"):
            self._decision(
                action=Action.OPEN_LONG,
                veto_reasons=(VetoReason.COOLDOWN,),
                intents=(intent,),
            )

    def test_digest_is_deterministic(self):
        assert self._decision().digest() == self._decision().digest()

    def test_digest_changes_with_action(self):
        a = self._decision()
        b = self._decision(action=Action.CLOSE)
        assert a.digest() != b.digest()

    def test_veto_reason_order_does_not_affect_digest(self):
        """거부 사유의 나열 순서는 의미가 없으므로 해시에 영향을 주면 안 된다."""
        a = self._decision(veto_reasons=(VetoReason.COOLDOWN, VetoReason.STALE_DATA))
        b = self._decision(veto_reasons=(VetoReason.STALE_DATA, VetoReason.COOLDOWN))
        assert a.digest() == b.digest()
