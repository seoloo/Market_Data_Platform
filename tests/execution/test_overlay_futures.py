"""O/v2 선물 다리 일일 실행기 (52 V2): 수량 맞추기·공백 없는 스톱 교체·발동 뒤 재진입·장애 주입."""

from __future__ import annotations

import itertools
import sqlite3
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from auto_trading.core.errors import OrderRejectedError
from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import FuturesFilters, FuturesOrderResult
from auto_trading.execution.futures_guard import FuturesSupervisor
from auto_trading.execution.futures_orders import FuturesOrderManager
from auto_trading.execution.overlay_futures import (
    FuturesLegTarget,
    OverlayExecutionReport,
    OverlayFuturesExecutor,
    daily_loss_blocks_increase,
)
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository
from auto_trading.storage.repositories.risk_events import RiskEventRepository

S = "BTCUSDT"
FILTERS = {S: FuturesFilters(Decimal("0.001"), Decimal("0.001"), Decimal(100), Decimal("0.1"))}
T0 = 1_790_207_999_999
DAY = 86_400_000


class SpyExchange(FakeFuturesExchange):
    """스톱을 취소할 때마다 '포지션이 있는데 걸린 스톱이 0' 인 순간이 있었는지 센다."""

    gaps: int = 0
    reject_stops: bool = False

    def cancel_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        result = super().cancel_stop(symbol, client_order_id)
        if self._qty.get(symbol, Decimal(0)) > 0 and not self.open_stops(symbol):
            self.gaps += 1
        return result

    def place_stop_market(
        self,
        symbol: str,
        side: str,
        stop_price: Decimal,
        client_order_id: str,
        *,
        quantity: Decimal | None = None,
    ) -> FuturesOrderResult:
        if self.reject_stops:
            raise OrderRejectedError("-1111 테스트: 스톱 거부")
        return super().place_stop_market(
            symbol, side, stop_price, client_order_id, quantity=quantity
        )


class Rig:
    def __init__(self, **fake: object) -> None:
        self.ex = SpyExchange(
            wallet=Decimal(1_000_000),
            prices={S: Decimal(50_000)},
            symbol_filters=FILTERS,
            **fake,  # type: ignore[arg-type]
        )
        self.conn: sqlite3.Connection = connect(":memory:")
        apply_migrations(self.conn, now_ms=1)
        ticks = itertools.count(1)
        self.store = FuturesOrderRepository(self.conn)
        self.m = FuturesOrderManager(
            self.ex,
            self.store,
            mode="paper",
            strategy_id="o2",
            strategy_tag="o2",
            clock=lambda: next(ticks),
        )
        sup = FuturesSupervisor(
            self.ex,
            self.m,
            self.store,
            RiskEventRepository(self.conn),
            mode="paper",
            symbols=[S],
            require_stop=True,
            orphan_policy="flatten",
            clock=lambda: 0,
        )
        self.halt: str | None = None
        self.x = OverlayFuturesExecutor(
            self.ex, self.m, self.store, sup, clock=lambda: 0, halted_reason=lambda: self.halt
        )
        self.day = 0

    def run(
        self, qty: str, stop: str | None = None, *, allowed: bool = True
    ) -> OverlayExecutionReport:
        self.day += 1
        price = None if stop is None else Decimal(stop)
        return self.x.run(
            T0 + self.day * DAY,
            [FuturesLegTarget(S, Decimal(qty), price)],
            increases_allowed=allowed,
        )

    def position(self) -> Decimal:
        pos = self.ex.account().positions.get(S)
        return pos.quantity if pos else Decimal(0)

    def stops(self) -> list[tuple[bool, Decimal, Decimal]]:
        return sorted((o.close_position, o.stop_price, o.orig_qty) for o in self.ex.open_stops(S))


def test_first_day_opens_and_protects_with_one_close_position_stop() -> None:
    r = Rig()
    rep = r.run("0.1", "40000")
    assert rep.state == "OK" and r.position() == Decimal("0.1")
    assert r.stops() == [(True, Decimal(40_000), Decimal(0))]  # 다리는 남지 않는다
    assert r.ex.gaps == 0


def test_daily_stop_replacement_never_leaves_a_gap() -> None:
    r = Rig()
    r.run("0.1", "40000")
    for price in ("41000", "42000", "39000"):
        rep = r.run("0.1", price)
        assert rep.state == "OK", rep
        assert r.stops() == [(True, Decimal(price), Decimal(0))]
    assert r.ex.gaps == 0  # 교체 중 한 번도 스톱 0 인 순간이 없었다


def test_same_price_keeps_the_stop_without_orders() -> None:
    r = Rig()
    r.run("0.1", "40000")
    placed = r.ex.placed_count
    rep = r.run("0.1", "40000")
    assert rep.legs[0].stop == "40000 유지" and r.ex.placed_count == placed


def test_reduce_then_close_cleans_up_stops() -> None:
    r = Rig()
    r.run("0.1", "40000")
    rep = r.run("0.05", "41000")
    assert r.position() == Decimal("0.05") and any("REDUCE" in o for o in rep.legs[0].orders)
    assert r.stops() == [(True, Decimal(41_000), Decimal(0))]
    rep = r.run("0", None)
    assert r.position() == 0 and r.stops() == []
    assert any("CLOSE" in o for o in rep.legs[0].orders)


def test_fired_stop_is_reported_and_reentry_follows_the_increase_rule() -> None:
    r = Rig()
    r.run("0.1", "40000")
    r.ex.set_mark_price(S, Decimal(39_000))  # 밤사이 −22% → 스톱 발동
    assert r.position() == 0
    blocked = r.run("0.1", "31200", allowed=False)  # 그날 손실 2% → 노출 증가 금지
    assert blocked.fired_stops and "PROTECT" in blocked.fired_stops[0]
    assert r.position() == 0 and not blocked.increases_allowed
    reentry = r.run("0.1", "31200")  # 다음 날 허용 → 추세 신호대로 다시 든다
    assert reentry.state == "OK" and r.position() == Decimal("0.1")
    assert r.stops() == [(True, Decimal(31_200), Decimal(0))]


def test_kill_switch_blocks_increase_but_allows_reduce_and_keeps_protection() -> None:
    r = Rig()
    r.run("0.1", "40000")
    r.halt = "점검"
    up = r.run("0.2", "41000")
    assert r.position() == Decimal("0.1") and not up.increases_allowed
    assert r.stops() == [(True, Decimal(41_000), Decimal(0))]  # 보호는 계속 갱신
    down = r.run("0.05", "41000")
    assert r.position() == Decimal("0.05") and down.state == "DEGRADED"  # 줄이기는 허용


def test_small_increase_below_min_notional_is_skipped() -> None:
    r = Rig()
    r.run("0.1", "40000")
    rep = r.run("0.101", "40000")  # 0.001 × 50,000 = 50 < 100
    assert r.position() == Decimal("0.1")
    assert any("최소 주문 금액" in n for n in rep.legs[0].notes)


def test_bridge_failure_keeps_old_stop() -> None:
    r = Rig()
    r.run("0.1", "40000")
    r.ex.fail_before_accept = 1  # 다음 주문 = 다리 스톱이 네트워크 오류
    rep = r.run("0.1", "42000")
    assert rep.state == "DEGRADED"
    assert r.stops() == [(True, Decimal(40_000), Decimal(0))]  # 옛 가격이라도 보호 중
    assert r.ex.gaps == 0
    fixed = r.run("0.1", "42000")  # 다음 날 결과 모름을 조회로 해소하고 교체
    assert r.stops() == [(True, Decimal(42_000), Decimal(0))] and fixed.state == "OK"


def test_old_stop_cancel_failure_leaves_bridge_protecting_then_consolidates() -> None:
    r = Rig()
    r.run("0.1", "40000")
    r.ex.fail_cancels = 1  # 옛 closePosition 취소 실패 → 새 closePosition 은 -4130
    rep = r.run("0.1", "42000")
    assert rep.state == "DEGRADED"
    assert r.stops() == [(False, Decimal(42_000), Decimal("0.1")), (True, Decimal(40_000), 0)]
    r.run("0.1", "42000")  # 다음 날: 스톱 두 개 → 하나로 정리
    assert r.stops() == [(True, Decimal(42_000), Decimal(0))]
    assert r.ex.gaps == 0


def test_no_stop_possible_on_entry_day_means_emergency_close() -> None:
    r = Rig()
    r.ex.reject_stops = True
    rep = r.run("0.1", "40000")
    assert rep.state == "HALTED" and r.position() == 0
    assert any(n.startswith("HALTED") for n in rep.legs[0].notes)


def test_recovery_halted_means_no_orders() -> None:
    r = Rig()
    r.run("0.1", "40000")
    for s in r.ex.open_stops(S):  # 누군가 보호 스톱을 지웠고, 다시 걸 수도 없다
        r.ex.cancel_stop(S, s.client_order_id)
    r.ex.reject_stops = True
    placed = r.ex.placed_count
    rep = r.run("0.2", "41000")
    assert rep.state == "HALTED" and r.position() == 0  # 복구가 비상 청산했다
    assert r.ex.placed_count == placed + 1  # 청산 한 건뿐 — 늘리기 주문 없음


def test_daily_loss_rule() -> None:
    assert daily_loss_blocks_increase(Decimal(10_000), Decimal(9_800))
    assert not daily_loss_blocks_increase(Decimal(10_000), Decimal(9_801))
    assert daily_loss_blocks_increase(Decimal(0), Decimal(1))  # 모르면 늘리지 않는다


@settings(max_examples=40, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 200),  # 목표 수량 (× 0.001)
            st.integers(-15, 15),  # 가격 변화 %
            st.booleans(),  # 취소 실패 주입
            st.booleans(),  # 주문 전 네트워크 오류 주입
            st.booleans(),  # 노출 증가 허용
        ),
        min_size=1,
        max_size=12,
    )
)
def test_invariant_position_is_never_left_unprotected(
    days: list[tuple[int, int, bool, bool, bool]],
) -> None:
    """어떤 목표·가격·장애 순서에서도: 실행이 끝나면 롱 포지션에는 거래소 스톱이 있다.

    숏 포지션은 생기지 않는다(줄이기·스톱은 모두 reduce-only).
    """
    r = Rig()
    price = Decimal(50_000)
    for qty, move, fail_cancel, fail_send, allowed in days:
        price = (price * (100 + move) / 100).quantize(Decimal("0.1"))
        r.ex.set_mark_price(S, price)
        r.ex.fail_cancels = int(fail_cancel)
        r.ex.fail_before_accept = int(fail_send)
        stop = (price * Decimal("0.8")).quantize(Decimal("0.1"))
        r.run(str(Decimal(qty) / 1000), str(stop), allowed=allowed)
        r.ex.fail_cancels = r.ex.fail_before_accept = 0
        pos = r.position()
        assert pos >= 0
        if pos > 0:
            assert r.ex.open_stops(S), f"보호 없는 포지션 {pos}"


@pytest.mark.parametrize("qty", ["0.1", "0.05"])
def test_quantity_stop_fires_reduce_only_on_fake(qty: str) -> None:
    """가짜 거래소: 다리 스톱은 수량만큼만(포지션 이하) 닫고, 둘 다 발동해도 숏이 되지 않는다."""
    r = Rig()
    r.run("0.1", "40000")
    r.m.place_bridge_stop(
        decision_ms=T0, symbol=S, side="SELL", stop_price=Decimal(41_000), quantity=Decimal(qty)
    )
    r.ex.set_mark_price(S, Decimal(39_000))  # 둘 다 발동
    assert r.position() == 0
