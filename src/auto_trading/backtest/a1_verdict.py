"""A1/v1 TRAIN 사전등록 판정 (docs/research/02-a1-preregistration.md §7·§11).

판정 순서가 규칙의 일부다. **주 arm 실제 거래 수를 먼저** 확인하고, 146건 미만이면
손익 통계를 계산하지 않고 ``DATA_INSUFFICIENT``를 돌려준다. 결과를 본 뒤 기준을
바꾸지 않도록 모든 수치는 여기 상수로 고정한다.

| 기준 | 정의 (§11 개정 반영) |
|---|---|
| T1 | 주 arm 순손익 bps 평균 − 2SE > 0 |
| T2 | 주 arm 비용 전 bps 평균 ≥ 42.44 |
| T3 | (주 arm − 대조군) 순손익 평균 차이 − 2SE > 0 (Welch SE) |
| T4 | 2021·2022 각 연도 주 arm 순손익 평균 > 0 |
| T5 | LONG·SHORT 각각 주 arm 순손익 평균 > 0 (원문 "각각 부호 동일"은 T1과 함께면 양수와 같다) |
| 안전 | 강제청산 0건 (BACKTEST_AND_TESTING Phase 4 안전성) |
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.core.enums import CloseReason, PositionSide

__all__ = [
    "MIN_MAIN_TRADES",
    "T2_PRE_COST_BPS",
    "TRAIN_YEARS",
    "A1Verdict",
    "CriterionResult",
    "SampleStat",
    "TradeRecord",
    "judge",
    "records_from_trades",
]

MIN_MAIN_TRADES = 146
"""§11: 검정력 80%·SD 95.64·효과 42.44·비용 19.91의 필요 표본. 미만이면 판정 불가."""
T2_PRE_COST_BPS = 42.44
"""§11: OOS 100거래로 확인 가능한 최소 비용 전 효과."""
TRAIN_YEARS = (2021, 2022)
"""§11: T4 연도."""
_Z = 2.0


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """판정에 필요한 거래 한 건의 값만 남긴 기록."""

    net_bps: float
    pre_cost_bps: float
    side: PositionSide
    year: int
    liquidated: bool


@dataclass(frozen=True, slots=True)
class SampleStat:
    n: int
    mean: float
    se: float

    @property
    def lower_bound(self) -> float:
        return self.mean - _Z * self.se

    def to_dict(self) -> dict[str, float | int | None]:
        """JSON 저장용. 표본이 2개 미만이면 평균·SE가 정의되지 않으므로 ``None``이다."""
        return {
            "n": self.n,
            "mean": _finite(self.mean),
            "se": _finite(self.se),
            "lower_2se": _finite(self.lower_bound),
        }


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class CriterionResult:
    name: str
    passed: bool
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class A1Verdict:
    status: str
    """``DATA_INSUFFICIENT`` | ``PASS`` | ``REJECT``."""
    main_trades: int
    control_trades: int
    criteria: tuple[CriterionResult, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "main_trades": self.main_trades,
            "control_trades": self.control_trades,
            "min_main_trades": MIN_MAIN_TRADES,
            "criteria": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.criteria
            ],
        }


def records_from_trades(trades: Iterable[BacktestTrade]) -> tuple[TradeRecord, ...]:
    """엔진 거래를 판정 기록으로 바꾼다. 기준가가 없는 거래는 판정할 수 없어 거부한다."""
    records: list[TradeRecord] = []
    for trade in trades:
        net = trade.net_bps
        liquidated = trade.close_reason is CloseReason.LIQUIDATION
        pre_cost = trade.pre_cost_bps
        if net is None or (pre_cost is None and not liquidated):
            raise ValueError(f"기준가가 없는 거래는 판정할 수 없다: entry_ms={trade.entry_ms}")
        records.append(
            TradeRecord(
                net_bps=float(net),
                # 강제청산은 기준가가 없다. 안전 기준에서 실격이므로 비용 전 값은 순손익으로 둔다.
                pre_cost_bps=float(net if pre_cost is None else pre_cost),
                side=trade.side,
                year=datetime.fromtimestamp(trade.entry_ms / 1000, tz=UTC).year,
                liquidated=liquidated,
            )
        )
    return tuple(records)


def _stat(values: Sequence[float]) -> SampleStat:
    if len(values) < 2:
        return SampleStat(n=len(values), mean=math.nan, se=math.inf)
    return SampleStat(
        n=len(values),
        mean=statistics.fmean(values),
        se=statistics.stdev(values) / math.sqrt(len(values)),
    )


def judge(main: Sequence[TradeRecord], control: Sequence[TradeRecord]) -> A1Verdict:
    """사전등록 순서대로 판정한다. 거래 수 미달이면 통계를 계산하지 않는다."""
    if len(main) < MIN_MAIN_TRADES:
        return A1Verdict("DATA_INSUFFICIENT", len(main), len(control))

    net = _stat([r.net_bps for r in main])
    pre_cost = _stat([r.pre_cost_bps for r in main])
    control_net = _stat([r.net_bps for r in control])
    difference = net.mean - control_net.mean
    difference_se = math.sqrt(net.se**2 + control_net.se**2)
    by_year = {year: _stat([r.net_bps for r in main if r.year == year]) for year in TRAIN_YEARS}
    by_side = {
        side.value: _stat([r.net_bps for r in main if r.side is side])
        for side in (PositionSide.LONG, PositionSide.SHORT)
    }
    liquidations = sum(r.liquidated for r in (*main, *control))

    criteria = (
        CriterionResult("T1", net.lower_bound > 0, {"main_net_bps": net.to_dict()}),
        CriterionResult(
            "T2",
            pre_cost.mean >= T2_PRE_COST_BPS,
            {"main_pre_cost_bps": pre_cost.to_dict(), "threshold_bps": T2_PRE_COST_BPS},
        ),
        CriterionResult(
            "T3",
            difference - _Z * difference_se > 0,
            {
                "control_net_bps": control_net.to_dict(),
                "difference_bps": _finite(difference),
                "difference_se": _finite(difference_se),
                "lower_2se": _finite(difference - _Z * difference_se),
            },
        ),
        CriterionResult(
            "T4",
            all(stat.n > 0 and stat.mean > 0 for stat in by_year.values()),
            {str(year): stat.to_dict() for year, stat in by_year.items()},
        ),
        CriterionResult(
            "T5",
            all(stat.n > 0 and stat.mean > 0 for stat in by_side.values()),
            {side: stat.to_dict() for side, stat in by_side.items()},
        ),
        CriterionResult("SAFETY", liquidations == 0, {"liquidations": liquidations}),
    )
    status = "PASS" if all(c.passed for c in criteria) else "REJECT"
    return A1Verdict(status, len(main), len(control), criteria)
