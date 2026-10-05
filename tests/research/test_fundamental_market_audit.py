"""명부·현금 경계의 합성 계약 검사. 실제 지급/구성 인증·전략 성과가 아니다."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from itertools import permutations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research.fundamental_market_audit import (
    CashClaim,
    CashReceipt,
    MembershipAnchor,
    MembershipChange,
    audit_cash_claim,
    audit_membership_chain,
)

CLAIM = CashClaim("synthetic-common-id", Decimal(10), Decimal("54.20"), 100, 110, "fixture")
RECEIPT = CashReceipt(CLAIM.security_id, Decimal(530), 200, 300, 210, "receipt-fixture")
START = MembershipAnchor(
    "synthetic-index", date(2022, 9, 30), 100, frozenset({"A", "B"}), "OFFICIAL_INDEX", 110, "start"
)
END = MembershipAnchor(
    START.index_id, date(2022, 11, 1), 400, frozenset({"C", "D"}), "OFFICIAL_INDEX", 410, "end"
)
CHANGES = (
    MembershipChange(
        "one", START.index_id, date(2022, 10, 3), frozenset({"C"}), frozenset({"A"}), 200, "one"
    ),
    MembershipChange(
        "two", START.index_id, date(2022, 11, 1), frozenset({"D"}), frozenset({"B"}), 300, "two"
    ),
)


def test_unknown_receipt_does_not_make_cash_spendable() -> None:
    result = audit_cash_claim(CLAIM, None, as_of_ms=150)
    assert result is not None
    assert result.gross_legal_right_usd == Decimal(542)
    assert result.confirmed_spendable_usd is None
    assert result.status == "SETTLEMENT_UNVERIFIED"


def test_cash_requires_both_receipt_and_availability() -> None:
    pending = audit_cash_claim(CLAIM, RECEIPT, as_of_ms=299)
    paid = audit_cash_claim(CLAIM, RECEIPT, as_of_ms=300)
    assert pending is not None and pending.confirmed_spendable_usd == 0
    assert pending.status == "CASH_NOT_YET_AVAILABLE"
    assert paid is not None and paid.confirmed_spendable_usd == Decimal(530)
    assert paid.status == "RECEIPT_CONFIRMED"
    assert paid.gross_legal_right_usd == Decimal(542)


@pytest.mark.parametrize("cutoff", [99, 100, 109])
def test_claim_requires_effective_and_known_time(cutoff: int) -> None:
    assert audit_cash_claim(CLAIM, None, as_of_ms=cutoff) is None


@pytest.mark.leakage
@given(st.integers(min_value=0, max_value=542))
def test_future_cash_receipt_does_not_change_past(amount: int) -> None:
    future = replace(RECEIPT, net_usd=Decimal(amount))
    assert audit_cash_claim(CLAIM, future, as_of_ms=150) == audit_cash_claim(
        CLAIM, None, as_of_ms=150
    )


@pytest.mark.parametrize(
    "receipt,error",
    [
        (replace(RECEIPT, security_id="other-class"), "RECEIPT_SECURITY_MISMATCH"),
        (replace(RECEIPT, net_usd=Decimal(543)), "RECEIPT_EXCEEDS_REVIEWED_CASH_RIGHT"),
    ],
)
def test_receipt_scope_and_amount_fail_closed(receipt: CashReceipt, error: str) -> None:
    with pytest.raises(DataIntegrityError, match=error):
        audit_cash_claim(CLAIM, receipt, as_of_ms=400)


def test_credit_before_completion_is_not_a_merger_receipt() -> None:
    with pytest.raises(CausalityError, match="CREDIT_BEFORE_MERGER_EFFECTIVE"):
        audit_cash_claim(CLAIM, replace(RECEIPT, credited_at_ms=99), as_of_ms=400)


@pytest.mark.parametrize("value", [Decimal(-1), Decimal("NaN"), Decimal("Infinity"), Decimal(0)])
def test_invalid_eligible_quantities(value: Decimal) -> None:
    with pytest.raises(DataIntegrityError):
        replace(CLAIM, eligible_shares=value)


@pytest.mark.parametrize("field,value", [("known_at_ms", 99), ("effective_at_ms", True)])
def test_unverified_claim_time_rejected(field: str, value: int) -> None:
    with pytest.raises(DataIntegrityError):
        if field == "known_at_ms":
            replace(CLAIM, known_at_ms=value)
        else:
            replace(CLAIM, effective_at_ms=value)


@pytest.mark.parametrize("field,value", [("cash_available_at_ms", 199), ("known_at_ms", 199)])
def test_receipt_time_order_rejected(field: str, value: int) -> None:
    with pytest.raises(DataIntegrityError):
        if field == "cash_available_at_ms":
            replace(RECEIPT, cash_available_at_ms=value)
        else:
            replace(RECEIPT, known_at_ms=value)


def test_change_order_and_identical_redelivery_are_deterministic() -> None:
    expected = audit_membership_chain(START, END, CHANGES, as_of_ms=500)
    assert expected.matched
    assert expected.applied_event_ids == ("one", "two")
    for items in permutations((*CHANGES, CHANGES[0])):
        assert audit_membership_chain(START, END, items, as_of_ms=500) == expected


def test_missing_change_is_detected_by_both_member_differences() -> None:
    result = audit_membership_chain(START, END, CHANGES[:1], as_of_ms=500)
    assert not result.matched
    assert result.missing_security_ids == frozenset({"D"})
    assert result.unexpected_security_ids == frozenset({"B"})


def test_member_count_equality_is_not_identity_equality() -> None:
    result = audit_membership_chain(START, END, (), as_of_ms=500)
    assert len(result.expected_security_ids) == len(END.security_ids)
    assert not result.matched


def test_offsetting_missing_changes_are_not_certified_by_matched_anchors() -> None:
    # 중간에 A->C->A가 빠져도 끝 명부가 같을 수 있으므로 matched는 완전성 PASS가 아니다.
    end = replace(END, security_ids=START.security_ids)
    result = audit_membership_chain(START, end, (), as_of_ms=500)
    assert result.matched
    assert result.applied_event_ids == ()


@pytest.mark.leakage
@given(st.integers(min_value=501, max_value=100_000))
def test_future_change_never_repairs_past_missing_change(known_at_ms: int) -> None:
    future = replace(CHANGES[1], known_at_ms=known_at_ms)
    assert audit_membership_chain(START, END, (*CHANGES[:1], future), as_of_ms=500) == (
        audit_membership_chain(START, END, CHANGES[:1], as_of_ms=500)
    )


def test_unknown_change_availability_is_not_inferred() -> None:
    unknown = replace(CHANGES[1], known_at_ms=None)
    assert not audit_membership_chain(START, END, (*CHANGES[:1], unknown), as_of_ms=500).matched


@pytest.mark.parametrize("anchor_side", ["start", "end"])
def test_etf_anchor_cannot_certify_an_index(anchor_side: str) -> None:
    start = replace(START, kind="ETF_HOLDINGS") if anchor_side == "start" else START
    end = replace(END, kind="ETF_HOLDINGS") if anchor_side == "end" else END
    with pytest.raises(DataIntegrityError, match="ANCHOR_NOT_OFFICIAL_INDEX"):
        audit_membership_chain(start, end, CHANGES, as_of_ms=500)


@pytest.mark.parametrize("known_at_ms", [None, 501])
def test_unavailable_anchor_fails_closed(known_at_ms: int | None) -> None:
    with pytest.raises((DataIntegrityError, CausalityError)):
        audit_membership_chain(START, replace(END, known_at_ms=known_at_ms), CHANGES, as_of_ms=500)


def test_unclosed_anchor_fails_closed() -> None:
    with pytest.raises(CausalityError, match="ANCHOR_SESSION_NOT_CLOSED"):
        audit_membership_chain(START, replace(END, session_close_ms=501), CHANGES, as_of_ms=500)


@pytest.mark.parametrize("index_id", ["other-index", ""])
def test_other_index_is_not_merged(index_id: str) -> None:
    with pytest.raises(DataIntegrityError):
        changed = replace(CHANGES[0], index_id=index_id)
        audit_membership_chain(START, END, (changed,), as_of_ms=500)


def test_conflicting_versions_are_not_arbitrarily_selected() -> None:
    with pytest.raises(DataIntegrityError, match="CONFLICTING_EVENT_VERSION"):
        audit_membership_chain(
            START, END, (*CHANGES, replace(CHANGES[0], source_ref="other-version")), as_of_ms=500
        )


def test_same_session_overlap_requires_explicit_revision_review() -> None:
    conflict = replace(CHANGES[0], event_id="duplicate-other-source")
    with pytest.raises(DataIntegrityError, match="SAME_SESSION_SECURITY_CONFLICT"):
        audit_membership_chain(START, END, (*CHANGES, conflict), as_of_ms=500)


@pytest.mark.parametrize(
    "change,error",
    [
        (replace(CHANGES[0], removals=frozenset({"Z"})), "REMOVAL_NOT_IN_CURRENT_MEMBERSHIP"),
        (replace(CHANGES[0], additions=frozenset({"B"})), "ADDITION_ALREADY_IN_CURRENT_MEMBERSHIP"),
    ],
)
def test_bad_membership_transition_fails_closed(change: MembershipChange, error: str) -> None:
    with pytest.raises(DataIntegrityError, match=error):
        audit_membership_chain(START, END, (change,), as_of_ms=500)


def test_events_outside_anchor_window_are_not_applied() -> None:
    outside = replace(CHANGES[0], event_id="outside", effective_session=START.session)
    assert audit_membership_chain(START, END, (*CHANGES, outside), as_of_ms=500).matched
