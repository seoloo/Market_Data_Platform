"""선물 일반 주문의 가격 한도 지정가 IOC (TRADING_CORE 시장가 금지, 2026-10-05).

mark ± 0.3% 지정가를 즉시 체결 아니면 취소로 보내고, 덜 차면 mark 를 다시 읽어 최대 3 번 건다.
"""

from __future__ import annotations

import itertools
import sqlite3
from decimal import Decimal

import pytest

from auto_trading.core.errors import ReconciliationError
from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import FuturesFilters, FuturesOrderType, OrderStatus
from auto_trading.execution.futures_orders import (
    DEFAULT_CAP,
    FuturesOrderManager,
    capped_price,
    futures_client_order_id,
)
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository

S = "BTCUSDT"
FILTERS = {S: FuturesFilters(Decimal("0.001"), Decimal("0.001"), Decimal(100), Decimal("0.1"))}
T0 = 1_790_207_999_999


def rig(**fake: object) -> tuple[FakeFuturesExchange, FuturesOrderManager, FuturesOrderRepository]:
    ex = FakeFuturesExchange(
        wallet=Decimal(1_000_000),
        prices={S: Decimal(50_000)},
        symbol_filters=FILTERS,
        **fake,  # type: ignore[arg-type]
    )
    conn: sqlite3.Connection = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    store = FuturesOrderRepository(conn)
    ticks = itertools.count(1)
    m = FuturesOrderManager(
        ex, store, mode="paper", strategy_id="o2", strategy_tag="o2", clock=lambda: next(ticks)
    )
    return ex, m, store


def test_capped_price_rounds_inside_the_cap() -> None:
    tick = Decimal("0.1")
    assert capped_price(Decimal("50000"), "BUY", DEFAULT_CAP, tick) == Decimal("50150.0")
    assert capped_price(Decimal("50000.07"), "BUY", DEFAULT_CAP, tick) == Decimal(
        "50150.0"
    )  # 50150.07 내림
    assert capped_price(Decimal("50000.07"), "SELL", DEFAULT_CAP, tick) == Decimal(
        "49850.1"
    )  # 49850.07 올림


def test_fake_ioc_fills_at_mark_only_when_limit_crosses() -> None:
    ex, _, _ = rig()
    hit = ex.place_limit_ioc(S, "BUY", Decimal("0.1"), Decimal(50_100), "a", reduce_only=False)
    assert hit.status is OrderStatus.FILLED and hit.avg_price == Decimal(50_000)
    assert hit.order_type is FuturesOrderType.LIMIT and hit.limit_price == Decimal(50_100)
    miss = ex.place_limit_ioc(S, "BUY", Decimal("0.1"), Decimal(49_900), "b", reduce_only=False)
    assert miss.status is OrderStatus.EXPIRED and miss.executed_qty == 0
    assert ex.get_order(S, "b") == miss and not ex.open_orders(S)  # 걸린 주문이 남지 않는다


def test_full_fill_is_one_limit_order() -> None:
    _, m, store = rig()
    res = m.submit_capped(
        decision_ms=T0, symbol=S, side="BUY", quantity=Decimal("0.1"), purpose="OPEN"
    )
    assert res.done and res.filled == Decimal("0.1") and res.stop_reason == "filled"
    (rec,) = store.all()
    assert (rec.order_type, rec.state, rec.limit_price) == ("LIMIT", "FILLED", Decimal("50150.0"))
    assert not rec.reduce_only


def test_partial_fills_requote_with_new_ids_up_to_three_times() -> None:
    _, m, store = rig(fill_ratio=Decimal("0.5"))
    res = m.submit_capped(
        decision_ms=T0, symbol=S, side="BUY", quantity=Decimal("0.8"), purpose="OPEN"
    )
    assert [r.filled_qty for r in res.records] == [Decimal("0.4"), Decimal("0.2"), Decimal("0.1")]
    assert res.filled == Decimal("0.7") and not res.done and res.stop_reason == "tries_exhausted"
    ids = [r.client_order_id for r in store.all()]
    assert ids == [futures_client_order_id("o2", T0, S, "OPEN", "B", k) for k in range(3)]
    assert all(r.state == "EXPIRED" for r in store.all())
    assert not m.blocked()  # IOC 는 끝난 주문이라 다음 진입을 막지 않는다


def test_price_moving_away_leaves_the_rest_unfilled() -> None:
    class Jumpy(FakeFuturesExchange):
        def mark_price(self, symbol: str) -> Decimal:
            price = super().mark_price(symbol)
            self.prices[symbol] = price * Decimal("1.01")  # 주문이 닿기 전에 1% 뛴다
            return price

    ex = Jumpy(wallet=Decimal(1_000_000), prices={S: Decimal(50_000)}, symbol_filters=FILTERS)
    conn = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    m = FuturesOrderManager(
        ex,
        FuturesOrderRepository(conn),
        mode="paper",
        strategy_id="o2",
        strategy_tag="o2",
        clock=lambda: 1,
    )
    res = m.submit_capped(
        decision_ms=T0, symbol=S, side="BUY", quantity=Decimal("0.1"), purpose="OPEN"
    )
    assert res.filled == 0 and len(res.records) == 3 and not res.done  # 한도 밖이면 사지 않는다
    assert ex.account().positions == {}


def test_reduce_only_close_and_unknown_stops_requoting() -> None:
    ex, m, store = rig()
    m.submit_capped(decision_ms=T0, symbol=S, side="BUY", quantity=Decimal("0.1"), purpose="OPEN")
    ex.lose_response_after_accept = 1
    res = m.submit_capped(
        decision_ms=T0, symbol=S, side="SELL", quantity=Decimal("0.1"), purpose="CLOSE"
    )
    assert res.stop_reason == "unresolved" and len(res.records) == 1
    assert res.records[0].state == "UNKNOWN" and res.records[0].reduce_only
    assert m.blocked()
    with pytest.raises(ReconciliationError):
        m.submit_capped(
            decision_ms=T0 + 1, symbol=S, side="BUY", quantity=Decimal("0.1"), purpose="OPEN"
        )
    (fixed,) = [r for r in m.recover() if r.purpose == "CLOSE"]
    assert fixed.state == "FILLED" and ex.account().positions == {}
    assert store.get(fixed.client_order_id).limit_price == Decimal("49850.0")  # type: ignore[union-attr]


def test_same_attempt_is_idempotent() -> None:
    ex, m, _ = rig()
    a = m.submit_limit(
        decision_ms=T0,
        symbol=S,
        side="BUY",
        quantity=Decimal("0.1"),
        price=Decimal(50_100),
        purpose="OPEN",
    )
    b = m.submit_limit(
        decision_ms=T0,
        symbol=S,
        side="BUY",
        quantity=Decimal("0.1"),
        price=Decimal(50_100),
        purpose="OPEN",
    )
    assert a == b and ex.placed_count == 1


def test_limit_purpose_must_be_trade() -> None:
    _, m, _ = rig()
    with pytest.raises(ValueError):
        m.submit_limit(
            decision_ms=T0,
            symbol=S,
            side="SELL",
            quantity=Decimal("0.1"),
            price=Decimal(1),
            purpose="PROTECT",
        )
