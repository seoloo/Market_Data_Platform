"""가용 TRAIN 데이터에서 다중 horizon edge table 을 생성한다."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from auto_trading.backtest.edge import (
    EdgeCollection,
    collect_edge_observations,
    diagnose_edge_observations,
    estimate_edge_table,
)
from auto_trading.backtest.feed import CandleFeed, FeedWindow
from auto_trading.config.loader import load_config
from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import Mode, PositionSide
from auto_trading.features.registry import build_feature_set
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.candles import CandleRepository
from auto_trading.strategy.cost import estimate_cost

stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
if stdout_reconfigure is not None:
    stdout_reconfigure(encoding="utf-8", errors="replace")
if stderr_reconfigure is not None:
    stderr_reconfigure(encoding="utf-8", errors="replace")


def _utc_ms(text: str, *, end: bool = False) -> int:
    parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
    value = int(parsed.timestamp() * 1000)
    return value + 86_400_000 - 1 if end else value


def _positive_csv(text: str) -> tuple[int, ...]:
    values = tuple(int(part.strip()) for part in text.split(",") if part.strip())
    if not values or any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("양의 정수를 쉼표로 구분해야 합니다")
    return values


def _windows(cfg: RootConfig) -> tuple[FeedWindow, ...]:
    warmups = build_feature_set(cfg.features).warmup_by_timeframe()
    safety = cfg.data.warmup_safety_bars
    return tuple(
        FeedWindow(timeframe=timeframe, bars=bars + safety)
        for timeframe, bars in sorted(warmups.items(), key=lambda item: item[0].ms)
    )


def _collect_chunk(
    from_ms: int,
    to_ms: int,
    train_to_ms: int,
    horizons_bars: tuple[int, ...],
) -> EdgeCollection:
    loaded = load_config(Mode.BACKTEST, apply_env=False, include_edge_table=False)
    cfg = loaded.config
    label_to_ms = min(
        train_to_ms,
        to_ms + max(horizons_bars) * cfg.features.t2_momentum.timeframe.ms,
    )
    with Database(cfg.data.db_path, read_only=True) as database:
        repository = CandleRepository(database.connection, exchange=cfg.exchange)
        feed = CandleFeed.load(
            repository,
            cfg.symbol,
            _windows(cfg),
            from_ms=from_ms,
            to_ms=label_to_ms,
        )
    return collect_edge_observations(
        cfg,
        feed,
        from_ms=from_ms,
        to_ms=to_ms,
        horizons_bars=horizons_bars,
        label_to_ms=label_to_ms,
    )


def _chunks(from_ms: int, to_ms: int, *, days: int = 92) -> tuple[tuple[int, int], ...]:
    if days <= 0:
        raise ValueError("days 는 1 이상이어야 합니다")
    chunks: list[tuple[int, int]] = []
    cursor = from_ms
    span_ms = days * 86_400_000
    while cursor <= to_ms:
        end = min(to_ms, cursor + span_ms - 1)
        chunks.append((cursor, end))
        cursor = end + 1
    return tuple(chunks)


def _collect_parallel(
    from_ms: int,
    to_ms: int,
    horizons_bars: tuple[int, ...],
    workers: int,
) -> EdgeCollection:
    chunks = _chunks(from_ms, to_ms)
    if workers == 1 or len(chunks) == 1:
        return _collect_chunk(from_ms, to_ms, to_ms, horizons_bars)

    collected: list[EdgeCollection] = []
    with ProcessPoolExecutor(max_workers=min(workers, len(chunks))) as executor:
        futures = {
            executor.submit(_collect_chunk, start, end, to_ms, horizons_bars): (start, end)
            for start, end in chunks
        }
        for future in as_completed(futures):
            start, end = futures[future]
            collected.append(future.result())
            print(f"chunk_complete={start}:{end}")
    observations = tuple(
        sorted(
            (item for result in collected for item in result.observations),
            key=lambda item: (item.as_of_ms, item.horizon_bars),
        )
    )
    return EdgeCollection(
        observations=observations,
        decisions_evaluated=sum(result.decisions_evaluated for result in collected),
        signals_observed=sum(result.signals_observed for result in collected),
        labels_skipped=sum(result.labels_skipped for result in collected),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, help="TRAIN 시작일 UTC (YYYY-MM-DD)")
    parser.add_argument("--to-date", required=True, help="TRAIN 종료일 UTC (YYYY-MM-DD)")
    parser.add_argument(
        "--horizons-bars",
        type=_positive_csv,
        default=(24, 72, 144, 288),
        help="5분봉 기준 horizon. 기본값 24,72,144,288 = 2h,6h,12h,24h",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/phase4/train_edge_table.json"),
    )
    parser.add_argument(
        "--config-output",
        type=Path,
        help="전략이 자동 로드할 edge_table YAML 경로 (예: config/edge_table.yaml)",
    )
    parser.add_argument(
        "--diagnostics-output",
        type=Path,
        help="24시간 비중첩 신호와 원 신호를 비교한 TRAIN 진단 JSON 경로",
    )
    parser.add_argument("--workers", type=int, default=1, help="92일 단위 병렬 프로세스 수")
    args = parser.parse_args()

    loaded = load_config(Mode.BACKTEST, apply_env=False, include_edge_table=False)
    cfg = loaded.config
    from_ms = _utc_ms(args.from_date)
    to_ms = _utc_ms(args.to_date, end=True)
    if to_ms <= from_ms:
        parser.error("--to-date 는 --from-date 보다 뒤여야 합니다")
    if args.workers <= 0:
        parser.error("--workers 는 1 이상이어야 합니다")

    collection = _collect_parallel(
        from_ms,
        to_ms,
        args.horizons_bars,
        args.workers,
    )
    table = estimate_edge_table(
        collection.observations,
        symbol=cfg.symbol,
        source_config_hash=loaded.hash,
        train_from_ms=from_ms,
        train_to_ms=to_ms,
        decision_timeframe=cfg.features.t2_momentum.timeframe,
        horizons_bars=args.horizons_bars,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(table.to_json(), encoding="utf-8")
    if args.config_output is not None:
        config_payload = table.payload()
        config_payload["digest"] = table.digest
        config_payload["aggregation"] = "all_horizons_min"
        args.config_output.parent.mkdir(parents=True, exist_ok=True)
        args.config_output.write_text(
            yaml.safe_dump(
                {"edge_table": config_payload},
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    if args.diagnostics_output is not None:
        fixed_cost = estimate_cost(
            cfg.risk.cost,
            side=PositionSide.LONG,
            funding_rate=Decimal(0),
        ).total_bps
        required_edge = fixed_cost + Decimal(str(cfg.policy.minimum_required_edge_bps))
        diagnostics = diagnose_edge_observations(
            collection.observations,
            table,
            embargo_bars=max(args.horizons_bars),
            required_edge_bps=required_edge,
        )
        args.diagnostics_output.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostics_output.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"config={loaded.hash[:12]} table={table.digest[:12]}")
    print(
        f"evaluated={collection.decisions_evaluated:,} signals={collection.signals_observed:,} "
        f"observations={len(collection.observations):,} "
        f"labels_skipped={collection.labels_skipped:,}"
    )
    print(f"buckets={len(table.buckets):,} output={args.output}")
    if args.config_output is not None:
        print(f"config_output={args.config_output}")
    if args.diagnostics_output is not None:
        print(f"diagnostics_output={args.diagnostics_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
