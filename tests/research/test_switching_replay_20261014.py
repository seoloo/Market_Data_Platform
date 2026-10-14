"""Hand-calculated accounting and execution boundaries, without historical returns."""

from decimal import Decimal

import pytest
from scripts.run_direction_switch_20261014 import books, reconcile

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.signal_switching_20261014 import Action, Direction, Signal
from auto_trading.research.switching_replay_20261014 import (
    MINUTE,
    Book,
    Frame,
    Market,
    Mode,
    Quote,
)
from auto_trading.research.wallet_grid_20261013 import Bar, Funding, Scenario

D = Decimal
START = 1_577_836_800_000  # 2020-01-01 UTC; ledger tax years use actual exit timestamps.


def frame(at_ms: int = START, price: str = "100", volume: str = "1000000") -> Frame:
    bar = Bar(D(price), D(price), D(price), D(price), D(volume))
    return Frame(at_ms, at_ms + MINUTE, "2020-01-01", (Quote("X", bar, bar, bar, bar),))


def book(market: Market = Market.CRYPTO, mode: Mode = Mode.SWITCH) -> Book:
    b = Book(market, mode, D("0.7"), Scenario("BASE"))
    b.rates["X"] = D(0)
    b.rate_ms["X"] = START
    return b


def signal(b: Book, f: Frame, direction: Direction) -> None:
    b.begin(f, {"X": (Signal(direction, f"{f.at_ms}:{direction}", f.at_ms),)}, {"X": D(1)})
    b.tick(f)


def test_spot_hand_cash_fees_and_long_cap() -> None:
    b = book()
    signal(b, frame(), Direction.LONG)
    fill = b.fills[0]
    assert fill["action"] == Action.OPEN
    assert fill["price"] == D("100.05")
    assert fill["fee"] == fill["quantity"] * D("100.05") * D("0.001")
    assert b.cash == D(10000) - fill["quantity"] * fill["price"] - fill["fee"]
    assert b.positions["X"].qty * D(100) <= D(7000)


def test_long_to_short_exact_flat_then_sixty_minutes_and_futures_margin() -> None:
    b = book()
    signal(b, frame(), Direction.LONG)
    signal(b, frame(START + 5 * MINUTE), Direction.SHORT)
    assert "X" not in b.positions
    b.tick(frame(START + 65 * MINUTE))
    assert "X" not in b.positions
    b.tick(frame(START + 66 * MINUTE))
    assert b.positions["X"].direction == Direction.SHORT
    assert b.positions["X"].qty * D(100) <= b.equity(frame().quotes) * D("0.1")
    assert b.fills[-1]["cash_delta"] == -b.fills[-1]["fee"]


def test_short_to_long_close_first_and_known_funding() -> None:
    b = book()
    signal(b, frame(), Direction.SHORT)
    qty = b.positions["X"].qty
    event = Funding(START + MINUTE, D("0.001"), D(100), True)
    f = frame(START + MINUTE)
    f = Frame(f.at_ms, f.end_ms, f.day, f.quotes, ((event,),))
    b.tick(f)
    assert b.funding_total == qty * D("0.1")
    assert len(b.funding_events) == 1
    signal(b, frame(START + 2 * MINUTE), Direction.LONG)
    assert "X" not in b.positions
    assert b.fills[-1]["action"] == Action.CLOSE
    assert b.fills[-1]["direction"] == Direction.SHORT


def test_partial_open_completes_after_ack_without_double_counting_held() -> None:
    b = book()
    signal(b, frame(volume="10000"), Direction.LONG)
    assert b.positions["X"].qty == D(10)
    b.tick(frame(START + MINUTE, volume="10000"))
    assert b.positions["X"].qty == D(20)
    b.tick(frame(START + 2 * MINUTE, volume="10000"))
    assert b.positions["X"].qty == D(30)
    assert b.counts["partial_fill"] == 3


def test_partial_close_keeps_old_direction_and_blocks_opposite() -> None:
    b = book()
    signal(b, frame(), Direction.LONG)
    signal(b, frame(START + 5 * MINUTE, volume="10000"), Direction.SHORT)
    assert b.positions["X"].direction == Direction.LONG
    assert b.orders["X"].action == Action.CLOSE
    assert b.fills[-1]["quantity"] == D(10)


def test_cancel_partial_open_keeps_opposite_close_active() -> None:
    b = book()
    signal(b, frame(volume="10000"), Direction.LONG)
    signal(b, frame(START + 5 * MINUTE, volume="10000"), Direction.SHORT)
    assert not b.orders
    assert b.active(START + 6 * MINUTE)
    b.tick(frame(START + 6 * MINUTE, volume="10000"))
    assert "X" not in b.positions
    assert b.fills[-1]["action"] == Action.CLOSE


def test_ack_five_wait_does_not_manufacture_future_flat_confirmation() -> None:
    b = Book(Market.CRYPTO, Mode.SWITCH, D("0.7"), Scenario("ACK5", ack_min=5))
    signal(b, frame(), Direction.LONG)
    signal(b, frame(START + 5 * MINUTE), Direction.FLAT)
    assert not b.positions
    f = frame(START + 6 * MINUTE)
    assert b.evidence(f.quotes[0], f).flat_confirmed_at_ms is None
    b.tick(f)
    assert not b.positions


def test_ordinary_limit_miss_is_not_market_fill() -> None:
    b = book()
    f = frame()
    previous = f.quotes[0].long
    jumped = frame(price="101").quotes[0].long
    f = Frame(f.at_ms, f.end_ms, f.day, (Quote("X", jumped, jumped, previous, previous),))
    signal(b, f, Direction.LONG)
    assert not b.fills
    assert b.counts["limit_miss"] == 1


def test_future_emergency_stop_exception_hand_loss() -> None:
    b = book()
    signal(b, frame(), Direction.SHORT)
    p = b.positions["X"]
    entry, qty, stop = p.entry, p.qty, p.stop
    bar = Bar(D(100), D(104), D(99), D(102), D(1000000))
    previous = frame().quotes[0].long
    f = Frame(
        START + MINUTE,
        START + 2 * MINUTE,
        "2020-01-01",
        (Quote("X", bar, bar, previous, previous),),
    )
    b.tick(f)
    fill = b.fills[-1]
    assert fill["reason"] == "EXCHANGE_STOP_MARKET_EXCEPTION"
    assert fill["price"] == stop * D("1.00245")
    assert fill["cash_delta"] == qty * (entry - fill["price"]) - fill["fee"]
    assert not b.positions


def test_stock_negative_signal_closes_long_reference() -> None:
    b = book(Market.STOCK, Mode.FULL)
    signal(b, frame(), Direction.LONG)
    signal(b, frame(START + 5 * MINUTE), Direction.SHORT)
    assert not b.positions
    assert all(f["direction"] == Direction.LONG for f in b.fills)


def test_sqqq_is_cash_etf_and_gap_stop_limit_cannot_fill() -> None:
    b = book(Market.STOCK)
    signal(b, frame(), Direction.SHORT)
    assert b.fills[0]["cash_delta"] < -b.fills[0]["fee"]
    p = b.positions["X"]
    bar = Bar(D(90), D(91), D(89), D(90), D(1000000))
    previous = frame().quotes[0].long
    f = Frame(
        START + MINUTE,
        START + 2 * MINUTE,
        "2020-01-01",
        (Quote("X", previous, bar, previous, previous),),
    )
    b.tick(f)
    assert b.positions["X"] is p
    assert b.counts["stop_limit_gap_unfilled"] == 1
    assert len(b.fills) == 1


def test_shared_drawdown_halts_but_exit_remains_allowed() -> None:
    b = book()
    signal(b, frame(), Direction.LONG)
    f = frame(START + 5 * MINUTE, "60")
    b.checkpoint(f)
    b.tick(f)
    assert b.halted
    assert not b.positions
    assert b.fills[-1]["action"] == Action.CLOSE


@pytest.mark.parametrize("market", list(Market))
def test_separate_ledger_reconciliation_and_sixty_four_fixed_cases(market: Market) -> None:
    assert len(books(market)) == 32
    assert len({b.key for b in books(market)}) == 32
    b = book(market)
    signal(b, frame(), Direction.SHORT)
    f = frame(START + 5 * MINUTE)
    signal(b, f, Direction.LONG)
    b.checkpoint(f)
    result = b.result(f.quotes)
    audit = reconcile(result, f.quotes, market)
    assert max(audit["differences"].values()) <= D("0.000000001")
    result["cash"] += D(1)
    with pytest.raises(DataIntegrityError, match="reconciliation failed"):
        reconcile(result, f.quotes, market)


def test_stale_funding_denies_new_future_risk() -> None:
    b = book()
    signal(b, frame(START + 9 * 60 * MINUTE), Direction.SHORT)
    assert not b.positions
    assert b.counts["funding_unknown_veto"] > 0


def test_stock_yearly_tax_cash_and_reserve_reconcile() -> None:
    b = book(Market.STOCK)
    signal(b, frame(), Direction.LONG)
    f = frame(START + 5 * MINUTE, "200")
    signal(b, f, Direction.FLAT)
    b.checkpoint(f)
    assert b.tax_reserve > D(0)
    assert max(reconcile(b.result(f.quotes), f.quotes, Market.STOCK)["differences"].values()) <= D(
        "0.000000001"
    )
    next_year = Frame(
        f.at_ms + 365 * 24 * 60 * MINUTE, f.end_ms + 365 * 24 * 60 * MINUTE, "2021-01-01", f.quotes
    )
    b.begin(next_year, {}, {})
    b.checkpoint(next_year)
    assert b.tax_reserve == D(0)
    assert b.tax_paid > D(0)
    assert max(reconcile(b.result(f.quotes), f.quotes, Market.STOCK)["differences"].values()) <= D(
        "0.000000001"
    )


@pytest.mark.leakage
def test_minute_high_is_not_used_for_new_ordinary_entry_price() -> None:
    a, b = book(), book()
    signal(a, frame(), Direction.LONG)
    f = frame()
    huge_high = Bar(D(100), D(999), D(1), D(100), D(1000000))
    signal(
        b,
        Frame(
            f.at_ms,
            f.end_ms,
            f.day,
            (Quote("X", huge_high, huge_high, f.quotes[0].long, f.quotes[0].short),),
        ),
        Direction.LONG,
    )
    assert a.fills == b.fills
