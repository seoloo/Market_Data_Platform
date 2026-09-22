"""사전 등록 신호 선별 (H2 / 16-h2-proposal.md §4).

H1 은 파이프라인을 전부 만든 뒤에야 edge 가 없다는 걸 알았다 (15-h1-postmortem.md).
그 순서를 뒤집기 위한 도구다 — **전략 코드를 쓰기 전에** 원자료의 조건부 평균만 본다.

세 가지 규칙이 이 모듈의 존재 이유다.

1. **방향을 미리 정하지 않는다.** 상·하위 decile 의 롱 선행수익을 둘 다 재고 부호는
   결과에서 읽는다. "이 지표는 매수 신호" 라고 정해두고 재면 반대 결과를 못 본다.
2. **embargo = horizon.** 2시간 보유를 재면서 24시간 금지구간을 쓰면 표본의 92%를
   이유 없이 버린다 (15 §4). 반대로 embargo 가 horizon 보다 짧으면 표본이 독립이 아니다.
3. **연도별로 따로 낸다.** 한 해가 전체를 만들어낸 경우를 걸러야 한다.
"""

from __future__ import annotations

import math
import statistics as stats
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "ScreenOutcome",
    "ScreenResult",
    "SignalPoint",
    "SignalSeries",
    "screen_signal",
]


@dataclass(frozen=True, slots=True)
class SignalPoint:
    """한 시점의 신호 값과 그 뒤 선행수익.

    ``forward_bps`` 는 **롱 기준** 비용 차감 전 수익률이다. 숏 판단은 부호를 뒤집어
    읽는다 — 신호마다 방향을 미리 정하지 않기 위해서다.
    """

    at_ms: int
    value: float
    forward_bps: dict[str, float]

    def __post_init__(self) -> None:
        if not self.forward_bps:
            raise ValueError("forward_bps 가 비어 있다")
        if math.isnan(self.value) or math.isinf(self.value):
            raise ValueError(f"신호 값이 유한하지 않다: {self.value}")


@dataclass(frozen=True, slots=True)
class ScreenOutcome:
    """한 (신호 × decile × horizon) 조합의 결과."""

    horizon: str
    bucket: str
    sample_count: int
    mean_bps: float
    stderr_bps: float
    positive_rate: float

    @property
    def tradeable_edge_bps(self) -> float:
        """실제로 취할 수 있는 edge.

        롱 수익이 음수면 숏으로 같은 크기를 취한다. 부호를 미리 정하지 않았으므로
        크기만 본다.
        """
        return abs(self.mean_bps)

    @property
    def lower_bound_bps(self) -> float:
        """평균 − 2SE 를 **취할 방향 기준**으로 본 하한."""
        return self.tradeable_edge_bps - 2 * self.stderr_bps

    def passes(self, required_bps: float, min_samples: int) -> bool:
        return self.sample_count >= min_samples and self.lower_bound_bps > required_bps


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """한 신호의 전체 선별 결과."""

    name: str
    total_points: int
    outcomes: tuple[ScreenOutcome, ...]
    quintile_means_bps: dict[str, tuple[float, ...]]
    """horizon 별 5분위 평균. 극단 분위가 중간보다 나은지(단조성) 보기 위한 값."""
    yearly_means_bps: dict[str, dict[int, float]]
    """horizon 별 {연도: 상·하위 decile 스프레드}. 한 해가 전체를 만들었는지 본다."""

    def passing(self, required_bps: float, min_samples: int) -> tuple[ScreenOutcome, ...]:
        return tuple(o for o in self.outcomes if o.passes(required_bps, min_samples))

    def is_year_consistent(self, horizon: str) -> bool:
        """해당 horizon 에서 모든 연도의 스프레드 부호가 같은가."""
        values = list(self.yearly_means_bps.get(horizon, {}).values())
        if len(values) < 2:
            return False
        return all(v > 0 for v in values) or all(v < 0 for v in values)


@dataclass(frozen=True, slots=True)
class SignalSeries:
    """선별 대상 신호 하나."""

    name: str
    points: tuple[SignalPoint, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("신호 이름이 비어 있다")
        previous = -1
        for point in self.points:
            if point.at_ms <= previous:
                raise ValueError(f"신호 시점이 오름차순이 아니다: {point.at_ms}")
            previous = point.at_ms


def screen_signal(
    series: SignalSeries,
    *,
    horizons_ms: dict[str, int],
    decile: float = 0.10,
) -> ScreenResult:
    """상·하위 ``decile`` 구간의 선행수익을 horizon 별로 낸다.

    분위 경계는 **구간 전체**에서 구한다. 선별은 "이 신호에 정보가 있는가" 를 묻는
    사후 분석이지 실거래 규칙이 아니므로, 여기서 causal 경계를 쓸 필요는 없다.
    다만 통과하면 전략에서는 반드시 causal 경계로 다시 만들어야 한다.
    """
    if not 0.0 < decile < 0.5:
        raise ValueError(f"decile 은 (0, 0.5) 이어야 한다: {decile}")
    points = series.points
    if not points:
        return ScreenResult(series.name, 0, (), {}, {})

    values = sorted(p.value for p in points)
    low_edge = values[max(0, int(len(values) * decile) - 1)]
    high_edge = values[min(len(values) - 1, int(len(values) * (1 - decile)))]

    outcomes: list[ScreenOutcome] = []
    quintiles: dict[str, tuple[float, ...]] = {}
    yearly: dict[str, dict[int, float]] = {}
    for horizon, embargo_ms in horizons_ms.items():
        buckets = {
            "low": _thin([p for p in points if p.value <= low_edge], embargo_ms),
            "high": _thin([p for p in points if p.value >= high_edge], embargo_ms),
        }
        for bucket, picked in buckets.items():
            outcomes.append(_summarise(horizon, bucket, picked))
        quintiles[horizon] = _quintile_means(points, horizon, embargo_ms)
        yearly[horizon] = _yearly_spread(buckets["high"], buckets["low"], horizon)
    return ScreenResult(
        name=series.name,
        total_points=len(points),
        outcomes=tuple(outcomes),
        quintile_means_bps=quintiles,
        yearly_means_bps=yearly,
    )


def _thin(points: Sequence[SignalPoint], embargo_ms: int) -> list[SignalPoint]:
    """중첩 제거. 시간순 첫 관측을 택하고 ``embargo_ms`` 안의 후속은 버린다.

    수익률을 보고 고르지 않는다 — 그러면 선별 자체가 과최적화가 된다.
    """
    kept: list[SignalPoint] = []
    last = -(10**18)
    for point in points:
        if point.at_ms - last >= embargo_ms:
            kept.append(point)
            last = point.at_ms
    return kept


def _summarise(horizon: str, bucket: str, picked: Sequence[SignalPoint]) -> ScreenOutcome:
    values = [p.forward_bps[horizon] for p in picked]
    if not values:
        return ScreenOutcome(horizon, bucket, 0, 0.0, math.inf, 0.0)
    if len(values) < 2:
        # 표본 1개는 평균은 있어도 오차를 모른다. 0 을 돌려주면 "효과 없음" 처럼
        # 보이므로 평균은 그대로 두고 오차를 무한대로 둬 절대 통과하지 못하게 한다.
        return ScreenOutcome(horizon, bucket, 1, values[0], math.inf, float(values[0] > 0))
    mean = stats.mean(values)
    stderr = stats.stdev(values) / math.sqrt(len(values))
    positive = sum(1 for v in values if v > 0) / len(values)
    return ScreenOutcome(horizon, bucket, len(values), mean, stderr, positive)


def _quintile_means(
    points: Sequence[SignalPoint], horizon: str, embargo_ms: int
) -> tuple[float, ...]:
    ordered = sorted(points, key=lambda p: p.value)
    size = len(ordered) // 5
    if size < 2:
        return ()
    means: list[float] = []
    for index in range(5):
        chunk = ordered[index * size : (index + 1) * size]
        thinned = _thin(sorted(chunk, key=lambda p: p.at_ms), embargo_ms)
        values = [p.forward_bps[horizon] for p in thinned]
        means.append(stats.mean(values) if values else 0.0)
    return tuple(means)


def _yearly_spread(
    high: Sequence[SignalPoint], low: Sequence[SignalPoint], horizon: str
) -> dict[int, float]:
    """연도별 (상위 decile 평균 − 하위 decile 평균)."""
    by_year: dict[int, dict[str, list[float]]] = {}
    for bucket, picked in (("high", high), ("low", low)):
        for point in picked:
            year = datetime.fromtimestamp(point.at_ms / 1000, tz=UTC).year
            by_year.setdefault(year, {"high": [], "low": []})[bucket].append(
                point.forward_bps[horizon]
            )
    spreads: dict[int, float] = {}
    for year, sides in sorted(by_year.items()):
        if len(sides["high"]) < 10 or len(sides["low"]) < 10:
            continue
        spreads[year] = stats.mean(sides["high"]) - stats.mean(sides["low"])
    return spreads
