"""오프라인 BTC 옵션 보호 롱 수량 필요조건 진단.

거래소 상태의 진실성/동시성, 계좌 위험, 청산 가능성을 검증하지 않는다.
운영 주문 경로에서 이 모듈을 안전 승인 게이트로 사용하면 안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

OptionKind = Literal["CALL", "PUT"]


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    settlement_asset: str
    expiry_ms: int
    kind: OptionKind
    strike: Decimal
    unit: Decimal


@dataclass(frozen=True)
class LongFirstSnapshot:
    long_contract: OptionContract
    short_contract: OptionContract
    # 아래 수량은 공통 계약 단위의 서로 겹치지 않는 예약/포지션이다.
    confirmed_long_qty: Decimal | None
    allocated_elsewhere_qty: Decimal | None
    pending_long_sell_qty: Decimal | None
    confirmed_short_qty: Decimal | None
    working_short_qty: Decimal | None
    unresolved_short_qty: Decimal | None
    proposed_short_qty: Decimal | None
    remote_state_complete: bool


@dataclass(frozen=True)
class ProtectionAudit:
    necessary_condition_met: bool
    available_long_qty: Decimal | None
    maximum_short_exposure_qty: Decimal | None
    reasons: tuple[str, ...]


def audit_long_first(snapshot: LongFirstSnapshot) -> ProtectionAudit:
    """수량·계약 일치의 필요조건만 검사한다. 실거래 승인값이 아니다."""
    reasons: list[str] = []
    long_leg = snapshot.long_contract
    short_leg = snapshot.short_contract
    for leg in (long_leg, short_leg):
        if (
            not leg.underlying
            or not leg.settlement_asset
            or leg.expiry_ms <= 0
            or leg.kind not in ("CALL", "PUT")
            or not leg.strike.is_finite()
            or not leg.unit.is_finite()
            or leg.strike <= 0
            or leg.unit <= 0
        ):
            reasons.append("INVALID_CONTRACT")
            break
    if (
        long_leg.underlying != short_leg.underlying
        or long_leg.settlement_asset != short_leg.settlement_asset
        or long_leg.expiry_ms != short_leg.expiry_ms
        or long_leg.kind != short_leg.kind
        or long_leg.unit != short_leg.unit
    ):
        reasons.append("CONTRACT_MISMATCH")
    if long_leg.strike.is_finite() and short_leg.strike.is_finite():
        if long_leg.kind == "CALL" and long_leg.strike <= short_leg.strike:
            reasons.append("INVALID_WING")
        if long_leg.kind == "PUT" and long_leg.strike >= short_leg.strike:
            reasons.append("INVALID_WING")
    if not snapshot.remote_state_complete:
        reasons.append("REMOTE_STATE_INCOMPLETE")

    quantities = (
        snapshot.confirmed_long_qty,
        snapshot.allocated_elsewhere_qty,
        snapshot.pending_long_sell_qty,
        snapshot.confirmed_short_qty,
        snapshot.working_short_qty,
        snapshot.unresolved_short_qty,
        snapshot.proposed_short_qty,
    )
    if any(value is None for value in quantities):
        reasons.append("REMOTE_QUANTITY_UNKNOWN")
        return ProtectionAudit(False, None, None, tuple(reasons))
    if any(not value.is_finite() or value < 0 for value in quantities if value is not None):
        reasons.append("INVALID_QUANTITY")
        return ProtectionAudit(False, None, None, tuple(reasons))

    # 위의 None 검사를 통과한 값만 사용한다.
    long_qty = snapshot.confirmed_long_qty
    elsewhere_qty = snapshot.allocated_elsewhere_qty
    pending_sell_qty = snapshot.pending_long_sell_qty
    short_qty = snapshot.confirmed_short_qty
    working_qty = snapshot.working_short_qty
    unresolved_qty = snapshot.unresolved_short_qty
    proposed_qty = snapshot.proposed_short_qty
    assert long_qty is not None
    assert elsewhere_qty is not None
    assert pending_sell_qty is not None
    assert short_qty is not None
    assert working_qty is not None
    assert unresolved_qty is not None
    assert proposed_qty is not None
    available_long = long_qty - elsewhere_qty - pending_sell_qty
    maximum_short = short_qty + working_qty + unresolved_qty + proposed_qty
    if available_long < 0 or maximum_short > available_long:
        reasons.append("INSUFFICIENT_CONFIRMED_LONG")
    return ProtectionAudit(not reasons, available_long, maximum_short, tuple(reasons))
