"""수동 스톱 훈련의 다른 트랙 보호 (59 §3): 이 종목에 우리 기록 밖의 포지션·스톱이 있으면 멈춘다."""

from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Any

import pytest
from scripts.testnet_stop_drill import SYMBOL, StopDrill

from auto_trading.core.errors import ConfigError
from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import FuturesFilters
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository
from auto_trading.storage.repositories.risk_events import RiskEventRepository

F = FuturesFilters(Decimal("0.1"), Decimal("0.1"), Decimal(5), Decimal("0.0001"))


def drill() -> tuple[StopDrill, FakeFuturesExchange]:
    ex = FakeFuturesExchange(
        wallet=Decimal(10_000), prices={SYMBOL: Decimal("1.5")}, symbol_filters={SYMBOL: F}
    )
    conn = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    d = StopDrill(
        ex,  # type: ignore[arg-type]  # 가짜 거래소
        FuturesOrderRepository(conn),
        RiskEventRepository(conn),
        1_790_000_000_000,
        Decimal(15),
        1.0,
    )
    ticks = itertools.count(1)
    d.manager = lambda: _manager(d, ticks)  # type: ignore[method-assign]
    return d, ex


def _manager(d: StopDrill, ticks: Any) -> Any:
    from auto_trading.execution.futures_orders import FuturesOrderManager

    return FuturesOrderManager(
        d.api,
        d.store,
        mode="testnet",
        strategy_id="stopdrill",
        strategy_tag="ts",
        clock=lambda: next(ticks),
    )


def test_empty_symbol_passes_and_allows_cleanup() -> None:
    d, _ = drill()
    d.guard_foreign()
    assert d.safe_to_clean


def test_other_tracks_position_stops_the_drill_without_cleanup() -> None:
    d, ex = drill()
    ex.place_market_order(SYMBOL, "BUY", Decimal(10), "demo-o2-xyz", reduce_only=False)
    with pytest.raises(ConfigError):
        d.guard_foreign()
    assert not d.safe_to_clean and ex.account().positions[SYMBOL].quantity == 10


def test_other_tracks_stop_stops_the_drill() -> None:
    d, ex = drill()
    d.manager().submit_market(
        decision_ms=1, symbol=SYMBOL, side="BUY", quantity=Decimal(10), purpose="OPEN"
    )
    ex.place_stop_market(SYMBOL, "SELL", Decimal("1.2"), "o2-abc-PS")  # 남의 보호 스톱
    with pytest.raises(ConfigError):
        d.guard_foreign()


def test_own_leftover_position_is_fine() -> None:
    d, _ = drill()
    d.manager().submit_market(
        decision_ms=1, symbol=SYMBOL, side="BUY", quantity=Decimal(10), purpose="OPEN"
    )
    d.guard_foreign()  # 지난번 우리 훈련이 남긴 것 — 정리해도 된다
    assert d.safe_to_clean
