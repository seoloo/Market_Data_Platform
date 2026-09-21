"""백테스트 한 실행의 기계 판독/사람 판독 리포트를 생성한다."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from auto_trading.backtest.charts import (
    write_bar_chart,
    write_histogram,
    write_line_chart,
    write_scatter,
)
from auto_trading.backtest.metrics import BacktestMetrics
from auto_trading.backtest.regimes import RegimePerformance
from auto_trading.core.money import ZERO

if TYPE_CHECKING:
    from auto_trading.backtest.engine import BacktestResult

__all__ = ["ReportArtifacts", "ReportMetadata", "generate_report"]

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REPORT_FILES = (
    "summary.json",
    "summary.md",
    "equity_curve.png",
    "drawdown.png",
    "monthly_returns.png",
    "trade_distribution.png",
    "mae_mfe_scatter.png",
    "regime_table.md",
    "correlation_matrix.md",
    "baselines.md",
    "robustness.md",
    "veto_breakdown.md",
    "trades.csv",
)


@dataclass(frozen=True, slots=True)
class ReportMetadata:
    """재현성을 고정하는 실행 메타데이터."""

    run_id: str
    config_hash: str
    git_sha: str
    split: str
    trials_count: int = 1

    def __post_init__(self) -> None:
        if not _RUN_ID_PATTERN.fullmatch(self.run_id):
            raise ValueError(f"안전하지 않은 run_id: {self.run_id!r}")
        if not self.config_hash:
            raise ValueError("config_hash 는 비어 있을 수 없다")
        if not self.git_sha:
            raise ValueError("git_sha 는 비어 있을 수 없다")
        if not self.split:
            raise ValueError("split 은 비어 있을 수 없다")
        if self.trials_count < 1:
            raise ValueError(f"trials_count 는 1 이상이어야 한다: {self.trials_count}")


@dataclass(frozen=True, slots=True)
class ReportArtifacts:
    directory: Path
    files: tuple[Path, ...]


def generate_report(
    result: BacktestResult,
    metrics: BacktestMetrics,
    metadata: ReportMetadata,
    *,
    output_root: Path = Path("reports"),
    regimes: tuple[RegimePerformance, ...] = (),
    correlation_markdown: str | None = None,
    baselines_markdown: str | None = None,
    robustness_markdown: str | None = None,
) -> ReportArtifacts:
    """문서 명세의 리포트 파일 13개를 모두 생성한다.

    아직 계산하지 않은 Phase 3 후속 항목은 빈 파일 대신 명시적인 ``미실행`` 문서를
    남긴다. 이로써 리포트 소비자는 누락과 실패를 구분할 수 있다.
    """
    directory = output_root / metadata.run_id
    directory.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": metadata.run_id,
        "config_hash": metadata.config_hash,
        "git_sha": metadata.git_sha,
        "data_range": {"from_ms": result.from_ms, "to_ms": result.to_ms},
        "split": metadata.split,
        "trials_count": metadata.trials_count,
        "symbol": result.symbol,
        "n_trades": metrics.n_trades,
        "decisions_evaluated": result.decisions_evaluated,
        "entries_signalled": result.entries_signalled,
        "close_reasons": dict(sorted(result.close_reasons.items())),
        "veto_counts": dict(sorted(result.veto_counts.items())),
        "metrics": metrics.to_dict(),
        "regimes": [regime.to_dict() for regime in regimes],
    }
    _write_text(
        directory / "summary.json",
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False),
    )
    _write_text(directory / "summary.md", _summary_markdown(result, metrics, metadata))
    _write_text(directory / "regime_table.md", _regime_markdown(regimes))
    _write_text(
        directory / "correlation_matrix.md",
        correlation_markdown or _pending("Feature 상관행렬", "Phase 3 feature 기록 연결 후 계산"),
    )
    _write_text(
        directory / "baselines.md",
        baselines_markdown or _pending("기준선 비교", "buy&hold·랜덤·단일 feature 미실행"),
    )
    _write_text(
        directory / "robustness.md",
        robustness_markdown or _pending("강건성 테스트", "비용 2배·섭동·지연·셔플 미실행"),
    )
    _write_text(directory / "veto_breakdown.md", _veto_markdown(result))
    _write_trades(directory / "trades.csv", result)
    _write_charts(directory, result, metrics)

    files = tuple(directory / name for name in _REPORT_FILES)
    missing = [path.name for path in files if not path.is_file()]
    if missing:  # pragma: no cover - 생성기 결함 방어
        raise RuntimeError(f"리포트 파일 생성 누락: {missing}")
    return ReportArtifacts(directory=directory, files=files)


def _summary_markdown(
    result: BacktestResult, metrics: BacktestMetrics, metadata: ReportMetadata
) -> str:
    warning = metrics.statistical_warning or "없음"
    lines = [
        f"# 백테스트 요약 — {metadata.run_id}",
        "",
        "## 재현성",
        "",
        f"- 심볼: `{result.symbol}`",
        f"- 구간: `{result.from_ms}` ~ `{result.to_ms}`",
        f"- 분할: `{metadata.split}`",
        f"- config hash: `{metadata.config_hash}`",
        f"- git SHA: `{metadata.git_sha}`",
        f"- metrics digest: `{metrics.digest()}`",
        "",
        "## 핵심 지표",
        "",
        "| 지표 | 값 |",
        "|---|---:|",
        f"| 거래 수 | {metrics.n_trades} |",
        f"| 총수익률 | {_pct(metrics.total_return_pct)} |",
        f"| CAGR | {_optional_pct(metrics.cagr_pct)} |",
        f"| 승률 | {_pct(metrics.win_rate_pct)} |",
        f"| 평균 R | {metrics.expectancy_r:.4f} |",
        f"| Profit Factor | {_optional_number(metrics.profit_factor)} |",
        f"| Sharpe | {_optional_number(metrics.sharpe)} |",
        f"| Sortino | {_optional_number(metrics.sortino)} |",
        f"| Max Drawdown | {_pct(metrics.max_drawdown_pct)} |",
        f"| Exposure | {_pct(metrics.exposure_pct)} |",
        f"| Cost / Gross Profit | {_optional_pct(metrics.cost_to_gross_profit)} |",
        f"| 강제청산 | {metrics.liquidations} |",
        "",
        f"> 경고: {warning}",
    ]
    if metrics.liquidations:
        lines.extend(("", "> **실격:** 강제청산이 한 번 이상 발생했습니다."))
    return "\n".join(lines)


def _regime_markdown(regimes: tuple[RegimePerformance, ...]) -> str:
    lines = [
        "# 국면별 성과",
        "",
        "| 국면 | 거래 수 | 순손익 | 평균 R | 승률 | 판단 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    if not regimes:
        lines.append("| 미분류 | 0 | 0 | 0 | 0% | 일봉 국면 입력 없음 |")
    for regime in regimes:
        lines.append(
            f"| {regime.regime} | {regime.n_trades} | {regime.total_net_pnl} | "
            f"{regime.average_r:.4f} | {_pct(regime.win_rate_pct)} | "
            f"{regime.warning or '표본 기준 충족'} |"
        )
    return "\n".join(lines)


def _veto_markdown(result: BacktestResult) -> str:
    total = sum(result.veto_counts.values())
    lines = [
        "# 진입 거부 사유",
        "",
        "| 사유 | 횟수 | 비중 |",
        "|---|---:|---:|",
    ]
    if not result.veto_counts:
        lines.append("| 없음 | 0 | 0% |")
    for reason, count in sorted(result.veto_counts.items(), key=lambda item: (-item[1], item[0])):
        ratio = count / total if total else 0.0
        lines.append(f"| `{reason}` | {count} | {_pct(ratio)} |")
    return "\n".join(lines)


def _pending(title: str, detail: str) -> str:
    return f"# {title}\n\n상태: **미실행**\n\n{detail}."


def _write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def _write_trades(path: Path, result: BacktestResult) -> None:
    fields = (
        "symbol",
        "side",
        "entry_ms",
        "exit_ms",
        "entry_price",
        "exit_price",
        "qty",
        "close_reason",
        "gross_pnl",
        "fees",
        "funding_paid",
        "net_pnl",
        "planned_risk",
        "r_multiple",
        "leverage",
        "holding_ms",
        "mae",
        "mfe",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(
                {
                    "symbol": trade.symbol,
                    "side": trade.side.value,
                    "entry_ms": trade.entry_ms,
                    "exit_ms": trade.exit_ms,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "qty": trade.qty,
                    "close_reason": trade.close_reason.value,
                    "gross_pnl": trade.gross_pnl,
                    "fees": trade.fees,
                    "funding_paid": trade.funding_paid,
                    "net_pnl": trade.net_pnl,
                    "planned_risk": trade.planned_risk,
                    "r_multiple": trade.r_multiple,
                    "leverage": trade.leverage,
                    "holding_ms": trade.holding_ms,
                    "mae": trade.mae,
                    "mfe": trade.mfe,
                }
            )


def _write_charts(directory: Path, result: BacktestResult, metrics: BacktestMetrics) -> None:
    equities = [float(equity) for _, equity in result.equity_curve]
    write_line_chart(directory / "equity_curve.png", equities)
    write_line_chart(
        directory / "drawdown.png",
        _drawdown_curve(result.equity_curve),
        color=(196, 62, 62),
        zero_line=True,
    )
    write_bar_chart(
        directory / "monthly_returns.png",
        [value for _, value in metrics.monthly_returns],
    )
    write_histogram(
        directory / "trade_distribution.png",
        [float(trade.r_multiple) for trade in result.trades],
    )
    write_scatter(
        directory / "mae_mfe_scatter.png",
        [
            (float(trade.mae / trade.entry_price), float(trade.mfe / trade.entry_price))
            for trade in result.trades
            if trade.entry_price > ZERO
        ],
    )


def _drawdown_curve(curve: list[tuple[int, Decimal]]) -> list[float]:
    drawdowns: list[float] = []
    peak = ZERO
    for _, equity in curve:
        peak = max(peak, equity)
        drawdowns.append(float(equity / peak - 1) if peak > ZERO else 0.0)
    return drawdowns


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _optional_pct(value: float | None) -> str:
    return "N/A" if value is None else _pct(value)


def _optional_number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"
