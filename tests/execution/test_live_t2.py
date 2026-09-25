"""T/v2 실거래 1단계 실행기 (57) — 가짜 거래소 장애 시험.

정상 체결 · 같은 날 재실행 · 대기 중 강제 종료 뒤 이어받기 · 제한 시간 지난 재시작 · 긴급 정지 ·
전송 결과 모름(거래소에 있음/없음) · 조회 장애로 다음 날 신규 금지 · post-only 거절 뒤 추격.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.core.errors import ExchangeError, OrderRejectedError, OrderStateUnknownError
from auto_trading.core.types import Candle
from auto_trading.exchange.binance_spot_demo import SpotFill
from auto_trading.exchange.spot import OrderStatus, SpotFilters, SpotOrderResult
from auto_trading.execution.live_t2 import LiveOptions, LiveT2Runner
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_days import PaperDayRepository
from auto_trading.storage.repositories.spot_orders import SpotOrderRepository

D = 86_400_000
START = 1_600_000_000_000 - 1_600_000_000_000 % D
SYMBOLS = ("BTCUSDT", "ETHUSDT")
PRICE = {"BTCUSDT": Decimal(100), "ETHUSDT": Decimal(50)}
FILTERS = SpotFilters(Decimal("0.0001"), Decimal("0.0001"), Decimal(5))
OPT = LiveOptions(mode="demo", strategy_id="t2_demo", tag="t2d", timeout_min=240, poll_s=30)


class CrashError(Exception):
    """대기 중 프로세스가 죽는 것을 흉내 낸다."""


class Signals:
    """꾸준히 오르는 일봉 → T/v2 목표가 양수."""

    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]:
        out = []
        for k in range(400):
            close_ms = START + (k + 1) * D - 1
            if close_ms > as_of_ms:
                break
            c = Decimal(100 + k) * (1 if symbol == "BTCUSDT" else Decimal("0.5"))
            out.append(Candle(START + k * D, close_ms, c, c, c, c, Decimal(1)))
        return out[-limit:]


@dataclass
class Venue:
    fill_after: int = 1
    """지정가가 몇 번째 조회에서 체결되는가 (큰 값이면 안 된다)."""
    reject_posts: int = 0
    unknown_on_post: bool = False
    unknown_but_placed: bool = True
    lookup_fails: bool = False
    cash: Decimal = Decimal(5000)
    orders: dict[str, SpotOrderResult] = field(default_factory=dict)
    prices: dict[str, Decimal] = field(default_factory=dict)
    polls: dict[str, int] = field(default_factory=dict)
    placed: list[str] = field(default_factory=list)
    base: dict[str, Decimal] = field(default_factory=dict)

    def balances(self) -> dict[str, Decimal]:
        return {"USDT": self.cash, **self.base}

    def last_price(self, symbol: str) -> Decimal:
        return PRICE[symbol]

    def filters(self, symbol: str) -> SpotFilters:
        return FILTERS

    def order_book(self, symbol: str, limit: int = 100):
        p = PRICE[symbol]
        return [(p - Decimal("0.05"), Decimal(10))], [(p + Decimal("0.05"), Decimal(10))]

    def _result(
        self,
        cid: str,
        symbol: str,
        side: str,
        qty: Decimal,
        status: OrderStatus,
        executed: Decimal,
    ) -> SpotOrderResult:
        r = SpotOrderResult(
            cid,
            f"x{len(self.orders)}",
            symbol,
            side,
            status,
            qty,
            executed,
            executed * self.prices.get(cid, PRICE[symbol]),
            Decimal(0),
            "",
        )
        self.orders[cid] = r
        return r

    def place_limit_maker(
        self, symbol: str, side: str, quantity: Decimal, price: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        if self.reject_posts > 0:
            self.reject_posts -= 1
            raise OrderRejectedError("would match")
        if self.unknown_on_post:
            self.unknown_on_post = False
            if self.unknown_but_placed:
                self.placed.append(client_order_id)
                self.prices[client_order_id] = price
                self._result(client_order_id, symbol, side, quantity, OrderStatus.NEW, Decimal(0))
            raise OrderStateUnknownError("timeout")
        self.placed.append(client_order_id)
        self.prices[client_order_id] = price
        return self._result(client_order_id, symbol, side, quantity, OrderStatus.NEW, Decimal(0))

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str
    ) -> SpotOrderResult:
        self.placed.append(client_order_id)
        self.prices[client_order_id] = PRICE[symbol] + Decimal("0.05")
        return self._result(client_order_id, symbol, side, quantity, OrderStatus.FILLED, quantity)

    def cancel_order(self, symbol, client_order_id):
        r = self.orders[client_order_id]
        self.orders[client_order_id] = replace(r, status=OrderStatus.CANCELED)
        return self.orders[client_order_id]

    def get_order(self, symbol, client_order_id):
        if self.lookup_fails:
            raise ExchangeError("network down")
        r = self.orders.get(client_order_id)
        if r is None or r.status is not OrderStatus.NEW:
            return r
        self.polls[client_order_id] = self.polls.get(client_order_id, 0) + 1
        if self.polls[client_order_id] >= self.fill_after:
            price = self.prices[client_order_id]
            r = replace(
                r, status=OrderStatus.FILLED, executed_qty=r.orig_qty, quote_qty=r.orig_qty * price
            )
            self.orders[client_order_id] = r
        return r

    def fills(self, symbol, exchange_order_id):
        (cid,) = [c for c, r in self.orders.items() if r.exchange_order_id == exchange_order_id]
        r = self.orders[cid]
        price = self.prices[cid]
        return [
            SpotFill(
                price,
                r.executed_qty,
                r.executed_qty * price,
                r.executed_qty * price * Decimal("0.001"),
                "USDT",
                not cid.endswith("m"),
            )
        ]


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms

    def sleep(self, s: float) -> None:
        self.ms += int(s * 1000)


def after(day: int) -> int:
    """일봉 ``day`` 가 마감된 뒤 21:12 KST 쯤 (12:12 UTC)."""
    return START + (day + 1) * D + 12 * 3_600_000


@pytest.fixture
def env(tmp_path: Path):
    conn = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    return conn, tmp_path / "HALT"


def make(conn, halt: Path, venue: Venue, clock: Clock, notes: list[str], sleep=None):
    return LiveT2Runner(
        venue,
        Signals(),
        SpotOrderRepository(conn),
        PaperDayRepository(conn),
        options=OPT,
        clock=clock,
        sleep=sleep or clock.sleep,
        halt_path=halt,
        notify=notes.append,
    )


def two_days(conn, halt, venue: Venue, notes: list[str], sleep=None) -> Clock:
    """첫날은 목표만 기록, 둘째 날 체결."""
    clock = Clock(after(200))
    make(conn, halt, venue, clock, notes).run_once(clock.ms)
    clock.ms = after(201)
    make(conn, halt, venue, clock, notes, sleep).run_once(clock.ms)
    return clock


def test_first_day_records_only_then_second_day_buys_as_maker(env) -> None:
    conn, halt = env
    venue = Venue(fill_after=2)
    notes: list[str] = []
    clock = Clock(after(200))
    day = make(conn, halt, venue, clock, notes).run_once(clock.ms)
    assert day.status == "NO_PRIOR_DECISION" and venue.placed == []
    clock.ms = after(201)
    runner = make(conn, halt, venue, clock, notes)
    runner.run_once(clock.ms)
    assert sorted(c.split("-")[2] for c in venue.placed) == ["BTCUSDT", "ETHUSDT"]
    states = {r.client_order_id: r.state for r in SpotOrderRepository(conn).all()}
    assert set(states.values()) == {"FILLED"}
    assert all(r.maker_qty > 0 and not r.chased for r in runner.reports)
    assert any(
        "롱 진입(매수)" in n
        and "이유: 20·60·120일 추세 3/3 상승 → 상승 추세" in n
        and "숏 없음" in n
        for n in notes
    )
    assert any("지정가 체결" in n for n in notes)


def test_same_day_rerun_sends_nothing(env) -> None:
    conn, halt = env
    venue = Venue()
    notes: list[str] = []
    clock = two_days(conn, halt, venue, notes)
    placed = list(venue.placed)
    make(conn, halt, venue, clock, notes).run_once(clock.ms)
    assert venue.placed == placed


def test_crash_while_waiting_is_resumed_without_duplicate(env) -> None:
    conn, halt = env
    venue = Venue(fill_after=5)
    notes: list[str] = []

    def crash(_s: float) -> None:
        raise CrashError

    with pytest.raises(CrashError):
        two_days(conn, halt, venue, notes, sleep=crash)
    assert len(venue.placed) == 2 and len(SpotOrderRepository(conn).unresolved()) == 2
    clock = Clock(after(201) + 10 * 60_000)  # 10 분 뒤 재시작, 아직 제한 시간 안
    runner = make(conn, halt, venue, clock, notes)
    runner.run_once(clock.ms)
    assert len(venue.placed) == 2  # 다시 보내지 않았다
    assert not SpotOrderRepository(conn).unresolved()
    assert all(r.resumed and r.maker_qty > 0 for r in runner.reports)
    assert any("이어받음" in n for n in notes)


def test_restart_after_deadline_cancels_and_chases(env) -> None:
    conn, halt = env
    venue = Venue(fill_after=10**9)
    notes: list[str] = []

    def crash(_s: float) -> None:
        raise CrashError

    with pytest.raises(CrashError):
        two_days(conn, halt, venue, notes, sleep=crash)
    clock = Clock(after(201) + 300 * 60_000)  # 5 시간 뒤
    runner = make(conn, halt, venue, clock, notes)
    runner.run_once(clock.ms)
    chase = [c for c in venue.placed if c.endswith("m")]
    assert len(chase) == 2
    assert all(r.chased and r.taker_qty > 0 for r in runner.reports)
    assert not SpotOrderRepository(conn).unresolved()


def test_halt_file_blocks_new_orders(env) -> None:
    conn, halt = env
    halt.write_text("거래소 점검", encoding="utf-8")
    venue = Venue()
    notes: list[str] = []
    clock = Clock(after(200))
    make(conn, halt, venue, clock, notes).run_once(clock.ms)
    clock.ms = after(201)
    day = make(conn, halt, venue, clock, notes).run_once(clock.ms)
    assert day.status == "HALTED" and venue.placed == []
    assert any("긴급 정지: 거래소 점검" in n for n in notes)


def test_unknown_send_that_reached_exchange_is_followed(env) -> None:
    conn, halt = env
    venue = Venue(unknown_on_post=True, unknown_but_placed=True)
    notes: list[str] = []
    two_days(conn, halt, venue, notes)
    assert len(venue.placed) == 2
    assert {r.state for r in SpotOrderRepository(conn).all()} == {"FILLED"}


def test_unknown_send_not_on_exchange_is_not_resent(env) -> None:
    conn, halt = env
    venue = Venue(unknown_on_post=True, unknown_but_placed=False)
    notes: list[str] = []
    two_days(conn, halt, venue, notes)
    states = sorted(r.state for r in SpotOrderRepository(conn).all())
    assert states.count("NOT_PLACED") == 1
    assert len(venue.placed) == 1  # 없는 것으로 확인된 주문은 다시 보내지 않는다
    assert any("다시 보내지 않음" in n for n in notes)


def test_unresolvable_lookup_blocks_next_day(env) -> None:
    conn, halt = env
    venue = Venue(fill_after=10**9)
    notes: list[str] = []

    def crash(_s: float) -> None:
        raise CrashError

    with pytest.raises(CrashError):
        two_days(conn, halt, venue, notes, sleep=crash)
    venue.lookup_fails = True
    clock = Clock(after(202))
    day = make(conn, halt, venue, clock, notes).run_once(clock.ms)
    assert day.status == "DEGRADED"
    assert len(venue.placed) == 2  # 새 주문 없음


def test_post_only_rejected_three_times_then_market(env) -> None:
    conn, halt = env
    venue = Venue(reject_posts=3)
    notes: list[str] = []
    two_days(conn, halt, venue, notes)
    records = {r.client_order_id: r.state for r in SpotOrderRepository(conn).all()}
    assert "REJECTED" in records.values()
    assert any(c.endswith("m") for c in venue.placed)


def test_quiet_day_says_why_nothing_was_ordered(env) -> None:
    conn, halt = env
    venue = Venue()
    notes: list[str] = []
    clock = two_days(conn, halt, venue, notes)  # 둘째 날 목표대로 샀다
    # 가짜 거래소는 잔고를 갱신하지 않는다 — 다음 날 체결할 목표(둘째 날 결정)대로 보유를 맞춘다
    decided = PaperDayRepository(conn).get(OPT.strategy_id, START + 202 * D - 1)
    assert decided is not None
    value = Decimal(0)
    for s, w in decided.targets.items():
        qty = Decimal(w or 0) * Decimal(5000) / PRICE[s]
        venue.base[s.removesuffix("USDT")] = qty
        value += qty * PRICE[s]
    venue.cash = Decimal(5000) - value
    notes.clear()
    clock.ms = after(202)
    make(conn, halt, venue, clock, notes).run_once(clock.ms)
    quiet = [n for n in notes if "오늘 주문 없음" in n]
    assert quiet and "BTCUSDT: 20·60·120일 추세 3/3 상승" in quiet[0]
    assert "목표 비중" in quiet[0] and "← 현재" in quiet[0]
