"""WA/v1 TRAIN을 사전등록대로 **한 번** 실행하고 판정한다 (08 §5·§9).

사용 예::

    python scripts/run_wa_train.py smoke --days 7   # 전체 규칙·달력 없음 → 진입 0건, 속도만 측정
    python scripts/run_wa_train.py run              # 실제 TRAIN 1회

``run``의 순서:

1. 작업 트리가 깨끗하고 이전 판정 파일이 없어야 한다 (TRAIN은 1회).
2. 우위 측정 모드(계좌 정지·G6 생략, BACKTEST 전용)로 주 arm 롱·숏, 대조군 롱·숏을 같은
   데이터로 재생한다. 숏은 숏 주문·손절·비용으로 따로 재생한다.
3. 거래 수 관문 → 방향 → T1~T4·안전 순으로 판정한다 (`backtest/wa_verdict.py`).
4. 전체 규칙 계좌 재생은 2023~2024 G6 달력이 없어 ``NOT_RUN``으로 기록한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.run_a1_train import _write_trades, git_state

from auto_trading.backtest.a1_verdict import records_from_trades
from auto_trading.backtest.engine import BacktestEngine, BacktestResult, ExchangeFilters
from auto_trading.backtest.feed import CandleFeed
from auto_trading.backtest.wa_verdict import judge_wa
from auto_trading.config.loader import compute_config_hash, load_config
from auto_trading.config.schema import RootConfig
from auto_trading.core.types import FundingRate
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.candles import CandleRepository
from auto_trading.storage.repositories.funding import FundingRateRepository

OUTPUT = Path("reports/wa/train")
INITIAL_EQUITY = Decimal("10000")
# 08 §5: TRAIN 2023-01-01 00:00 ~ 2024-12-31 23:59:59.999 UTC
FROM_MS = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
TO_MS = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1
_DAY_MS = 86_400_000
ARMS = (
    ("main_long", "long", False),
    ("main_short", "short", False),
    ("control_long", "long", True),
    ("control_short", "short", True),
)


def wa_config(direction: str, control: bool) -> RootConfig:
    """기본 백테스트 설정 위에 WA/v1 고정 규칙만 얹는다 (H1 edge table은 쓰지 않는다)."""
    data = load_config("backtest", apply_env=False, include_edge_table=False).config.model_dump()
    data.update(
        strategy_id="wa_v1",
        wa={"direction": direction, "control_without_state": control},
    )
    data["risk"]["tight_stop_action"] = "REDUCE_POSITION"
    data["risk"]["take_profit"]["enabled"] = False
    return RootConfig.model_validate(data)


def load_inputs(
    cfg: RootConfig, from_ms: int, to_ms: int
) -> tuple[CandleFeed, tuple[FundingRate, ...]]:
    with Database(cfg.data.db_path, read_only=True) as database:
        feed = CandleFeed.load(
            CandleRepository(database.connection, exchange=cfg.exchange),
            cfg.symbol,
            BacktestEngine.feed_windows(cfg),
            from_ms=from_ms,
            to_ms=to_ms,
        )
        funding = FundingRateRepository(database.connection, exchange=cfg.exchange).get_range(
            cfg.symbol, from_ms - _DAY_MS, to_ms
        )
    return feed, funding


def replay(
    name: str,
    cfg: RootConfig,
    feed: CandleFeed,
    funding: tuple[FundingRate, ...],
    *,
    research: bool,
    from_ms: int,
    to_ms: int,
) -> BacktestResult:
    started = time.monotonic()
    engine = BacktestEngine(cfg, feed, funding=funding, research_edge_mode=research)
    result = engine.run(from_ms=from_ms, to_ms=to_ms, initial_equity=INITIAL_EQUITY)
    elapsed = time.monotonic() - started
    print(f"  {name}: 평가 {result.decisions_evaluated:,}회, {elapsed:,.0f}초", flush=True)
    return result


def smoke(days: int) -> int:
    """전체 규칙·달력 없음 → G6가 모든 진입을 막는다. 거래 0건을 확인하고 속도만 잰다."""
    cfg = wa_config("long", control=False)
    to_ms = FROM_MS + days * _DAY_MS - 1
    feed, funding = load_inputs(cfg, FROM_MS, to_ms)
    result = replay("smoke", cfg, feed, funding, research=False, from_ms=FROM_MS, to_ms=to_ms)
    if result.trades:
        raise AssertionError("달력 없이 거래가 발생했다. G6 fail-closed가 깨졌다")
    return 0


def run(output: Path) -> int:
    verdict_path = output / "verdict.json"
    if verdict_path.exists():
        print(f"이미 판정이 있다: {verdict_path}. TRAIN은 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 TRAIN을 실행한다.")
        return 3

    configs = {name: wa_config(direction, control) for name, direction, control in ARMS}
    feed, funding = load_inputs(configs["main_long"], FROM_MS, TO_MS)
    print(f"git={sha[:12]} TRAIN {FROM_MS}~{TO_MS} (우위 측정 모드)", flush=True)
    results = {
        name: replay(
            name, configs[name], feed, funding, research=True, from_ms=FROM_MS, to_ms=TO_MS
        )
        for name, _, _ in ARMS
    }
    counts = {name: len(result.trades) for name, result in results.items()}
    print(f"거래 수 {counts}", flush=True)

    records = {name: records_from_trades(result.trades) for name, result in results.items()}
    verdict = judge_wa(
        records["main_long"],
        records["main_short"],
        records["control_long"],
        records["control_short"],
    )
    output.mkdir(parents=True, exist_ok=True)
    for name, result in results.items():
        _write_trades(output / f"trades_{name}.csv", result.trades)
    record = {
        "verdict": verdict.to_dict(),
        "git_sha": sha,
        "config_hashes": {name: compute_config_hash(cfg) for name, cfg in configs.items()},
        "from_ms": FROM_MS,
        "to_ms": TO_MS,
        "initial_equity": str(INITIAL_EQUITY),
        "filters": {key: str(value) for key, value in asdict(ExchangeFilters()).items()},
        "research_edge_mode": True,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "arms": {
            name: {
                "decisions_evaluated": result.decisions_evaluated,
                "entries_signalled": result.entries_signalled,
                "veto_counts": dict(sorted(result.veto_counts.items())),
                "close_reasons": dict(sorted(result.close_reasons.items())),
            }
            for name, result in results.items()
        },
    }
    verdict_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"판정: {verdict.status} 방향={verdict.direction} ({verdict_path})")
    for criterion in verdict.criteria:
        print(f"  {criterion.name}: {'통과' if criterion.passed else '실패'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    smoke_cmd = sub.add_parser("smoke", help="전체 규칙·달력 없음 속도 측정 (거래 0건 확인)")
    smoke_cmd.add_argument("--days", type=int, default=7)
    run_cmd = sub.add_parser("run", help="실제 TRAIN 1회")
    run_cmd.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.command == "smoke":
        return smoke(args.days)
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
