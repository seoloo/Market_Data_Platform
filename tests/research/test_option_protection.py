"""옵션 보호 롱 오프라인 필요조건의 고정·경계·속성 시험."""

from dataclasses import replace
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from auto_trading.research.option_protection import (
    LongFirstSnapshot,
    OptionContract,
    audit_long_first,
)


def base_snapshot() -> LongFirstSnapshot:
    return LongFirstSnapshot(
        long_contract=OptionContract(
            "BTCUSDT", "USDT", 1_800_000_000_000, "CALL", Decimal(110_000), Decimal(1)
        ),
        short_contract=OptionContract(
            "BTCUSDT", "USDT", 1_800_000_000_000, "CALL", Decimal(100_000), Decimal(1)
        ),
        confirmed_long_qty=Decimal("0.03"),
        allocated_elsewhere_qty=Decimal("0.01"),
        pending_long_sell_qty=Decimal(0),
        confirmed_short_qty=Decimal("0.01"),
        working_short_qty=Decimal(0),
        unresolved_short_qty=Decimal(0),
        proposed_short_qty=Decimal("0.01"),
        remote_state_complete=True,
    )


def test_only_remote_confirmed_long_covers_proposed_short() -> None:
    snapshot = base_snapshot()
    result = audit_long_first(snapshot)
    assert result.necessary_condition_met
    assert result.available_long_qty == Decimal("0.02")
    assert result.maximum_short_exposure_qty == Decimal("0.02")


def test_unknown_short_reserves_maximum_even_if_no_fill_is_visible() -> None:
    result = audit_long_first(replace(base_snapshot(), unresolved_short_qty=Decimal("0.01")))
    assert not result.necessary_condition_met
    assert "INSUFFICIENT_CONFIRMED_LONG" in result.reasons


def test_pending_long_sale_must_be_reserved_before_short_entry() -> None:
    result = audit_long_first(replace(base_snapshot(), pending_long_sell_qty=Decimal("0.01")))
    assert not result.necessary_condition_met


def test_missing_remote_quantity_fails_closed() -> None:
    result = audit_long_first(replace(base_snapshot(), working_short_qty=None))
    assert not result.necessary_condition_met
    assert result.available_long_qty is None
    assert "REMOTE_QUANTITY_UNKNOWN" in result.reasons


def test_incomplete_reconciliation_fails_closed() -> None:
    result = audit_long_first(replace(base_snapshot(), remote_state_complete=False))
    assert not result.necessary_condition_met
    assert "REMOTE_STATE_INCOMPLETE" in result.reasons


def test_contract_mismatch_and_wrong_wing_fail() -> None:
    snapshot = base_snapshot()
    assert not audit_long_first(
        replace(
            snapshot, long_contract=replace(snapshot.long_contract, expiry_ms=1_900_000_000_000)
        )
    ).necessary_condition_met
    assert not audit_long_first(
        replace(snapshot, long_contract=replace(snapshot.long_contract, strike=Decimal(90_000)))
    ).necessary_condition_met
    assert not audit_long_first(
        replace(snapshot, long_contract=replace(snapshot.long_contract, settlement_asset="BTC"))
    ).necessary_condition_met


def test_put_protection_requires_lower_long_strike() -> None:
    snapshot = base_snapshot()
    valid_put = replace(
        snapshot,
        long_contract=replace(snapshot.long_contract, kind="PUT", strike=Decimal(90_000)),
        short_contract=replace(snapshot.short_contract, kind="PUT"),
    )
    assert audit_long_first(valid_put).necessary_condition_met
    assert not audit_long_first(
        replace(valid_put, long_contract=replace(valid_put.long_contract, strike=Decimal(110_000)))
    ).necessary_condition_met


def test_nonfinite_quantity_and_contract_fail_closed() -> None:
    snapshot = base_snapshot()
    assert (
        "INVALID_QUANTITY"
        in audit_long_first(replace(snapshot, unresolved_short_qty=Decimal("NaN"))).reasons
    )
    assert (
        "INVALID_CONTRACT"
        in audit_long_first(
            replace(snapshot, long_contract=replace(snapshot.long_contract, strike=Decimal("NaN")))
        ).reasons
    )


def test_partial_fill_then_unknown_keeps_short_reservation() -> None:
    opening = replace(
        base_snapshot(),
        confirmed_long_qty=Decimal("0.04"),
        unresolved_short_qty=Decimal("0.02"),
    )
    assert not audit_long_first(opening).necessary_condition_met
    reduced_proposal = replace(opening, proposed_short_qty=Decimal(0))
    assert audit_long_first(reduced_proposal).necessary_condition_met
    partial = replace(
        reduced_proposal,
        confirmed_short_qty=Decimal("0.02"),
        unresolved_short_qty=Decimal("0.01"),
    )
    assert audit_long_first(partial).necessary_condition_met


@given(
    long_units=st.integers(min_value=0, max_value=100),
    elsewhere_units=st.integers(min_value=0, max_value=100),
    pending_sell_units=st.integers(min_value=0, max_value=100),
    short_units=st.integers(min_value=0, max_value=100),
    working_units=st.integers(min_value=0, max_value=100),
    unknown_units=st.integers(min_value=0, max_value=100),
    proposed_units=st.integers(min_value=0, max_value=100),
)
def test_any_accepted_state_satisfies_quantity_invariant(
    long_units: int,
    elsewhere_units: int,
    pending_sell_units: int,
    short_units: int,
    working_units: int,
    unknown_units: int,
    proposed_units: int,
) -> None:
    snapshot = replace(
        base_snapshot(),
        confirmed_long_qty=Decimal(long_units) / 100,
        allocated_elsewhere_qty=Decimal(elsewhere_units) / 100,
        pending_long_sell_qty=Decimal(pending_sell_units) / 100,
        confirmed_short_qty=Decimal(short_units) / 100,
        working_short_qty=Decimal(working_units) / 100,
        unresolved_short_qty=Decimal(unknown_units) / 100,
        proposed_short_qty=Decimal(proposed_units) / 100,
    )
    result = audit_long_first(snapshot)
    expected = (
        short_units + working_units + unknown_units + proposed_units
        <= long_units - elsewhere_units - pending_sell_units
    )
    assert result.necessary_condition_met == expected
    if result.necessary_condition_met:
        assert result.available_long_qty is not None
        assert result.maximum_short_exposure_qty is not None
        assert result.maximum_short_exposure_qty <= result.available_long_qty
