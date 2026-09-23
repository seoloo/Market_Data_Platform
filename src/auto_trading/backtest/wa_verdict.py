"""WA/v1 TRAIN 사전등록 판정 (docs/research/08-wa-preregistration.md §5).

판정 순서가 규칙의 일부다. 거래 수 관문을 먼저 보고, 미달이면 손익 통계를 계산하지 않는다.
방향 `D`는 관문 통과 뒤 주 arm **롱 재생**의 비용 전 평균 부호로 정하고, 이후 모든 기준은
`D` 방향으로 **따로 재생한** 거래(숏은 숏 주문·손절·비용)로 계산한다.

| 기준 | 정의 |
|---|---|
| 관문 | min(주 arm 롱 거래 수, 주 arm 숏 거래 수) ≥ 176 |
| T1 | `D` 순손익 bps 평균 − 2SE > 0 |
| T2 | `D` 비용 전 bps 평균 ≥ 60 |
| T3 | (주 arm `D` − 대조군 `D`) 순손익 평균 차이 − 2SE > 0 (Welch) |
| T4 | 2023·2024 각 연도 `D` 순손익 평균 > 0 |
| 안전 | 우위 측정 재생(주 arm·대조군 `D`) 강제청산 0건 |
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from auto_trading.backtest.a1_verdict import TradeRecord

__all__ = ["MIN_TRADES", "T2_PRE_COST_BPS", "TRAIN_YEARS", "WACriterion", "WAVerdict", "judge_wa"]

MIN_TRADES = 176
"""08 §5: required_samples(SD 206.15, 효과 60, 비용 15.79)."""
T2_PRE_COST_BPS = 60.0
TRAIN_YEARS = (2023, 2024)
_Z = 2.0


@dataclass(frozen=True, slots=True)
class WACriterion:
    name: str
    passed: bool
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WAVerdict:
    status: str
    """``DATA_INSUFFICIENT`` | ``PASS`` | ``REJECT``."""
    counts: dict[str, int]
    direction: str | None = None
    criteria: tuple[WACriterion, ...] = field(default=())
    account_replay: str = "NOT_RUN"
    """전체 규칙 계좌 재생 상태. G6 달력 없이는 실행하지 않으며 통과로 표시하지 않는다."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "direction": self.direction,
            "counts": self.counts,
            "min_trades": MIN_TRADES,
            "account_replay": self.account_replay,
            "criteria": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.criteria
            ],
        }


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _stat(values: Sequence[float]) -> tuple[int, float, float]:
    if len(values) < 2:
        return len(values), math.nan, math.inf
    return len(values), statistics.fmean(values), statistics.stdev(values) / math.sqrt(len(values))


def _stat_dict(values: Sequence[float]) -> dict[str, float | int | None]:
    n, mean, se = _stat(values)
    return {"n": n, "mean": _finite(mean), "se": _finite(se), "lower_2se": _finite(mean - _Z * se)}


def judge_wa(
    main_long: Sequence[TradeRecord],
    main_short: Sequence[TradeRecord],
    control_long: Sequence[TradeRecord],
    control_short: Sequence[TradeRecord],
) -> WAVerdict:
    counts = {
        "main_long": len(main_long),
        "main_short": len(main_short),
        "control_long": len(control_long),
        "control_short": len(control_short),
    }
    if min(len(main_long), len(main_short)) < MIN_TRADES:
        return WAVerdict("DATA_INSUFFICIENT", counts)

    direction_mean = statistics.fmean(r.pre_cost_bps for r in main_long)
    if direction_mean == 0:
        return WAVerdict(
            "REJECT",
            counts,
            criteria=(WACriterion("DIRECTION", False, {"main_long_pre_cost_mean_bps": 0.0}),),
        )
    long_wins = direction_mean > 0
    direction = "long" if long_wins else "short"
    main = main_long if long_wins else main_short
    control = control_long if long_wins else control_short

    n, net_mean, net_se = _stat([r.net_bps for r in main])
    _, pre_mean, _ = _stat([r.pre_cost_bps for r in main])
    _, control_mean, control_se = _stat([r.net_bps for r in control])
    difference = net_mean - control_mean
    difference_se = math.sqrt(net_se**2 + control_se**2)
    yearly = {year: [r.net_bps for r in main if r.year == year] for year in TRAIN_YEARS}
    liquidations = sum(r.liquidated for r in (*main, *control))

    criteria = (
        WACriterion(
            "DIRECTION", True, {"main_long_pre_cost_mean_bps": direction_mean, "chosen": direction}
        ),
        WACriterion(
            "T1",
            net_mean - _Z * net_se > 0,
            {"main_net_bps": _stat_dict([r.net_bps for r in main])},
        ),
        WACriterion(
            "T2",
            pre_mean >= T2_PRE_COST_BPS,
            {
                "main_pre_cost_bps": _stat_dict([r.pre_cost_bps for r in main]),
                "threshold_bps": T2_PRE_COST_BPS,
            },
        ),
        WACriterion(
            "T3",
            difference - _Z * difference_se > 0,
            {
                "control_net_bps": _stat_dict([r.net_bps for r in control]),
                "difference_bps": _finite(difference),
                "difference_se": _finite(difference_se),
                "lower_2se": _finite(difference - _Z * difference_se),
            },
        ),
        WACriterion(
            "T4",
            all(len(values) >= 2 and statistics.fmean(values) > 0 for values in yearly.values()),
            {str(year): _stat_dict(values) for year, values in yearly.items()},
        ),
        WACriterion("SAFETY", liquidations == 0, {"liquidations": liquidations, "n": n}),
    )
    status = "PASS" if all(c.passed for c in criteria) else "REJECT"
    return WAVerdict(status, counts, direction, criteria)
