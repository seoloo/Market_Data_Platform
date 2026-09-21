"""TRAIN 구간에서 조건부 기대수익(edge table)을 추정한다.

edge 는 진입 신호 시점의 방향 점수·변동성 국면·고정 보유 horizon 으로 묶은
**비용 차감 전** 방향 수익률 평균이다. 비용은 ``strategy.cost`` 가 별도로 추정하므로
여기서 수수료나 슬리피지를 빼면 비용을 두 번 차감하게 된다.

이 모듈은 여러 horizon 을 한 번에 산출할 수 있지만 어느 하나를 자동으로 고르지 않는다.
TRAIN 성적이 가장 좋은 horizon 을 선택하면 바로 과최적화가 되기 때문이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Any

from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import Action, Mode, PositionSide, Timeframe
from auto_trading.core.hashing import sha256_hex
from auto_trading.core.types import DataQuality, MarketSnapshot, PortfolioState
from auto_trading.strategy.decision import evaluate_scores
from auto_trading.strategy.policy import resolve_action

from .feed import CandleFeed

__all__ = [
    "EdgeBucket",
    "EdgeCollection",
    "EdgeObservation",
    "EdgeTable",
    "collect_edge_observations",
    "estimate_edge_table",
    "forward_return_bps",
]

_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class EdgeObservation:
    """한 진입 신호의 고정 horizon 사후 결과."""

    as_of_ms: int
    side: PositionSide
    score: float
    regime: str
    horizon_bars: int
    gross_return_bps: Decimal

    def __post_init__(self) -> None:
        if self.side is PositionSide.FLAT:
            raise ValueError("edge 관측 방향은 LONG 또는 SHORT 여야 한다")
        if not 0.0 <= self.score <= 100.0:
            raise ValueError(f"score 는 [0,100] 이어야 한다: {self.score}")
        if not self.regime:
            raise ValueError("regime 은 비어 있을 수 없다")
        if self.horizon_bars <= 0:
            raise ValueError(f"horizon_bars 는 1 이상이어야 한다: {self.horizon_bars}")


@dataclass(frozen=True, slots=True)
class EdgeBucket:
    """동일 조건 관측치의 요약.

    ``sample_count < min_samples`` 면 관측 평균은 기록하되 실제 게이트 입력인
    ``edge_bps`` 는 0으로 닫힌다.
    """

    side: PositionSide
    regime: str
    horizon_bars: int
    score_from: float
    score_to: float
    sample_count: int
    observed_mean_bps: Decimal
    positive_rate: Decimal
    min_samples: int

    @property
    def available(self) -> bool:
        return self.sample_count >= self.min_samples

    @property
    def edge_bps(self) -> Decimal:
        return self.observed_mean_bps if self.available else Decimal(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side.value,
            "regime": self.regime,
            "horizon_bars": self.horizon_bars,
            "score_from": self.score_from,
            "score_to": self.score_to,
            "sample_count": self.sample_count,
            "observed_mean_bps": str(self.observed_mean_bps),
            "positive_rate": str(self.positive_rate),
            "min_samples": self.min_samples,
            "available": self.available,
            "edge_bps": str(self.edge_bps),
        }


@dataclass(frozen=True, slots=True)
class EdgeTable:
    """검증 구간에서 그대로 고정해 사용할 TRAIN 산출물."""

    symbol: str
    source_config_hash: str
    train_from_ms: int
    train_to_ms: int
    decision_timeframe: Timeframe
    score_boundaries: tuple[float, ...]
    horizons_bars: tuple[int, ...]
    min_samples: int
    observations: int
    buckets: tuple[EdgeBucket, ...]

    def __post_init__(self) -> None:
        _validate_boundaries(self.score_boundaries)
        if self.train_to_ms <= self.train_from_ms:
            raise ValueError("TRAIN 종료는 시작보다 뒤여야 한다")
        if not self.horizons_bars or any(value <= 0 for value in self.horizons_bars):
            raise ValueError("horizons_bars 는 양의 정수를 하나 이상 가져야 한다")
        if self.min_samples <= 0:
            raise ValueError("min_samples 는 1 이상이어야 한다")

    def lookup(
        self,
        *,
        side: PositionSide,
        score: float,
        regime: str,
        horizon_bars: int,
    ) -> EdgeBucket | None:
        """조건에 맞는 버킷. 없으면 호출자가 fail-closed 하도록 ``None``."""
        bounds = _score_range(score, self.score_boundaries)
        if bounds is None:
            return None
        lower, upper = bounds
        return next(
            (
                bucket
                for bucket in self.buckets
                if bucket.side is side
                and bucket.regime == regime
                and bucket.horizon_bars == horizon_bars
                and bucket.score_from == lower
                and bucket.score_to == upper
            ),
            None,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source_config_hash": self.source_config_hash,
            "train_from_ms": self.train_from_ms,
            "train_to_ms": self.train_to_ms,
            "decision_timeframe": self.decision_timeframe.value,
            "score_boundaries": list(self.score_boundaries),
            "horizons_bars": list(self.horizons_bars),
            "min_samples": self.min_samples,
            "observations": self.observations,
            "buckets": [bucket.to_dict() for bucket in self.buckets],
        }

    @property
    def digest(self) -> str:
        """설정 버전에 포함할 결정론적 테이블 해시."""
        return sha256_hex(self.payload())

    def to_json(self) -> str:
        payload = self.payload()
        payload["digest"] = self.digest
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class EdgeCollection:
    """실데이터 스코어 재생 결과와 진단 카운터."""

    observations: tuple[EdgeObservation, ...]
    decisions_evaluated: int
    signals_observed: int
    labels_skipped: int


def forward_return_bps(
    side: PositionSide,
    entry_price: Decimal,
    exit_price: Decimal,
) -> Decimal:
    """포지션 방향으로 부호를 맞춘 비용 차감 전 수익률."""
    if side is PositionSide.FLAT:
        raise ValueError("FLAT 방향의 forward return 은 정의할 수 없다")
    if entry_price <= 0 or exit_price <= 0:
        raise ValueError("가격은 0보다 커야 한다")
    return (exit_price - entry_price) / entry_price * _BPS * side.sign


def collect_edge_observations(
    cfg: RootConfig,
    feed: CandleFeed,
    *,
    from_ms: int,
    to_ms: int,
    horizons_bars: tuple[int, ...],
    label_to_ms: int | None = None,
) -> EdgeCollection:
    """라이브와 같은 ``decide()`` 점수 경로로 TRAIN 관측치를 만든다.

    진입은 신호 봉 다음 5분봉 시가, 청산 라벨은 그로부터 ``horizon_bars`` 뒤
    5분봉 시가다. 다음 5분봉 시가는 실제 엔진의 다음 1분봉 시가와 같은 시각이므로
    신호 봉 종가 체결이라는 낙관 가정을 피한다.
    """
    horizons = tuple(sorted(set(horizons_bars)))
    if not horizons or any(value <= 0 for value in horizons):
        raise ValueError("horizons_bars 는 양의 정수를 하나 이상 가져야 한다")
    decision_tf = _decision_timeframe(cfg)
    resolved_label_to = to_ms if label_to_ms is None else label_to_ms
    if resolved_label_to < to_ms:
        raise ValueError("label_to_ms 는 평가 종료 시점보다 빠를 수 없다")
    decision_bars = feed.bars_between(decision_tf, from_ms, to_ms)
    if not decision_bars:
        return EdgeCollection((), 0, 0, 0)
    label_bars = feed.bars_between(
        decision_tf,
        decision_bars[0].open_ms,
        resolved_label_to,
    )
    by_open = {bar.open_ms: bar for bar in label_bars}
    state = _flat_portfolio()
    observations: list[EdgeObservation] = []
    evaluated = 0
    signals = 0
    skipped = 0

    for bar in decision_bars:
        close_ms = bar.close_ms
        if not all(feed.has_warmup(tf, close_ms) for tf in feed.timeframes):
            continue
        snapshot = _snapshot(feed, cfg, close_ms)
        evaluation = evaluate_scores(snapshot, cfg)
        evaluated += 1
        if not evaluation.available:
            continue
        action = resolve_action(evaluation.scores, state, cfg.policy)
        if action not in (Action.OPEN_LONG, Action.OPEN_SHORT):
            continue
        signals += 1
        side = PositionSide.LONG if action is Action.OPEN_LONG else PositionSide.SHORT
        score = (
            evaluation.scores.long_score
            if side is PositionSide.LONG
            else evaluation.scores.short_score
        )
        regime = evaluation.scores.regime or "unknown"
        entry_ms = bar.open_ms + decision_tf.ms
        entry = by_open.get(entry_ms)
        if entry is None:
            skipped += len(horizons)
            continue
        for horizon in horizons:
            exit_bar = by_open.get(entry_ms + horizon * decision_tf.ms)
            if exit_bar is None:
                skipped += 1
                continue
            observations.append(
                EdgeObservation(
                    as_of_ms=close_ms,
                    side=side,
                    score=score,
                    regime=regime,
                    horizon_bars=horizon,
                    gross_return_bps=forward_return_bps(side, entry.open, exit_bar.open),
                )
            )
    return EdgeCollection(tuple(observations), evaluated, signals, skipped)


def estimate_edge_table(
    observations: tuple[EdgeObservation, ...],
    *,
    symbol: str,
    source_config_hash: str,
    train_from_ms: int,
    train_to_ms: int,
    decision_timeframe: Timeframe,
    score_boundaries: tuple[float, ...] = (70.0, 80.0, 90.0, 100.0),
    min_samples: int = 30,
    horizons_bars: tuple[int, ...] | None = None,
) -> EdgeTable:
    """관측치를 고정 버킷으로 집계한다.

    버킷 경계는 실행 전에 입력으로 고정되며, 데이터 분포를 보고 자동 조정하지 않는다.
    """
    _validate_boundaries(score_boundaries)
    if min_samples <= 0:
        raise ValueError("min_samples 는 1 이상이어야 한다")
    grouped: dict[tuple[PositionSide, str, int, float, float], list[Decimal]] = {}
    observed_horizons: set[int] = set()
    for observation in observations:
        bounds = _score_range(observation.score, score_boundaries)
        if bounds is None:
            continue
        lower, upper = bounds
        key = (
            observation.side,
            observation.regime,
            observation.horizon_bars,
            lower,
            upper,
        )
        grouped.setdefault(key, []).append(observation.gross_return_bps)
        observed_horizons.add(observation.horizon_bars)

    horizons = (
        tuple(sorted(observed_horizons))
        if horizons_bars is None
        else tuple(sorted(set(horizons_bars)))
    )
    if not horizons or any(value <= 0 for value in horizons):
        raise ValueError("horizons_bars 는 양의 정수를 하나 이상 가져야 한다")

    buckets = []
    for (side, regime, horizon, lower, upper), values in sorted(
        grouped.items(),
        key=lambda item: (
            item[0][2],
            item[0][0].value,
            item[0][1],
            item[0][3],
        ),
    ):
        count = len(values)
        mean = sum(values, Decimal(0)) / count
        positive = Decimal(sum(value > 0 for value in values)) / count
        buckets.append(
            EdgeBucket(
                side=side,
                regime=regime,
                horizon_bars=horizon,
                score_from=lower,
                score_to=upper,
                sample_count=count,
                observed_mean_bps=mean,
                positive_rate=positive,
                min_samples=min_samples,
            )
        )
    return EdgeTable(
        symbol=symbol,
        source_config_hash=source_config_hash,
        train_from_ms=train_from_ms,
        train_to_ms=train_to_ms,
        decision_timeframe=decision_timeframe,
        score_boundaries=score_boundaries,
        horizons_bars=horizons,
        min_samples=min_samples,
        observations=len(observations),
        buckets=tuple(buckets),
    )


def _snapshot(feed: CandleFeed, cfg: RootConfig, as_of_ms: int) -> MarketSnapshot:
    series = tuple(feed.series(tf, as_of_ms) for tf in feed.timeframes)
    mark = feed.last_close(_decision_timeframe(cfg), as_of_ms)
    if mark is None:  # pragma: no cover - warm-up 검사 뒤이므로 방어 코드
        raise ValueError("결정 시점에 마감된 기준 봉이 없다")
    return MarketSnapshot(
        symbol=feed.symbol,
        as_of_ms=as_of_ms,
        mode=Mode.BACKTEST,
        series=series,
        mark_price=mark,
        last_price=mark,
        spread_bps=cfg.risk.max_spread_bps / Decimal(5),
        data_quality=DataQuality(ok=True),
    )


def _flat_portfolio() -> PortfolioState:
    equity = Decimal("10000")
    return PortfolioState(
        equity=equity,
        wallet_balance=equity,
        available_balance=equity,
        equity_high_water=equity,
    )


def _decision_timeframe(cfg: RootConfig) -> Timeframe:
    return min(
        (
            cfg.features.t1_pullback.timeframe,
            cfg.features.t2_momentum.timeframe,
            cfg.features.t3_volume.timeframe,
        ),
        key=lambda timeframe: timeframe.ms,
    )


def _validate_boundaries(boundaries: tuple[float, ...]) -> None:
    if len(boundaries) < 2:
        raise ValueError("score_boundaries 는 경계가 2개 이상이어야 한다")
    if tuple(sorted(set(boundaries))) != boundaries:
        raise ValueError("score_boundaries 는 중복 없이 오름차순이어야 한다")
    if boundaries[0] < 0.0 or boundaries[-1] > 100.0:
        raise ValueError("score_boundaries 는 [0,100] 범위여야 한다")


def _score_range(score: float, boundaries: tuple[float, ...]) -> tuple[float, float] | None:
    for index, (lower, upper) in enumerate(pairwise(boundaries)):
        is_last = index == len(boundaries) - 2
        if lower <= score < upper or (is_last and score == upper):
            return lower, upper
    return None
