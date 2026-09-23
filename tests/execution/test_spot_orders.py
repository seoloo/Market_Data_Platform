"""현물 주문 실행 (Phase 5): 멱등(100회 → 1건)·장애 주입·강제 종료/재시작 10회 복구·상태 성질."""

from __future__ import annotations

import contextlib
import itertools
import random
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from auto_trading.exchange.fake_spot import FakeSpotExchange
from auto_trading.exchange.spot import SpotFilters, SpotOrderResult
from auto_trading.execution.spot_orders import SpotOrderManager, client_order_id
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.spot_orders import (
    OPEN_STATES,
    TERMINAL_STATES,
    SpotOrderRecord,
    SpotOrderRepository,
)

FILTERS = {"BTCUSDT": SpotFilters(Decimal("0.00001"), Decimal("0.00001"), Decimal(5))}
DAY = 86_400_000
T0 = 1_788_220_799_999


class Crash(BaseException):
    """프로세스 강제 종료를 흉내 낸다 — 관리자가 잡지 않는다."""


class CrashingExchange:
    """``crash_before_send`` / ``crash_after_accept`` 를 한 번씩 일으키는 래퍼."""

    def __init__(self, inner: FakeSpotExchange) -> None:
        self.inner = inner
        self.crash_before_send = False
        self.crash_after_accept = False

    def filters(self, symbol: str) -> SpotFilters:
        return self.inner.filters(symbol)

    def balances(self) -> dict[str, Decimal]:
        return self.inner.balances()

    def last_price(self, symbol: str) -> Decimal:
        return self.inner.last_price(symbol)

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        if self.crash_before_send:
            self.crash_before_send = False
            raise Crash
        result = self.inner.place_market_order(symbol, side, quantity, client_order_id)
        if self.crash_after_accept:
            self.crash_after_accept = False
            raise Crash
        return result

    def get_order(self, symbol: str, client_order_id: str) -> SpotOrderResult | None:
        return self.inner.get_order(symbol, client_order_id)


def new_fake(**kwargs: object) -> FakeSpotExchange:
    return FakeSpotExchange(
        balance={"USDT": Decimal(1_000_000)},
        prices={"BTCUSDT": Decimal(50_000)},
        symbol_filters=FILTERS,
        **kwargs,  # type: ignore[arg-type]
    )


def open_db(path: Path | str) -> sqlite3.Connection:
    conn = connect(path)
    apply_migrations(conn, now_ms=1)
    return conn


def manager(exchange: object, conn: sqlite3.Connection) -> SpotOrderManager:
    ticks = itertools.count(1)
    return SpotOrderManager(
        exchange,  # type: ignore[arg-type]
        SpotOrderRepository(conn),
        mode="paper",
        strategy_id="t2_long",
        strategy_tag="t2l",
        clock=lambda: next(ticks),
    )


def buy(m: SpotOrderManager, decision_ms: int = T0, qty: str = "0.01") -> SpotOrderRecord:
    return m.submit(
        decision_ms=decision_ms,
        symbol="BTCUSDT",
        side="BUY",
        quantity=Decimal(qty),
        target_weight=Decimal("0.3"),
    )


def test_client_order_id_is_deterministic_and_fits_binance_limit() -> None:
    cid = client_order_id("t2l", T0, "BTCUSDT", "BUY")
    assert cid == client_order_id("t2l", T0, "BTCUSDT", "BUY") == f"t2l-{T0}-BTCUSDT-B"
    assert len(cid) <= 36
    with pytest.raises(ValueError, match="36"):
        client_order_id("t2l-very-long-tag", T0, "1000SHIBUSDT", "SELL")


def test_same_intent_100_times_places_one_order(tmp_path: Path) -> None:
    ex = new_fake()
    m = manager(ex, open_db(tmp_path / "db.sqlite"))
    records = [buy(m) for _ in range(100)]
    assert ex.placed_count == 1
    assert {r.state for r in records} == {"FILLED"}
    assert len({r.client_order_id for r in records}) == 1


def test_rejected_order_is_terminal_and_not_resent(tmp_path: Path) -> None:
    ex = new_fake()
    m = manager(ex, open_db(tmp_path / "db.sqlite"))
    assert buy(m, qty="0.00005").state == "REJECTED"  # 2.5 USDT < 5
    assert buy(m, qty="0.00005").state == "REJECTED"
    assert ex.placed_count == 0 and not m.blocked()


def test_network_error_before_accept_becomes_not_placed_without_resend(tmp_path: Path) -> None:
    ex = new_fake(fail_before_accept=1)
    m = manager(ex, open_db(tmp_path / "db.sqlite"))
    assert buy(m).state == "UNKNOWN"
    assert m.blocked()  # 해소 전 신규 주문 금지
    (recovered,) = m.recover()
    assert recovered.state == "NOT_PLACED"
    assert buy(m).state == "NOT_PLACED"  # 같은 의도를 다시 보내지 않는다
    assert ex.placed_count == 0 and not m.blocked()


def test_lost_response_is_recovered_from_exchange(tmp_path: Path) -> None:
    ex = new_fake(lose_response_after_accept=1)
    m = manager(ex, open_db(tmp_path / "db.sqlite"))
    assert buy(m).state == "UNKNOWN"
    (recovered,) = m.recover()
    assert recovered.state == "FILLED" and recovered.filled_qty == Decimal("0.01")
    assert ex.placed_count == 1


def test_failed_lookup_keeps_order_unresolved(tmp_path: Path) -> None:
    ex = new_fake(lose_response_after_accept=1, fail_lookups=1)
    m = manager(ex, open_db(tmp_path / "db.sqlite"))
    buy(m)
    (still,) = m.recover()
    assert still.state == "UNKNOWN" and m.blocked()
    (done,) = m.recover()
    assert done.state == "FILLED" and not m.blocked()


def test_ten_forced_restarts_recover_without_duplicates(tmp_path: Path) -> None:
    """Phase 5 게이트: 강제 종료/재시작 10회 복구. 매 회 다른 지점에서 죽는다."""
    path = tmp_path / "db.sqlite"
    ex = CrashingExchange(new_fake())
    rng = random.Random(20260924)
    for day in range(10):
        decision = T0 + day * DAY
        conn = open_db(path)
        m = manager(ex, conn)
        m.recover()
        point = rng.choice(["before_send", "after_accept", "none"])
        ex.crash_before_send = point == "before_send"
        ex.crash_after_accept = point == "after_accept"
        try:
            buy(m, decision)
        except Crash:
            conn.close()  # 프로세스 사망
            conn = open_db(path)  # 재시작
            m = manager(ex, conn)
            m.recover()
            buy(m, decision)  # 같은 의도 재실행 — 멱등해야 한다
        conn.close()
    conn = open_db(path)
    records = SpotOrderRepository(conn).all()
    assert len(records) == 10
    assert all(r.state in TERMINAL_STATES for r in records)
    placed = set(ex.inner.orders)
    assert all((r.state == "FILLED") == (r.client_order_id in placed) for r in records)
    assert ex.inner.placed_count == len(placed)  # 같은 ID로 두 번 들어간 주문 없음
    filled_btc = sum((r.filled_qty for r in records if r.state == "FILLED"), Decimal(0))
    assert ex.inner.balance["BTC"] == filled_btc


FAULTS = st.sampled_from(["ok", "net_before", "lost_after", "crash_before", "crash_after"])


@settings(max_examples=60, deadline=None)
@given(steps=st.lists(st.tuples(FAULTS, st.integers(1, 3)), min_size=1, max_size=12))
def test_state_invariants_under_random_faults(steps: list[tuple[str, int]]) -> None:
    """임의 장애 순서에서도 ID당 주문 최대 1건, 복구 뒤 기록은 거래소와 일치하고 모두 종결된다."""
    ex = CrashingExchange(new_fake())
    conn = open_db(":memory:")
    for i, (fault, repeats) in enumerate(steps):
        ex.inner.fail_before_accept = int(fault == "net_before")
        ex.inner.lose_response_after_accept = int(fault == "lost_after")
        ex.crash_before_send = fault == "crash_before"
        ex.crash_after_accept = fault == "crash_after"
        for _ in range(repeats):
            m = manager(ex, conn)
            with contextlib.suppress(Crash):
                buy(m, T0 + i * DAY)
        ex.crash_before_send = ex.crash_after_accept = False
    m = manager(ex, conn)
    m.recover()
    records = {r.client_order_id: r for r in SpotOrderRepository(conn).all()}
    assert not [r for r in records.values() if r.state in OPEN_STATES]
    for cid in ex.inner.orders:
        assert records[cid].state == "FILLED"
    assert ex.inner.placed_count == len(ex.inner.orders)
    assert not any("Duplicate" in (r.error or "") for r in records.values())
