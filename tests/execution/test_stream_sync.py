"""사용자 스트림 이벤트 → 주문 기록 (가짜 거래소). 이벤트는 신호, 조회가 확정."""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import FuturesFilters, FuturesOrderResult
from auto_trading.exchange.user_stream import UserEvent, parse_user_event
from auto_trading.execution.futures_orders import FuturesOrderManager
from auto_trading.execution.stream_sync import StreamSync
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository

S = "ETHUSDT"
FILTERS = {S: FuturesFilters(Decimal("0.001"), Decimal("0.001"), Decimal(20), Decimal("0.01"))}


class CountingExchange(FakeFuturesExchange):
    lookups: int = 0

    def get_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        self.lookups += 1
        return super().get_stop(symbol, client_order_id)

    def get_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        self.lookups += 1
        return super().get_order(symbol, client_order_id)


@pytest.fixture
def env(tmp_path: Path) -> tuple[CountingExchange, FuturesOrderManager, StreamSync]:
    conn = connect(tmp_path / "t.db")
    apply_migrations(conn, now_ms=1)
    ex = CountingExchange(
        wallet=Decimal(10_000), prices={S: Decimal(2_000)}, symbol_filters=FILTERS
    )
    store = FuturesOrderRepository(conn)
    ticks = itertools.count(1)
    m = FuturesOrderManager(
        ex, store, mode="testnet", strategy_id="t", strategy_tag="tw", clock=lambda: next(ticks)
    )
    return ex, m, StreamSync(m, store)


def order_event(cid: str, status: str, filled: str = "0", **extra: Any) -> UserEvent:
    o = {"s": S, "c": cid, "S": "SELL", "X": status, "z": filled, **extra}
    return parse_user_event({"e": "ORDER_TRADE_UPDATE", "E": 1, "o": o})


def algo_event(cid: str, status: str, filled: str = "0") -> UserEvent:
    o = {"caid": cid, "aid": 1, "s": S, "S": "SELL", "X": status, "aq": filled}
    return parse_user_event({"e": "ALGO_UPDATE", "E": 1, "o": o})


def open_with_stop(m: FuturesOrderManager) -> str:
    m.submit_market(decision_ms=1, symbol=S, side="BUY", quantity=Decimal("0.02"), purpose="OPEN")
    stop = m.place_protective_stop(decision_ms=1, symbol=S, side="SELL", stop_price=Decimal(1_990))
    assert stop.state == "SUBMITTED"
    return stop.client_order_id


def test_triggered_stop_is_confirmed_by_lookup(env: Any) -> None:
    ex, m, sync = env
    cid = open_with_stop(m)
    ex.set_mark_price(S, Decimal(1_985))  # 거래소에서 발동 — 로컬은 아직 모른다
    result = sync.handle(algo_event(cid, "FINISHED", "0.02"))
    assert result.kind == "REFRESHED"
    assert result.record is not None
    assert result.record.state == "FILLED"
    assert result.record.filled_qty == Decimal("0.02")


def test_out_of_order_fill_before_trigger_is_lagging_then_resolved(env: Any) -> None:
    ex, m, sync = env
    cid = open_with_stop(m)
    # 실측처럼 체결 이벤트가 먼저 오지만 조회는 아직 NEW (거래소 쪽 반영 전)
    early = sync.handle(order_event(cid, "FILLED", "0.02", st="ALGO_CONDITION", si=1))
    assert early.kind == "LAGGING"
    assert sync.lagging == {cid}
    ex.set_mark_price(S, Decimal(1_985))
    done = sync.retry_lagging()
    assert [r.state for r in done] == ["FILLED"]
    assert not sync.lagging


def test_duplicate_event_does_not_lookup_again(env: Any) -> None:
    ex, m, sync = env
    cid = open_with_stop(m)
    ex.set_mark_price(S, Decimal(1_985))
    assert sync.handle(algo_event(cid, "FINISHED", "0.02")).kind == "REFRESHED"
    before = ex.lookups
    assert sync.handle(algo_event(cid, "FINISHED", "0.02")).kind == "DUPLICATE"
    assert ex.lookups == before


def test_cancel_made_outside_manager_is_picked_up(env: Any) -> None:
    ex, m, sync = env
    cid = open_with_stop(m)
    ex.cancel_stop(S, cid)  # 관리자를 거치지 않은 취소 (사람·다른 프로세스)
    result = sync.handle(algo_event(cid, "CANCELED"))
    assert result.kind == "REFRESHED"
    assert result.record is not None and result.record.state == "CANCELED"


def test_unknown_order_is_untracked_and_not_looked_up(env: Any) -> None:
    ex, _, sync = env
    before = ex.lookups
    assert sync.handle(algo_event("someone-else", "NEW")).kind == "UNTRACKED"
    assert ex.lookups == before


def test_account_update_returns_positions(env: Any) -> None:
    _, _, sync = env
    event = parse_user_event(
        {"e": "ACCOUNT_UPDATE", "E": 1, "a": {"m": "ORDER", "P": [{"s": S, "pa": "0.02"}]}}
    )
    result = sync.handle(event)
    assert result.kind == "ACCOUNT"
    assert result.positions == {S: Decimal("0.02")}


def test_on_connect_recovers_changes_missed_while_disconnected(env: Any) -> None:
    ex, m, sync = env
    cid = open_with_stop(m)
    ex.set_mark_price(S, Decimal(1_985))  # 끊긴 동안 발동 — 이벤트 없음
    recovered = {r.client_order_id: r.state for r in sync.on_connect()}
    assert recovered[cid] == "FILLED"


def test_terminal_record_is_not_looked_up(env: Any) -> None:
    ex, m, sync = env
    opened = m.submit_market(
        decision_ms=2, symbol=S, side="BUY", quantity=Decimal("0.02"), purpose="OPEN"
    )
    before = ex.lookups
    result = sync.handle(order_event(opened.client_order_id, "FILLED", "0.02"))
    assert result.kind == "REFRESHED" and result.record is not None
    assert result.record.state == "FILLED"
    assert ex.lookups == before
