"""R1 중기 상대 추세의 사전등록 신호와 평가.

정의는 ``docs/phase0/26-r1-preregistration.md``에 고정돼 있다. 이 모듈은 I/O 없이
일봉 시계열과 단면 관측만 받아 신호·비용 후 결과를 계산한다.
"""

from __future__ import annotations

import math
import statistics as stats
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

DAY_MS = 86_400_000
LOOKBACK_DAYS = 21


def momentum_signal(
    closes: Mapping[int, float], entry_ms: int, *, lookback_days: int = LOOKBACK_DAYS
) -> float | None:
    """진입 직전까지 마감된 일봉만으로 ``lookback_days`` 수익률을 계산한다."""
    if lookback_days <= 0:
        raise ValueError("lookback_days는 1 이상이어야 한다")
    recent = closes.get(entry_ms - DAY_MS)
    old = closes.get(entry_ms - (lookback_days + 1) * DAY_MS)
    if recent is None or old is None or old <= 0:
        return None
    return (recent - old) / old


def cumulative_funding_bps(rates: Mapping[int, float], entry_ms: int, exit_ms: int) -> float | None:
    """보유 중 정산된 funding rate의 합(bps).

    진입·청산과 정확히 같은 시각의 정산은 포지션 보유 여부가 불명확하므로 제외한다.
    데이터가 하나도 없으면 0으로 만들지 않고 ``None``을 반환한다.
    """
    if exit_ms <= entry_ms:
        raise ValueError("exit_ms는 entry_ms보다 커야 한다")
    values = [rate for at_ms, rate in rates.items() if entry_ms < at_ms < exit_ms]
    return sum(values) * 10_000 if values else None


@dataclass(frozen=True, slots=True)
class R1Observation:
    at_ms: int
    signal: dict[str, float]
    price_forward_bps: dict[str, float]
    funding_rate_bps: dict[str, float]

    def __post_init__(self) -> None:
        missing_price = set(self.signal) - set(self.price_forward_bps)
        missing_funding = set(self.signal) - set(self.funding_rate_bps)
        if not self.signal:
            raise ValueError("단면이 비어 있다")
        if missing_price or missing_funding:
            raise ValueError("신호 심볼의 가격 또는 funding이 없다")

    def ranked(self) -> list[tuple[str, float]]:
        return sorted(self.signal.items(), key=lambda item: (item[1], item[0]))


@dataclass(frozen=True, slots=True)
class R1Sample:
    gross_bps: float
    funding_bps: float
    net_bps: float
    quantiles_bps: tuple[float, ...]


def sample_return(
    observation: R1Observation, *, leg: int = 3, buckets: int = 5, cost_bps: float = 22.0
) -> R1Sample | None:
    """상위 LONG/하위 SHORT의 가격·funding·비용 후 자본 수익률."""
    order = observation.ranked()
    if len(order) < max(2 * leg, 2 * buckets):
        return None
    shorts = [symbol for symbol, _ in order[:leg]]
    longs = [symbol for symbol, _ in order[-leg:]]
    gross = (
        stats.mean(observation.price_forward_bps[symbol] for symbol in longs)
        - stats.mean(observation.price_forward_bps[symbol] for symbol in shorts)
    ) / 2
    funding = (
        -stats.mean(observation.funding_rate_bps[symbol] for symbol in longs)
        + stats.mean(observation.funding_rate_bps[symbol] for symbol in shorts)
    ) / 2
    bucket_size = len(order) // buckets
    quantiles = tuple(
        stats.mean(
            observation.price_forward_bps[symbol]
            for symbol, _ in order[index * bucket_size : (index + 1) * bucket_size]
        )
        for index in range(buckets)
    )
    return R1Sample(gross, funding, gross + funding - cost_bps, quantiles)


@dataclass(frozen=True, slots=True)
class R1Result:
    horizon: str
    sample_count: int
    gross_mean_bps: float
    funding_mean_bps: float
    net_mean_bps: float
    net_stderr_bps: float
    positive_rate: float
    quantile_means_bps: tuple[float, ...]
    yearly_net_means_bps: dict[int, float]

    @property
    def lower_bound_bps(self) -> float:
        return self.net_mean_bps - 2 * self.net_stderr_bps

    @property
    def is_monotone_rising(self) -> bool:
        return len(self.quantile_means_bps) >= 3 and all(
            left <= right for left, right in pairwise(self.quantile_means_bps)
        )

    @property
    def years_positive(self) -> bool:
        return len(self.yearly_net_means_bps) >= 2 and all(
            value > 0 for value in self.yearly_net_means_bps.values()
        )

    def numeric_pass(self, min_samples: int = 200) -> bool:
        return self.sample_count >= min_samples and self.lower_bound_bps > 0


def evaluate_r1(
    horizon: str,
    observations: Sequence[R1Observation],
    *,
    embargo_ms: int,
    cost_bps: float = 22.0,
    leg: int = 3,
    buckets: int = 5,
) -> R1Result:
    """중첩을 제거하고 R1의 비용 후 성과를 집계한다."""
    if embargo_ms <= 0:
        raise ValueError("embargo_ms는 1 이상이어야 한다")
    rows: list[tuple[int, R1Sample]] = []
    last = -(10**18)
    for observation in sorted(observations, key=lambda item: item.at_ms):
        if observation.at_ms - last < embargo_ms:
            continue
        sample = sample_return(observation, leg=leg, buckets=buckets, cost_bps=cost_bps)
        if sample is None:
            continue
        rows.append((observation.at_ms, sample))
        last = observation.at_ms
    if len(rows) < 2:
        return R1Result(horizon, len(rows), 0.0, 0.0, 0.0, math.inf, 0.0, (), {})

    samples = [sample for _, sample in rows]
    net = [sample.net_bps for sample in samples]
    years: dict[int, list[float]] = {}
    for at_ms, sample in rows:
        year = datetime.fromtimestamp(at_ms / 1000, tz=UTC).year
        years.setdefault(year, []).append(sample.net_bps)
    return R1Result(
        horizon=horizon,
        sample_count=len(samples),
        gross_mean_bps=stats.mean(sample.gross_bps for sample in samples),
        funding_mean_bps=stats.mean(sample.funding_bps for sample in samples),
        net_mean_bps=stats.mean(net),
        net_stderr_bps=stats.stdev(net) / math.sqrt(len(net)),
        positive_rate=sum(value > 0 for value in net) / len(net),
        quantile_means_bps=tuple(
            stats.mean(sample.quantiles_bps[index] for sample in samples)
            for index in range(buckets)
        ),
        yearly_net_means_bps={
            year: stats.mean(values) for year, values in sorted(years.items()) if len(values) >= 30
        },
    )


def r1_verdict(
    results: Sequence[R1Result], *, min_samples: int = 200
) -> tuple[bool, tuple[str, ...]]:
    """26 §5의 모든 조건을 결합한다."""
    reasons: list[str] = []
    if len(results) != 2 or {result.horizon for result in results} != {"1d", "3d"}:
        raise ValueError("1d와 3d 결과가 각각 하나씩 필요하다")
    if any(result.sample_count < min_samples for result in results):
        reasons.append("SAMPLE")
    if not all(result.net_mean_bps > 0 for result in results):
        reasons.append("HORIZON_SIGN")
    numeric = [result for result in results if result.numeric_pass(min_samples)]
    if not numeric:
        reasons.append("NET_EDGE")
    qualifying = [
        result for result in numeric if result.is_monotone_rising and result.years_positive
    ]
    if numeric and not any(result.is_monotone_rising for result in numeric):
        reasons.append("MONOTONICITY")
    if numeric and not any(result.years_positive for result in numeric):
        reasons.append("YEAR_SIGN")
    if numeric and not qualifying and not reasons:
        reasons.append("CRITERIA_NOT_SAME_HORIZON")
    return not reasons and bool(qualifying), tuple(reasons)
