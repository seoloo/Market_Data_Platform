"""F — K/v1 전향 관찰 (docs/research/12-f-k-forward-registration.md).

K/v1 규칙(`backtest/carry.py` 기본값, 커밋 d8ec352)을 **바꾸지 않고** 2026-09-01 이후에만 적용한다.
전향 데이터가 12개월(2027-08-31까지) 쌓이기 전에는 보고만 하고 판정하지 않는다.

매달 데이터를 갱신한 뒤 실행한다::

    python scripts/backfill_funding.py
    python scripts/backfill.py --timeframes 1h
    python scripts/backfill_spot.py --start <지난달 1일> --end <지난달 말일>
    python scripts/run_k_forward.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_k_carry import DB, INITIAL_EQUITY, describe, load

from auto_trading.backtest.carry import CarryConfig, judge_carry, simulate_carry

OUTPUT = Path("reports/k/forward")
FORWARD_FROM = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
JUDGE_FROM = int(datetime(2027, 9, 1, tzinfo=UTC).timestamp() * 1000)
"""전향 12개월이 끝나는 시각. 이 뒤의 데이터가 있어야 판정한다."""
_DAY = 86_400_000


def run(output: Path) -> int:
    sha, dirty = git_state()
    hours, funding = load(DB)
    last_funding = max((f.funding_ms for f in funding), default=0)
    last_spot = max((h.open_ms for h in hours if h.spot_open is not None), default=0)
    to_ms = min(last_funding, last_spot)
    if to_ms <= FORWARD_FROM:
        print("전향 구간 데이터가 아직 없다 (funding·현물 모두 2026-09 이후가 필요).")
        return 0
    result = simulate_carry(
        hours,
        [f for f in funding if FORWARD_FROM - 30 * _DAY <= f.funding_ms <= to_ms],
        from_ms=FORWARD_FROM,
        to_ms=to_ms,
        initial_equity=INITIAL_EQUITY,
        cfg=CarryConfig(),
    )
    record: dict[str, Any] = {
        "git_sha": sha,
        "dirty": dirty,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "from_ms": FORWARD_FROM,
        "to_ms": to_ms,
        "report": describe(result),
    }
    if to_ms >= JUDGE_FROM - 1:
        years = sorted({datetime.fromtimestamp(ms / 1000, tz=UTC).year for ms, _ in result.equity})
        record["verdict"] = judge_carry(result, years=years).to_dict()
        print(f"판정: {record['verdict']['status']}")
    else:
        print("전향 12개월 전: 보고만 한다 (판정 없음).")
    stamp = datetime.fromtimestamp(to_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
    path = output / f"{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"기록: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(run(OUTPUT))
