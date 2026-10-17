"""PEAD 장부 (2026-10-17): 추격 방지 없이 장 시작 매수 · 손절 없음 · 20 번째 장 마감 무렵 매도."""

from auto_trading.ops import size_order as so
from auto_trading.ops import stop_guard as sg
from auto_trading.ops.size_order import PEAD, PEAD_HOLD, Ticket
from tests.ops.test_size_order import FakeTrader


def _pead(ref: float = 50.0) -> Ticket:
    return Ticket("1", "ABC", "ABC", "USD", 2, ref, 0, session="US:2026-10-19", kind=PEAD)


def test_buys_at_open_without_gap_guard_and_no_stop() -> None:
    t, tr = _pead(ref=50.0), FakeTrader(ask=56.0)  # 반응일 종가보다 +12% 에 열림
    so.step_buy(t, tr, 1, session="US:2026-10-19", live=True, regular_open=56.0)
    assert t.status == "buying" and tr.placed[0]["limit"] == 56.17  # 매도 1호가 + 0.3%
    tr.fills["o0"] = ("FILLED", 2.0, 56.1)
    msgs = so.step_buy(t, tr, 2, session="US:2026-10-19", live=True)
    assert t.status == "holding" and t.stop == 0.0
    assert "PEAD" in msgs[0] and "손절 없이" in msgs[0]
    normal = Ticket("2", "XYZ", "XYZ", "USD", 2, 50.0, 0)
    so.step_buy(
        normal, FakeTrader(ask=56.0), 1, session="US:2026-10-19", live=True, regular_open=56.0
    )
    assert normal.status == "canceled"  # 💹 보통 장부는 +3% 넘게 열리면 안 산다


def test_no_stop_and_sells_on_20th_session_close() -> None:
    t = _pead()
    t.status, t.filled, t.avg = "holding", 2.0, 50.0
    sg.check_stop(t, 1.0)  # 크게 빠져도 손절 없음
    assert t.status == "holding"
    for k in range(1, PEAD_HOLD):
        day = f"US:d{k:02d}"
        sg.check_hold_exit(t, day, sell_live=True, closing=False)
        sg.check_hold_exit(t, day, sell_live=True, closing=True)  # 19 번째까진 마감에도 안 팜
        assert t.status == "holding"
    sg.check_hold_exit(t, "US:d20", sell_live=False, closing=False)  # 장 밖은 세지 않음
    assert len(t.hold_days) == PEAD_HOLD - 1
    sg.check_hold_exit(t, "US:d20", sell_live=True, closing=False)
    assert t.status == "holding" and len(t.hold_days) == PEAD_HOLD
    sg.check_hold_exit(t, "US:d20", sell_live=True, closing=True)
    assert t.status == "selling" and "20" in t.note
    tr = FakeTrader(bid=55.0)
    msgs = sg.step_sell(t, tr, 0, live=True, sellable=2.0, fx=1_400.0)
    assert "📅" in msgs[0] and "예상 손익" in msgs[0]
    tr.fills["o0"] = ("FILLED", 2.0, 55.0)
    msgs = sg.step_sell(t, tr, 1, live=True, sellable=0.0, fx=1_400.0)
    assert t.status == "closed" and "PEAD 매도 끝" in msgs[0]


def test_round_trip_and_status_line() -> None:
    t = _pead()
    t.status, t.filled, t.avg, t.hold_days = "holding", 2.0, 50.0, ["US:a", "US:b"]
    back = Ticket.from_dict(t.to_dict())
    assert back.kind == PEAD and back.hold_days == ["US:a", "US:b"]
    old = Ticket.from_dict({k: v for k, v in t.to_dict().items() if k not in ("kind", "hold_days")})
    assert old.kind == "" and old.hold_days == []  # 예전 장부도 읽힘
    line = sg.status_lines([t], {"ABC": 55.0})[0]
    assert "PEAD 2/20" in line and "+10.0%" in line and "손절 없음" in line
