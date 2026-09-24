"""O/v1 Paper 실행기 (Phase 6).

연구 시뮬레이터와 비트 단위 일치, 멱등, 누락일 보정, fail-closed, 원장 분리를 고정한다.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import sqlite3
from decimal import Decimal
from itertools import pairwise

import pytest

from auto_trading.backtest.overlay import (
    O2_STOP,
    O3_2X,
    OverlayBar,
    OverlayConfig,
    OverlayResult,
    simulate_overlay,
)
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.execution.paper_overlay import (
    PaperOverlayRunner,
    state_from_json,
    state_to_json,
    verify_ledger,
)
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_overlay_days import (
    OverlayPaperDay,
    OverlayPaperDayRepository,
)

D = 86_400_000
P0 = 1_700_000_000_000 - 1_700_000_000_000 % D  # Paper 첫 결정일 (open_ms)
HISTORY = 160
DAYS = 150
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def path(n: int, seed: int, drift: float, vol: float = 0.03) -> list[float]:
    rng = random.Random(seed)
    price, out = 100.0, []
    for _ in range(n):
        price *= math.exp(drift + rng.gauss(0, vol))
        out.append(round(price, 6))
    return out


class FakeMarket:
    """``now``가 일봉 ``k``의 마감 직후일 때의 공개 시세."""

    def __init__(self, *, seed: int = 7, drift: float = 0.004, vol: float = 0.03) -> None:
        n = HISTORY + DAYS
        first = P0 - HISTORY * D
        opens = [first + k * D for k in range(n)]
        self.spot: dict[str, dict[int, tuple[float, float]]] = {}
        self.perp: dict[str, dict[int, tuple[float, float]]] = {}
        self.funding_data: dict[str, list[tuple[int, float]]] = {}
        rng = random.Random(seed + 100)
        for i, s in enumerate(SYMBOLS):
            closes = path(n, seed + i, drift, vol)
            self.spot[s] = {t: (round(c * 0.98, 6), c) for t, c in zip(opens, closes, strict=True)}
            self.perp[s] = {
                t: (round(c * 1.001 * 0.97, 6), round(c * 1.001, 6))
                for t, c in zip(opens, closes, strict=True)
            }
            # 정산 시각에 +2ms 지터를 넣는다 (실제 Binance 기록처럼)
            self.funding_data[s] = [
                (t + h * 8 * 3_600_000 + 2, round(rng.uniform(-0.0001, 0.0004), 8))
                for t in opens
                for h in range(3)
            ]
        self.live_skew = Decimal(1)

    @staticmethod
    def _candles(src: dict[int, tuple[float, float]], as_of_ms: int, limit: int) -> list[Candle]:
        out = [
            Candle(
                t,
                t + D - 1,
                Decimal(repr(c)),
                Decimal(repr(c)),
                Decimal(repr(lo)),
                Decimal(repr(c)),
                Decimal(1),
            )
            for t, (lo, c) in sorted(src.items())
            if t + D - 1 <= as_of_ms
        ]
        return out[-limit:]

    def spot_daily(self, symbol: str, as_of_ms: int, limit: int) -> list[Candle]:
        return self._candles(self.spot[symbol], as_of_ms, limit)

    def perp_daily(self, symbol: str, as_of_ms: int, limit: int) -> list[Candle]:
        return self._candles(self.perp[symbol], as_of_ms, limit)

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        return [(t, r) for t, r in self.funding_data[symbol] if start_ms <= t <= end_ms]

    def spot_price(self, symbol: str) -> Decimal:
        return Decimal(1234) * self.live_skew

    def perp_price(self, symbol: str) -> Decimal:
        return Decimal(1235) * self.live_skew


def now_after(day_open: int) -> int:
    return day_open + D + 5 * 60_000


def setup(
    market: FakeMarket | None = None,
) -> tuple[PaperOverlayRunner, FakeMarket, sqlite3.Connection]:
    conn = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    market = market or FakeMarket()
    runner = PaperOverlayRunner(market, OverlayPaperDayRepository(conn), clock=lambda: 1)
    return runner, market, conn


def research(market: FakeMarket, last_day: int, cfg: OverlayConfig | None = None) -> OverlayResult:
    spot = {s: {t: OverlayBar(lo, c) for t, (lo, c) in market.spot[s].items()} for s in SYMBOLS}
    perp = {s: {t: OverlayBar(lo, c) for t, (lo, c) in market.perp[s].items()} for s in SYMBOLS}
    return simulate_overlay(
        spot,
        perp,
        market.funding_data,
        from_ms=P0,
        to_ms=last_day,
        initial_equity=10_000.0,
        cfg=cfg if cfg is not None else OverlayConfig(),
    )


class TestResearchParity:
    @pytest.mark.parametrize(
        ("vol", "capped"),
        [(0.03, False), (0.012, True)],
        ids=["normal", "leverage-cap"],
    )
    def test_daily_runs_reproduce_the_research_path_bit_for_bit(
        self, vol: float, capped: bool
    ) -> None:
        runner, market, conn = setup(FakeMarket(vol=vol))
        last = P0 + (DAYS - 2) * D
        day = P0
        while day <= last:
            runner.run_once(now_after(day))
            day += D
        rows = OverlayPaperDayRepository(conn).all("o1_overlay")
        expected = research(market, last)
        assert [r.decision_ms + 1 - D for r in rows] == expected.days
        assert [r.equity for r in rows] == expected.equity  # 비트 단위
        assert [r.tally["exposure"] for r in rows] == expected.exposure
        assert sum(r.tally["trades"] for r in rows) == expected.trades
        assert sum(r.tally["funding"] for r in rows) == pytest.approx(expected.funding, rel=1e-12)
        # 실제로 선물을 썼고 funding 을 냈는지 — 빈 경로의 일치는 의미가 없다
        assert expected.funding < 0
        assert expected.max_futures_leverage > 0
        # 저변동 경로는 현물을 줄여 선물로 대신 드는 분기(k < 1)까지 지나가야 한다
        assert (expected.capped_days > 0) is capped
        assert sum(r.tally["capped_days"] for r in rows) == expected.capped_days

    def test_status_is_ok_after_the_first_day(self) -> None:
        runner, _, conn = setup()
        for k in range(5):
            runner.run_once(now_after(P0 + k * D))
        statuses = [r.status for r in OverlayPaperDayRepository(conn).all("o1_overlay")]
        assert statuses == ["NO_PRIOR_DECISION", "OK", "OK", "OK", "OK"]


class TestIdempotence:
    def test_second_run_on_the_same_day_writes_nothing(self) -> None:
        runner, _, conn = setup()
        first = runner.run_once(now_after(P0))
        again = runner.run_once(now_after(P0) + 3_600_000)
        assert again == first[-1:]
        assert len(OverlayPaperDayRepository(conn).all("o1_overlay")) == 1


class TestMissedDays:
    def _run(self, days: list[int]) -> tuple[list[OverlayPaperDay], FakeMarket]:
        runner, market, conn = setup()
        for k in days:
            runner.run_once(now_after(P0 + k * D))
        return OverlayPaperDayRepository(conn).all("o1_overlay"), market

    # 0~130일 실행, 131~134일 4일 누락, 135일 실행
    def test_missed_days_are_filled_in_holding_only(self) -> None:
        rows, _ = self._run([*range(0, 131), 135])
        tail = rows[-6:]
        assert [r.status for r in tail] == ["OK", *["MISSED"] * 4, "NO_PRIOR_DECISION"]
        for r in tail[1:]:
            assert not [f for f in r.fills if f["reason"] == "rebalance"]
            assert r.market["executed_target"] is None
        # 날짜가 비지 않는다
        opens = [r.decision_ms for r in rows]
        assert all(b - a == D for a, b in pairwise(opens))

    def test_missed_days_still_charge_funding_on_held_futures(self) -> None:
        rows, _ = self._run([*range(0, 131), 135])
        held = [r for r in rows[-5:-1] if any(q for q in r.state["fut_qty"].values())]
        assert held, "선물을 들고 있는 누락일이 있어야 이 검사가 의미가 있다"
        assert all(r.tally["funding"] != 0 for r in held)

    def test_positions_do_not_change_on_missed_days(self) -> None:
        rows, _ = self._run([*range(0, 131), 135])
        before = rows[-6].state
        for r in rows[-5:]:
            if r.tally["liquidations"] or r.tally["halted_day"]:
                continue
            assert r.state["spot_qty"] == before["spot_qty"]
            assert r.state["fut_qty"] == before["fut_qty"]

    def test_the_day_after_catch_up_trades_normally(self) -> None:
        rows, _ = self._run([*range(0, 131), 135, 136])
        assert rows[-1].status == "OK"
        assert rows[-1].market["executed_target"] == rows[-2].state["target"]

    def test_too_long_a_gap_is_refused(self) -> None:
        runner, _, conn = setup()
        runner.run_once(now_after(P0))
        with pytest.raises(DataIntegrityError, match="수동 확인"):
            runner.run_once(now_after(P0 + 40 * D))
        assert len(OverlayPaperDayRepository(conn).all("o1_overlay")) == 1


class TestFailClosed:
    def test_missing_funding_writes_nothing_and_the_next_run_recovers(self) -> None:
        runner, market, conn = setup()
        for k in range(3):
            runner.run_once(now_after(P0 + k * D))
        day = P0 + 3 * D
        saved = list(market.funding_data["ETHUSDT"])
        market.funding_data["ETHUSDT"] = [(t, r) for t, r in saved if not day <= t < day + D]
        with pytest.raises(DataIntegrityError, match="funding"):
            runner.run_once(now_after(day))
        repo = OverlayPaperDayRepository(conn)
        assert len(repo.all("o1_overlay")) == 3
        market.funding_data["ETHUSDT"] = saved
        runner.run_once(now_after(day))
        assert [r.status for r in repo.all("o1_overlay")][-1] == "OK"

    def test_missing_perp_bar_writes_nothing(self) -> None:
        runner, market, conn = setup()
        runner.run_once(now_after(P0))
        del market.perp["BTCUSDT"][P0 + D]
        with pytest.raises(DataIntegrityError, match="무기한 일봉 결측"):
            runner.run_once(now_after(P0 + D))
        assert len(OverlayPaperDayRepository(conn).all("o1_overlay")) == 1

    def test_gap_inside_the_signal_history_is_refused(self) -> None:
        """target_weight 는 위치로 과거를 찾는다. 빈 날이 있으면 '20일 전'이 틀어진다."""
        market = FakeMarket()
        del market.spot["ETHUSDT"][P0 - 50 * D]
        runner, _, conn = setup(market)
        with pytest.raises(DataIntegrityError, match="빈 날"):
            runner.run_once(now_after(P0))
        assert OverlayPaperDayRepository(conn).all("o1_overlay") == []


class TestLedger:
    def test_state_round_trips_through_json_exactly(self) -> None:
        runner, _, conn = setup()
        for k in range(130):
            runner.run_once(now_after(P0 + k * D))
        row = OverlayPaperDayRepository(conn).latest("o1_overlay")
        assert row is not None
        state = state_from_json(json.loads(json.dumps(row.state)))
        assert state_to_json(state) == row.state

    def test_live_prices_are_recorded_but_not_used(self) -> None:
        a_runner, _, a_conn = setup()
        b_market = FakeMarket()
        b_market.live_skew = Decimal(5)
        b_runner, _, b_conn = setup(b_market)
        for k in range(10):
            a_runner.run_once(now_after(P0 + k * D))
            b_runner.run_once(now_after(P0 + k * D))
        a = OverlayPaperDayRepository(a_conn).all("o1_overlay")
        b = OverlayPaperDayRepository(b_conn).all("o1_overlay")
        assert [r.equity for r in a] == [r.equity for r in b]
        assert a[-1].observed["spot"] != b[-1].observed["spot"]

    def test_t2_paper_tables_are_untouched(self) -> None:
        runner, _, conn = setup()
        for k in range(5):
            runner.run_once(now_after(P0 + k * D))
        assert conn.execute("SELECT COUNT(*) FROM paper_days").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM spot_orders").fetchone()[0] == 0


class TestVerifyLedger:
    def _ledger(self) -> list[OverlayPaperDay]:
        runner, _, conn = setup()
        for k in [*range(0, 131), 135, 136, 137]:
            runner.run_once(now_after(P0 + k * D))
        return OverlayPaperDayRepository(conn).all("o1_overlay")

    def test_a_clean_ledger_including_catch_up_has_no_problems(self) -> None:
        assert verify_ledger(self._ledger()) == []

    def test_an_edited_equity_is_caught(self) -> None:
        rows = self._ledger()
        rows[50] = dataclasses.replace(rows[50], equity=rows[50].equity + 0.01)
        assert any("자본" in p for p in verify_ledger(rows))

    def test_a_swapped_target_is_caught(self) -> None:
        rows = self._ledger()
        assert rows[-1].status == "OK"
        market = dict(rows[-1].market)
        market["executed_target"] = {"BTCUSDT": 0.5, "ETHUSDT": 0.5}
        rows[-1] = dataclasses.replace(rows[-1], market=market)
        assert any("목표" in p for p in verify_ledger(rows))

    def test_a_deleted_day_is_caught(self) -> None:
        rows = self._ledger()
        del rows[60]
        problems = verify_ledger(rows)
        assert any("간격" in p for p in problems)


class TestO2Ledger:
    """O/v2(비상 스톱 −20%) 별도 원장 (docs/research/28)."""

    @staticmethod
    def crash_market() -> FakeMarket:
        market = FakeMarket(vol=0.012)  # 저변동 상승 — 선물을 들고 있다
        for k in (40, 80, 120):  # 무기한 저가만 전날 종가의 75% (종가는 그대로)
            day = P0 + k * D
            for s in SYMBOLS:
                prev_close = market.perp[s][day - D][1]
                market.perp[s][day] = (round(prev_close * 0.75, 6), market.perp[s][day][1])
        return market

    def test_daily_runs_reproduce_research_with_stop(self) -> None:
        market = self.crash_market()
        conn = connect(":memory:")
        apply_migrations(conn, now_ms=1)
        repo = OverlayPaperDayRepository(conn)
        runner = PaperOverlayRunner(
            market, repo, clock=lambda: 1, cfg=O2_STOP, strategy_id="o2_stop"
        )
        last = P0 + (DAYS - 2) * D
        day = P0
        while day <= last:
            runner.run_once(now_after(day))
            day += D
        rows = repo.all("o2_stop")
        expected = research(market, last, O2_STOP)
        assert expected.stop_hits > 0  # 스톱이 실제로 발동한 경로의 일치여야 의미가 있다
        assert [r.equity for r in rows] == expected.equity  # 비트 단위
        assert sum(r.tally["stop_hits"] for r in rows) == expected.stop_hits
        assert verify_ledger(rows, cfg=O2_STOP) == []
        assert verify_ledger(rows) != []  # O/v1 규칙으로는 재현되지 않는다
        assert repo.all("o1_overlay") == []  # 원장 분리

    def test_o1_record_format_is_unchanged(self) -> None:
        runner, _, conn = setup()
        runner.run_once(now_after(P0))
        (row,) = OverlayPaperDayRepository(conn).all("o1_overlay")
        assert "stop_hits" not in row.tally

    def test_o3_two_times_ledger_verifies_with_its_own_rules(self) -> None:
        market = self.crash_market()
        conn = connect(":memory:")
        apply_migrations(conn, now_ms=1)
        repo = OverlayPaperDayRepository(conn)
        runner = PaperOverlayRunner(market, repo, clock=lambda: 1, cfg=O3_2X, strategy_id="o3_2x")
        for k in range(130):
            runner.run_once(now_after(P0 + k * D))
        rows = repo.all("o3_2x")
        expected = research(market, P0 + 129 * D, O3_2X)
        assert [r.equity for r in rows] == expected.equity  # 비트 단위
        assert verify_ledger(rows, cfg=O3_2X) == []
        assert verify_ledger(rows, cfg=O2_STOP) != []  # 1.5배 규칙으로는 재현되지 않는다
        assert max(r.tally["exposure"] for r in rows) > 1.5  # 실제로 2배 쪽으로 컸다
