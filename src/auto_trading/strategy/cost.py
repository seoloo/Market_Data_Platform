"""진입 전 비용·엣지 게이트 계산."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import CostConfig
from auto_trading.core.enums import PositionSide
from auto_trading.core.money import ZERO

__all__ = ["CostEstimate", "EdgeEstimate", "estimate_cost", "estimate_edge"]

_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class CostEstimate:
    total_bps: Decimal
    fee_bps: Decimal
    slippage_bps: Decimal
    funding_bps: Decimal


@dataclass(frozen=True, slots=True)
class EdgeEstimate:
    edge_bps: Decimal
    sample_count: int
    available: bool


def estimate_cost(
    cfg: CostConfig,
    *,
    side: PositionSide,
    funding_rate: Decimal | None,
) -> CostEstimate:
    """시장가 왕복 비용을 bps로 계산한다. 유리한 funding은 0으로 보수 처리한다."""
    fee = cfg.taker_fee_rate * Decimal(2) * _BPS
    slippage = cfg.slippage_entry_rate * Decimal(2) * _BPS
    funding = ZERO
    if cfg.include_funding:
        rate = cfg.expected_funding_rate if funding_rate is None else funding_rate
        signed_cost = rate if side is PositionSide.LONG else -rate
        funding = max(ZERO, signed_cost * _BPS)
    return CostEstimate(
        total_bps=fee + slippage + funding,
        fee_bps=fee,
        slippage_bps=slippage,
        funding_bps=funding,
    )


def estimate_edge(edge_bps: Decimal | None, sample_count: int | None) -> EdgeEstimate:
    """TRAIN에서 고정한 edge만 사용하며 표본 30개 미만은 fail-closed 처리한다."""
    count = 0 if sample_count is None else sample_count
    if edge_bps is None or count < 30:
        return EdgeEstimate(edge_bps=ZERO, sample_count=count, available=False)
    return EdgeEstimate(edge_bps=edge_bps, sample_count=count, available=True)
