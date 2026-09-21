"""로컬 SQLite 실데이터를 이벤트 기반 엔진으로 재생하고 리포트를 생성한다."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from auto_trading.backtest.engine import BacktestEngine
from auto_trading.backtest.feed import CandleFeed
from auto_trading.backtest.metrics import compute_metrics
from auto_trading.backtest.report import ReportMetadata, generate_report
from auto_trading.config.loader import load_config
from auto_trading.core.enums import Mode, Timeframe
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.candles import CandleRepository
from auto_trading.storage.repositories.funding import FundingRateRepository

_DAY_MS = 86_400_000

stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
if stdout_reconfigure is not None:
    stdout_reconfigure(encoding="utf-8", errors="replace")
if stderr_reconfigure is not None:
    stderr_reconfigure(encoding="utf-8", errors="replace")


def _utc_ms(text: str, *, end: bool = False) -> int:
    parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    value = int(parsed.timestamp() * 1000)
    return value + _DAY_MS - 1 if end else value


def _positive_decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("정확한 양수 Decimal이어야 합니다") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("0보다 커야 합니다")
    return value


def _git_version() -> str:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return f"{sha}-dirty" if dirty else sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, help="재생 시작일 UTC (YYYY-MM-DD)")
    parser.add_argument("--to-date", required=True, help="재생 종료일 UTC (YYYY-MM-DD)")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--initial-equity", type=_positive_decimal, default=Decimal("10000"))
    parser.add_argument("--output-root", type=Path, default=Path("reports/phase4"))
    parser.add_argument(
        "--edge-table",
        type=Path,
        help="기본 config/edge_table.yaml 대신 사용할 YAML",
    )
    args = parser.parse_args()

    from_ms = _utc_ms(args.from_date)
    to_ms = _utc_ms(args.to_date, end=True)
    if to_ms <= from_ms:
        parser.error("--to-date 는 --from-date 보다 뒤여야 합니다")

    loaded = load_config(
        Mode.BACKTEST,
        apply_env=False,
        edge_table_path=args.edge_table,
    )
    cfg = loaded.config
    if cfg.edge_table is None:
        parser.error("edge table이 없습니다. 추정기를 먼저 실행해야 합니다")

    with Database(cfg.data.db_path, read_only=True) as database:
        candle_repository = CandleRepository(database.connection, exchange=cfg.exchange)
        funding_repository = FundingRateRepository(database.connection, exchange=cfg.exchange)
        feed = CandleFeed.load(
            candle_repository,
            cfg.symbol,
            BacktestEngine.feed_windows(cfg),
            from_ms=from_ms,
            to_ms=to_ms,
        )
        funding = funding_repository.get_range(
            cfg.symbol,
            from_ms - _DAY_MS,
            to_ms,
        )

    print(
        f"config={loaded.hash[:12]} edge={cfg.edge_table.digest[:12]} "
        f"minutes={feed.count(Timeframe.M1):,} funding={len(funding):,}"
    )
    result = BacktestEngine(cfg, feed, funding=funding).run(
        from_ms=from_ms,
        to_ms=to_ms,
        initial_equity=args.initial_equity,
    )
    metrics = compute_metrics(result)
    artifacts = generate_report(
        result,
        metrics,
        ReportMetadata(
            run_id=args.run_id,
            config_hash=loaded.hash,
            git_sha=_git_version(),
            split="train",
        ),
        output_root=args.output_root,
    )
    print(result.summary())
    print(f"metrics={metrics.digest()} report={artifacts.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
