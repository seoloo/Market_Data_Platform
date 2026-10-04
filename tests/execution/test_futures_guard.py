"""선물 재시작 정합성 복구·보호 주문 감시 (TRADING_CORE §재시작·정합성 복구).

거래소가 진실의 원천이다. 자동 조치는 셋뿐(보호 스톱·원인 불명 정리·할 일 없는 스톱 취소)이고
나머지 차이는 기록한 뒤 사람이 확인한다. 조회를 못 하면 아무 주문도 보내지 않는다.
"""

from __future__ import annotations

import itertools
import sqlite3
from decimal import Decimal

import pytest

from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import FuturesFilters, FuturesPosition
from auto_trading.execution.futures_guard import (
    FuturesSupervisor,
    RecoveryReport,
    expected_positions,
)
from auto_trading.execution.futures_orders import FuturesOrderManager
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository
from auto_trading.storage.repositories.risk_events import RiskEventRepository

FILTERS = {
    "BTCUSDT": FuturesFilters(Decimal("0.001"), Decimal("0.001"), Decimal(100), Decimal("0.1"))
}
T0 = 1_790_207_999_999
DAY = 86_400_000


class Rig:
    def __init__(
        self, *, require_stop: bool = True, orphan_policy: str = "flatten", **fake: object
    ) -> None:
        self.ex = FakeFuturesExchange(
            wallet=Decimal(1_000_000),
            prices={"BTCUSDT": Decimal(50_000)},
            symbol_filters=FILTERS,
            **fake,  # type: ignore[arg-type]
        )
        self.conn: sqlite3.Connection = connect(":memory:")
        apply_migrations(self.conn, now_ms=1)
        ticks = itertools.count(1)
        self.store = FuturesOrderRepository(self.conn)
        self.events = RiskEventRepository(self.conn)
        self.m = FuturesOrderManager(
            self.ex,
            self.store,
            mode="paper",
            strategy_id="o1_overlay",
            strategy_tag="o1",
            clock=lambda: next(ticks),
        )
        self.guard = FuturesSupervisor(
            self.ex,
            self.m,
            self.store,
            self.events,
            mode="paper",
            symbols=["BTCUSDT"],
            require_stop=require_stop,
            orphan_policy=orphan_policy,
            clock=lambda: 0,  # 재시도 창(3초) 안에서 시도 횟수만으로 끝나게
        )

    def open_long(self, qty: str = "0.1", decision: int = T0) -> None:
        self.m.submit_market(
            decision_ms=decision,
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal(qty),
            purpose="OPEN",
        )

    def recover(self, price: str | None = "45000", decision: int = T0 + DAY) -> RecoveryReport:
        return self.guard.recover(decision, lambda s, p: None if price is None else Decimal(price))

    def kinds(self, report: RecoveryReport) -> set[str]:
        return {i.kind for i in report.issues}

    def position(self) -> Decimal:
        pos = self.ex.account().positions.get("BTCUSDT")
        return pos.quantity if pos else Decimal(0)


class TestClean:
    def test_protected_position_is_running_with_no_action(self) -> None:
        rig = Rig()
        rig.open_long()
        rig.m.place_protective_stop(
            decision_ms=T0, symbol="BTCUSDT", side="SELL", stop_price=Decimal(45_000)
        )
        report = rig.recover()
        assert report.state == "RUNNING" and report.issues == [] and report.actions == []
        assert report.entries_allowed

    def test_flat_account_is_running(self) -> None:
        assert Rig().recover().state == "RUNNING"

    def test_stop_that_fired_during_downtime_reconciles_cleanly(self) -> None:
        rig = Rig()
        rig.open_long()
        rig.m.place_protective_stop(
            decision_ms=T0, symbol="BTCUSDT", side="SELL", stop_price=Decimal(45_000)
        )
        rig.ex.set_mark_price("BTCUSDT", Decimal(44_000))  # 프로세스가 꺼진 동안 발동
        report = rig.recover()
        assert report.state == "RUNNING" and report.issues == []
        assert rig.store.get(f"o1-{T0}-BTCUSDT-PS").state == "FILLED"  # type: ignore[union-attr]


class TestProtection:
    def test_unprotected_position_gets_a_stop(self) -> None:
        rig = Rig()
        rig.open_long()
        report = rig.recover()
        assert "UNPROTECTED" in rig.kinds(report)
        assert report.state == "RUNNING"
        assert [s.stop_price for s in rig.m.working_stops()] == [Decimal(45_000)]
        assert rig.recover().issues == []  # 다음 복구에서는 깨끗하다

    def test_retry_uses_a_fresh_attempt_number(self) -> None:
        rig = Rig()
        rig.open_long()
        prices = iter([Decimal(55_000), Decimal(45_000)])  # 첫 가격은 즉시 발동 위치 → 거절
        report = rig.guard.recover(T0 + DAY, lambda s, p: next(prices))
        assert report.state == "RUNNING"
        (stop,) = rig.m.working_stops()
        assert stop.client_order_id.endswith("-PS-1")

    def test_failing_protection_flattens_and_halts(self) -> None:
        rig = Rig()
        rig.open_long()
        report = rig.recover(price="55000")  # 3번 모두 거절
        assert report.state == "HALTED" and not report.entries_allowed
        assert rig.position() == 0
        assert any("비상 청산" in a and "청산 완료" in a for a in report.actions)
        assert sum("보호 스톱" in a for a in report.actions) == 3

    def test_no_stop_price_flattens_and_halts(self) -> None:
        rig = Rig()
        rig.open_long()
        report = rig.recover(price=None)
        assert report.state == "HALTED" and rig.position() == 0

    def test_failed_emergency_close_is_not_reported_as_closed(self) -> None:
        rig = Rig()
        rig.open_long()
        rig.ex.fail_before_accept = 4  # 스톱 3번 + 청산 1번 모두 전송 실패
        report = rig.recover()
        assert report.state == "HALTED"
        assert rig.position() == Decimal("0.1")
        assert any("청산 미완료" in a for a in report.actions)
        assert not any("청산 완료" in a for a in report.actions)

    def test_policy_without_stop_only_reports(self) -> None:
        """O/v1 연구 예외처럼 보호 스톱을 요구하지 않는 정책 — 기록만 한다."""
        rig = Rig(require_stop=False)
        rig.open_long()
        report = rig.recover()
        assert report.state == "RUNNING"
        assert [(i.kind, i.severity) for i in report.issues] == [("UNPROTECTED", "info")]
        assert rig.m.working_stops() == [] and report.actions == []

    def test_stop_on_the_wrong_side_is_replaced(self) -> None:
        """실거래소는 반대 방향 closePosition 스톱을 받지 않는다(-4509). 숏에서 건 스톱이
        남은 채 포지션이 롱으로 뒤집힌 비정상 상태를 흉내 낸다."""
        rig = Rig()
        rig.m.submit_market(
            decision_ms=T0, symbol="BTCUSDT", side="SELL", quantity=Decimal("0.1"), purpose="OPEN"
        )
        rig.m.place_protective_stop(
            decision_ms=T0, symbol="BTCUSDT", side="BUY", stop_price=Decimal(55_000)
        )
        rig.ex._qty["BTCUSDT"] = Decimal("0.1")  # 스톱은 남고 포지션만 뒤집힌 상태
        rig.store.add_adjustment(0, "BTCUSDT", Decimal("0.2"), "테스트: 외부에서 뒤집힌 포지션")
        report = rig.recover()
        assert "STOP_WRONG_SIDE" in rig.kinds(report)
        assert [s.side for s in rig.m.working_stops()] == ["SELL"]

    def test_stale_stop_on_a_flat_account_is_cancelled(self) -> None:
        """실거래소는 평탄해지면 closePosition 스톱을 자동 정리한다(테스트넷 실측).

        그래도 스톱만 남은 비정상 상태를 가정해 방어 경로를 검사한다.
        """
        rig = Rig()
        rig.open_long()
        rig.m.place_protective_stop(
            decision_ms=T0, symbol="BTCUSDT", side="SELL", stop_price=Decimal(45_000)
        )
        rig.ex._qty["BTCUSDT"] = Decimal(0)  # 스톱은 남고 포지션만 사라진 상태를 흉내
        rig.store.add_adjustment(0, "BTCUSDT", Decimal("-0.1"), "테스트: 외부에서 사라진 포지션")
        report = rig.recover()
        assert "STALE_STOP" in rig.kinds(report)
        assert rig.m.working_stops() == [] and report.state == "RUNNING"


class TestOrphans:
    def _orphan(self, rig: Rig) -> None:
        rig.ex.place_market_order("BTCUSDT", "BUY", Decimal("0.2"), "manual-1", reduce_only=False)

    def test_flatten_policy_closes_and_records_the_external_position(self) -> None:
        rig = Rig()
        self._orphan(rig)
        report = rig.recover()
        assert "ORPHAN_POSITION" in rig.kinds(report)
        assert rig.position() == 0
        assert rig.store.adjustments() == [("BTCUSDT", Decimal("0.2"))]
        # 정리 뒤에는 기록 합과 거래소가 다시 맞는다 — 매번 불일치로 잡히지 않는다
        assert rig.recover(decision=T0 + 2 * DAY).issues == []

    def test_hold_with_stop_adopts_and_protects(self) -> None:
        rig = Rig(orphan_policy="hold_with_stop")
        self._orphan(rig)
        report = rig.recover()
        assert rig.position() == Decimal("0.2")
        assert len(rig.m.working_stops()) == 1
        assert report.state == "RUNNING"
        assert rig.recover(decision=T0 + 2 * DAY).issues == []


class TestManualReview:
    def test_liquidated_position_degrades_without_orders(self) -> None:
        rig = Rig()
        rig.ex.wallet = Decimal(10_000)
        rig.open_long("0.5")  # 25,000 명목 / 3배
        # 10,000 + 0.5(p − 50,000) ≤ 0.5·p·0.5% → p ≤ 30,150
        rig.ex.set_mark_price("BTCUSDT", Decimal(30_000))  # 청산
        placed = rig.ex.placed_count
        report = rig.recover()
        assert "MISSING_POSITION" in rig.kinds(report)
        assert report.state == "DEGRADED" and not report.entries_allowed
        assert rig.ex.placed_count == placed  # 자동으로 아무것도 보내지 않았다
        assert rig.store.adjustments() == []  # 자동 조정도 하지 않는다

    def test_quantity_mismatch_degrades(self) -> None:
        rig = Rig()
        rig.open_long()
        rig.m.place_protective_stop(
            decision_ms=T0, symbol="BTCUSDT", side="SELL", stop_price=Decimal(45_000)
        )
        rig.ex.place_market_order("BTCUSDT", "BUY", Decimal("0.05"), "manual-2", reduce_only=False)
        report = rig.recover()
        assert "QTY_MISMATCH" in rig.kinds(report) and report.state == "DEGRADED"

    def test_order_we_did_not_place_degrades_without_fighting_it(self) -> None:
        """모르는 스톱이 이미 보호 중이면 우리 스톱은 -4130 으로 막힌다.

        자동 조치를 했다면 3번 거부 뒤 멀쩡한 포지션을 비상 청산했을 것이다.
        """
        rig = Rig()
        rig.open_long()
        rig.ex.place_stop_market("BTCUSDT", "SELL", Decimal(45_000), "someone-else")
        placed = rig.ex.placed_count
        report = rig.recover()
        assert "UNTRACKED_ORDER" in rig.kinds(report) and report.state == "DEGRADED"
        assert rig.ex.placed_count == placed and rig.position() == Decimal("0.1")
        assert any("자동 조치하지 않음" in a for a in report.actions)

    def test_account_query_failure_sends_nothing(self) -> None:
        rig = Rig()
        rig.open_long()
        rig.ex.fail_account = 1  # 진입 주문의 증거금 확인이 아니라 복구 때 실패하게
        placed = rig.ex.placed_count
        report = rig.recover()
        assert rig.kinds(report) == {"ACCOUNT_QUERY_FAILED"}
        assert report.state == "DEGRADED"
        assert rig.ex.placed_count == placed

    def test_unresolved_order_degrades(self) -> None:
        rig = Rig(lose_response_after_accept=1, fail_lookups=2)
        rig.open_long()  # 응답 유실 → UNKNOWN, 조회도 실패
        report = rig.recover()
        assert "UNRESOLVED_ORDERS" in rig.kinds(report) and report.state == "DEGRADED"


class TestAudit:
    def test_issues_and_actions_are_written_to_risk_events(self) -> None:
        rig = Rig()
        rig.open_long()
        rig.recover()
        events = rig.events.recent()
        kinds = {e.kind for e in events}
        assert {"futures.UNPROTECTED", "futures.RECOVERY"} <= kinds
        recovery = next(e for e in events if e.kind == "futures.RECOVERY")
        assert recovery.action_taken is not None and "보호 스톱" in recovery.action_taken

    def test_clean_recovery_writes_nothing(self) -> None:
        rig = Rig()
        rig.recover()
        assert rig.events.recent() == []


@pytest.mark.parametrize(
    ("records_side", "expected"),
    [(("BUY", "BUY"), Decimal("0.3")), (("BUY", "SELL"), Decimal("-0.1"))],
)
def test_expected_positions_sum_signed_fills_and_adjustments(
    records_side: tuple[str, str], expected: Decimal
) -> None:
    rig = Rig()
    first, second = records_side
    rig.ex.default_leverage = 10
    rig.m.submit_market(
        decision_ms=T0, symbol="BTCUSDT", side=first, quantity=Decimal("0.1"), purpose="OPEN"
    )
    rig.m.submit_market(
        decision_ms=T0 + DAY,
        symbol="BTCUSDT",
        side=second,
        quantity=Decimal("0.2"),
        purpose="OPEN",
    )
    got = expected_positions(rig.store.all(), [])
    assert got["BTCUSDT"] == expected
    assert expected_positions([], [("BTCUSDT", Decimal("0.2"))]) == {"BTCUSDT": Decimal("0.2")}
    assert isinstance(rig.ex.account().positions["BTCUSDT"], FuturesPosition)


class TestSharedAccount:
    """데모·테스트넷 계좌를 여러 트랙이 나눠 쓴다 (문서 59 §3).

    다른 트랙 포지션은 건드리지 않는다.
    """

    def rig(self, *, shared: bool) -> tuple[FakeFuturesExchange, FuturesSupervisor]:
        filters = {
            **FILTERS,
            "ETHUSDT": FuturesFilters(
                Decimal("0.001"), Decimal("0.001"), Decimal(5), Decimal("0.01")
            ),
        }
        ex = FakeFuturesExchange(
            wallet=Decimal(1_000_000),
            prices={"BTCUSDT": Decimal(50_000), "ETHUSDT": Decimal(2_500)},
            symbol_filters=filters,
        )
        conn = connect(":memory:")
        apply_migrations(conn, now_ms=1)
        ticks = itertools.count(1)
        store = FuturesOrderRepository(conn)
        m = FuturesOrderManager(
            ex, store, mode="demo", strategy_id="mine", strategy_tag="mn", clock=lambda: next(ticks)
        )
        guard = FuturesSupervisor(
            ex,
            m,
            store,
            RiskEventRepository(conn),
            mode="demo",
            symbols=["BTCUSDT"],
            require_stop=True,
            orphan_policy="flatten",
            clock=lambda: 0,
            shared_account=shared,
        )
        # 다른 트랙이 ETHUSDT 를 들고 있다 (우리 기록에 없음, 스톱도 잠깐 없음 — 교체 중일 수 있다)
        ex.place_market_order("ETHUSDT", "BUY", Decimal("0.2"), "other-1", reduce_only=False)
        return ex, guard

    def test_shared_account_leaves_other_tracks_alone(self) -> None:
        ex, guard = self.rig(shared=True)
        report = guard.recover(T0, lambda s, p: Decimal(2_000))
        assert report.state == "RUNNING" and report.issues == [] and report.actions == []
        assert ex.account().positions["ETHUSDT"].quantity == Decimal("0.2")

    def test_default_single_strategy_account_still_flattens_orphans(self) -> None:
        ex, guard = self.rig(shared=False)
        report = guard.recover(T0, lambda s, p: Decimal(2_000))
        assert "ORPHAN_POSITION" in {i.kind for i in report.issues}
        assert "ETHUSDT" not in ex.account().positions  # 실거래(전략 하나) 규칙은 그대로


def test_orphan_flatten_uses_capped_limit_unless_told_market() -> None:
    """원인 불명 정리는 지정가 IOC (TRADING_CORE). market 은 Phase 7 훈련 경로 유지용."""
    for close, kind in (("limit", "LIMIT"), ("market", "MARKET")):
        rig = Rig()
        rig.guard = FuturesSupervisor(
            rig.ex,
            rig.m,
            rig.store,
            rig.events,
            mode="paper",
            symbols=["BTCUSDT"],
            require_stop=True,
            orphan_policy="flatten",
            orphan_close=close,
            clock=lambda: 0,
        )
        rig.ex.place_market_order("BTCUSDT", "BUY", Decimal("0.2"), "manual-1", reduce_only=False)
        rig.recover()
        assert rig.position() == 0
        assert [o.order_type for o in rig.store.all() if o.purpose == "CLOSE"] == [kind]
