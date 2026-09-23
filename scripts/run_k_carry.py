"""K/v1 펀딩 캐리를 사전등록대로 실행·판정한다 (docs/research/10-k-carry-preregistration.md).

사용 예::

    python scripts/run_k_carry.py train      # TRAIN 1회 (2023~2024)
    python scripts/run_k_carry.py validate   # TRAIN PASS 뒤 1회: VALIDATION + STRESS

두 명령 모두 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

from auto_trading.backtest.carry import (
    CarryConfig,
    CarryHour,
    CarryResult,
    FundingEvent,
    annualized,
    judge_carry,
    max_drawdown,
    monthly_returns,
    simulate_carry,
    yearly_returns,
)

DB = Path("data/trading.db")
OUTPUT = Path("reports/k")
INITIAL_EQUITY = Decimal(10_000)
_HOUR = 3_600_000
_DAY = 24 * _HOUR


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


PERIODS = {
    "train": (_ms(2023, 1, 1), _ms(2025, 1, 1) - 1, (2023, 2024)),
    "validation": (_ms(2025, 1, 1), _ms(2026, 9, 1) - 1, (2025, 2026)),
    "stress": (_ms(2020, 1, 1), _ms(2023, 1, 1) - 1, (2020, 2021, 2022)),
}


def load(path: Path) -> tuple[list[CarryHour], list[FundingEvent]]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        perp = conn.execute(
            "SELECT open_ms, open, high, close FROM candles WHERE exchange = 'binance_usdm'"
            " AND symbol = 'BTCUSDT' AND timeframe = '1h' ORDER BY open_ms"
        ).fetchall()
        spot = {
            int(ms): (Decimal(str(o)), Decimal(str(c)))
            for ms, o, c in conn.execute(
                "SELECT open_ms, open, close FROM candles WHERE exchange = 'binance_spot'"
                " AND symbol = 'BTCUSDT' AND timeframe = '1h'"
            )
        }
        rates = conn.execute(
            "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
            " AND symbol = 'BTCUSDT' ORDER BY funding_ms"
        ).fetchall()
    hours = []
    perp_open: dict[int, Decimal] = {}
    for ms, o, h, c in perp:
        s = spot.get(int(ms))
        hour = CarryHour(
            int(ms),
            Decimal(str(o)),
            Decimal(str(h)),
            Decimal(str(c)),
            s[0] if s else None,
            s[1] if s else None,
        )
        hours.append(hour)
        perp_open[int(ms)] = hour.perp_open
    funding = []
    for ms, rate in rates:
        bar = int(ms) - int(ms) % _HOUR
        if bar in perp_open:  # 정산 시각 선물 1h 시가를 mark 가격 대용으로 쓴다 (10 §2)
            funding.append(FundingEvent(int(ms), Decimal(str(rate)), perp_open[bar]))
    return hours, funding


def replay(
    hours: list[CarryHour], funding: list[FundingEvent], period: str, *, control: bool
) -> CarryResult:
    from_ms, to_ms, _ = PERIODS[period]
    history = [f for f in funding if from_ms - 30 * _DAY <= f.funding_ms <= to_ms]
    return simulate_carry(
        hours,
        history,
        from_ms=from_ms,
        to_ms=to_ms,
        initial_equity=INITIAL_EQUITY,
        cfg=CarryConfig(use_filter=not control),
    )


def describe(result: CarryResult) -> dict[str, Any]:
    return {
        **result.summary(),
        "annualized": annualized(result),
        "mdd": max_drawdown(result),
        "yearly": {str(k): v for k, v in yearly_returns(result).items()},
        "monthly": monthly_returns(result),
    }


def evaluate(period: str, hours: list[CarryHour], funding: list[FundingEvent]) -> dict[str, Any]:
    _, _, years = PERIODS[period]
    main = replay(hours, funding, period, control=False)
    control = replay(hours, funding, period, control=True)
    if period == "stress":
        verdict = judge_carry(main, years=years, require_return=False, max_mdd=0.10)
    else:
        verdict = judge_carry(main, years=years)
    print(f"{period}: {verdict.status}", flush=True)
    for name, passed, detail in verdict.criteria:
        print(f"  {name}: {'통과' if passed else '실패'} {detail}", flush=True)
    print(f"  대조군(상시 보유) 연환산 {annualized(control):.4f} MDD {max_drawdown(control):.4f}")
    return {
        "verdict": verdict.to_dict(),
        "main": describe(main),
        "control_always_hold": describe(control),
    }


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run(stage: str, output: Path) -> int:
    path = output / stage / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    if stage == "validate":
        train = output / "train" / "verdict.json"
        if not train.exists():
            print("TRAIN 판정이 없다.")
            return 3
        if json.loads(train.read_text(encoding="utf-8"))["train"]["verdict"]["status"] != "PASS":
            print("TRAIN이 PASS가 아니다. 사전등록상 VALIDATION을 실행하지 않는다.")
            return 3
    hours, funding = load(DB)
    periods = ("train",) if stage == "train" else ("validation", "stress")
    record: dict[str, Any] = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {name: str(value) for name, value in asdict(CarryConfig()).items()},
        "initial_equity": str(INITIAL_EQUITY),
    }
    for period in periods:
        record[period] = evaluate(period, hours, funding)
    _write(path, record)
    print(f"기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("train", "validate"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return run(args.stage, args.output)


if __name__ == "__main__":
    sys.exit(main())
