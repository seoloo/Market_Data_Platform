"""Evidence contract tests; no network or actual account/order access."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import CausalityError, DataIntegrityError, StaleDataError
from auto_trading.exchange.fake_futures import FakeFuturesExchange
from auto_trading.exchange.futures import FuturesFilters, FuturesOrderType
from auto_trading.execution.futures_orders import FuturesOrderManager, capped_price
from auto_trading.research.execution_evidence_20261013 import (
    Depth,
    FillEvidence,
    FundingCash,
    Mark,
    OrderEvidence,
    Rules,
    Scope,
    audit_orders,
    capped_limit,
    estimate_depth,
    replay_short,
)
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository

D = Decimal
SCOPE = Scope("SYNTHETIC_USDM", "AUDIT", "BTCUSDT")
RULES = Rules(D("0.1"), D("0.001"), D("0.001"), D("0.1"))
BOOK = Depth(
    "BTCUSDT",
    190,
    200,
    0,
    0,
    ((D(100), D(1)), (D("99.9"), D(2))),
    ((D("100.1"), D(1)), (D("100.2"), D(2))),
)
MARK = Mark("BTCUSDT", D(100), 200, 190)
ORDER = OrderEvidence(SCOPE, "intent", "order", "SELL", D(1), D(1), D(1), D(99), 100, 900, "FILLED")
FILL = FillEvidence(SCOPE, "order", "trade", "SELL", D(1), D(100), D("0.05"), "USDT", 500, 600)


def test_buy_sell_depth_and_haircut_are_only_indicative() -> None:
    buy = estimate_depth(BOOK, MARK, RULES, "BUY", D(2), as_of_ms=200, visible_usage=D(1))
    sell = estimate_depth(BOOK, MARK, RULES, "SELL", D(2), as_of_ms=200, visible_usage=D(1))
    conservative = estimate_depth(
        BOOK, MARK, RULES, "BUY", D(2), as_of_ms=200, visible_usage=D("0.25")
    )
    assert buy.indicative_vwap == D("100.15")
    assert sell.indicative_vwap == D("99.95")
    assert conservative.indicative_qty == D("0.75")
    assert conservative.remaining_qty == D("1.25")
    assert buy.status == conservative.status == "INDICATIVE_ONLY"


def test_cap_does_not_consume_levels_outside_limit() -> None:
    book = replace(BOOK, asks=((D("100.1"), D(1)), (D(101), D(10))))
    out = estimate_depth(book, MARK, RULES, "BUY", D(2), as_of_ms=200, visible_usage=D(1))
    assert out.indicative_qty == out.remaining_qty == D(1)


def test_quantity_rounding_and_minimum() -> None:
    out = estimate_depth(BOOK, MARK, RULES, "BUY", D("0.0009"), as_of_ms=200, visible_usage=D(1))
    assert out.status == "BELOW_MIN"
    assert out.requested_qty == out.indicative_qty == D(0)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_price_rounding_matches_existing_executor(side: str) -> None:
    if side == "BUY":
        price = capped_limit(D("50000.07"), "BUY", D("0.1"), D(30))
    else:
        price = capped_limit(D("50000.07"), "SELL", D("0.1"), D(30))
    assert price == capped_price(D("50000.07"), side, D("0.003"), D("0.1"))


@pytest.mark.parametrize(
    "book",
    [
        replace(BOOK, bids=()),
        replace(BOOK, bids=((D(100), D(1)), (D(100), D(2)))),
        replace(BOOK, asks=((D("100.2"), D(1)), (D("100.1"), D(1)))),
        replace(BOOK, bids=((D(101), D(1)),)),
        replace(BOOK, bids=((D("100.01"), D(1)),)),
        replace(BOOK, asks=((D("100.1"), D("NaN")),)),
        replace(BOOK, clock_offset_ms=None),
        replace(BOOK, clock_uncertainty_ms=251),
        replace(BOOK, symbol="SOLUSDT"),
    ],
)
def test_invalid_depth_fail_closed(book: Depth) -> None:
    with pytest.raises(DataIntegrityError):
        estimate_depth(book, MARK, RULES, "BUY", D(1), as_of_ms=200, visible_usage=D(1))


def test_spread_limit_and_unregistered_depth_use() -> None:
    with pytest.raises(DataIntegrityError):
        estimate_depth(
            replace(BOOK, asks=((D(102), D(1)),)),
            MARK,
            RULES,
            "BUY",
            D(1),
            as_of_ms=200,
            visible_usage=D(1),
        )
    with pytest.raises(DataIntegrityError):
        estimate_depth(BOOK, MARK, RULES, "BUY", D(1), as_of_ms=200, visible_usage=D("0.5"))


@pytest.mark.leakage
def test_future_quote_unavailable_even_with_old_exchange_time() -> None:
    with pytest.raises(CausalityError):
        estimate_depth(
            replace(BOOK, received_ms=201),
            MARK,
            RULES,
            "BUY",
            D(1),
            as_of_ms=200,
            visible_usage=D(1),
        )
    with pytest.raises(CausalityError):
        estimate_depth(
            BOOK,
            replace(MARK, received_ms=201),
            RULES,
            "BUY",
            D(1),
            as_of_ms=200,
            visible_usage=D(1),
        )


def test_exchange_future_and_stale_quote() -> None:
    with pytest.raises(CausalityError):
        estimate_depth(
            replace(BOOK, exchange_ms=201),
            MARK,
            RULES,
            "BUY",
            D(1),
            as_of_ms=200,
            visible_usage=D(1),
        )
    with pytest.raises(StaleDataError):
        estimate_depth(BOOK, MARK, RULES, "BUY", D(1), as_of_ms=1201, visible_usage=D(1))


@pytest.mark.parametrize("mark", [replace(MARK, exchange_ms=None), replace(MARK, exchange_ms=-1)])
def test_unknown_mark_exchange_time_rejected(mark: Mark) -> None:
    with pytest.raises(DataIntegrityError):
        estimate_depth(BOOK, mark, RULES, "BUY", D(1), as_of_ms=200, visible_usage=D(1))


def test_received_now_cannot_hide_stale_exchange_mark() -> None:
    with pytest.raises(StaleDataError):
        estimate_depth(
            replace(BOOK, received_ms=1500, exchange_ms=1490),
            replace(MARK, received_ms=1500),
            RULES,
            "BUY",
            D(1),
            as_of_ms=1500,
            visible_usage=D(1),
        )


def test_identical_fill_retransmit_and_different_scope_id() -> None:
    later = replace(FILL, received_ms=700)
    other_scope = replace(SCOPE, venue="SYNTHETIC_OTHER")
    other_order, other_fill = replace(ORDER, scope=other_scope), replace(FILL, scope=other_scope)
    out = audit_orders((ORDER, other_order), (FILL, later, other_fill), as_of_ms=1000)
    assert out.complete and len(out.unique_fills) == 2


@pytest.mark.parametrize(
    "bad",
    [
        replace(FILL, fee=None),
        replace(FILL, fee_asset="BNB"),
        replace(FILL, price=D(98)),
        replace(FILL, qty=D("NaN")),
        replace(FILL, side="BUY"),
        replace(FILL, scope=replace(SCOPE, strategy="OTHER")),
        replace(FILL, exchange_ms=901, received_ms=950),
    ],
)
def test_fill_missing_fee_limit_scope_and_time(bad: FillEvidence) -> None:
    with pytest.raises(DataIntegrityError):
        audit_orders((ORDER,), (bad,), as_of_ms=1000)


def test_conflicting_fill_identity_and_overfill_rejected() -> None:
    with pytest.raises(DataIntegrityError, match="conflicting"):
        audit_orders((ORDER,), (FILL, replace(FILL, fee=D("0.06"))), as_of_ms=1000)
    with pytest.raises(DataIntegrityError, match="overfilled"):
        audit_orders((ORDER,), (replace(FILL, qty=D(2)),), as_of_ms=1000)


def test_multiple_attempts_cannot_overfill_original_intent() -> None:
    other_order = replace(ORDER, order_id="other")
    other_fill = replace(FILL, order_id="other", trade_id="other")
    with pytest.raises(DataIntegrityError, match="aggregate"):
        audit_orders((ORDER, other_order), (FILL, other_fill), as_of_ms=1000)


def test_missing_execution_and_unknown_are_incomplete() -> None:
    assert not audit_orders((ORDER,), (), as_of_ms=1000).complete
    out = audit_orders((replace(ORDER, state="UNKNOWN"),), (FILL,), as_of_ms=1000)
    assert out.issues == ("UNRESOLVED_ORDER",)
    with pytest.raises(DataIntegrityError, match="incomplete"):
        replay_short(out, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=1000)


def test_terminal_partial_fill_still_has_position() -> None:
    order = replace(ORDER, cumulative_qty=D("0.4"), state="EXPIRED")
    evidence = audit_orders((order,), (replace(FILL, qty=D("0.4")),), as_of_ms=1000)
    ledger = replay_short(evidence, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=1000)
    assert evidence.complete and ledger.short_qty == D("0.4")
    assert ledger.cash == D("999.95")


@pytest.mark.leakage
def test_future_fill_conflict_does_not_change_past_audit() -> None:
    before = audit_orders((ORDER,), (FILL,), as_of_ms=1000)
    after = audit_orders(
        (ORDER,), (FILL, replace(FILL, fee=D(999), received_ms=1001)), as_of_ms=1000
    )
    assert before == after
    with pytest.raises(CausalityError):
        replay_short(before, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=999)


def test_hand_accounting_partial_close_funding_and_unrealized() -> None:
    close = replace(
        ORDER,
        intent_id="close",
        order_id="close",
        side="BUY",
        requested_qty=D("0.4"),
        intent_qty=D("0.4"),
        cumulative_qty=D("0.4"),
        limit_price=D(96),
    )
    fill_close = replace(
        FILL,
        order_id="close",
        trade_id="close",
        side="BUY",
        qty=D("0.4"),
        price=D(95),
        fee=D("0.019"),
        exchange_ms=700,
        received_ms=750,
    )
    audit = audit_orders((ORDER, close), (fill_close, FILL), as_of_ms=1000)
    fund = FundingCash(SCOPE, "f", D("0.1"), 650, 680)
    ledger = replay_short(
        audit,
        SCOPE,
        (fund, replace(fund, received_ms=800)),
        initial_cash=D(1000),
        mark=Mark("BTCUSDT", D(98), 1000),
        as_of_ms=1000,
    )
    assert ledger.short_qty == D("0.6") and ledger.average_entry == D(100)
    assert ledger.realized == D(2) and ledger.fees == D("0.069")
    assert ledger.funding == D("0.1") and ledger.unrealized == D("1.2")
    assert ledger.equity == D("1003.231")


@pytest.mark.leakage
def test_future_funding_does_not_change_past_ledger() -> None:
    evidence = audit_orders((ORDER,), (FILL,), as_of_ms=1000)
    before = replay_short(evidence, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=1000)
    future = FundingCash(SCOPE, "future", D(999), 800, 1001)
    after = replay_short(evidence, SCOPE, (future,), initial_cash=D(1000), mark=MARK, as_of_ms=1000)
    assert before == after


def test_funding_duplicate_conflict_and_stale_valuation_rejected() -> None:
    evidence = audit_orders((ORDER,), (FILL,), as_of_ms=1000)
    event = FundingCash(SCOPE, "f", D("0.1"), 650, 680)
    with pytest.raises(DataIntegrityError, match="duplicate"):
        replay_short(
            evidence,
            SCOPE,
            (event, replace(event, cash_usdt=D(1))),
            initial_cash=D(1000),
            mark=MARK,
            as_of_ms=1000,
        )
    with pytest.raises(StaleDataError):
        replay_short(evidence, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=1500)


def test_position_reversal_and_same_ms_ambiguity_rejected() -> None:
    close = replace(ORDER, intent_id="close", order_id="close", side="BUY", limit_price=D(101))
    fill_close = replace(FILL, order_id="close", trade_id="close", side="BUY")
    ambiguous = audit_orders((ORDER, close), (FILL, fill_close), as_of_ms=1000)
    with pytest.raises(DataIntegrityError, match="ambiguous"):
        replay_short(ambiguous, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=1000)
    close = replace(close, requested_qty=D(2), cumulative_qty=D(2), intent_qty=D(2))
    fill_close = replace(fill_close, qty=D(2), exchange_ms=700, received_ms=750)
    too_much = audit_orders((ORDER, close), (FILL, fill_close), as_of_ms=1000)
    with pytest.raises(DataIntegrityError, match="over-reduction"):
        replay_short(too_much, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=1000)


@given(st.integers(min_value=1, max_value=40))
def test_duplicate_replay_equity_is_invariant(copies: int) -> None:
    evidence = audit_orders((ORDER,), (FILL,) * copies, as_of_ms=1000)
    ledger = replay_short(evidence, SCOPE, (), initial_cash=D(1000), mark=MARK, as_of_ms=1000)
    assert ledger.short_qty == D(1) and ledger.equity == D("999.95")


def test_existing_fake_partial_recenter_exposes_initial_cap_drift() -> None:
    class Recentered(FakeFuturesExchange):
        queries = 0

        def mark_price(self, symbol: str) -> Decimal:
            if self.queries:
                self.prices[symbol] *= D("0.99")
            self.queries += 1
            return self.prices[symbol]

    ex = Recentered(
        wallet=D(10000),
        prices={"BTCUSDT": D(50000)},
        symbol_filters={"BTCUSDT": FuturesFilters(D("0.001"), D("0.001"), D(1), D("0.1"))},
        fill_ratio=D("0.5"),
    )
    with sqlite3.connect(":memory:") as conn:
        apply_migrations(conn, now_ms=1)
        manager = FuturesOrderManager(
            ex,
            FuturesOrderRepository(conn),
            mode="synthetic",
            strategy_id="audit",
            strategy_tag="audit",
            clock=lambda: 1000,
        )
        result = manager.submit_capped(
            decision_ms=100, symbol="BTCUSDT", side="SELL", quantity=D("0.008"), purpose="OPEN"
        )
        assert len(result.records) == 3 and result.filled == D("0.007")
        assert any(r.avg_price < D(49850) for r in result.records)
        assert all(o.order_type is FuturesOrderType.LIMIT for o in ex.orders.values())
        orders = tuple(
            OrderEvidence(
                SCOPE,
                "open",
                r.client_order_id,
                "SELL",
                r.qty,
                D("0.008"),
                r.filled_qty,
                r.limit_price,
                100,
                1000,
                r.state,
            )
            for r in result.records
        )
        fills = tuple(
            FillEvidence(
                SCOPE,
                r.client_order_id,
                str(i),
                "SELL",
                r.filled_qty,
                r.avg_price,
                r.fee,
                r.fee_asset,
                200 + i,
                300 + i,
            )
            for i, r in enumerate(result.records)
        )
        audit = audit_orders(orders, fills, as_of_ms=1000)
        ledger = replay_short(
            audit,
            SCOPE,
            (),
            initial_cash=D(10000),
            mark=Mark("BTCUSDT", ex.prices["BTCUSDT"], 1000),
            as_of_ms=1000,
        )
        assert ledger.short_qty == abs(ex.account().positions["BTCUSDT"].quantity)
        assert ledger.cash == ex.wallet
        assert ledger.average_entry == ex.account().positions["BTCUSDT"].entry_price
