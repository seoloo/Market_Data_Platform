from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.backtest.engine import BacktestResult
from auto_trading.backtest.metrics import compute_metrics
from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.backtest.regimes import RegimePerformance
from auto_trading.backtest.report import ReportMetadata, generate_report
from auto_trading.core.enums import CloseReason, PositionSide

D = Decimal
DAY = 86_400_000


def _trade(index: int, gross_pnl: str, side: PositionSide) -> BacktestTrade:
    entry = index * 90 * DAY
    return BacktestTrade(
        symbol="BTCUSDT",
        side=side,
        entry_ms=entry,
        exit_ms=entry + DAY,
        entry_price=D("100"),
        exit_price=D("101"),
        qty=D("10"),
        close_reason=CloseReason.TAKE_PROFIT if D(gross_pnl) > 0 else CloseReason.STOP,
        gross_pnl=D(gross_pnl),
        fees=D("1"),
        funding_paid=D("0"),
        planned_risk=D("100"),
        leverage=2,
        mae=D(str(index + 1)),
        mfe=D(str((index + 1) * 2)),
    )


def _result() -> BacktestResult:
    trades = [
        _trade(0, "201", PositionSide.LONG),
        _trade(1, "-99", PositionSide.SHORT),
        _trade(2, "301", PositionSide.LONG),
        _trade(3, "-199", PositionSide.SHORT),
    ]
    return BacktestResult(
        symbol="BTCUSDT",
        from_ms=0,
        to_ms=365 * DAY,
        initial_equity=D("10000"),
        final_equity=D("10200"),
        trades=trades,
        equity_curve=[
            (0, D("10000")),
            (90 * DAY, D("10100")),
            (180 * DAY, D("10000")),
            (270 * DAY, D("10300")),
            (365 * DAY, D("10200")),
        ],
        decisions_evaluated=100,
        entries_signalled=7,
        veto_counts={"SAME_DIRECTION_LIMIT": 3, "REENTRY_COOLDOWN": 1},
        close_reasons={"take_profit": 2, "stop": 2},
        bars_in_position=25,
        bars_total=100,
    )


def test_metrics_cover_required_statistics() -> None:
    metrics = compute_metrics(_result(), bootstrap_samples=200)

    assert metrics.n_trades == 4
    assert metrics.total_return_pct == pytest.approx(0.02)
    assert metrics.cagr_pct == pytest.approx(0.02)
    assert metrics.win_rate_pct == pytest.approx(0.5)
    assert metrics.profit_factor == pytest.approx(5 / 3)
    assert metrics.expectancy == D("50")
    assert metrics.expectancy_r == pytest.approx(0.5)
    assert metrics.average_win == D("250")
    assert metrics.average_loss == D("-150")
    assert metrics.risk_reward == pytest.approx(5 / 3)
    assert metrics.max_consecutive_losses == 1
    assert metrics.max_drawdown_pct == pytest.approx(100 / 10100)
    assert metrics.exposure_pct == pytest.approx(0.25)
    assert metrics.total_fees == D("4")
    assert metrics.total_net_pnl == D("200")
    assert metrics.statistical_warning == "n<100, 통계적 근거 부족"
    assert metrics.sharpe is not None
    assert metrics.sortino is not None
    assert metrics.t_statistic is not None
    assert metrics.deflated_sharpe_probability is not None
    assert metrics.sharpe_ci_95_low is not None
    assert metrics.sharpe_ci_95_high is not None
    assert metrics.mae_pct.median == pytest.approx(0.025)
    assert metrics.mfe_pct.median == pytest.approx(0.05)


def test_zero_trade_metrics_are_explicitly_undefined_not_infinite() -> None:
    result = BacktestResult(
        symbol="BTCUSDT",
        from_ms=0,
        to_ms=DAY,
        initial_equity=D("10000"),
        final_equity=D("10000"),
    )

    metrics = compute_metrics(result, bootstrap_samples=1)

    assert metrics.n_trades == 0
    assert metrics.profit_factor is None
    assert metrics.risk_reward is None
    assert metrics.sharpe is None
    assert metrics.sortino is None
    assert metrics.sharpe_ci_95_low is None
    assert metrics.sharpe_ci_95_high is None
    assert metrics.turnover == 0.0


@pytest.mark.golden
def test_same_input_has_byte_identical_metric_digest() -> None:
    first = compute_metrics(_result(), trials_count=7, bootstrap_samples=250)
    second = compute_metrics(_result(), trials_count=7, bootstrap_samples=250)

    assert first.to_dict() == second.to_dict()
    assert first.digest() == second.digest()
    assert first.digest() == "8759f82dca821a1ff68a285549f45ea332a181857fc3cbc911771c6fc98b288d"


def test_report_generates_all_artifacts_deterministically(tmp_path: Path) -> None:
    result = _result()
    metrics = compute_metrics(result, bootstrap_samples=50)
    metadata = ReportMetadata(
        run_id="golden-run-001",
        config_hash="cfg123",
        git_sha="abc789",
        split="train",
        trials_count=3,
    )
    regimes = (
        RegimePerformance(
            regime="trend_up/normal_vol",
            n_trades=4,
            total_net_pnl=D("200"),
            average_r=0.5,
            win_rate_pct=0.5,
            warning="n<50, 국면별 판단 보류",
        ),
    )

    artifacts = generate_report(
        result,
        metrics,
        metadata,
        output_root=tmp_path,
        regimes=regimes,
    )
    before = {path.name: path.read_bytes() for path in artifacts.files}
    rerun = generate_report(
        result,
        metrics,
        metadata,
        output_root=tmp_path,
        regimes=regimes,
    )
    after = {path.name: path.read_bytes() for path in rerun.files}

    assert len(artifacts.files) == 13
    assert before == after
    for name in (
        "equity_curve.png",
        "drawdown.png",
        "monthly_returns.png",
        "trade_distribution.png",
        "mae_mfe_scatter.png",
    ):
        assert before[name].startswith(b"\x89PNG\r\n\x1a\n")
    summary = json.loads(before["summary.json"])
    assert summary["config_hash"] == "cfg123"
    assert summary["git_sha"] == "abc789"
    assert summary["data_range"] == {"from_ms": 0, "to_ms": 365 * DAY}
    assert summary["split"] == "train"
    assert summary["n_trades"] == 4


def test_report_rejects_path_traversal_run_id() -> None:
    with pytest.raises(ValueError, match="run_id"):
        ReportMetadata(run_id="../escape", config_hash="x", git_sha="y", split="train")
