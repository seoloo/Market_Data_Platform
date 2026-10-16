from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.ops import size_order as m
from auto_trading.ops.size_order import Ticket


@dataclass
class R:
    accepted: bool
    code: str
    message: str = ""


@dataclass
class S:
    status: str
    filled_qty: Decimal
    avg_price: Decimal | None
    filled_amount: Decimal | None
    commission: Decimal | None = Decimal(0)


@dataclass
class FakeTrader:
    ask: float = 100.0
    bid: float = 99.9
    fills: dict[str, tuple[str, float, float]] = field(default_factory=dict)
    """order_id → (상태, 체결 수량, 체결가)."""
    placed: list[dict[str, Any]] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)
    fail: Exception | None = None

    def asks(self, symbol: str) -> list[tuple[Any, Any]]:
        return [(Decimal(str(self.ask)), Decimal(100))] if self.ask else []

    def bids(self, symbol: str) -> list[tuple[Any, Any]]:
        return [(Decimal(str(self.bid)), Decimal(1000))]

    def order(self, symbol: str, side: str, qty: int, limit: Any, **kw: Any) -> R:
        if self.fail:
            raise self.fail
        oid = f"o{len(self.placed)}"
        self.placed.append({"id": oid, "side": side, "qty": qty, "limit": float(limit), **kw})
        self.fills.setdefault(oid, ("PENDING", 0.0, 0.0))
        return R(True, oid)

    def order_status(self, order_id: str) -> S:
        st, q, px = self.fills[order_id]
        return S(st, Decimal(str(q)), Decimal(str(px)), Decimal(str(q * px)))

    def cancel(self, order_id: str) -> R:
        self.canceled.append(order_id)
        _st, q, px = self.fills[order_id]
        self.fills[order_id] = ("CANCELED", q, px)
        return R(True, order_id)


def _t(qty: int = 10, ref: float = 100.0, symbol: str = "NVDA") -> Ticket:
    return Ticket("1015220000", symbol, symbol, "USD", qty, ref, 0)


def test_waits_until_session_then_buys_at_ask_plus_chase() -> None:
    t, tr = _t(), FakeTrader(ask=100.0)
    assert m.step_buy(t, tr, 1, session="US:2026-10-15", live=False) == []
    assert t.status == "reserved" and not tr.placed
    m.step_buy(t, tr, 2, session="US:2026-10-15", live=True, regular_open=100.0)
    assert t.status == "buying" and t.session == "US:2026-10-15"
    o = tr.placed[0]
    assert (o["side"], o["qty"], o["limit"], o["time_in_force"]) == ("BUY", 10, 100.3, "DAY")
    tr.fills["o0"] = ("FILLED", 10.0, 100.2)
    msgs = m.step_buy(t, tr, 3, session="US:2026-10-15", live=True)
    assert t.status == "holding" and t.filled == 10 and abs(t.avg - 100.2) < 1e-9
    assert t.stop == 97.19  # 100.2 × 0.97 = 97.194 → 내림
    assert "체결" in msgs[0] and "손절 등록" in msgs[0]


def test_gap_up_over_3_percent_cancels() -> None:
    t, tr = _t(ref=100.0), FakeTrader(ask=103.5)
    msgs = m.step_buy(t, tr, 1, session="US:d", live=True, regular_open=103.5)
    assert t.status == "canceled" and not tr.placed and "갭 상승" in msgs[0]


def test_reprice_up_to_three_then_partial() -> None:
    t, tr = _t(qty=10), FakeTrader(ask=100.0)
    m.step_buy(t, tr, 0, session="US:d", live=True, regular_open=100.0)
    tr.fills["o0"] = ("PARTIAL_FILLED", 4.0, 100.1)
    m.step_buy(t, tr, 60_000, session="US:d", live=True)  # 2 분 안 — 기다림
    assert tr.canceled == [] and len(tr.placed) == 1
    m.step_buy(t, tr, m.REPRICE_MS, session="US:d", live=True)  # 2 분 — 취소
    assert tr.canceled == ["o0"]
    tr.ask = 100.9  # 시작 가격 +1% 상한 = 101.00
    m.step_buy(t, tr, m.REPRICE_MS + 1, session="US:d", live=True)
    assert tr.placed[1]["qty"] == 6 and tr.placed[1]["limit"] == 101.0
    tr.fills["o1"] = ("CANCELED", 0.0, 0.0)
    m.step_buy(t, tr, m.REPRICE_MS + 2, session="US:d", live=True)
    tr.fills["o2"] = ("CANCELED", 2.0, 100.8)
    msgs = m.step_buy(t, tr, m.REPRICE_MS + 3, session="US:d", live=True)
    assert len(tr.placed) == m.MAX_BUY_ORDERS
    assert t.status == "holding" and t.filled == 6 and "나머지 취소" in msgs[0]


def test_reservation_expires_after_its_session() -> None:
    t, tr = _t(), FakeTrader(ask=0.0)  # 호가 없음 — 장은 시작했지만 못 냄
    m.step_buy(t, tr, 1, session="US:d1", live=True, regular_open=100.0)
    assert t.status == "reserved" and t.session == "US:d1"
    msgs = m.step_buy(t, tr, 2, session="US:d2", live=True)
    assert t.status == "canceled" and "예약 만료" in msgs[0]


def test_block_and_unknown_result() -> None:
    t = _t()
    msgs = m.step_buy(t, FakeTrader(), 1, session="US:d", live=True, block="실적 발표 10/16")
    assert t.status == "canceled" and "실적" in msgs[0]
    t2, tr = _t(), FakeTrader(fail=OrderStateUnknownError("x"))
    msgs = m.step_buy(t2, tr, 1, session="US:d", live=True, regular_open=100.0)
    assert t2.status == "failed" and "결과 모름" in msgs[0]


def test_no_fill_cancels_and_dict_roundtrip() -> None:
    t, tr = _t(), FakeTrader()
    m.step_buy(t, tr, 0, session="US:d", live=True, regular_open=100.0)
    tr.fills["o0"] = ("CANCELED", 0.0, 0.0)
    m.step_buy(t, tr, 1, session="US:d", live=False)  # 장 끝 — 다시 안 냄
    assert t.status == "canceled"
    back = Ticket.from_dict(t.to_dict())
    assert back == t and back.buys[0].order_id == "o0"


def test_missing_regular_open_does_not_substitute_observed_ask() -> None:
    t, tr = _t(), FakeTrader(ask=100)
    m.step_buy(t, tr, 1, session="US:d", live=True)
    assert t.status == "reserved" and not tr.placed


def test_actual_open_not_late_observed_ask_sets_cap() -> None:
    t, tr = _t(), FakeTrader(ask=102)
    m.step_buy(t, tr, 1, session="US:d", live=True, regular_open=100)
    assert tr.placed[0]["limit"] == 101  # 상한은 실제 시가 +1%, 늦게 본 호가가 아님


def test_price_ran_away_after_recommendation_cancels() -> None:
    """장중 예약: 기준은 추천 때 가격 — 그 뒤 3% 넘게 오르면 추격하지 않는다 (2026-10-16)."""
    t, tr = _t(ref=100.0), FakeTrader(ask=110)
    msgs = m.step_buy(t, tr, 1, session="US:d", live=True, regular_open=100.0)
    assert t.status == "canceled" and not tr.placed and "추격 매수 방지" in msgs[0]


def test_intraday_reservation_uses_recommendation_price_as_cap() -> None:
    """장중에 누른 예약은 opening=추천 가격(size_guard) → 상한 = 추천 가격 +1%."""
    t, tr = _t(ref=120.0), FakeTrader(ask=120.0)  # 그날 시가는 100 이었어도
    m.step_buy(t, tr, 1, session="US:d", live=True, regular_open=120.0)
    assert tr.placed[0]["limit"] == 120.36  # min(120 × 1.003, 121.2)


def test_changed_event_cancels_then_keeps_partial_without_reentry() -> None:
    t, tr = _t(), FakeTrader()
    m.step_buy(t, tr, 0, session="US:d", live=True, regular_open=100)
    tr.fills["o0"] = ("PARTIAL_FILLED", 4, 100)
    m.step_buy(t, tr, 1, session="US:d", live=True, block="새 CPI 발표")
    assert tr.canceled == ["o0"] and t.status == "buying"
    m.step_buy(t, tr, 2, session="US:d", live=True)  # 차단 사유가 사라져도 남은 매수 재개 금지
    assert len(tr.placed) == 1 and t.status == "holding" and t.filled == 4 and t.stop == 97


def test_cancel_ack_is_not_final_and_unknown_status_never_reorders() -> None:
    class SlowCancel(FakeTrader):
        def cancel(self, order_id: str) -> R:
            self.canceled.append(order_id)
            return R(True, order_id)

    t, tr = _t(), SlowCancel()
    m.step_buy(t, tr, 0, session="US:d", live=True, regular_open=100)
    m.step_buy(t, tr, 1, session="US:d", live=True, block="일정 미확인")
    m.step_buy(t, tr, 2, session="US:d", live=True)
    assert t.status == "buying" and len(tr.placed) == 1
    tr.fills["o0"] = ("UNKNOWN", 0, 0)
    import pytest

    with pytest.raises(OrderStateUnknownError):
        m.step_buy(t, tr, 3, session="US:d", live=True)
    assert len(tr.placed) == 1
