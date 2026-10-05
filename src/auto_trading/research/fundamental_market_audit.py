"""종료 현금 권리·역사 명부 계약 감사. 연구 전용, I/O·주문·NAV/수익 계산 없음.

시각·증권 ID·출처의 실제 인증은 외부 감사가 제공한다. 결과 일치가 자료 완전성이나
실거래 승인은 아니다. 금액은 일반 적격 보통주의 검토된 현금 합병만 취급한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from auto_trading.core.errors import CausalityError, DataIntegrityError


def _ms(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataIntegrityError("INVALID_EPOCH_MS")


def _amount(value: Decimal, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise DataIntegrityError("INVALID_DECIMAL_AMOUNT")
    if positive and value == 0:
        raise DataIntegrityError("NONPOSITIVE_AMOUNT")


def _text(*values: str) -> None:
    if any(not value.strip() for value in values):
        raise DataIntegrityError("EMPTY_EVIDENCE_OR_SECURITY_ID")


@dataclass(frozen=True)
class CashClaim:
    security_id: str
    eligible_shares: Decimal
    gross_usd_per_share: Decimal
    effective_at_ms: int
    known_at_ms: int
    source_ref: str

    def __post_init__(self) -> None:
        _text(self.security_id, self.source_ref)
        _amount(self.eligible_shares, positive=True)
        _amount(self.gross_usd_per_share, positive=True)
        _ms(self.effective_at_ms)
        _ms(self.known_at_ms)
        if self.known_at_ms < self.effective_at_ms:
            raise DataIntegrityError("CLAIM_KNOWN_BEFORE_EFFECTIVE")


@dataclass(frozen=True)
class CashReceipt:
    security_id: str
    net_usd: Decimal
    credited_at_ms: int
    cash_available_at_ms: int
    known_at_ms: int
    source_ref: str

    def __post_init__(self) -> None:
        _text(self.security_id, self.source_ref)
        _amount(self.net_usd)
        for value in (self.credited_at_ms, self.cash_available_at_ms, self.known_at_ms):
            _ms(value)
        if self.cash_available_at_ms < self.credited_at_ms:
            raise DataIntegrityError("CASH_AVAILABLE_BEFORE_CREDIT")
        if self.known_at_ms < self.credited_at_ms:
            raise DataIntegrityError("RECEIPT_KNOWN_BEFORE_CREDIT")


@dataclass(frozen=True)
class ClaimAudit:
    gross_legal_right_usd: Decimal
    confirmed_spendable_usd: Decimal | None
    status: Literal["SETTLEMENT_UNVERIFIED", "CASH_NOT_YET_AVAILABLE", "RECEIPT_CONFIRMED"]


def audit_cash_claim(
    claim: CashClaim, receipt: CashReceipt | None, *, as_of_ms: int
) -> ClaimAudit | None:
    """법적 권리와 확인된 재투자 가능 현금 분리. 권리 금액은 NAV 평가액이 아니다."""
    _ms(as_of_ms)
    if claim.known_at_ms > as_of_ms or claim.effective_at_ms > as_of_ms:
        return None
    gross = claim.eligible_shares * claim.gross_usd_per_share
    if receipt is None or receipt.known_at_ms > as_of_ms:
        return ClaimAudit(gross, None, "SETTLEMENT_UNVERIFIED")
    if receipt.security_id != claim.security_id:
        raise DataIntegrityError("RECEIPT_SECURITY_MISMATCH")
    if receipt.credited_at_ms < claim.effective_at_ms:
        raise CausalityError("CREDIT_BEFORE_MERGER_EFFECTIVE")
    if receipt.net_usd > gross:
        raise DataIntegrityError("RECEIPT_EXCEEDS_REVIEWED_CASH_RIGHT")
    if receipt.cash_available_at_ms > as_of_ms:
        return ClaimAudit(gross, Decimal(0), "CASH_NOT_YET_AVAILABLE")
    return ClaimAudit(gross, receipt.net_usd, "RECEIPT_CONFIRMED")


@dataclass(frozen=True)
class MembershipAnchor:
    index_id: str
    # 같은 세션 장마감의 증권 ID. 이름/티커만으로 영구 ID를 인증하지 않는다.
    session: date
    session_close_ms: int
    security_ids: frozenset[str]
    kind: Literal["OFFICIAL_INDEX", "ETF_HOLDINGS"]
    known_at_ms: int | None
    source_ref: str

    def __post_init__(self) -> None:
        _text(self.index_id, self.source_ref, *self.security_ids)
        if type(self.session) is not date:
            raise DataIntegrityError("INVALID_SESSION_DATE")
        _ms(self.session_close_ms)
        if not isinstance(self.security_ids, frozenset) or not self.security_ids:
            raise DataIntegrityError("EMPTY_OR_MUTABLE_ANCHOR")
        if self.kind not in ("OFFICIAL_INDEX", "ETF_HOLDINGS"):
            raise DataIntegrityError("UNKNOWN_ANCHOR_KIND")
        if self.known_at_ms is not None:
            _ms(self.known_at_ms)


@dataclass(frozen=True)
class MembershipChange:
    event_id: str
    index_id: str
    effective_session: date
    additions: frozenset[str]
    removals: frozenset[str]
    known_at_ms: int | None
    source_ref: str

    def __post_init__(self) -> None:
        _text(self.event_id, self.index_id, self.source_ref, *self.additions, *self.removals)
        if type(self.effective_session) is not date:
            raise DataIntegrityError("INVALID_SESSION_DATE")
        if not isinstance(self.additions, frozenset) or not isinstance(self.removals, frozenset):
            raise DataIntegrityError("MUTABLE_MEMBERSHIP_CHANGE")
        if not self.additions and not self.removals:
            raise DataIntegrityError("EMPTY_MEMBERSHIP_CHANGE")
        if self.additions & self.removals:
            raise DataIntegrityError("CONFLICTING_MEMBERSHIP_CHANGE")
        if self.known_at_ms is not None:
            _ms(self.known_at_ms)


@dataclass(frozen=True)
class MembershipAudit:
    expected_security_ids: frozenset[str]
    missing_security_ids: frozenset[str]
    unexpected_security_ids: frozenset[str]
    applied_event_ids: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return not self.missing_security_ids and not self.unexpected_security_ids


def audit_membership_chain(
    start: MembershipAnchor,
    end: MembershipAnchor,
    changes: Sequence[MembershipChange],
    *,
    as_of_ms: int,
) -> MembershipAudit:
    """두 공식 장마감 명부 사이 사건 대조. 일치는 전 구간 완전성 인증이 아니다.

    동일 날짜의 동일 증권을 여러 사건이 바꾸면 명시적 정정 정규화 전 거부한다.
        미확인/미래 가용 사건을 추정 적용하지 않는다. 알려진 후기 자료로 상태 복원은
    가능하나 그 결과를 과거 판단 시점의 입력으로 인증하지 않는다.
    """
    _ms(as_of_ms)
    if (
        start.index_id != end.index_id
        or start.session >= end.session
        or start.session_close_ms >= end.session_close_ms
    ):
        raise DataIntegrityError("ANCHOR_SCOPE_MISMATCH")
    for anchor in (start, end):
        if anchor.kind != "OFFICIAL_INDEX":
            raise DataIntegrityError("ANCHOR_NOT_OFFICIAL_INDEX")
        if anchor.known_at_ms is None:
            raise DataIntegrityError("ANCHOR_AVAILABILITY_UNVERIFIED")
        if anchor.known_at_ms > as_of_ms:
            raise CausalityError("ANCHOR_NOT_AVAILABLE_AS_OF")
        if anchor.session_close_ms > as_of_ms:
            raise CausalityError("ANCHOR_SESSION_NOT_CLOSED")
    visible: dict[str, MembershipChange] = {}
    for change in changes:
        if change.known_at_ms is None or change.known_at_ms > as_of_ms:
            continue
        if not start.session < change.effective_session <= end.session:
            continue
        if change.index_id != start.index_id:
            raise DataIntegrityError("CHANGE_INDEX_MISMATCH")
        if change.event_id in visible and visible[change.event_id] != change:
            raise DataIntegrityError("CONFLICTING_EVENT_VERSION")
        visible[change.event_id] = change
    members = set(start.security_ids)
    touched: set[tuple[date, str]] = set()
    applied: list[str] = []
    for change in sorted(
        visible.values(), key=lambda item: (item.effective_session, item.event_id)
    ):
        keys = {(change.effective_session, item) for item in change.additions | change.removals}
        if touched & keys:
            raise DataIntegrityError("SAME_SESSION_SECURITY_CONFLICT")
        touched.update(keys)
        if not change.removals <= members:
            raise DataIntegrityError("REMOVAL_NOT_IN_CURRENT_MEMBERSHIP")
        if change.additions & members:
            raise DataIntegrityError("ADDITION_ALREADY_IN_CURRENT_MEMBERSHIP")
        members.difference_update(change.removals)
        members.update(change.additions)
        applied.append(change.event_id)
    expected = frozenset(members)
    return MembershipAudit(
        expected,
        end.security_ids - expected,
        expected - end.security_ids,
        tuple(applied),
    )
