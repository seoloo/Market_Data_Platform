from auto_trading.ops import stop_guard as m
from auto_trading.ops.size_order import Ticket
from tests.ops.test_size_order import FakeTrader


def _held(qty: int = 10, avg: float = 100.0) -> Ticket:
    t = Ticket("1", "NVDA", "NVIDIA", "USD", qty, avg, 0, status="holding")
    t.filled, t.avg, t.stop = float(qty), avg, 97.0
    return t


def test_triggers_only_at_or_below_stop() -> None:
    t = _held()
    m.check_stop(t, 97.01)
    assert t.status == "holding"
    m.check_stop(t, None)
    assert t.status == "holding"
    m.check_stop(t, 97.0)
    assert t.status == "selling" and "손절" in t.note


def test_sells_registered_qty_only_capped_by_sellable() -> None:
    t, tr = _held(qty=10), FakeTrader(bid=96.9)
    m.check_stop(t, 96.9)
    msgs = m.step_sell(t, tr, 0, live=True, sellable=8.0, fx=1_400.0)
    o = tr.placed[0]
    assert (o["side"], o["qty"], o["limit"], o["time_in_force"]) == ("SELL", 8, 96.9, "DAY")
    assert "손절 시작" in msgs[0]
    assert "예상 손해 약: -$31.00 (약 -4.3만 원) · -3.1%" in msgs[0]  # (96.9 − 100) × 10
    tr.fills["o0"] = ("FILLED", 8.0, 96.9)
    m.step_sell(t, tr, 1, live=True, sellable=2.0, fx=1_400.0)
    assert tr.placed[1]["qty"] == 2
    tr.fills["o1"] = ("FILLED", 2.0, 96.5)
    msgs = m.step_sell(t, tr, 2, live=True, sellable=0.0, fx=1_400.0)
    assert t.status == "closed" and "손절 매도 끝" in msgs[0]
    # 손익 = (8 × 96.9 + 2 × 96.5 − 10 × 100) × 1,400
    assert "손해: -$31.80 (약 -4.5만 원) · -3.2%" in msgs[0]  # −31.8 × 1,400 = −44,520


def test_waits_when_closed_and_fails_after_max() -> None:
    t, tr = _held(), FakeTrader()
    t.status = "selling"
    assert m.step_sell(t, tr, 0, live=False, sellable=10.0) == [] and not tr.placed
    for i in range(m.MAX_SELL_ORDERS):
        m.step_sell(t, tr, i * 200_000, live=True, sellable=10.0)
        tr.fills[f"o{i}"] = ("CANCELED", 0.0, 0.0)
    msgs = m.step_sell(t, tr, 10**9, live=True, sellable=10.0)
    assert t.status == "failed" and "못 팖" in msgs[0]


def test_register_app_and_status_lines() -> None:
    t = m.register_app("2", "005930", "삼성전자", 12.7, 70_000.0, 0)
    assert t.status == "holding" and t.filled == 12 and t.stop == 67_900.0 and t.source == "app"
    lines = m.status_lines([t, _held()], {"005930": 69_000.0})
    assert "삼성전자 12주" in lines[0] and "-1.4%" in lines[0]
    assert "손절 $97.00" in lines[1] and "2분마다" in lines[-1]


def test_kr_stop_alert_shows_won_loss() -> None:
    t = m.register_app("3", "005930", "삼성전자", 10, 70_000.0, 0)
    tr = FakeTrader(bid=67_800.0)
    m.check_stop(t, 67_800.0)
    msgs = m.step_sell(t, tr, 0, live=True, sellable=10.0)
    assert "예상 손해 약: -2.2만 원 · -3.1%" in msgs[0]
