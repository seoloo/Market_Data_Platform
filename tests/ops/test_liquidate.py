"""'전부 정리' — 범위 · 계획 · 가격(1% 바닥) · 업비트 IOC · 토스 주문 수명 · Deneb 장부.

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
    deneb_to_book,
    floor_price,
    groups_of,
    job_text,
    run_futures_item,
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
        "vega_coins": ("BTC", "ETH", "SOL"),
        "toss_holdings": {"TQQQ": D(3), "SCHD": D(3), "BTQ": D("1.5"), "005930": D(2)},
        "toss_sellable": {"TQQQ": D(3), "SCHD": D(3), "BTQ": D("1.5"), "005930": D(2)},
        "deneb_qty": {"TQQQ": D(1), "SCHD": D(3)},
        "acc_qty": {"TQQQ": D(9), "BTQ": D("1.5"), "005930": D(2)},
        "is_kr": _kr,
    }
    kw.update(over)
    return build_items(groups, **kw)  # type: ignore[arg-type]


def test_scopes() -> None:
    assert groups_of("all") == ("vega", "deneb", "acc", "altair", "stock", "sirius")
    assert groups_of("sym:NVDA") == ()  # 한 종목 정리는 그룹을 멈추지 않는다
    assert groups_of("altair") == ("altair",)
    assert groups_of("deneb") == ("deneb",)
    with pytest.raises(ValueError):
        groups_of("everything")


def test_plan_vega_only_sells_vega_coins_and_skips_dust() -> None:
    items, notes = _plan(("vega",))
    assert [(i.venue, i.symbol, i.want) for i in items] == [("upbit", "BTC", D("0.01"))]
    assert any("ETH" in n and "5,000" in n for n in notes)  # 50 원어치 → 남김
    assert all(i.symbol != "XRP" for i in items)  # Vega 밖 코인은 건드리지 않음


def test_plan_splits_deneb_and_accumulation() -> None:
    g, _ = _plan(("deneb",))
    assert {(i.symbol, i.want, i.deneb_part) for i in g} == {
        ("TQQQ", D(1), D(1)),
        ("SCHD", D(3), D(3)),
    }
    a, notes = _plan(("acc",))
    got = {(i.symbol, i.venue, i.want) for i in a}
    assert got == {("TQQQ", "toss_us", D(2)), ("BTQ", "toss_us", D(1)), ("005930", "toss_kr", D(2))}
    assert all(i.deneb_part == 0 for i in a)
    assert any("BTQ" in n and "소수점" in n for n in notes)
    every, _ = _plan(("vega", "deneb", "acc"))
    tqqq = next(i for i in every if i.symbol == "TQQQ")
    assert (tqqq.want, tqqq.deneb_part, tqqq.groups) == (D(3), D(1), ["deneb", "acc"])


def test_plan_leaves_manual_holdings_alone() -> None:
    held = {"APH": D(65), "BTQ": D(2), "122630": D(90), "SCHD": D(3)}
    items, notes = _plan(
        ("vega", "deneb", "acc"),
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
    item = Item("upbit", "BTC", ["vega"], D("0.01"))
    c = _Upbit([D("0.004"), D("0.006"), D(1)], unknown_on=1)
    log = run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.sold == D("0.01") and item.status == "done"
    assert [s[0] for s in c.sent] == [D("0.01"), D("0.006")]
    assert len({s[2] for s in c.sent}) == 2  # 회차마다 새 identifier
    assert c.sent[0][1] == D(99_998_000) and c.sent[1][1] == D(99_996_000)  # 2 칸 → 4 칸
    assert "BTC 0.01 팜" in log[-1]


def test_upbit_fill_read_from_order_query_not_ack() -> None:
    item = Item("upbit", "BTC", ["vega"], D("0.01"))
    c = _Upbit([D("0.01")], ack_wait=True)
    run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.sold == D("0.01") and item.status == "done" and len(c.sent) == 1


def test_upbit_old_zero_record_refreshed_without_new_order() -> None:
    """2026-10-13: 첫 주문에 다 팔렸는데 0 으로 적혀 같은 수량을 다시 팔려 했다."""
    item = Item("upbit", "BTC", ["vega"], D("0.01"))
    c = _Upbit([D("0.01")], ack_wait=True)
    c.known["liq-J-BTC-0"] = _UFill(D("0.01"), D(1_000_000), D(500))
    item.orders.append(
        Order("liq-J-BTC-0", "liq-J-BTC-0", D("0.01"), D(1), 0, closed=True, status="IOC")
    )
    run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.sold == D("0.01") and item.status == "done" and c.sent == []


def test_upbit_no_balance_ends_item() -> None:
    item = Item("upbit", "BTC", ["vega"], D("0.01"))
    c = _Upbit([D(0)])
    c.empty = True
    log = run_upbit_item(item, c, "J", lambda: 0, lambda _s: None)
    assert item.status == "done" and "잔고 없음" in log[0]


def test_upbit_stops_with_leftover_after_rounds() -> None:
    item = Item("upbit", "BTC", ["vega"], D("0.01"))
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
    item = Item("toss_us", "SCHD", ["deneb"], D(3), deneb_part=D(3))
    t = _Toss()
    run_toss_item(item, t, "J1", 0, session_open=False)
    assert item.status == "waiting" and not t.sent
    log = run_toss_item(item, t, "J1", 1_000, session_open=True)
    assert t.sent == [("SCHD", 3, D("50.00"), "DAY", "liq-J1-SCHD-0")]  # 3 주가 차는 호가
    assert item.status == "working" and "지정가 $50.00" in log[0]
    t.orders["o0"] = _TStatus("FILLED", D(3), D(150), D("0.15"))
    run_toss_item(item, t, "J1", 2_000, session_open=True)
    assert item.status == "done" and item.sold == D(3)
    take, usd = deneb_to_book(item)
    assert take == D(3) and usd == D("149.85")
    item.deneb_booked += take
    assert deneb_to_book(item) == (D(0), D(0))


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
    it = Item("toss_us", "TQQQ", ["deneb", "acc"], D(3), deneb_part=D(1))
    it.orders.append(Order("o1", "c1", D(3), D("50.1"), 5, filled=D(2), amount=D("100.2")))
    job = Job("20261011T2300", "all", "텔레그램", 1, [it], notes=["n"])
    st.put(job)
    back = st.jobs()[0]
    assert back.items[0].orders[0].amount == D("100.2") and back.items[0].sold == D(2)
    assert back.items[0].deneb_part == D(1) and st.active()[0].job_id == job.job_id
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
    assert halted_for(halt, "vega") is None
    made = set_group_halts(halt, ("vega", "deneb"), "전부 정리 테스트")
    assert made == ["vega", "deneb"] and set_group_halts(halt, ("vega",), "x") == []
    assert halted_for(halt, "vega") == "전부 정리 테스트" and halted_for(halt, "acc") is None
    halt.write_text("전체\n", encoding="utf-8")
    assert halted_for(halt, "acc") == "전체"
    assert group_path(halt, "acc").name == "HALT_acc"
    job = Job("J", "deneb", "오르빗", 0, [Item("toss_us", "SCHD", ["deneb"], D(3))])
    text = job_text(job)
    assert "Deneb 만" in text and "토스 미국 SCHD 0/3" in text and "정지풀기" in text


def test_toss_unknown_end_status_closes_order() -> None:
    """장 마감에 만료된 당일 주문처럼 모르는 끝 상태도 끝난 주문 — 정리가 멈추지 않는다."""
    item = Item("toss_us", "TQQQ", ["deneb"], D(3), deneb_part=D(3))
    item.orders.append(Order("o0", "c0", D(3), D(50), 0))
    t = _Toss()
    t.orders["o0"] = _TStatus("EXPIRED", D(1), D(50), D("0.05"))
    run_toss_item(item, t, "J", 1, session_open=False)
    assert item.orders[0].closed and item.status == "waiting" and item.remaining == D(2)


def test_vega_includes_binance_coins_and_skips_dust() -> None:
    items, notes = _plan(
        ("vega",),
        upbit_balances={},
        binance_balances={"BTC": D("0.002"), "ETH": D("0.0001"), "BNB": D(1)},
        binance_prices={"BTC": D(60_000), "ETH": D(3_000)},
        binance_min={"BTC": D(5), "ETH": D(5)},
    )
    assert [(i.venue, i.symbol, i.want) for i in items] == [("binance", "BTC", D("0.002"))]
    assert items[0].currency == "USDT"
    assert any("ETH" in n and "바이낸스 최소 주문" in n for n in notes)


def test_binance_runner_sells_with_ioc_and_reads_fills() -> None:
    from auto_trading.exchange.spot import OrderStatus, SpotOrderResult
    from auto_trading.ops.liquidate import run_binance_item

    class Fake:
        def __init__(self) -> None:
            self.book: dict[str, SpotOrderResult] = {}

        def order_book(self, symbol: str, limit: int = 20) -> tuple[list, list]:  # type: ignore[type-arg]
            return [(D(60_000) - i, D(1)) for i in range(10)], []

        def place_limit_ioc(
            self, symbol: str, side: str, qty: D, price: D, ident: str
        ) -> SpotOrderResult:
            assert side == "SELL" and price == D(59_998)  # 매수 호가 세 번째 칸
            r = SpotOrderResult(
                ident, "1", symbol, side, OrderStatus("FILLED"), qty, qty, qty * price, D(0), ""
            )
            self.book[ident] = r
            return r

        def get_order(self, symbol: str, ident: str) -> SpotOrderResult | None:
            return self.book.get(ident)

    item = Item("binance", "BTC", ["vega"], D("0.002"))
    log = run_binance_item(
        item, Fake(), "j1", lambda: 0, lambda s: None, step=D("0.00001"), min_notional=D(5)
    )
    assert item.status == "done" and item.sold == D("0.002")
    assert "USDT" in log[-1]


def test_old_saved_job_with_glide_names_still_loads(tmp_path: Path) -> None:
    """2026-10-15 이름 변경 전 기록(glide_part · 범위 cruise)도 읽는다."""
    from auto_trading.ops.liquidate import Job

    job = Job.from_dict(
        {
            "job_id": "j1",
            "scope": "cruise",
            "source": "telegram",
            "created_ms": 1,
            "items": [
                {
                    "venue": "toss_us",
                    "symbol": "TQQQ",
                    "groups": ["glide"],
                    "want": "3",
                    "glide_part": "1",
                    "glide_booked": "0",
                    "orders": [],
                }
            ],
        }
    )
    assert job.scope == "vega" and job.items[0].groups == ["deneb"]
    assert job.items[0].deneb_part == D(1)
    assert groups_of("glide") == ("deneb",)


class _FutResult:
    def __init__(self, qty: Decimal, px: Decimal) -> None:
        self.executed_qty, self.avg_price, self.fee = qty, px, Decimal("0.01")


class FakeFutures:
    """한 번에 ``fill`` 만큼만 차는 가짜 선물 거래소."""

    def __init__(self, fill: Decimal) -> None:
        self.fill = fill
        self.sent: list[tuple[str, str, Decimal, Decimal, bool]] = []

    def mark_price(self, symbol: str) -> Decimal:
        return Decimal("80000")

    def place_limit_ioc(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
        *,
        reduce_only: bool,
    ) -> _FutResult:
        self.sent.append((symbol, side, quantity, price, reduce_only))
        return _FutResult(min(self.fill, quantity), price)

    def get_order(self, symbol: str, client_order_id: str) -> None:
        return None


def test_altair_items_close_longs_and_shorts() -> None:
    items, _notes = build_items(
        ("altair",),
        upbit_balances={},
        upbit_prices={},
        vega_coins=(),
        toss_holdings={},
        toss_sellable={},
        deneb_qty={},
        acc_qty={},
        is_kr=lambda s: False,
        altair_positions={"BTC": Decimal("0.011"), "ETH": Decimal("-0.28")},
    )
    assert [(i.venue, i.symbol, i.want, i.side) for i in items] == [
        ("binance_fut", "BTC", Decimal("0.011"), "SELL"),
        ("binance_fut", "ETH", Decimal("0.28"), "BUY"),
    ]
    assert items[0].currency == "USDC"


def test_run_futures_item_reduce_only_limit_steps_down_one_percent() -> None:
    it = Item("binance_fut", "BTC", ["altair"], Decimal("0.011"), side="SELL")
    c = FakeFutures(Decimal("0.004"))
    log = run_futures_item(
        it,
        c,
        "J",
        lambda: 0,
        lambda s: None,
        held=Decimal("0.011"),
        step=Decimal("0.001"),
        tick=Decimal("0.1"),
    )
    assert [x[3] for x in c.sent] == [Decimal("79840.0"), Decimal("79680.0"), Decimal("79520.0")]
    assert all(x[1] == "SELL" and x[4] is True and x[0] == "BTCUSDC" for x in c.sent)
    assert it.status == "done" and it.sold == Decimal("0.011") and "닫음" in log[-1]
    short = Item("binance_fut", "ETH", ["altair"], Decimal("0.28"), side="BUY")
    c2 = FakeFutures(Decimal("0"))
    run_futures_item(
        short,
        c2,
        "J",
        lambda: 0,
        lambda s: None,
        held=Decimal("0.28"),
        step=Decimal("0.001"),
        tick=Decimal("0.1"),
    )
    assert len(c2.sent) == 5 and c2.sent[-1][3] == Decimal("80800.0")  # +1% 까지만
    assert short.status == "working"
    gone = Item("binance_fut", "ETH", ["altair"], Decimal("0.28"), side="BUY")
    run_futures_item(
        gone,
        c2,
        "J",
        lambda: 0,
        lambda s: None,
        held=Decimal(0),
        step=Decimal("0.001"),
        tick=Decimal("0.1"),
    )
    assert gone.status == "done" and gone.note == "포지션 없음"


def _toss_plan(groups: tuple[str, ...], **kw: object) -> tuple[list[Item], list[str]]:
    base: dict[str, object] = {
        "upbit_balances": {},
        "upbit_prices": {},
        "vega_coins": (),
        "toss_holdings": {"NVDA": Decimal(10), "TQQQ": Decimal(12), "APH": Decimal(3)},
        "toss_sellable": {},
        "deneb_qty": {"TQQQ": Decimal(10)},
        "acc_qty": {},
        "is_kr": lambda s: False,
        "stock_qty": {"NVDA": Decimal(8), "TQQQ": Decimal(0)},
    }
    base.update(kw)
    return build_items(groups, **base)  # type: ignore[arg-type]


def test_stock_group_sells_only_ledger_shares() -> None:
    items, notes = _toss_plan(("stock",))
    assert [(i.symbol, i.want, i.groups) for i in items] == [("NVDA", Decimal(8), ["stock"])]
    assert any("수동 보유" in n and "NVDA 2" in n and "APH 3" in n for n in notes)


def test_single_symbol_sells_stock_and_manual_but_not_strategy_shares() -> None:
    items, _ = _toss_plan((), only_symbol="NVDA")
    assert [(i.symbol, i.want, i.groups) for i in items] == [
        ("NVDA", Decimal(10), ["stock", "manual"])
    ]
    items, notes = _toss_plan((), only_symbol="TQQQ")
    assert [(i.symbol, i.want) for i in items] == [("TQQQ", Decimal(2))]
    assert any("Deneb 몫 10" in n and "남김" in n for n in notes)
    items, notes = _toss_plan((), only_symbol="MSFT")
    assert not items and any("보유 없음" in n for n in notes)


def test_symbol_scope_label_and_text() -> None:
    from auto_trading.ops.liquidate import scope_label, symbol_of

    assert symbol_of("sym:nvda") == "NVDA" and symbol_of("all") is None
    assert scope_label("sym:NVDA") == "NVDA 한 종목" and scope_label("stock") == "개별주만"
    job = Job("J", "sym:NVDA", "텔레그램", 0, [Item("toss_us", "NVDA", ["stock"], Decimal(10))])
    text = job_text(job)
    assert "NVDA 한 종목" in text and "새 매수 정지" not in text
