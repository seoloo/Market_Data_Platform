"""QS 실거래 (문서 118) — QNDX 적립을 110 QS 규칙으로.

멈춤·TQQQ 매수·매도·적립금 회계. 주문은 가짜.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.core.types import Candle
from auto_trading.exchange.kis import BuyingPower, ReservationResult
from auto_trading.ops.qndx_alert import FEE, PLANS, Order, State, advise
from auto_trading.ops.qs_live import (
    MAX_TQQQ_QTY,
    QsLiveState,
    plan_tqqq_order,
    qndx_paused,
    reconcile_tqqq,
    resolve_tqqq_orders,
    update_signal,
)

QNDX = PLANS[0]
FX, TQ = 1_400.0, 81.0
NIGHT = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)  # _kst_now 꼴: KST 화요일 밤 9시


def _qs(baseline: float = 0.0) -> QsLiveState:
    return QsLiveState("2026-10-05", baseline, baseline, baseline * 70.0)


def _plan(qs: QsLiveState, accrued: float, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "accrued_krw": accrued,
        "fx_rate": FX,
        "last_usd": TQ,
        "weekday": 1,
        "kst_hhmm": "21:00",
        "trading_enabled": True,
        "halted": None,
    }
    return plan_tqqq_order(qs, "2026-10-06", **(args | kw))


def test_signal_transitions_and_pause() -> None:
    qs = _qs()
    notes = update_signal(qs, "2026-10-05", "2026-10-02", True)
    assert qs.want == "NONE" and not qndx_paused(qs) and "시작" in notes[0]
    update_signal(qs, "2026-10-06", "2026-10-05", False)  # 꺾임 — QS TQQQ 없음
    assert qs.want == "NONE" and qndx_paused(qs)
    update_signal(qs, "2026-10-07", "2026-10-06", False)
    assert qndx_paused(qs)
    update_signal(qs, "2026-10-08", "2026-10-07", True)  # 회복 → TQQQ 살 차례, QNDX 는 계속 멈춤
    assert qs.want == "BUY" and qndx_paused(qs)
    qs.want = "NONE"  # 그날 주문이 거절됐다고 하자
    assert update_signal(qs, "2026-10-08", "2026-10-07", True) == []  # 같은 날 재실행: 그대로
    assert qs.want == "NONE" and not qndx_paused(qs)
    update_signal(qs, "2026-10-09", "2026-10-08", True)
    assert qs.want == "NONE"  # 다음 날에도 다시 사지 않는다


def test_buy_sizing_uses_whole_accrual_in_whole_shares() -> None:
    qs = _qs()
    qs.prev_on, qs.want = True, "BUY"
    order = _plan(qs, 400_000.0)  # 285.71 USD
    limit = 83.43  # 81 × 1.03 내림
    assert (order.side, order.limit_usd) == ("BUY", limit)
    assert order.qty == int((400_000.0 / FX) // (limit * (1 + FEE))) == 3
    big = _plan(qs, 100_000_000.0)
    assert big.qty == MAX_TQQQ_QTY or big.qty * big.limit_usd <= 5_000.0


def test_too_little_cash_gives_up_this_recovery() -> None:
    qs = _qs()
    qs.prev_on, qs.want = True, "BUY"
    order = _plan(qs, 50_000.0)
    assert order.side == "NONE" and "모자란다" in order.reason and qs.want == "NONE"
    assert not qndx_paused(qs)  # QNDX 적립으로 이어간다


def test_buy_then_sell_round_trip_returns_money_to_accrual() -> None:
    qs = _qs()
    update_signal(qs, "2026-10-05", "2026-10-02", False)
    update_signal(qs, "2026-10-06", "2026-10-05", True)
    assert qs.want == "BUY"
    qs.orders.append(Order("2026-10-06", 83.43, "SENT"))
    notes = reconcile_tqqq(qs, "2026-10-07", 3.0, 82.0, 82.0, FX)
    notes += resolve_tqqq_orders(qs, "2026-10-07", changed=True)
    assert qs.want == "NONE" and qs.strategy_qty == 3.0 and qs.orders[-1].status == "FILLED"
    spent = 3 * 82.0 * FX * (1 + FEE)
    assert abs(qs.net_krw() - spent) < 1e-6 and "매수 확인" in notes[0]
    update_signal(qs, "2026-10-07", "2026-10-06", True)
    update_signal(qs, "2026-10-08", "2026-10-07", False)  # 꺾임 → 판다
    assert qs.want == "SELL"
    qs.orders.clear()  # _plan 의 날짜가 고정이라 (실제로는 날이 바뀐다)
    sell = _plan(qs, 0.0)
    assert (sell.side, sell.qty, sell.limit_usd) == ("SELL", 3, 78.56)  # 81 × 0.97 내림
    reconcile_tqqq(qs, "2026-10-09", 0.0, 0.0, 90.0, FX)
    back = 3 * 90.0 * (1 - FEE) * FX
    assert qs.want == "NONE" and qs.trades[-1].estimated
    assert abs(qs.net_krw() - (spent - back)) < 1e-6  # 이익이면 음수 → 적립금이 늘어난다


def test_user_owned_tqqq_is_never_sold() -> None:
    qs = _qs(baseline=5.0)
    update_signal(qs, "2026-10-05", "2026-10-02", True)
    update_signal(qs, "2026-10-06", "2026-10-05", False)
    assert qs.want == "NONE" and _plan(qs, 0.0).side == "NONE"
    qs.want = "BUY"
    reconcile_tqqq(qs, "2026-10-07", 7.0, 75.0, 80.0, FX)  # QS 가 2 주 더 삼
    qs.prev_on = True
    update_signal(qs, "2026-10-08", "2026-10-07", False)
    assert qs.want == "SELL" and _plan(qs, 0.0).qty == 2


def test_unsold_tqqq_is_kept_and_topped_up_when_trend_returns() -> None:
    """못 판 채 다시 켜지면 110 과 같아지도록 그대로 두고 쌓인 현금으로 더 산다."""
    qs = _qs()
    qs.last_qty, qs.prev_on, qs.want = 2.0, False, "SELL"
    notes = update_signal(qs, "2026-10-06", "2026-10-05", True)
    assert qs.want == "BUY" and qs.strategy_qty == 2.0 and "팔기 전에" in notes[0]


@pytest.mark.parametrize(
    ("kw", "why"),
    [
        ({"trading_enabled": False}, "앱에서"),
        ({"halted": "점검"}, "긴급 정지"),
        ({"weekday": 5}, "주말"),
        ({"kst_hhmm": "23:00"}, "접수 시간"),
    ],
)
def test_order_gates(kw: dict[str, Any], why: str) -> None:
    qs = _qs()
    qs.prev_on, qs.want = True, "BUY"
    order = _plan(qs, 400_000.0, **kw)
    assert order.side == "NONE" and why in order.reason and qs.want == "BUY"


def test_one_order_a_day_and_wait_for_open_orders() -> None:
    qs = _qs()
    qs.prev_on, qs.want = True, "BUY"
    qs.orders.append(Order("2026-10-06", 83.0, "REJECTED"))
    assert "이미" in _plan(qs, 400_000.0).reason
    qs.orders = [Order("2026-10-05", 83.0, "UNKNOWN")]
    assert "확인되지 않은" in _plan(qs, 400_000.0).reason


def test_advise_pause_and_extra_spent() -> None:
    s = State("QNDX", "2026-09-26", 0.0, 0.0, 0.0)
    normal = advise(s, QNDX, "2026-10-06", 25.0, FX, weekday=1)
    paused = advise(s, QNDX, "2026-10-06", 25.0, FX, weekday=1, extra_spent=50_000.0, paused=True)
    assert normal.status == "BUY" and paused.status == "PAUSE"
    assert paused.accrued == normal.accrued - 50_000.0
    assert paused.month_spent == normal.month_spent  # TQQQ 는 월 한도에 넣지 않는다


def test_state_round_trip() -> None:
    qs = _qs(1.0)
    update_signal(qs, "2026-10-05", "2026-10-02", True)
    reconcile_tqqq(qs, "2026-10-06", 2.0, 75.0, 80.0, FX)
    qs.orders.append(Order("2026-10-06", 80.0, "SENT"))
    again = QsLiveState.from_dict(json.loads(json.dumps(qs.to_dict())))
    assert again == qs


# ---------------------------------------------------------------- 스크립트 (가짜 증권사)


class _Broker:
    def __init__(self, *, outcome: str = "ok", max_qty: str = "10") -> None:
        self.outcome, self.max_qty = outcome, max_qty
        self.sent: list[tuple[str, int, Decimal]] = []

    def buying_power(self, symbol: str, exch: str, price: Decimal) -> BuyingPower:
        return BuyingPower(Decimal(self.max_qty), Decimal("1400"), Decimal("500"))

    def _send(self, side: str, qty: int, limit: Decimal) -> ReservationResult:
        self.sent.append((side, qty, limit))
        if self.outcome == "unknown":
            raise OrderStateUnknownError("timeout")
        return ReservationResult(
            self.outcome == "ok", "APBK0013" if self.outcome == "ok" else "X1", "msg", {}
        )

    def reserve_buy(self, symbol: str, exch: str, qty: int, limit: Decimal) -> ReservationResult:
        assert symbol == "TQQQ"
        return self._send("BUY", qty, limit)

    def reserve_sell(self, symbol: str, exch: str, qty: int, limit: Decimal) -> ReservationResult:
        assert symbol == "TQQQ"
        return self._send("SELL", qty, limit)

    def daily_history(self, symbol: str, **kw: Any) -> list[Candle]:
        day = 86_400_000
        start = int(datetime(2026, 7, 31, tzinfo=UTC).timestamp() * 1000) - 149 * day
        out = []
        for k in range(150):
            px = Decimal(str(round(500 + k * 1.5, 2)))
            out.append(Candle(start + k * day, start + (k + 1) * day - 1, px, px, px, px, px))
        return out


def _mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    import scripts.qndx_alert as mod

    monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(mod, "QS_STATE", tmp_path / "QS.json")
    monkeypatch.setattr(mod, "HALT", tmp_path / "HALT")
    return mod


CTX = {"on": True, "as_of": "2026-10-05", "parts": {}, "tqqq_last": TQ, "fx": FX}


@pytest.mark.parametrize(
    ("outcome", "status", "word", "want"),
    [
        ("ok", "SENT", "접수", "BUY"),
        ("reject", "REJECTED", "다시 보내지 않는다", "NONE"),
        ("unknown", "UNKNOWN", "결과 모름", "BUY"),
    ],
)
def test_script_buy_order_written_before_sending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str, status: str, word: str, want: str
) -> None:
    mod = _mod(monkeypatch, tmp_path)
    qs, broker = _qs(), _Broker(outcome=outcome)
    qs.prev_on, qs.want = True, "BUY"
    line = mod._qs_order(broker, qs, CTX, 400_000.0, True, NIGHT)
    assert broker.sent == [("BUY", 3, Decimal("83.43"))] and word in line
    assert qs.orders[-1].status == status and qs.want == want
    saved = json.loads((tmp_path / "QS.json").read_text(encoding="utf-8"))
    assert saved["orders"][-1]["date"] == "2026-10-06"  # 보내기 전에 기록됨
    mod._qs_order(broker, qs, CTX, 400_000.0, True, NIGHT)  # 같은 날 다시
    assert len(broker.sent) == 1


def test_script_sell_uses_sell_path_and_buy_respects_buying_power(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _mod(monkeypatch, tmp_path)
    qs, broker = _qs(), _Broker()
    qs.last_qty, qs.prev_on, qs.want = 4.0, False, "SELL"
    assert "매도 접수" in mod._qs_order(broker, qs, CTX, 0.0, True, NIGHT)
    assert broker.sent == [("SELL", 4, Decimal("78.56"))]
    qs2, poor = _qs(), _Broker(max_qty="1")
    qs2.prev_on, qs2.want = True, "BUY"
    mod._qs_order(poor, qs2, CTX, 400_000.0, True, NIGHT)
    assert poor.sent == [("BUY", 1, Decimal("83.43"))]


def test_script_signal_uses_only_closed_bars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _mod(monkeypatch, tmp_path)
    now = datetime(2026, 7, 31, 21, 0, tzinfo=UTC)
    monkeypatch.setattr(mod.time, "time", lambda: datetime(2026, 7, 31, 12, tzinfo=UTC).timestamp())
    on, as_of, parts = mod._qs_signal(_Broker(), now)
    assert on and as_of == "2026-07-30" and parts["r20"] > 0  # 7-31 봉은 아직 안 끝남


class _FullBroker(_Broker):
    def __init__(self, closes_up: bool, **kw: Any) -> None:
        super().__init__(**kw)
        self.closes_up = closes_up

    def quote_detail(self, symbol: str, exchange: str) -> Any:
        from auto_trading.exchange.kis import QuoteDetail

        last = Decimal("81") if symbol == "TQQQ" else Decimal("25")
        return QuoteDetail(symbol, exchange, last, None, Decimal("1400"), None)

    def daily_history(self, symbol: str, **kw: Any) -> list[Candle]:
        bars = super().daily_history(symbol, **kw)
        if self.closes_up:
            return bars
        down = []
        for b in bars:
            px = Decimal(2000) - b.close
            down.append(Candle(b.open_ms, b.close_ms, px, px, px, px, b.volume))
        return down


def test_one_with_qs_pauses_qndx_when_trend_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from auto_trading.exchange.overseas import OverseasBalance

    mod = _mod(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "REPORTS", tmp_path / "reports")
    now = datetime(2026, 7, 31, 21, 0, tzinfo=UTC)
    monkeypatch.setattr(mod.time, "time", lambda: datetime(2026, 7, 31, 12, tzinfo=UTC).timestamp())
    qndx = State("QNDX", "2026-07-01", 0.0, 0.0, 0.0)  # 31 만 원 쌓임 → 평소면 살 차례
    (tmp_path / "QNDX.json").write_text(json.dumps(qndx.to_dict()), encoding="utf-8")
    creds = SimpleNamespace(trading_enabled=True)
    balance = OverseasBalance((), Decimal(0))

    qs = _qs()
    qs.prev_on = True
    broker = _FullBroker(closes_up=False)  # 내리막 → 꺼짐
    text, report = mod._one_with_qs(broker, qs, QNDX, balance, now, creds)
    assert report["status"] == "PAUSE" and report["qs"]["qndx_paused"] is True
    assert "매수 멈춤" in text and "꺼짐" in text and broker.sent == []
    saved = json.loads((tmp_path / "QS.json").read_text(encoding="utf-8"))
    assert saved["prev_on"] is False
    assert (tmp_path / "reports" / "QS" / "2026-07-31.json").exists()

    # 다음 날 다시 켜짐 → TQQQ 를 산다 (QNDX 는 멈춤)
    nxt = datetime(2026, 8, 3, 21, 0, tzinfo=UTC)
    monkeypatch.setattr(mod.time, "time", lambda: datetime(2026, 8, 3, 12, tzinfo=UTC).timestamp())
    up = _FullBroker(closes_up=True)
    monkeypatch.setattr(
        up, "daily_history", lambda symbol, **kw: _shift(_FullBroker(True).daily_history(symbol), 3)
    )
    text, report = mod._one_with_qs(up, qs, QNDX, balance, nxt, creds)
    assert report["status"] == "PAUSE" and up.sent and up.sent[0][0] == "BUY"
    assert "TQQQ" in text and "접수" in text


def _shift(bars: list[Candle], days: int) -> list[Candle]:
    d = 86_400_000 * days
    return [
        Candle(b.open_ms + d, b.close_ms + d, b.open, b.high, b.low, b.close, b.volume)
        for b in bars
    ]
