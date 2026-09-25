"""T/v2 Paper 실행기 (Phase 6): 연구와의 결정·자본 일치, 멱등, 누락일, 강제 종료, fail-closed."""

from __future__ import annotations

import math
import random
import sqlite3
from decimal import Decimal

import pytest

from auto_trading.backtest.trend import DailyBar, TrendConfig, simulate_trend, target_weight
from auto_trading.core.types import Candle
from auto_trading.exchange.spot import SpotFilters
from auto_trading.execution.paper_t2 import PaperT2Runner, ledger_balances
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository
from auto_trading.storage.repositories.spot_orders import SpotOrderRecord, SpotOrderRepository

D = 86_400_000
START = 1_600_000_000_000 - 1_600_000_000_000 % D
SYMBOLS = ("BTCUSDT", "ETHUSDT")
FILTERS = SpotFilters(Decimal("0.00000001"), Decimal("0.00000001"), Decimal(5))


def walk(n: int, seed: int, drift: float) -> list[Decimal]:
    rng = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= math.exp(drift + rng.gauss(0, 0.03))
        out.append(Decimal(repr(round(price, 6))))
    return out


class Market:
    """일봉 k의 close_ms 이후(= 일봉 k+1 시작) 시점의 시장. 현재가 = 일봉 k 종가."""

    def __init__(self, closes: dict[str, list[Decimal]]) -> None:
        self.closes = closes
        self.day = 0

    def candles(self, symbol: str) -> list[Candle]:
        return [
            Candle(START + k * D, START + (k + 1) * D - 1, c, c, c, c, Decimal(1))
            for k, c in enumerate(self.closes[symbol])
        ]

    def closed_daily(self, symbol: str, as_of_ms: int) -> list[Candle]:
        return [c for c in self.candles(symbol) if c.close_ms <= as_of_ms]

    def last_price(self, symbol: str) -> Decimal:
        return self.closes[symbol][self.day]

    def filters(self, symbol: str) -> SpotFilters:
        return FILTERS


def now_after(day: int) -> int:
    """일봉 ``day``가 마감된 직후 (다음 날 00:05 UTC)."""
    return START + (day + 1) * D + 5 * 60_000


def setup(closes: dict[str, list[Decimal]]) -> tuple[PaperT2Runner, Market, sqlite3.Connection]:
    conn = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    market = Market(closes)
    runner = PaperT2Runner(
        market,
        SpotOrderRepository(conn),
        PaperDayRepository(conn),
        clock=lambda: 1,
    )
    return runner, market, conn


def run_day(runner: PaperT2Runner, market: Market, day: int) -> PaperDay:
    market.day = day
    return runner.run_once(now_after(day))


CLOSES = {"BTCUSDT": walk(420, 1, 0.002), "ETHUSDT": walk(420, 2, -0.001)}


def test_decisions_and_equity_match_research_simulator() -> None:
    """Phase 6 Parity: 매일 목표 비중이 연구와 같고(1e-9), 자본 경로가 0.1% 안에서 같다."""
    runner, market, _ = setup(CLOSES)
    first, last = 130, 419
    research_cfg = TrendConfig(long_only=True)
    paper_equity = {}
    for day in range(first, last + 1):
        row = run_day(runner, market, day)
        paper_equity[day] = row.equity
        for symbol in SYMBOLS:
            expected = target_weight([float(c) for c in CLOSES[symbol][: day + 1]], research_cfg)
            got = row.targets[symbol]
            assert expected is not None and got is not None
            assert float(got) == pytest.approx(expected, abs=1e-9)
    bars = {
        s: {START + k * D: DailyBar(float(c), float(c)) for k, c in enumerate(CLOSES[s])}
        for s in SYMBOLS
    }
    research = simulate_trend(
        bars,
        {},
        from_ms=START + first * D,
        to_ms=START + last * D,
        initial_equity=10_000.0,
        cfg=TrendConfig(long_only=True, spot_cost=0.0015),
    )
    research_equity = dict(zip(range(first, last + 1), research.equity, strict=True))
    for day in (first + 10, (first + last) // 2, last):
        assert float(paper_equity[day]) == pytest.approx(research_equity[day], rel=1e-3)
    assert paper_equity[last] != Decimal(10_000)  # 실제로 거래가 일어났다


def test_same_day_rerun_is_idempotent() -> None:
    runner, market, conn = setup(CLOSES)
    for day in range(130, 140):
        run_day(runner, market, day)
    orders_before = len(SpotOrderRepository(conn).all())
    again = run_day(runner, market, 139)
    assert again == PaperDayRepository(conn).get("t2_long", now_after(139) - now_after(139) % D - 1)
    assert len(SpotOrderRepository(conn).all()) == orders_before


def test_missed_day_does_not_execute_stale_targets() -> None:
    runner, market, conn = setup(CLOSES)
    run_day(runner, market, 130)
    skipped = run_day(runner, market, 132)  # 131일 실행 누락
    assert skipped.status == "NO_PRIOR_DECISION" and skipped.executed == []
    assert SpotOrderRepository(conn).all() == []
    resumed = run_day(runner, market, 133)
    assert resumed.status == "OK"


class CrashOnceDays(PaperDayRepository):
    """첫 insert에서 프로세스가 죽는다 — 체결은 기록됐지만 일일 행은 없는 상태."""

    __slots__ = ("crashed",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.crashed = False

    def insert(self, day: PaperDay) -> None:
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("강제 종료")
        super().insert(day)


def test_crash_after_fills_before_day_row_does_not_duplicate_orders() -> None:
    runner, market, conn = setup(CLOSES)
    run_day(runner, market, 200)
    crashing = PaperT2Runner(
        market, SpotOrderRepository(conn), CrashOnceDays(conn), clock=lambda: 1
    )
    with pytest.raises(RuntimeError):
        run_day(crashing, market, 201)
    orders = SpotOrderRepository(conn).all()
    assert orders  # 체결은 이미 기록됐다
    row = run_day(crashing, market, 201)  # 재시작 후 같은 날 재실행
    assert SpotOrderRepository(conn).all() == orders  # 추가 주문 없음
    assert row.status == "OK"


def test_unresolved_order_blocks_trading() -> None:
    runner, market, conn = setup(CLOSES)
    run_day(runner, market, 200)
    SpotOrderRepository(conn).insert(
        SpotOrderRecord(
            client_order_id="t2l-stuck",
            mode="paper",
            strategy_id="t2_long",
            decision_ms=1,
            symbol="BTCUSDT",
            side="BUY",
            qty=Decimal(1),
            target_weight=Decimal("0.1"),
            state="SUBMITTED",  # 접수됐는데 거래소에서 찾지 못함 → 해소 불가
            created_ms=1,
            updated_ms=1,
        )
    )
    row = run_day(runner, market, 201)
    assert row.status == "DEGRADED" and row.executed == []


def test_ledger_balances_follow_fills_and_fees() -> None:
    def rec(side: str, qty: str, quote: str, fee: str, state: str = "FILLED") -> SpotOrderRecord:
        return SpotOrderRecord(
            client_order_id=f"{side}{qty}{state}",
            mode="paper",
            strategy_id="t2_long",
            decision_ms=1,
            symbol="BTCUSDT",
            side=side,
            qty=Decimal(qty),
            target_weight=Decimal(0),
            state=state,
            created_ms=1,
            updated_ms=1,
            filled_qty=Decimal(qty),
            quote_qty=Decimal(quote),
            fee=Decimal(fee),
        )

    records = [
        rec("BUY", "0.1", "5000", "7.5"),
        rec("SELL", "0.04", "2100", "3.15"),
        rec("BUY", "1", "50000", "75", state="REJECTED"),
    ]
    balance = ledger_balances(records, initial_quote=Decimal(10_000))
    assert balance["BTC"] == Decimal("0.06")
    assert balance["USDT"] == Decimal(10_000) - 5000 - Decimal("7.5") + 2100 - Decimal("3.15")


def test_shadow_mode_keeps_a_separate_ledger() -> None:
    """그림자 운영(51)은 같은 실행기를 다른 mode·금액으로 돌린다 — Paper 원장과 섞이지 않는다."""
    conn = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    market = Market(CLOSES)
    orders, days = SpotOrderRepository(conn), PaperDayRepository(conn)
    paper = PaperT2Runner(market, orders, days, clock=lambda: 1)
    shadow = PaperT2Runner(
        market,
        orders,
        days,
        clock=lambda: 1,
        initial_quote=Decimal(1_000),
        strategy_id="t2_shadow_1000",
        strategy_tag="t2s",
        mode="shadow",
    )
    for day in range(130, 150):
        market.day = day
        p = paper.run_once(now_after(day))
        s = shadow.run_once(now_after(day))
        assert p.targets == s.targets  # 같은 신호
    rows = orders.all()
    assert {r.mode for r in rows} == {"paper", "shadow"}
    assert all(r.strategy_id == "t2_shadow_1000" for r in rows if r.mode == "shadow")
    assert float(s.equity) == pytest.approx(float(p.equity) / 10, rel=0.02)  # 금액만 1/10
