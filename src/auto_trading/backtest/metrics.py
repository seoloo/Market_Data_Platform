"""백테스트 성과 지표.

금액은 :class:`~decimal.Decimal` 로 끝까지 유지하고, 비율·통계량만 ``float`` 로
계산한다. 부트스트랩도 고정 시드 LCG를 사용하므로 같은 거래 기록은 플랫폼과 실행
순서에 무관하게 같은 결과를 낸다.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.core.hashing import sha256_hex
from auto_trading.core.money import ZERO

if TYPE_CHECKING:
    from auto_trading.backtest.engine import BacktestResult

__all__ = [
    "BacktestMetrics",
    "DistributionStats",
    "compute_metrics",
]

_YEAR_MS = 365 * 24 * 60 * 60 * 1000
_MONTH_MS = 30 * 24 * 60 * 60 * 1000
_DEFAULT_BOOTSTRAP_SAMPLES = 1_000
_DEFAULT_BOOTSTRAP_SEED = 0x5EED_2026


@dataclass(frozen=True, slots=True)
class DistributionStats:
    """작은 분포의 결정론적 5수치 요약."""

    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "minimum": self.minimum,
            "p25": self.p25,
            "median": self.median,
            "p75": self.p75,
            "maximum": self.maximum,
        }


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """리포트와 DB 저장에 공통으로 쓰는 성과 지표 묶음."""

    n_trades: int
    statistical_warning: str | None
    total_return_pct: float
    cagr_pct: float | None
    win_rate_pct: float
    profit_factor: float | None
    expectancy: Decimal
    expectancy_r: float
    average_win: Decimal
    average_loss: Decimal
    risk_reward: float | None
    max_consecutive_losses: int
    sharpe: float | None
    sortino: float | None
    t_statistic: float | None
    deflated_sharpe_probability: float | None
    sharpe_ci_95_low: float | None
    sharpe_ci_95_high: float | None
    max_drawdown_pct: float
    max_drawdown_duration_ms: int
    ulcer_index: float
    exposure_pct: float
    turnover: float
    trades_per_month: float
    cost_to_gross_profit: float | None
    total_gross_pnl: Decimal
    total_net_pnl: Decimal
    total_fees: Decimal
    total_funding_paid: Decimal
    average_holding_ms: int
    liquidations: int
    monthly_returns: tuple[tuple[str, float], ...]
    mae_pct: DistributionStats
    mfe_pct: DistributionStats

    def to_dict(self) -> dict[str, Any]:
        """JSON 안전 형식. Decimal은 정밀도 손실 없이 문자열로 보낸다."""
        return {
            "n_trades": self.n_trades,
            "statistical_warning": self.statistical_warning,
            "total_return_pct": self.total_return_pct,
            "cagr_pct": self.cagr_pct,
            "win_rate_pct": self.win_rate_pct,
            "profit_factor": self.profit_factor,
            "expectancy": str(self.expectancy),
            "expectancy_r": self.expectancy_r,
            "average_win": str(self.average_win),
            "average_loss": str(self.average_loss),
            "risk_reward": self.risk_reward,
            "max_consecutive_losses": self.max_consecutive_losses,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "t_statistic": self.t_statistic,
            "deflated_sharpe_probability": self.deflated_sharpe_probability,
            "sharpe_ci_95": [self.sharpe_ci_95_low, self.sharpe_ci_95_high],
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_drawdown_duration_ms": self.max_drawdown_duration_ms,
            "ulcer_index": self.ulcer_index,
            "exposure_pct": self.exposure_pct,
            "turnover": self.turnover,
            "trades_per_month": self.trades_per_month,
            "cost_to_gross_profit": self.cost_to_gross_profit,
            "total_gross_pnl": str(self.total_gross_pnl),
            "total_net_pnl": str(self.total_net_pnl),
            "total_fees": str(self.total_fees),
            "total_funding_paid": str(self.total_funding_paid),
            "average_holding_ms": self.average_holding_ms,
            "liquidations": self.liquidations,
            "monthly_returns": dict(self.monthly_returns),
            "mae_pct": self.mae_pct.to_dict(),
            "mfe_pct": self.mfe_pct.to_dict(),
        }

    def digest(self) -> str:
        """골든 테스트와 동일 실행 비교에 쓰는 정규화 SHA-256."""
        return sha256_hex(self.to_dict())


def compute_metrics(
    result: BacktestResult,
    *,
    trials_count: int = 1,
    bootstrap_samples: int = _DEFAULT_BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = _DEFAULT_BOOTSTRAP_SEED,
) -> BacktestMetrics:
    """한 번의 백테스트 원자료에서 모든 필수 지표를 계산한다.

    Sharpe/Sortino/t-stat/DSR/bootstrap CI는 분봉의 0 수익률로 표본 수를 부풀리지
    않도록 UTC 일별 종가 수익률을 쓴다. 평균 R은 별도로 거래별 R 배수에서 계산한다.
    """
    if trials_count < 1:
        raise ValueError(f"trials_count 는 1 이상이어야 한다: {trials_count}")
    if bootstrap_samples < 1:
        raise ValueError(f"bootstrap_samples 는 1 이상이어야 한다: {bootstrap_samples}")

    trades = result.trades
    nets = [trade.net_pnl for trade in trades]
    winners = [value for value in nets if value > ZERO]
    losers = [value for value in nets if value < ZERO]
    r_multiples = [float(trade.r_multiple) for trade in trades]
    total_gross = sum((trade.gross_pnl for trade in trades), start=ZERO)
    total_net = sum(nets, start=ZERO)
    total_fees = sum((trade.fees for trade in trades), start=ZERO)
    total_funding = sum((trade.funding_paid for trade in trades), start=ZERO)

    daily_returns = _daily_equity_returns(result)
    drawdown, duration_ms, ulcer = _drawdown_stats(result.equity_curve)
    ci_low, ci_high = _bootstrap_sharpe_ci(
        daily_returns,
        samples=bootstrap_samples,
        seed=bootstrap_seed,
        annualization=math.sqrt(365.0),
    )
    elapsed_ms = max(0, result.to_ms - result.from_ms)
    positive_gross = sum(
        (trade.gross_pnl for trade in trades if trade.gross_pnl > ZERO),
        start=ZERO,
    )
    paid_cost = total_fees + max(total_funding, ZERO)

    return BacktestMetrics(
        n_trades=len(trades),
        statistical_warning=("n<100, 통계적 근거 부족" if len(trades) < 100 else None),
        total_return_pct=_total_return(result.initial_equity, result.final_equity),
        cagr_pct=_cagr(result.initial_equity, result.final_equity, elapsed_ms),
        win_rate_pct=len(winners) / len(trades) if trades else 0.0,
        profit_factor=_profit_factor(winners, losers),
        expectancy=_decimal_mean(nets),
        expectancy_r=statistics.fmean(r_multiples) if r_multiples else 0.0,
        average_win=_decimal_mean(winners),
        average_loss=_decimal_mean(losers),
        risk_reward=_risk_reward(winners, losers),
        max_consecutive_losses=_max_consecutive_losses(nets),
        sharpe=_annualized_sharpe(daily_returns, 365.0),
        sortino=_annualized_sortino(daily_returns, 365.0),
        t_statistic=_t_statistic(daily_returns),
        deflated_sharpe_probability=_deflated_sharpe(daily_returns, trials_count),
        sharpe_ci_95_low=ci_low,
        sharpe_ci_95_high=ci_high,
        max_drawdown_pct=drawdown,
        max_drawdown_duration_ms=duration_ms,
        ulcer_index=ulcer,
        exposure_pct=result.exposure,
        turnover=_turnover(trades, result.initial_equity, result.final_equity),
        trades_per_month=(len(trades) / (elapsed_ms / _MONTH_MS) if elapsed_ms else 0.0),
        cost_to_gross_profit=(float(paid_cost / positive_gross) if positive_gross > ZERO else None),
        total_gross_pnl=total_gross,
        total_net_pnl=total_net,
        total_fees=total_fees,
        total_funding_paid=total_funding,
        average_holding_ms=(
            round(statistics.fmean(trade.holding_ms for trade in trades)) if trades else 0
        ),
        liquidations=result.liquidations,
        monthly_returns=_monthly_returns(result),
        mae_pct=_excursion_distribution(trades, use_mae=True),
        mfe_pct=_excursion_distribution(trades, use_mae=False),
    )


def _total_return(initial: Decimal, final: Decimal) -> float:
    return float((final - initial) / initial) if initial > ZERO else 0.0


def _cagr(initial: Decimal, final: Decimal, elapsed_ms: int) -> float | None:
    if initial <= ZERO or final <= ZERO or elapsed_ms <= 0:
        return None
    years = elapsed_ms / _YEAR_MS
    try:
        return math.expm1(math.log(float(final / initial)) / years)
    except OverflowError:
        return None


def _decimal_mean(values: list[Decimal]) -> Decimal:
    return sum(values, start=ZERO) / len(values) if values else ZERO


def _profit_factor(winners: list[Decimal], losers: list[Decimal]) -> float | None:
    loss = abs(sum(losers, start=ZERO))
    return float(sum(winners, start=ZERO) / loss) if loss > ZERO else None


def _risk_reward(winners: list[Decimal], losers: list[Decimal]) -> float | None:
    average_loss = abs(_decimal_mean(losers))
    return float(_decimal_mean(winners) / average_loss) if average_loss > ZERO else None


def _max_consecutive_losses(nets: list[Decimal]) -> int:
    longest = 0
    current = 0
    for value in nets:
        if value < ZERO:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _daily_equity_returns(result: BacktestResult) -> list[float]:
    if not result.equity_curve:
        return []
    day_ends: list[Decimal] = []
    current_day = result.equity_curve[0][0] // 86_400_000
    last_equity = result.initial_equity
    for timestamp_ms, equity in result.equity_curve:
        day = timestamp_ms // 86_400_000
        if day != current_day:
            day_ends.append(last_equity)
            current_day = day
        last_equity = equity
    day_ends.append(last_equity)

    returns: list[float] = []
    previous = result.initial_equity
    for current in day_ends:
        if previous > ZERO:
            returns.append(float(current / previous - 1))
        previous = current
    return returns


def _annualized_sharpe(returns: list[float], periods_per_year: float) -> float | None:
    ratio = _sample_ratio(returns)
    if ratio is None or periods_per_year <= 0.0:
        return None
    return ratio * math.sqrt(periods_per_year)


def _annualized_sortino(returns: list[float], periods_per_year: float) -> float | None:
    if not returns or periods_per_year <= 0.0:
        return None
    downside = math.sqrt(statistics.fmean(min(value, 0.0) ** 2 for value in returns))
    if downside <= 0.0:
        return None
    return statistics.fmean(returns) / downside * math.sqrt(periods_per_year)


def _sample_ratio(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    deviation = statistics.stdev(values)
    return statistics.fmean(values) / deviation if deviation > 0.0 else None


def _t_statistic(values: list[float]) -> float | None:
    ratio = _sample_ratio(values)
    return ratio * math.sqrt(len(values)) if ratio is not None else None


def _deflated_sharpe(values: list[float], trials_count: int) -> float | None:
    """Bailey/Lopez de Prado의 DSR 정규 근사."""
    observed = _sample_ratio(values)
    if observed is None or len(values) < 3:
        return None
    mean = statistics.fmean(values)
    centered = [value - mean for value in values]
    variance = statistics.fmean(value * value for value in centered)
    if variance <= 0.0:
        return None
    skew = statistics.fmean(value**3 for value in centered) / variance**1.5
    kurtosis = statistics.fmean(value**4 for value in centered) / variance**2
    expected_max = _expected_max_sharpe(len(values), trials_count)
    denominator_sq = 1.0 - skew * observed + ((kurtosis - 1.0) / 4.0) * observed**2
    if denominator_sq <= 0.0:
        return None
    statistic = (observed - expected_max) * math.sqrt(len(values) - 1) / math.sqrt(denominator_sq)
    return statistics.NormalDist().cdf(statistic)


def _expected_max_sharpe(sample_count: int, trials_count: int) -> float:
    if trials_count <= 1:
        return 0.0
    normal = statistics.NormalDist()
    euler_gamma = 0.5772156649015329
    variance = 1.0 / max(1, sample_count - 1)
    first = normal.inv_cdf(1.0 - 1.0 / trials_count)
    second = normal.inv_cdf(1.0 - 1.0 / (trials_count * math.e))
    return math.sqrt(variance) * ((1.0 - euler_gamma) * first + euler_gamma * second)


def _bootstrap_sharpe_ci(
    values: list[float], *, samples: int, seed: int, annualization: float
) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    state = seed & 0x7FFF_FFFF
    estimates: list[float] = []
    for _ in range(samples):
        resampled: list[float] = []
        for _ in values:
            state = (1_103_515_245 * state + 12_345) & 0x7FFF_FFFF
            resampled.append(values[state % len(values)])
        estimate = _sample_ratio(resampled)
        if estimate is not None and math.isfinite(estimate):
            estimates.append(estimate * annualization)
    if not estimates:
        return None, None
    estimates.sort()
    return _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def _drawdown_stats(curve: list[tuple[int, Decimal]]) -> tuple[float, int, float]:
    if not curve:
        return 0.0, 0, 0.0
    peak = curve[0][1]
    peak_ms = curve[0][0]
    max_drawdown = 0.0
    max_duration_ms = 0
    drawdowns: list[float] = []
    for timestamp_ms, equity in curve:
        if equity >= peak:
            peak = equity
            peak_ms = timestamp_ms
            drawdown = 0.0
        elif peak > ZERO:
            drawdown = float((peak - equity) / peak)
            max_drawdown = max(max_drawdown, drawdown)
            max_duration_ms = max(max_duration_ms, timestamp_ms - peak_ms)
        else:
            drawdown = 0.0
        drawdowns.append(drawdown)
    ulcer = math.sqrt(statistics.fmean(value * value for value in drawdowns))
    return max_drawdown, max_duration_ms, ulcer


def _monthly_returns(result: BacktestResult) -> tuple[tuple[str, float], ...]:
    if not result.equity_curve:
        return ()
    output: list[tuple[str, float]] = []
    current_key = _month_key(result.equity_curve[0][0])
    baseline = result.initial_equity
    last_equity = baseline
    for timestamp_ms, equity in result.equity_curve:
        key = _month_key(timestamp_ms)
        if key != current_key:
            output.append((current_key, float(last_equity / baseline - 1)))
            baseline = last_equity
            current_key = key
        last_equity = equity
    output.append((current_key, float(last_equity / baseline - 1)))
    return tuple(output)


def _month_key(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).strftime("%Y-%m")


def _turnover(trades: list[BacktestTrade], initial: Decimal, final: Decimal) -> float:
    average_equity = (initial + final) / 2
    if average_equity <= ZERO:
        return 0.0
    notional = sum(
        (trade.qty * trade.entry_price + trade.qty * trade.exit_price for trade in trades),
        start=ZERO,
    )
    return float(notional / average_equity)


def _excursion_distribution(trades: list[BacktestTrade], *, use_mae: bool) -> DistributionStats:
    values = sorted(
        float((trade.mae if use_mae else trade.mfe) / trade.entry_price)
        for trade in trades
        if trade.entry_price > ZERO
    )
    if not values:
        return DistributionStats(None, None, None, None, None)
    return DistributionStats(
        minimum=values[0],
        p25=_percentile(values, 0.25),
        median=_percentile(values, 0.5),
        p75=_percentile(values, 0.75),
        maximum=values[-1],
    )


def _percentile(sorted_values: list[float], quantile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight
