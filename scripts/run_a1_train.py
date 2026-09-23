"""A1/v1 TRAIN을 사전등록대로 **한 번** 실행하고 판정한다 (02 §7·§11).

사용 예::

    python scripts/run_a1_train.py smoke --days 7   # 달력 미주입 → 진입 0건, 속도만 측정
    python scripts/run_a1_train.py run              # 실제 TRAIN 1회 (사용자 확인 후)

``run``의 순서:

1. 작업 트리가 깨끗하고 이전 판정 파일이 없어야 한다 (TRAIN은 1회).
2. DB의 ``macro_events``가 G6 매니페스트와 정확히 같아야 달력을 만든다.
3. 주 arm과 대조군(수축 조건 제거)을 같은 엔진·데이터·달력으로 재생한다.
4. **주 arm 거래 수를 먼저** 본다. 146건 미만이면 손익 통계 없이 ``DATA_INSUFFICIENT``.
5. 판정·메타데이터·원거래를 ``reports/a1/train/``에 남긴다.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.build_g6_calendar import FROM_MS, MANIFEST, TO_MS

from auto_trading.backtest.a1_verdict import judge, records_from_trades
from auto_trading.backtest.calendar import MacroCalendar
from auto_trading.backtest.calendar_manifest import CalendarManifest
from auto_trading.backtest.engine import BacktestEngine, BacktestResult, ExchangeFilters
from auto_trading.backtest.feed import CandleFeed
from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.config.loader import LoadedConfig, compute_config_hash, load_config
from auto_trading.config.schema import RootConfig
from auto_trading.core.macro import ScheduledMacroEvent
from auto_trading.core.types import FundingRate
from auto_trading.storage.db import Database
from auto_trading.storage.repositories.candles import CandleRepository
from auto_trading.storage.repositories.funding import FundingRateRepository
from auto_trading.storage.repositories.macro_events import MacroEventRepository

OUTPUT = Path("reports/a1/train")
INITIAL_EQUITY = Decimal("10000")
_DAY_MS = 86_400_000


def configs() -> tuple[LoadedConfig, RootConfig, RootConfig]:
    """(적재 정보, 주 arm, 대조군). 대조군은 수축 조건만 제거한다 (02 §4)."""
    loaded = load_config("backtest", strategy="a1_v1", apply_env=False)
    main = loaded.config
    if main.a1 is None:  # pragma: no cover - schema 가 보장한다
        raise ValueError("A1 설정이 없다")
    control = main.model_copy(
        update={"a1": main.a1.model_copy(update={"control_without_contraction": True})}
    )
    return loaded, main, control


def verify_calendar(
    manifest: CalendarManifest, stored: Iterable[ScheduledMacroEvent]
) -> MacroCalendar:
    """DB에서 읽은 일정이 매니페스트와 정확히 같을 때만 달력을 만든다."""
    rows = tuple(stored)
    if set(rows) != set(manifest.events()) or len(rows) != len(manifest.entries):
        raise ValueError("DB의 macro_events가 G6 매니페스트와 다르다. TRAIN을 실행하지 않는다")
    return MacroCalendar(rows, manifest.coverage())


def git_state() -> tuple[str, bool]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return sha, bool(dirty)


def _load_inputs(
    cfg: RootConfig, from_ms: int, to_ms: int
) -> tuple[CandleFeed, tuple[ScheduledMacroEvent, ...], tuple[FundingRate, ...]]:
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
        events = MacroEventRepository(database.connection).schedule_revisions(
            indicators=frozenset(cfg.risk.blackout), as_of_ms=to_ms
        )
    return feed, events, funding


def _write_trades(path: Path, trades: list[BacktestTrade]) -> None:
    fields = (
        "side",
        "entry_ms",
        "exit_ms",
        "close_reason",
        "entry_reference_price",
        "exit_reference_price",
        "entry_price",
        "exit_price",
        "qty",
        "pre_cost_bps",
        "net_bps",
        "fees",
        "funding_paid",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(fields)
        for t in trades:
            writer.writerow(
                (
                    t.side.value,
                    t.entry_ms,
                    t.exit_ms,
                    t.close_reason.value,
                    t.entry_reference_price,
                    t.exit_reference_price,
                    t.entry_price,
                    t.exit_price,
                    t.qty,
                    t.pre_cost_bps,
                    t.net_bps,
                    t.fees,
                    t.funding_paid,
                )
            )


def _run_arm(
    name: str,
    cfg: RootConfig,
    feed: CandleFeed,
    funding: tuple[FundingRate, ...],
    calendar: MacroCalendar | None,
    from_ms: int,
    to_ms: int,
) -> BacktestResult:
    started = time.monotonic()
    engine = BacktestEngine(cfg, feed, funding=funding, macro_calendar=calendar)
    result = engine.run(from_ms=from_ms, to_ms=to_ms, initial_equity=INITIAL_EQUITY)
    print(f"  {name}: 평가 {result.decisions_evaluated:,}회, {time.monotonic() - started:,.0f}초")
    return result


def smoke(days: int) -> int:
    """달력을 주입하지 않아 G6가 모든 진입을 막는다. 거래 0건을 확인하고 속도만 잰다."""
    _, main, _ = configs()
    to_ms = FROM_MS + days * _DAY_MS - 1
    feed, _, funding = _load_inputs(main, FROM_MS, to_ms)
    result = _run_arm("smoke(주 arm, 달력 없음)", main, feed, funding, None, FROM_MS, to_ms)
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

    loaded, main, control = configs()
    manifest = CalendarManifest.from_dict(json.loads(MANIFEST.read_text(encoding="utf-8")))
    feed, events, funding = _load_inputs(main, FROM_MS, TO_MS)
    calendar = verify_calendar(manifest, events)
    print(f"git={sha[:12]} config={loaded.hash[:12]} manifest={manifest.digest()[:12]}")
    print(
        f"TRAIN {datetime.fromtimestamp(FROM_MS / 1000, tz=UTC)} ~ "
        f"{datetime.fromtimestamp(TO_MS / 1000, tz=UTC)}"
    )

    main_result = _run_arm("주 arm", main, feed, funding, calendar, FROM_MS, TO_MS)
    control_result = _run_arm("대조군", control, feed, funding, calendar, FROM_MS, TO_MS)
    print(f"주 arm 거래 {len(main_result.trades)}건 / 대조군 {len(control_result.trades)}건")

    verdict = judge(
        records_from_trades(main_result.trades), records_from_trades(control_result.trades)
    )
    output.mkdir(parents=True, exist_ok=True)
    _write_trades(output / "trades_main.csv", main_result.trades)
    _write_trades(output / "trades_control.csv", control_result.trades)
    record = {
        "verdict": verdict.to_dict(),
        "git_sha": sha,
        "config_hash_main": loaded.hash,
        "config_hash_control": compute_config_hash(control),
        "manifest_digest": manifest.digest(),
        "from_ms": FROM_MS,
        "to_ms": TO_MS,
        "initial_equity": str(INITIAL_EQUITY),
        "filters": {key: str(value) for key, value in asdict(ExchangeFilters()).items()},
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "arms": {
            name: {
                "decisions_evaluated": result.decisions_evaluated,
                "entries_signalled": result.entries_signalled,
                "veto_counts": dict(sorted(result.veto_counts.items())),
            }
            for name, result in (("main", main_result), ("control", control_result))
        },
    }
    verdict_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"판정: {verdict.status}  ({verdict_path})")
    for criterion in verdict.criteria:
        print(f"  {criterion.name}: {'통과' if criterion.passed else '실패'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    smoke_cmd = sub.add_parser("smoke", help="달력 미주입 속도 측정 (거래 0건 확인)")
    smoke_cmd.add_argument("--days", type=int, default=7)
    run_cmd = sub.add_parser("run", help="실제 TRAIN 1회")
    run_cmd.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.command == "smoke":
        return smoke(args.days)
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
