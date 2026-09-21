"""Buy & Hold·랜덤 진입·단일 feature 기준선.

랜덤 기준선은 실제 전략 거래의 방향, 거래 수, 보유시간, 포지션 크기, funding 부담을
보존하고 **진입 시점만** 바꾼다. 비교 조건까지 바꾸면 전략의 신호가 아니라 레버리지나
회전율 차이를 재게 되기 때문이다. 모든 난수는 고정 LCG라 재실행 결과가 동일하다.
"""

from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from auto_trading.backtest.fills import SlippageModel
from auto_trading.backtest.metrics import BacktestMetrics
from auto_trading.config.schema import CostConfig, RootConfig
from auto_trading.core.enums import Timeframe
from auto_trading.core.hashing import sha256_hex
from auto_trading.core.money import ZERO
from auto_trading.core.types import Candle
from auto_trading.features.base import FeatureResult
from auto_trading.features.registry import build_feature_set, float_param, zone_param

if TYPE_CHECKING:
    from collections.abc import Sequence

    from auto_trading.backtest.engine import BacktestResult
    from auto_trading.backtest.feed import CandleFeed
    from auto_trading.features.base import Feature

__all__ = [
    "BaselineComparison",
    "BaselineOutcome",
    "RandomBenchmark",
    "SignalObservation",
    "SingleFeatureSignals",
    "collect_single_feature_signals",
    "compute_baselines",
]

_DAY_MS = 86_400_000
_DEFAULT_RANDOM_RUNS = 1_000
_DEFAULT_RANDOM_SEED = 0xBA5E_2026


@dataclass(frozen=True, slots=True)
class SignalObservation:
    """해당 시각의 봉이 마감된 뒤 알 수 있는 방향 신호."""

    as_of_ms: int
    direction: int

    def __post_init__(self) -> None:
        if self.as_of_ms < 0:
            raise ValueError(f"as_of_ms 가 음수: {self.as_of_ms}")
        if self.direction not in (-1, 0, 1):
            raise ValueError(f"direction 은 -1/0/1 이어야 한다: {self.direction}")


@dataclass(frozen=True, slots=True)
class SingleFeatureSignals:
    """단일 feature 전략에 사용할 causal 방향 시계열."""

    name: str
    observations: tuple[SignalObservation, ...] = ()
    directional: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        times = [observation.as_of_ms for observation in self.observations]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError(f"{self.name} signal 이 시간 오름차순이 아니거나 중복")


@dataclass(frozen=True, slots=True)
class BaselineOutcome:
    """하나의 기준선 결과. ``None``은 0이 아니라 계산 불가를 뜻한다."""

    name: str
    n_trades: int
    total_return_pct: float | None
    sharpe: float | None
    note: str = ""

    @property
    def available(self) -> bool:
        return self.total_return_pct is not None

    def to_dict(self) -> dict[str, str | int | float | bool | None]:
        return {
            "name": self.name,
            "available": self.available,
            "n_trades": self.n_trades,
            "total_return_pct": self.total_return_pct,
            "sharpe": self.sharpe,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class RandomBenchmark:
    """랜덤 진입 분포와 실제 전략의 위치."""

    simulations: int
    strategy_percentile: float | None
    p05_return_pct: float | None
    median_return_pct: float | None
    p95_return_pct: float | None
    distinguishable_at_95pct: bool | None
    returns_digest: str | None
    note: str = ""

    def to_dict(self) -> dict[str, str | int | float | bool | None]:
        return {
            "simulations": self.simulations,
            "strategy_percentile": self.strategy_percentile,
            "p05_return_pct": self.p05_return_pct,
            "median_return_pct": self.median_return_pct,
            "p95_return_pct": self.p95_return_pct,
            "distinguishable_at_95pct": self.distinguishable_at_95pct,
            "returns_digest": self.returns_digest,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    """리포트에 항상 함께 붙는 기준선 비교 전체."""

    equal_weight: BaselineOutcome
    buy_and_hold: BaselineOutcome
    random_entry: RandomBenchmark
    single_features: tuple[BaselineOutcome, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "equal_weight": self.equal_weight.to_dict(),
            "buy_and_hold": self.buy_and_hold.to_dict(),
            "random_entry": self.random_entry.to_dict(),
            "single_features": [outcome.to_dict() for outcome in self.single_features],
        }

    def digest(self) -> str:
        return sha256_hex(self.to_dict())

    def to_markdown(self) -> str:
        random = self.random_entry
        lines = [
            "# 기준선 비교",
            "",
            "## 전략과 Buy & Hold",
            "",
            "| 기준 | 거래 수 | 총수익률 | Sharpe | 비고 |",
            "|---|---:|---:|---:|---|",
            _outcome_row(self.equal_weight),
            _outcome_row(self.buy_and_hold),
            "",
            "## 랜덤 진입",
            "",
        ]
        if random.strategy_percentile is None:
            lines.append(f"계산 불가: {random.note}")
        else:
            verdict = (
                "95백분위 초과 — 랜덤과 구분 가능"
                if random.distinguishable_at_95pct
                else "95백분위 미만 — 통계적으로 랜덤과 구분 불가"
            )
            lines.extend(
                (
                    f"- 시뮬레이션: {random.simulations:,}회",
                    f"- 전략 백분위: {random.strategy_percentile * 100:.2f}%",
                    f"- 랜덤 수익률 5/50/95백분위: "
                    f"{_pct(random.p05_return_pct)} / {_pct(random.median_return_pct)} / "
                    f"{_pct(random.p95_return_pct)}",
                    f"- 판정: **{verdict}**",
                    f"- 분포 digest: `{random.returns_digest}`",
                )
            )
        lines.extend(
            (
                "",
                "## 단일 feature",
                "",
                "| feature | 거래 수 | 총수익률 | Sharpe | 비고 |",
                "|---|---:|---:|---:|---|",
            )
        )
        lines.extend(_outcome_row(outcome) for outcome in self.single_features)
        lines.extend(
            (
                "",
                "> 랜덤 기준선은 실제 전략과 방향·거래 수·보유시간·노출 크기·비용을 "
                "같게 두고 진입 시점만 변경합니다.",
            )
        )
        return "\n".join(lines)


def compute_baselines(
    result: BacktestResult,
    metrics: BacktestMetrics,
    minute_bars: Sequence[Candle],
    cost: CostConfig,
    *,
    single_features: tuple[SingleFeatureSignals, ...] = (),
    random_runs: int = _DEFAULT_RANDOM_RUNS,
    random_seed: int = _DEFAULT_RANDOM_SEED,
    slippage: SlippageModel | None = None,
) -> BaselineComparison:
    """동일 기간·동일 비용 조건의 기준선들을 계산한다."""
    if random_runs < 1:
        raise ValueError(f"random_runs 는 1 이상이어야 한다: {random_runs}")
    bars = tuple(bar for bar in minute_bars if result.from_ms <= bar.open_ms <= result.to_ms)
    _validate_bars(bars)
    slippage_model = slippage or SlippageModel()
    equal_weight = BaselineOutcome(
        name="equal_weight_w0",
        n_trades=metrics.n_trades,
        total_return_pct=metrics.total_return_pct,
        sharpe=metrics.sharpe,
        note="현재 5개 feature 등가중 전략",
    )
    buy_and_hold = _buy_and_hold(result.initial_equity, bars, cost, slippage_model)
    random_entry = _random_benchmark(
        result,
        bars,
        cost,
        slippage_model,
        runs=random_runs,
        seed=random_seed,
    )
    feature_outcomes = tuple(
        _single_feature_baseline(result, bars, cost, slippage_model, signals)
        for signals in single_features
    )
    return BaselineComparison(
        equal_weight=equal_weight,
        buy_and_hold=buy_and_hold,
        random_entry=random_entry,
        single_features=feature_outcomes,
    )


def collect_single_feature_signals(
    feed: CandleFeed,
    cfg: RootConfig,
    *,
    from_ms: int,
    to_ms: int,
) -> tuple[SingleFeatureSignals, ...]:
    """feature 원래 의미를 보존한 단독 방향 신호를 causal하게 수집한다.

    B2(변동성)와 T3(거래량)는 방향이 없는 보조 feature라 억지로 매수/매도 방향을
    만들지 않고 N/A로 기록한다. 이는 실패가 아니라 feature 역할의 명시적 결과다.
    """
    features = build_feature_set(cfg.features)
    directional: tuple[tuple[Feature, str], ...] = (
        (features.trend, "b1_trend"),
        (features.pullback, "t1_pullback"),
        (features.momentum, "t2_momentum"),
    )
    collected: dict[str, tuple[SignalObservation, ...]] = {}
    for feature, name in directional:
        observations: list[SignalObservation] = []
        for candle in feed.bars_between(feature.timeframe, from_ms, to_ms):
            if candle.close_ms < from_ms or candle.close_ms > to_ms:
                continue
            if not feed.has_warmup(feature.timeframe, candle.close_ms):
                continue
            result = feature.compute(feed.series(feature.timeframe, candle.close_ms))
            direction = _feature_direction(name, result, cfg)
            observations.append(SignalObservation(candle.close_ms, direction))
        collected[name] = tuple(observations)

    return (
        SingleFeatureSignals(
            "b1_trend",
            collected["b1_trend"],
            note="|B1|이 bias 임계값을 넘으면 추세 방향",
        ),
        SingleFeatureSignals(
            "b2_volregime",
            directional=False,
            note="변동성 국면은 방향 신호가 아니라 게이트·사이징 전용",
        ),
        SingleFeatureSignals(
            "t1_pullback",
            collected["t1_pullback"],
            note="RSI long/short 되돌림 구간을 대칭 방향으로 평가",
        ),
        SingleFeatureSignals(
            "t2_momentum",
            collected["t2_momentum"],
            note="|z|가 최소 임계값을 넘으면 모멘텀 방향",
        ),
        SingleFeatureSignals(
            "t3_volume",
            directional=False,
            note="거래량은 방향이 없는 확인 계수",
        ),
    )


@dataclass(frozen=True, slots=True)
class _TradeTemplate:
    direction: int
    duration_bars: int
    notional_ratio: Decimal
    funding_rate: Decimal


def _buy_and_hold(
    initial_equity: Decimal,
    bars: tuple[Candle, ...],
    cost: CostConfig,
    slippage: SlippageModel,
) -> BaselineOutcome:
    liquid = [bar for bar in bars if bar.volume > ZERO]
    if len(liquid) < 2 or initial_equity <= ZERO:
        return BaselineOutcome(
            "buy_and_hold",
            0,
            None,
            None,
            "체결 가능한 1분봉이 2개 미만",
        )
    entry = liquid[0]
    exit_bar = liquid[-1]
    entry_price = _market_price(
        entry,
        direction=1,
        is_entry=True,
        notional=initial_equity,
        slippage=slippage,
    )
    qty = initial_equity / (entry_price * (Decimal(1) + cost.taker_fee_rate))
    exit_price = _market_price(
        exit_bar,
        direction=1,
        is_entry=False,
        notional=qty * exit_bar.close,
        slippage=slippage,
        use_close=True,
    )
    final_equity = qty * exit_price * (Decimal(1) - cost.taker_fee_rate)
    daily_returns = _buy_hold_daily_returns(initial_equity, qty, liquid, cost)
    return BaselineOutcome(
        name="buy_and_hold",
        n_trades=1,
        total_return_pct=float(final_equity / initial_equity - 1),
        sharpe=_annualized_sharpe(daily_returns),
        note="1x long, 진입·청산 taker 수수료와 슬리피지 반영",
    )


def _random_benchmark(
    result: BacktestResult,
    bars: tuple[Candle, ...],
    cost: CostConfig,
    slippage: SlippageModel,
    *,
    runs: int,
    seed: int,
) -> RandomBenchmark:
    templates = _trade_templates(result)
    if not templates:
        return RandomBenchmark(
            simulations=0,
            strategy_percentile=None,
            p05_return_pct=None,
            median_return_pct=None,
            p95_return_pct=None,
            distinguishable_at_95pct=None,
            returns_digest=None,
            note="실제 전략 거래가 0건이라 동일 거래 수 랜덤 기준선을 만들 수 없음",
        )
    minimum_span = sum(template.duration_bars for template in templates) + len(templates) - 1
    if len(bars) - 1 < minimum_span:
        return RandomBenchmark(
            simulations=0,
            strategy_percentile=None,
            p05_return_pct=None,
            median_return_pct=None,
            p95_return_pct=None,
            distinguishable_at_95pct=None,
            returns_digest=None,
            note="구간이 실제 거래의 총 보유시간보다 짧음",
        )

    generator = _Lcg(seed)
    returns: list[float] = []
    for _ in range(runs):
        ordered = list(templates)
        generator.shuffle(ordered)
        schedule = _random_schedule(bars, ordered, generator)
        if schedule is None:
            return RandomBenchmark(
                simulations=len(returns),
                strategy_percentile=None,
                p05_return_pct=None,
                median_return_pct=None,
                p95_return_pct=None,
                distinguishable_at_95pct=None,
                returns_digest=None,
                note="거래량 0 구간 때문에 비중첩 랜덤 일정을 만들 수 없음",
            )
        equity = result.initial_equity
        for template, entry_index, exit_index in schedule:
            equity = _apply_trade(
                equity,
                template,
                bars[entry_index],
                bars[exit_index],
                cost,
                slippage,
            )
        returns.append(float(equity / result.initial_equity - 1))

    returns.sort()
    realized_net = sum((trade.net_pnl for trade in result.trades), start=ZERO)
    strategy_return = float(realized_net / result.initial_equity)
    below = bisect.bisect_left(returns, strategy_return)
    equal = bisect.bisect_right(returns, strategy_return) - below
    percentile = (below + 0.5 * equal) / len(returns)
    return RandomBenchmark(
        simulations=len(returns),
        strategy_percentile=percentile,
        p05_return_pct=_percentile(returns, 0.05),
        median_return_pct=_percentile(returns, 0.50),
        p95_return_pct=_percentile(returns, 0.95),
        distinguishable_at_95pct=percentile >= 0.95,
        returns_digest=sha256_hex(returns),
        note="완결 거래 기준; 실제 거래 template 보존, 진입 시점만 무작위",
    )


def _single_feature_baseline(
    result: BacktestResult,
    bars: tuple[Candle, ...],
    cost: CostConfig,
    slippage: SlippageModel,
    signals: SingleFeatureSignals,
) -> BaselineOutcome:
    if not signals.directional:
        return BaselineOutcome(signals.name, 0, None, None, signals.note)
    templates = _trade_templates(result)
    if not templates:
        return BaselineOutcome(
            signals.name,
            0,
            None,
            None,
            "보유시간·노출을 맞출 실제 전략 거래가 없음",
        )
    average_duration = max(
        1,
        round(statistics.fmean(template.duration_bars for template in templates)),
    )
    average_notional = sum((template.notional_ratio for template in templates), start=ZERO) / len(
        templates
    )
    average_funding = sum((template.funding_rate for template in templates), start=ZERO) / len(
        templates
    )
    template_by_direction = {
        -1: _TradeTemplate(-1, average_duration, average_notional, average_funding),
        1: _TradeTemplate(1, average_duration, average_notional, average_funding),
    }
    opens = [bar.open_ms for bar in bars]
    equity = result.initial_equity
    equity_events: list[tuple[int, Decimal]] = []
    last_exit_index = -1
    n_trades = 0
    for observation in signals.observations:
        if observation.direction == 0:
            continue
        target_entry = bisect.bisect_right(opens, observation.as_of_ms)
        entry_index = _next_liquid_index(bars, max(target_entry, last_exit_index + 1))
        if entry_index is None:
            break
        exit_index = _next_liquid_index(bars, entry_index + average_duration)
        if exit_index is None:
            break
        equity = _apply_trade(
            equity,
            template_by_direction[observation.direction],
            bars[entry_index],
            bars[exit_index],
            cost,
            slippage,
        )
        equity_events.append((bars[exit_index].open_ms, equity))
        last_exit_index = exit_index
        n_trades += 1
    if n_trades == 0:
        return BaselineOutcome(signals.name, 0, 0.0, None, f"{signals.note}; 진입 신호 없음")
    daily_returns = _daily_returns_from_events(
        result.initial_equity,
        equity_events,
        result.from_ms,
        result.to_ms,
    )
    return BaselineOutcome(
        name=signals.name,
        n_trades=n_trades,
        total_return_pct=float(equity / result.initial_equity - 1),
        sharpe=_annualized_sharpe(daily_returns),
        note=f"{signals.note}; 평균 보유시간·평균 노출 고정",
    )


def _feature_direction(name: str, result: FeatureResult, cfg: RootConfig) -> int:
    if not result.available:
        return 0
    if name == "b1_trend":
        trend_value = result.normalized
        if trend_value is None or abs(trend_value) < cfg.policy.bias_threshold:
            return 0
        return 1 if trend_value > 0.0 else -1
    if name == "t1_pullback":
        rsi_value = result.raw
        if rsi_value is None:
            return 0
        long_zone = zone_param(cfg.features.t1_pullback, "long_zone", (35.0, 50.0))
        short_zone = zone_param(cfg.features.t1_pullback, "short_zone", (50.0, 65.0))
        if Decimal(str(long_zone[0])) <= rsi_value < Decimal(str(long_zone[1])):
            return 1
        if Decimal(str(short_zone[0])) < rsi_value <= Decimal(str(short_zone[1])):
            return -1
        return 0
    if name == "t2_momentum":
        momentum_value = result.normalized
        threshold = float_param(cfg.features.t2_momentum, "min_abs_z", 0.3)
        if momentum_value is None or abs(momentum_value) < threshold:
            return 0
        return 1 if momentum_value > 0.0 else -1
    return 0


def _trade_templates(result: BacktestResult) -> tuple[_TradeTemplate, ...]:
    if result.initial_equity <= ZERO:
        return ()
    equity_times = [timestamp_ms for timestamp_ms, _equity in result.equity_curve]
    templates: list[_TradeTemplate] = []
    for trade in result.trades:
        entry_notional = trade.entry_price * trade.qty
        if entry_notional <= ZERO:
            continue
        equity_index = bisect.bisect_right(equity_times, trade.entry_ms) - 1
        entry_equity = (
            result.equity_curve[equity_index][1] if equity_index >= 0 else result.initial_equity
        )
        if entry_equity <= ZERO:
            continue
        templates.append(
            _TradeTemplate(
                direction=trade.side.sign,
                duration_bars=max(1, round(trade.holding_ms / Timeframe.M1.ms)),
                notional_ratio=entry_notional / entry_equity,
                funding_rate=trade.funding_paid / entry_notional,
            )
        )
    return tuple(templates)


def _random_schedule(
    bars: tuple[Candle, ...],
    templates: list[_TradeTemplate],
    generator: _Lcg,
) -> list[tuple[_TradeTemplate, int, int]] | None:
    total_duration = sum(template.duration_bars for template in templates)
    slack = len(bars) - 1 - total_duration - len(templates) + 1
    for _attempt in range(100):
        offsets = sorted(generator.randrange(slack + 1) for _ in templates)
        schedule: list[tuple[_TradeTemplate, int, int]] = []
        elapsed = 0
        previous_exit = -1
        valid = True
        for position, (template, offset) in enumerate(zip(templates, offsets, strict=True)):
            raw_entry = elapsed + position + offset
            entry_index = _next_liquid_index(bars, max(raw_entry, previous_exit + 1))
            if entry_index is None:
                valid = False
                break
            exit_index = _next_liquid_index(bars, entry_index + template.duration_bars)
            if exit_index is None:
                valid = False
                break
            schedule.append((template, entry_index, exit_index))
            previous_exit = exit_index
            elapsed += template.duration_bars
        if valid:
            return schedule
    return None


def _apply_trade(
    equity: Decimal,
    template: _TradeTemplate,
    entry_bar: Candle,
    exit_bar: Candle,
    cost: CostConfig,
    slippage: SlippageModel,
) -> Decimal:
    if equity <= ZERO:
        return ZERO
    direction = Decimal(template.direction)
    notional = equity * template.notional_ratio
    entry_price = _market_price(
        entry_bar,
        direction=template.direction,
        is_entry=True,
        notional=notional,
        slippage=slippage,
    )
    qty = notional / entry_price
    exit_price = _market_price(
        exit_bar,
        direction=template.direction,
        is_entry=False,
        notional=qty * exit_bar.open,
        slippage=slippage,
    )
    gross = (exit_price - entry_price) * qty * direction
    fees = (entry_price * qty + exit_price * qty) * cost.taker_fee_rate
    funding = notional * template.funding_rate if cost.include_funding else ZERO
    return max(ZERO, equity + gross - fees - funding)


def _market_price(
    bar: Candle,
    *,
    direction: int,
    is_entry: bool,
    notional: Decimal,
    slippage: SlippageModel,
    use_close: bool = False,
) -> Decimal:
    reference = bar.close if use_close else bar.open
    bps = slippage.estimate(
        is_stop=False,
        notional=notional,
        bar_quote_volume=bar.quote_volume,
    )
    sign = Decimal(direction if is_entry else -direction)
    return reference * (Decimal(1) + sign * bps / Decimal(10_000))


def _buy_hold_daily_returns(
    initial: Decimal,
    qty: Decimal,
    bars: list[Candle],
    cost: CostConfig,
) -> list[float]:
    day_ends: list[Decimal] = []
    current_day = bars[0].open_ms // _DAY_MS
    last = bars[0]
    for bar in bars:
        day = bar.open_ms // _DAY_MS
        if day != current_day:
            day_ends.append(last.close)
            current_day = day
        last = bar
    day_ends.append(last.close)
    returns: list[float] = []
    previous = initial
    for close in day_ends:
        equity = qty * close
        if previous > ZERO:
            returns.append(float(equity / previous - 1))
        previous = equity
    return returns


def _daily_returns_from_events(
    initial: Decimal,
    events: list[tuple[int, Decimal]],
    from_ms: int,
    to_ms: int,
) -> list[float]:
    by_day = {timestamp_ms // _DAY_MS: equity for timestamp_ms, equity in events}
    returns: list[float] = []
    previous = initial
    current = initial
    for day in range(from_ms // _DAY_MS, to_ms // _DAY_MS + 1):
        current = by_day.get(day, current)
        if previous > ZERO:
            returns.append(float(current / previous - 1))
        previous = current
    return returns


def _annualized_sharpe(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    deviation = statistics.stdev(returns)
    if deviation <= 0.0:
        return None
    return statistics.fmean(returns) / deviation * math.sqrt(365.0)


def _next_liquid_index(bars: tuple[Candle, ...], start: int) -> int | None:
    for index in range(max(0, start), len(bars)):
        if bars[index].volume > ZERO:
            return index
    return None


def _validate_bars(bars: tuple[Candle, ...]) -> None:
    opens = [bar.open_ms for bar in bars]
    if opens != sorted(opens) or len(opens) != len(set(opens)):
        raise ValueError("minute_bars 가 시간 오름차순이 아니거나 중복")
    if any(bar.open_ms % Timeframe.M1.ms != 0 for bar in bars):
        raise ValueError("minute_bars 에 1분 경계가 아닌 봉이 있음")


def _percentile(values: list[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _outcome_row(outcome: BaselineOutcome) -> str:
    return (
        f"| {outcome.name} | {outcome.n_trades} | {_pct(outcome.total_return_pct)} | "
        f"{_number(outcome.sharpe)} | {outcome.note} |"
    )


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


class _Lcg:
    """Python 버전과 무관한 작은 결정론적 난수 생성기."""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        self._state = seed & 0x7FFF_FFFF

    def randrange(self, stop: int) -> int:
        if stop <= 0:
            raise ValueError(f"stop 은 양수여야 한다: {stop}")
        self._state = (1_103_515_245 * self._state + 12_345) & 0x7FFF_FFFF
        return self._state % stop

    def shuffle(self, values: list[_TradeTemplate]) -> None:
        for index in range(len(values) - 1, 0, -1):
            other = self.randrange(index + 1)
            values[index], values[other] = values[other], values[index]
