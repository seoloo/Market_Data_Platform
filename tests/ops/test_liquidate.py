"""'전부 정리' — 범위 · 계획 · 가격(1% 바닥) · 업비트 IOC · 토스 주문 수명 · Glide 장부.

저장 · 일회용 버튼 번호 · 실행 잠금 · 그룹 정지 파일도.

가짜 클라이언트로만. 실제 주문 없음.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.core.errors import ExchangeError, OrderStateUnknownError
from auto_trading.ops.kill_switch import group_path, halted_for
from auto_trading.ops.liquidate import (
    TOSS_REPLACE_MS,
    Item,
    Job,
    Order,
    Store,
    build_items,
    choose_price,
    floor_price,
    glide_to_book,
    groups_of,
    job_text,
    run_toss_item,
    run_upbit_item,
    set_group_halts,
    upbit_level_price,
)

D = Decimal


def _kr(s: str) -> bool:
    return s.isdigit()


def _plan(groups: tuple[str, ...], **over: object) -> tuple[list[Item], list[str]]:
    kw: dict[str, object] = {
        "upbit_balances": {"BTC": D("0.01"), "ETH": D("0.00001"), "XRP": D(100), "KRW": D(5)},
        "upbit_prices": {"BTC": D(100_000_000), "ETH": D(5_000_000)},
        "cruise_coins": ("BTC", "ETH", "SOL"),
        "toss_holdings": {"TQQQ": D(3), "SCHD": D(3), "BTQ": D("1.5"), "005930": D(2)},
        "toss_sellable": {"TQQQ": D(3), "SCHD": D(3), "BTQ": D("1.5"), "005930": D(2)},
        "glide_qty": {"TQQQ": D(1), "SCHD": D(3)},
        "acc_qty": {"TQQQ": D(9), "BTQ": D("1.5"), "005930": D(2)},
        "is_kr": _kr,
    }
    kw.update(over)
    return build_items(groups, **kw)  # type: ignore[arg-type]


def test_scopes() -> None:
    assert groups_of("all") == ("cruise", "glide", "acc")
    assert groups_of("glide") == ("glide",)
    with pytest.raises(ValueError):
        groups_of("everything")


def test_plan_cruise_only_sells_cruise_coins_and_skips_dust() -> None:
    items, notes = _plan(("cruise",))
    assert [(i.venue, i.symbol, i.want) for i in items] == [("upbit", "BTC", D("0.01"))]
    assert any("ETH" in n and "5,000" in n for n in notes)  # 50 원어치 → 남김
    assert all(i.symbol != "XRP" for i in items)  # Cruise 밖 코인은 건드리지 않음


def test_plan_splits_glide_and_accumulation() -> None:
    g, _ = _plan(("glide",))
    assert {(i.symbol, i.want, i.glide_part) for i in g} == {
        ("TQQQ", D(1), D(1)),
        ("SCHD", D(3), D(3)),
    }
    a, notes = _plan(("acc",))
    got = {(i.symbol, i.venue, i.want) for i in a}
    assert got == {("TQQQ", "toss_us", D(2)), ("BTQ", "toss_us", D(1)), ("005930", "toss_kr", D(2))}
    assert all(i.glide_part == 0 for i in a)
    assert any("BTQ" in n and "소수점" in n for n in notes)
    every, _ = _plan(("cruise", "glide", "acc"))
    tqqq = next(i for i in every if i.symbol == "TQQQ")
    assert (tqqq.want, tqqq.glide_part, tqqq.groups) == (D(3), D(1), ["glide", "acc"])


def test_plan_leaves_manual_holdings_alone() -> None:
    held = {"APH": D(65), "BTQ": D(2), "122630": D(90), "SCHD": D(3)}
    items, notes = _plan(
        ("cruise", "glide", "acc"),
        toss_holdings=held,
        toss_sellable=held,
        acc_qty={"BTQ": D(1)},
    )
    assert {(i.symbol, i.want) for i in items if i.venue != "upbit"} == {
        ("BTQ", D(1)),
        ("SCHD", D(3)),
    }
    manual = next(n for n in notes if n.startswith("수동 보유"))
    assert "APH 65" in manual and "122630 90" in manual and "BTQ 1" in manual


def test_plan_respects_sellable() -> None:
    items, notes = _plan(("acc",), toss_sellable={"TQQQ": D(1), "BTQ": D(0), "005930": D(2)})
    assert {(i.symbol, i.want) for i in items} == {("TQQQ", D(1)), ("005930", D(2))}
    assert any("묶여" in n for n in notes)


def test_price_never_below_one_percent_of_best_bid() -> None:
    bids = [(D(100), D(1)), (D("99.5"), D(1)), (D("99.2"), D(5)), (D("98.9"), D(100))]
    assert choose_price(bids, D(1)) == D(100)
    assert choose_price(bids, D(2)) == D("99.5")
    assert choose_price(bids, D(500)) == D("99.2")  # 1% 안 마지막 호가에서 멈춤
    assert choose_price([], D(1)) is None
    up = [D(x) for x in ("100", "99.9", "99.8", "99.7", "99.6", "98")]
    assert upbit_level_price(up, 0) == D("99.8")
    assert upbit_level_price(up, 5) == D("99.6")  # 98 은 1% 밖
    assert floor_price(D("12.349"), "toss_us") == D("12.34")
    assert floor_price(D("0.98765"), "toss_us") == D("0.9876")


@dataclass
class _UFill:
    executed_volume: Decimal
    funds: Decimal
    paid_fee: Decimal
    state: str = "done"


class _Upbit:
    def __init__(
        self, fills: list[Decimal], unknown_on: int = -1, *, ack_wait: bool = False
    ) -> None:
        self.fills, self.unknown_on, self.ack_wait = fills, unknown_on, ack_wait
        self.empty = False
        self.sent: list[tuple[Decimal, Decimal, str]] = []
        self.known: dict[str, _UFill] = {}
        self.bids = [D(100_000_000) - D(1000) * k for k in range(10)]

    def orderbook(self, market: str) -> tuple[list[Decimal], list[Decimal]]:
        return [], self.bids

    def place_limit_ioc(
        self, market: str, side: str, volume: Decimal, price: Decimal, identifier: str
    ) -> _UFill:
        assert side == "ask" and market == "KRW-BTC"
        if self.empty:
            raise ExchangeError('업비트 주문 거절 400: {"error":{"name":"insufficient_funds_ask"}}')
        n = len(self.sent)
        self.sent.append((volume, price, identifier))
        f = min(volume, self.fills[n])
        fill = _UFill(f, f * price, f * price * D("0.0005"))
        self.known[identifier] = fill
        if n == self.unknown_on:
            raise OrderStateUnknownError("timeout")
        if self.ack_wait:  # 실제 업비트: 주문 응답은 체결 전 상태(wait · 0)
            return _UFill(D(0), D(0), D(0), "wait")
        return fill

    def get_order(self, identifier: str) -> _UFill | None:
        return self.known.get(identifier)


def test_upbit_ioc_rounds_until_sold() -> None:
    item = Item("upbit", "BTC", ["cruise"], D("0.01"))
    c = _Upbit([D("0.004"), D("0.006"), D(1)], unknown_on=1)
    log = run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.sold == D("0.01") and item.status == "done"
    assert [s[0] for s in c.sent] == [D("0.01"), D("0.006")]
    assert len({s[2] for s in c.sent}) == 2  # 회차마다 새 identifier
    assert c.sent[0][1] == D(99_998_000) and c.sent[1][1] == D(99_996_000)  # 2 칸 → 4 칸
    assert "BTC 0.01 팜" in log[-1]


def test_upbit_fill_read_from_order_query_not_ack() -> None:
    item = Item("upbit", "BTC", ["cruise"], D("0.01"))
    c = _Upbit([D("0.01")], ack_wait=True)
    run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.sold == D("0.01") and item.status == "done" and len(c.sent) == 1


def test_upbit_old_zero_record_refreshed_without_new_order() -> None:
    """2026-10-13: 첫 주문에 다 팔렸는데 0 으로 적혀 같은 수량을 다시 팔려 했다."""
    item = Item("upbit", "BTC", ["cruise"], D("0.01"))
    c = _Upbit([D("0.01")], ack_wait=True)
    c.known["liq-J-BTC-0"] = _UFill(D("0.01"), D(1_000_000), D(500))
    item.orders.append(
        Order("liq-J-BTC-0", "liq-J-BTC-0", D("0.01"), D(1), 0, closed=True, status="IOC")
    )
    run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.sold == D("0.01") and item.status == "done" and c.sent == []


def test_upbit_no_balance_ends_item() -> None:
    item = Item("upbit", "BTC", ["cruise"], D("0.01"))
    c = _Upbit([D(0)])
    c.empty = True
    log = run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.status == "done" and "잔고 없음" in log[0]


def test_upbit_stops_with_leftover_after_rounds() -> None:
    item = Item("upbit", "BTC", ["cruise"], D("0.01"))
    c = _Upbit([D(0)] * 10)
    run_upbit_item(item, c, "J", lambda: 0, lambda _s: None, rounds=3)
    assert item.status == "working" and len(c.sent) == 3 and item.remaining == D("0.01")


@dataclass
class _TStatus:
    status: str
    filled_qty: Decimal
    filled_amount: Decimal | None
    commission: Decimal | None


@dataclass
class _TRes:
    accepted: bool
    code: str
    message: str = ""


class _Toss:
    def __init__(self) -> None:
        self.orders: dict[str, _TStatus] = {}
        self.sent: list[tuple[str, int, Decimal, str, str]] = []
        self.canceled: list[str] = []
        self.reject = False
        self.unknown = False

    def bids(self, symbol: str) -> list[tuple[Decimal, Decimal]]:
        return [(D("50.10"), D(1)), (D("50.00"), D(10))]

    def order(
        self,
        symbol: str,
        side: str,
        qty: int,
        limit: Decimal,
        *,
        client_order_id: str,
        time_in_force: str = "CLS",
    ) -> _TRes:
        if self.unknown:
            raise OrderStateUnknownError("timeout")
        if self.reject:
            return _TRes(False, "order-hours-closed", "장 시간 아님")
        oid = f"o{len(self.sent)}"
        self.sent.append((symbol, qty, limit, time_in_force, client_order_id))
        self.orders[oid] = _TStatus("PENDING", D(0), None, None)
        return _TRes(True, oid)

    def order_status(self, order_id: str) -> _TStatus:
        return self.orders[order_id]

    def cancel(self, order_id: str) -> _TRes:
        self.canceled.append(order_id)
        return _TRes(True, "c" + order_id)


def test_toss_waits_for_session_then_sells_day_limit() -> None:
    item = Item("toss_us", "SCHD", ["glide"], D(3), glide_part=D(3))
    t = _Toss()
    run_toss_item(item, t, "J1", 0, session_open=False)
    assert item.status == "waiting" and not t.sent
    log = run_toss_item(item, t, "J1", 1_000, session_open=True)
    assert t.sent == [("SCHD", 3, D("50.00"), "DAY", "liq-J1-SCHD-0")]  # 3 주가 차는 호가
    assert item.status == "working" and "지정가 $50.00" in log[0]
    t.orders["o0"] = _TStatus("FILLED", D(3), D(150), D("0.15"))
    run_toss_item(item, t, "J1", 2_000, session_open=True)
    assert item.status == "done" and item.sold == D(3)
    take, usd = glide_to_book(item)
    assert take == D(3) and usd == D("149.85")
    item.glide_booked += take
    assert glide_to_book(item) == (D(0), D(0))


def test_toss_cancels_stale_partial_and_replaces_rest() -> None:
    item = Item("toss_us", "TQQQ", ["acc"], D(5))
    t = _Toss()
    run_toss_item(item, t, "J", 0, session_open=True)
    t.orders["o0"] = _TStatus("PARTIAL_FILLED", D(2), D(100), D("0.1"))
    run_toss_item(item, t, "J", TOSS_REPLACE_MS - 1, session_open=True)
    assert not t.canceled
    run_toss_item(item, t, "J", TOSS_REPLACE_MS, session_open=True)
    assert t.canceled == ["o0"] and len(t.sent) == 1  # 취소 확인 전에는 새로 안 냄
    t.orders["o0"] = _TStatus("CANCELED", D(2), D(100), D("0.1"))
    run_toss_item(item, t, "J", TOSS_REPLACE_MS + 1, session_open=True)
    assert t.sent[1][1] == 3 and t.sent[1][4] == "liq-J-TQQQ-1"


def test_toss_session_close_keeps_waiting_and_rejects_retry() -> None:
    item = Item("toss_us", "TQQQ", ["acc"], D(2))
    t = _Toss()
    t.reject = True
    log = run_toss_item(item, t, "J", 0, session_open=True)
    assert item.status == "working" and "거절" in log[0]
    t.reject = False
    run_toss_item(item, t, "J", 1, session_open=True)
    assert len(t.sent) == 1


def test_toss_unknown_result_is_not_resent() -> None:
    item = Item("toss_kr", "005930", ["acc"], D(2))
    t = _Toss()
    t.unknown = True
    run_toss_item(item, t, "J", 0, session_open=True)
    assert item.status == "failed" and "결과 모름" in item.note
    t.unknown = False
    run_toss_item(item, t, "J", 1, session_open=True)  # 실패한 항목은 부르는 쪽이 건너뛴다
    assert item.orders[0].status == "UNKNOWN"


def test_toss_blocked_order_marks_failed() -> None:
    class Blocked(_Toss):
        def order(self, *a: object, **k: object) -> _TRes:
            raise ExchangeError("TOSS_TRADING_ENABLED 꺼짐")

    item = Item("toss_us", "TQQQ", ["acc"], D(1))
    run_toss_item(item, Blocked(), "J", 0, session_open=True)
    assert item.status == "failed" and "막힘" in item.note


def test_store_roundtrip_requests_and_lock(tmp_path: Path) -> None:
    st = Store(tmp_path / "liq")
    it = Item("toss_us", "TQQQ", ["glide", "acc"], D(3), glide_part=D(1))
    it.orders.append(Order("o1", "c1", D(3), D("50.1"), 5, filled=D(2), amount=D("100.2")))
    job = Job("20261011T2300", "all", "텔레그램", 1, [it], notes=["n"])
    st.put(job)
    back = st.jobs()[0]
    assert back.items[0].orders[0].amount == D("100.2") and back.items[0].sold == D(2)
    assert back.items[0].glide_part == D(1) and st.active()[0].job_id == job.job_id
    n = st.new_request(0, "liq")
    assert not st.use_request(n, 1, "unh")  # 종류가 다르면 안 됨 (번호는 버려짐)
    n = st.new_request(0, "liq")
    assert st.use_request(n, 1, "liq") and not st.use_request(n, 2, "liq")  # 한 번만
    n = st.new_request(0, "liq")
    assert not st.use_request(n, 5 * 60_000, "liq")  # 5 분 지남
    with st.running() as ok:
        assert ok
        with st.running() as again:
            assert not again


def test_group_halts_and_text(tmp_path: Path) -> None:
    halt = tmp_path / "data" / "HALT"
    assert halted_for(halt, "cruise") is None
    made = set_group_halts(halt, ("cruise", "glide"), "전부 정리 테스트")
    assert made == ["cruise", "glide"] and set_group_halts(halt, ("cruise",), "x") == []
    assert halted_for(halt, "cruise") == "전부 정리 테스트" and halted_for(halt, "acc") is None
    halt.write_text("전체\n", encoding="utf-8")
    assert halted_for(halt, "acc") == "전체"
    assert group_path(halt, "acc").name == "HALT_acc"
    job = Job("J", "glide", "오르빗", 0, [Item("toss_us", "SCHD", ["glide"], D(3))])
    text = job_text(job)
    assert "Glide 만" in text and "토스 미국 SCHD 0/3" in text and "정지풀기" in text
