"""선물 주문 실행 (Phase 5 선물 게이트).

동일 의도 100회 → 주문 1건, 장애 주입, 강제 종료/재시작 10회 복구, 무작위 장애 상태 성질.
선물 고유: 결과를 모르는 주문은 신규 진입만 막고 축소·청산·보호는 막지 않는다.
걸린 보호 스톱은 정상 상태이며, 발동·만료는 복구 때 거래소 조회로 반영한다.
"""

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

from auto_trading.core.errors import ReconciliationError
from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import (
    FuturesAccount,
    FuturesFilters,
    FuturesOrderResult,
)
from auto_trading.execution.futures_orders import FuturesOrderManager, futures_client_order_id
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import (
    TERMINAL_STATES,
    FuturesOrderRecord,
    FuturesOrderRepository,
)

FILTERS = {
    "BTCUSDT": FuturesFilters(Decimal("0.001"), Decimal("0.001"), Decimal(100), Decimal("0.1"))
}
DAY = 86_400_000
T0 = 1_790_207_999_999


class Crash(BaseException):
    """프로세스 강제 종료를 흉내 낸다 — 관리자가 잡지 않는다."""


class CrashingExchange:
    """``crash_before_send`` / ``crash_after_accept`` 를 한 번씩 일으키는 래퍼."""

    def __init__(self, inner: FakeFuturesExchange) -> None:
        self.inner = inner
        self.crash_before_send = False
        self.crash_after_accept = False

    def _around(self, call: object) -> FuturesOrderResult:
        if self.crash_before_send:
            self.crash_before_send = False
            raise Crash
        result: FuturesOrderResult = call()  # type: ignore[operator]
        if self.crash_after_accept:
            self.crash_after_accept = False
            raise Crash
        return result

    def filters(self, symbol: str) -> FuturesFilters:
        return self.inner.filters(symbol)

    def account(self) -> FuturesAccount:
        return self.inner.account()

    def mark_price(self, symbol: str) -> Decimal:
        return self.inner.mark_price(symbol)

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str, *, reduce_only: bool
    ) -> FuturesOrderResult:
        return self._around(
            lambda: self.inner.place_market_order(
                symbol, side, quantity, client_order_id, reduce_only=reduce_only
            )
        )

    def place_stop_market(
        self, symbol: str, side: str, stop_price: Decimal, client_order_id: str
    ) -> FuturesOrderResult:
        return self._around(
            lambda: self.inner.place_stop_market(symbol, side, stop_price, client_order_id)
        )

    def cancel_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self.inner.cancel_order(symbol, client_order_id)

    def get_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self.inner.get_order(symbol, client_order_id)

    def open_orders(self, symbol: str) -> list[FuturesOrderResult]:
        return self.inner.open_orders(symbol)

    def get_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self.inner.get_stop(symbol, client_order_id)

    def cancel_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        return self.inner.cancel_stop(symbol, client_order_id)

    def open_stops(self, symbol: str) -> list[FuturesOrderResult]:
        return self.inner.open_stops(symbol)


def new_fake(**kwargs: object) -> FakeFuturesExchange:
    return FakeFuturesExchange(
        wallet=Decimal(1_000_000),
        prices={"BTCUSDT": Decimal(50_000)},
        symbol_filters=FILTERS,
        **kwargs,  # type: ignore[arg-type]
    )


def open_db(path: Path | str) -> sqlite3.Connection:
    conn = connect(path)
    apply_migrations(conn, now_ms=1)
    return conn


def manager(exchange: object, conn: sqlite3.Connection) -> FuturesOrderManager:
    ticks = itertools.count(1)
    return FuturesOrderManager(
        exchange,  # type: ignore[arg-type]
        FuturesOrderRepository(conn),
        mode="paper",
        strategy_id="o1_overlay",
        strategy_tag="o1",
        clock=lambda: next(ticks),
    )


def open_long(
    m: FuturesOrderManager, decision_ms: int = T0, qty: str = "0.1"
) -> FuturesOrderRecord:
    return m.submit_market(
        decision_ms=decision_ms, symbol="BTCUSDT", side="BUY", quantity=Decimal(qty), purpose="OPEN"
    )


def protect(
    m: FuturesOrderManager, decision_ms: int = T0, stop: str = "45000"
) -> FuturesOrderRecord:
    return m.place_protective_stop(
        decision_ms=decision_ms, symbol="BTCUSDT", side="SELL", stop_price=Decimal(stop)
    )


class TestClientOrderId:
    def test_deterministic_and_within_binance_rules(self) -> None:
        cid = futures_client_order_id("o1", T0, "BTCUSDT", "OPEN", "BUY")
        assert cid == f"o1-{T0}-BTCUSDT-OB"
        assert cid == futures_client_order_id("o1", T0, "BTCUSDT", "OPEN", "BUY")
        assert len(cid) <= 36

    def test_purposes_and_attempts_give_distinct_ids(self) -> None:
        ids = {
            futures_client_order_id("o1", T0, "BTCUSDT", p, "SELL", a)
            for p in ("OPEN", "REDUCE", "CLOSE", "PROTECT")
            for a in (0, 1)
        }
        assert len(ids) == 8

    @pytest.mark.parametrize(
        ("tag", "symbol", "purpose"),
        [("too-long-strategy-tag", "1000SHIBUSDT", "OPEN"), ("o1", "BTCUSDT", "HEDGE")],
    )
    def test_invalid_ids_are_refused(self, tag: str, symbol: str, purpose: str) -> None:
        with pytest.raises(ValueError):
            futures_client_order_id(tag, T0, symbol, purpose, "BUY")


class TestIdempotence:
    def test_same_intent_100_times_places_one_order(self, tmp_path: Path) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        records = [open_long(m) for _ in range(100)]
        assert ex.placed_count == 1
        assert {r.state for r in records} == {"FILLED"}
        assert ex.account().positions["BTCUSDT"].quantity == Decimal("0.1")

    def test_same_stop_100_times_places_one_stop(self, tmp_path: Path) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        stops = [protect(m) for _ in range(100)]
        assert {s.state for s in stops} == {"SUBMITTED"}
        assert len(ex.open_stops("BTCUSDT")) == 1


class TestFaults:
    def test_rejected_order_is_terminal_and_not_resent(self, tmp_path: Path) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        assert open_long(m, qty="0.001").state == "REJECTED"  # 50 < 100
        assert open_long(m, qty="0.001").state == "REJECTED"
        assert ex.placed_count == 0 and not m.blocked()

    def test_network_error_becomes_not_placed_without_resend(self, tmp_path: Path) -> None:
        ex = new_fake(fail_before_accept=1)
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        assert open_long(m).state == "UNKNOWN"
        assert m.blocked()
        (recovered,) = m.recover()
        assert recovered.state == "NOT_PLACED"
        assert open_long(m).state == "NOT_PLACED"  # 같은 의도를 다시 보내지 않는다
        assert ex.placed_count == 0 and not m.blocked()

    def test_explicit_attempt_can_reissue_after_not_placed(self, tmp_path: Path) -> None:
        ex = new_fake(fail_before_accept=1)
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        m.recover()
        again = m.submit_market(
            decision_ms=T0,
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.1"),
            purpose="OPEN",
            attempt=1,
        )
        assert again.state == "FILLED" and again.client_order_id.endswith("-1")

    def test_lost_response_is_recovered_from_exchange(self, tmp_path: Path) -> None:
        ex = new_fake(lose_response_after_accept=1)
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        assert open_long(m).state == "UNKNOWN"
        (recovered,) = m.recover()
        assert recovered.state == "FILLED" and recovered.filled_qty == Decimal("0.1")
        assert recovered.avg_price == Decimal(50_000)

    def test_failed_lookup_keeps_order_unresolved(self, tmp_path: Path) -> None:
        ex = new_fake(lose_response_after_accept=1, fail_lookups=1)
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        (still,) = m.recover()
        assert still.state == "UNKNOWN" and m.blocked()
        (done,) = m.recover()
        assert done.state == "FILLED" and not m.blocked()


class TestRiskReductionIsNeverBlocked:
    def _blocked_with_long(self, tmp_path: Path) -> tuple[FakeFuturesExchange, FuturesOrderManager]:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        ex.fail_before_accept = 1
        open_long(m, T0 + DAY)  # 결과 모름 → blocked
        assert m.blocked()
        return ex, m

    def test_new_entry_is_refused_while_blocked(self, tmp_path: Path) -> None:
        ex, m = self._blocked_with_long(tmp_path)
        with pytest.raises(ReconciliationError, match="신규 진입"):
            open_long(m, T0 + 2 * DAY)
        assert ex.placed_count == 1

    def test_close_and_protect_still_go_through(self, tmp_path: Path) -> None:
        ex, m = self._blocked_with_long(tmp_path)
        stop = protect(m, T0 + DAY)
        closed = m.submit_market(
            decision_ms=T0 + DAY,
            symbol="BTCUSDT",
            side="SELL",
            quantity=Decimal("0.1"),
            purpose="CLOSE",
        )
        assert stop.state == "SUBMITTED"
        assert closed.state == "FILLED" and closed.reduce_only
        assert ex.account().positions == {}


class TestProtectiveStops:
    def test_a_working_stop_does_not_block(self, tmp_path: Path) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        protect(m)
        assert not m.blocked()
        assert [s.client_order_id for s in m.working_stops("BTCUSDT")] == [f"o1-{T0}-BTCUSDT-PS"]

    def test_triggered_stop_is_recorded_on_recover(self, tmp_path: Path) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        protect(m)
        ex.set_mark_price("BTCUSDT", Decimal(44_000))
        (stop,) = m.recover()
        assert stop.state == "FILLED"
        assert stop.avg_price == Decimal(44_000) and stop.filled_qty == Decimal("0.1")
        assert m.working_stops() == []

    def test_cancel_moves_the_stop_to_canceled(self, tmp_path: Path) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        stop = protect(m)
        assert m.cancel(stop.client_order_id).state == "CANCELED"
        assert ex.open_stops("BTCUSDT") == []

    def test_cancelling_an_already_triggered_stop_reads_the_final_state(
        self, tmp_path: Path
    ) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        stop = protect(m)
        ex.set_mark_price("BTCUSDT", Decimal(44_000))
        assert m.cancel(stop.client_order_id).state == "FILLED"

    def test_failed_cancel_keeps_the_stop_working(self, tmp_path: Path) -> None:
        ex = new_fake(fail_cancels=1)
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        stop = protect(m)
        kept = m.cancel(stop.client_order_id)
        assert kept.state == "SUBMITTED" and "취소 실패" in (kept.error or "")

    def test_rejected_stop_is_terminal(self, tmp_path: Path) -> None:
        ex = new_fake()
        m = manager(ex, open_db(tmp_path / "db.sqlite"))
        open_long(m)
        assert protect(m, stop="55000").state == "REJECTED"  # 즉시 발동 위치


def _entry_then_stop(
    m: FuturesOrderManager,
    ex: CrashingExchange,
    decision: int,
    *,
    crash_at: str | None = None,
    crash_point: str = "none",
) -> None:
    """진입 → 보호 스톱. ``crash_at`` 단계에서 ``crash_point`` 위치에 강제 종료를 건다."""
    for name, call in (("entry", open_long), ("stop", protect)):
        if name == crash_at:
            ex.crash_before_send = crash_point == "before_send"
            ex.crash_after_accept = crash_point == "after_accept"
        call(m, decision)


def test_ten_forced_restarts_recover_without_duplicates(tmp_path: Path) -> None:
    """Phase 5 게이트: 강제 종료/재시작 10회. 매 회 진입과 보호 스톱 사이 다른 지점에서 죽는다."""
    path = tmp_path / "db.sqlite"
    ex = CrashingExchange(new_fake())
    rng = random.Random(20260924)
    for day in range(10):
        decision = T0 + day * DAY
        conn = open_db(path)
        m = manager(ex, conn)
        m.recover()
        where = rng.choice(["entry", "stop"])
        point = rng.choice(["before_send", "after_accept", "none"])
        try:
            _entry_then_stop(m, ex, decision, crash_at=where, crash_point=point)
        except Crash:
            conn.close()  # 프로세스 사망
            conn = open_db(path)  # 재시작
            m = manager(ex, conn)
            m.recover()
            _entry_then_stop(m, ex, decision)  # 같은 의도 재실행 — 멱등해야 한다
        # 다음 날로 넘어가기 전 포지션 정리 (그날 스톱은 청산으로 만료된다)
        m.submit_market(
            decision_ms=decision,
            symbol="BTCUSDT",
            side="SELL",
            quantity=ex.inner.account().positions["BTCUSDT"].quantity,
            purpose="CLOSE",
        )
        m.recover()
        conn.close()
    conn = open_db(path)
    records = FuturesOrderRepository(conn).all()
    assert len(records) == 30  # 날마다 진입·스톱·청산
    assert all(r.state in TERMINAL_STATES for r in records)
    placed = set(ex.inner.orders)
    assert all((r.state in {"FILLED", "EXPIRED"}) == (r.client_order_id in placed) for r in records)
    assert ex.inner.placed_count == len(placed)  # 같은 ID로 두 번 들어간 주문 없음
    assert ex.inner.account().positions == {}


FAULTS = st.sampled_from(["ok", "net_before", "lost_after", "crash_before", "crash_after"])


@settings(max_examples=60, deadline=None)
@given(steps=st.lists(st.tuples(FAULTS, st.integers(1, 3)), min_size=1, max_size=10))
def test_state_invariants_under_random_faults(steps: list[tuple[str, int]]) -> None:
    """임의 장애 순서에서도 ID당 주문 최대 1건, 복구 뒤 기록은 거래소와 일치한다."""
    ex = CrashingExchange(new_fake())
    conn = open_db(":memory:")
    for i, (fault, repeats) in enumerate(steps):
        ex.inner.fail_before_accept = int(fault == "net_before")
        ex.inner.lose_response_after_accept = int(fault == "lost_after")
        ex.crash_before_send = fault == "crash_before"
        ex.crash_after_accept = fault == "crash_after"
        for _ in range(repeats):
            m = manager(ex, conn)
            with contextlib.suppress(Crash, ReconciliationError):
                m.recover()
                open_long(m, T0 + i * DAY, qty="0.01")
        ex.crash_before_send = ex.crash_after_accept = False
    m = manager(ex, conn)
    m.recover()
    records = {r.client_order_id: r for r in FuturesOrderRepository(conn).all()}
    assert not [r for r in records.values() if r.needs_reconcile]
    for cid, order in ex.inner.orders.items():
        assert records[cid].state == "FILLED"
        assert records[cid].filled_qty == order.executed_qty
    assert ex.inner.placed_count == len(ex.inner.orders)
    filled = sum((r.filled_qty for r in records.values() if r.state == "FILLED"), Decimal(0))
    position = ex.inner.account().positions.get("BTCUSDT")
    assert (position.quantity if position else Decimal(0)) == filled
