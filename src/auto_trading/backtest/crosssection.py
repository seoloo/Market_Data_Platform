"""교차 심볼 롱숏 포트폴리오 구성과 평가 (H3 / 23-h3-preregistration.md).

H2 까지는 "이 심볼이 오를까" 를 물었다. 여기서는 **"어느 심볼이 상대적으로 셀까"** 를
묻는다. 질문이 바뀌면 잡음의 정체가 바뀐다 — 시장 전체 방향이 롱숏에서 상쇄되어
표준편차가 1/4 로 떨어진다 (22-multi-symbol-pivot.md §6 실측).

세 가지 규칙이 이 모듈의 존재 이유다.

1. **유니버스는 시점별로 주어진다.** 오늘 거래되는 심볼로 과거를 재면 망한 종목이
   숏 바구니에서 빠져 성과가 부풀려진다 (생존 편향, 22 §5).
2. **방향을 미리 정하지 않는다.** 상위 k 롱 / 하위 k 숏으로 고정하고 부호는 결과에서
   읽는다. "이 지표는 높을수록 좋다" 고 정해두면 반대 결과를 못 본다.
3. **비용은 자본 대비로 센다.** 자본을 2k 개 포지션에 나눠 담으므로 명목가 합이
   자본과 같고, 각 포지션이 자기 명목가에 내는 비용의 합은 단일 포지션과 같다.
"""

from __future__ import annotations

import math
import statistics as stats
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

__all__ = [
    "CrossSectionResult",
    "PanelObservation",
    "evaluate_panel",
    "long_short_return",
    "quantile_returns",
]


@dataclass(frozen=True, slots=True)
class PanelObservation:
    """한 리밸런스 시점의 단면.

    ``values`` 와 ``forward`` 의 키는 **그 시점 유니버스 소속 심볼**이다.
    상장 전이거나 이미 폐지된 심볼은 애초에 들어오지 않는다.
    """

    at_ms: int
    values: dict[str, float]
    forward_bps: dict[str, float]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("단면이 비어 있다")
        missing = set(self.values) - set(self.forward_bps)
        if missing:
            raise ValueError(f"선행수익이 없는 심볼이 있다: {sorted(missing)[:5]}")

    @property
    def size(self) -> int:
        return len(self.values)

    def ranked(self) -> list[tuple[str, float]]:
        """신호값 오름차순. 동점은 심볼 이름으로 깨서 결정론을 지킨다."""
        return sorted(self.values.items(), key=lambda item: (item[1], item[0]))


def long_short_return(observation: PanelObservation, leg: int) -> float | None:
    """상위 ``leg`` 롱 − 하위 ``leg`` 숏. 자본 대비 수익률(bps).

    2 로 나누는 이유: 자본의 절반씩 양쪽에 담기 때문이다. 나누지 않으면 총
    명목가가 자본의 2배인 포지션을 잡은 셈이 되어 비용과 단위가 맞지 않는다.
    """
    if observation.size < 2 * leg:
        return None
    order = observation.ranked()
    shorts = [observation.forward_bps[s] for s, _ in order[:leg]]
    longs = [observation.forward_bps[s] for s, _ in order[-leg:]]
    return (stats.mean(longs) - stats.mean(shorts)) / 2


def quantile_returns(observation: PanelObservation, buckets: int = 5) -> tuple[float, ...] | None:
    """신호값 낮은 쪽부터 ``buckets`` 분위의 평균 선행수익.

    단조성 확인용이다. 극단 분위만 좋고 중간이 뒤죽박죽이면 신호가 아니라 우연이다.
    """
    if observation.size < buckets * 2:
        return None
    order = observation.ranked()
    size = len(order) // buckets
    return tuple(
        stats.mean([observation.forward_bps[s] for s, _ in order[i * size : (i + 1) * size]])
        for i in range(buckets)
    )


@dataclass(frozen=True, slots=True)
class CrossSectionResult:
    """한 신호 × 한 horizon 의 평가 결과."""

    name: str
    horizon: str
    sample_count: int
    mean_bps: float
    stderr_bps: float
    positive_rate: float
    quantile_means_bps: tuple[float, ...]
    yearly_means_bps: dict[int, float]

    @property
    def tradeable_edge_bps(self) -> float:
        """실제로 취할 수 있는 크기. 부호가 음수면 롱숏을 뒤집어 잡는다."""
        return abs(self.mean_bps)

    @property
    def lower_bound_bps(self) -> float:
        return self.tradeable_edge_bps - 2 * self.stderr_bps

    def passes(self, required_bps: float, min_samples: int) -> bool:
        return self.sample_count >= min_samples and self.lower_bound_bps > required_bps

    @property
    def is_monotone(self) -> bool:
        """분위 평균이 한 방향으로 단조인가. 부호는 어느 쪽이든 좋다."""
        values = self.quantile_means_bps
        if len(values) < 3:
            return False
        rising = all(a <= b for a, b in pairwise(values))
        falling = all(a >= b for a, b in pairwise(values))
        return rising or falling

    @property
    def is_year_consistent(self) -> bool:
        """모든 연도에서 부호가 같은가. 한 해가 전체를 만들었으면 실패다."""
        values = list(self.yearly_means_bps.values())
        if len(values) < 2:
            return False
        return all(v > 0 for v in values) or all(v < 0 for v in values)


def evaluate_panel(
    name: str,
    horizon: str,
    observations: Sequence[PanelObservation],
    *,
    embargo_ms: int,
    leg: int = 3,
    buckets: int = 5,
) -> CrossSectionResult:
    """중첩을 제거하고 롱숏 성과를 집계한다.

    ``embargo_ms`` 는 **horizon 과 같게** 준다. 짧으면 표본이 독립이 아니고,
    길면 이유 없이 표본을 버린다 (15-h1-postmortem.md §4).
    """
    if embargo_ms <= 0:
        raise ValueError(f"embargo_ms 는 1 이상이어야 한다: {embargo_ms}")

    returns: list[tuple[int, float]] = []
    quantiles: list[tuple[float, ...]] = []
    last = -(10**18)
    for observation in sorted(observations, key=lambda o: o.at_ms):
        if observation.at_ms - last < embargo_ms:
            continue
        value = long_short_return(observation, leg)
        if value is None:
            continue
        last = observation.at_ms
        returns.append((observation.at_ms, value))
        row = quantile_returns(observation, buckets)
        if row is not None:
            quantiles.append(row)

    if len(returns) < 2:
        return CrossSectionResult(name, horizon, len(returns), 0.0, math.inf, 0.0, (), {})

    values = [v for _, v in returns]
    mean = stats.mean(values)
    stderr = stats.stdev(values) / math.sqrt(len(values))
    positive = sum(1 for v in values if v > 0) / len(values)
    quantile_means = (
        tuple(stats.mean(row[i] for row in quantiles) for i in range(buckets)) if quantiles else ()
    )
    return CrossSectionResult(
        name=name,
        horizon=horizon,
        sample_count=len(values),
        mean_bps=mean,
        stderr_bps=stderr,
        positive_rate=positive,
        quantile_means_bps=quantile_means,
        yearly_means_bps=_by_year(returns),
    )


def _by_year(returns: Sequence[tuple[int, float]]) -> dict[int, float]:
    grouped: dict[int, list[float]] = {}
    for at_ms, value in returns:
        year = datetime.fromtimestamp(at_ms / 1000, tz=UTC).year
        grouped.setdefault(year, []).append(value)
    return {
        year: stats.mean(values) for year, values in sorted(grouped.items()) if len(values) >= 30
    }
