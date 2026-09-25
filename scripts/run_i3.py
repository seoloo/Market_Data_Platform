"""I3 MACD 히스토그램 롱숏 무기한 (docs/research/47).

F1b(45)와 같은 자료·시뮬레이터·판정 기준을 쓰고 방향 규칙만 MACD(12,26,9) 히스토그램 부호로 바꾼다.

사용 예::

    python -m scripts.run_i3 check   # 자료 점검만 (45 와 같은 자료, 수익 미계산)
    python -m scripts.run_i3 run     # 사용자 승인 뒤 1회 판정

``run`` 은 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_f1 import FROM_MS, INITIAL, TO_MS, _t2_returns, check, describe, judge, load
from scripts.run_t_trend import stats

from auto_trading.backtest.fast_trend import I3, simulate_fast_trend
from auto_trading.backtest.trend import REALISTIC_PERP_COST

OUTPUT = Path("reports/i3")


def run(output: Path) -> int:
    path = output / "run" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    signal, bars, funding, filled = load()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    main = simulate_fast_trend(signal, bars, funding, cfg=I3, **kw)
    rep = describe(main)
    verdict = judge(rep)
    refs = {
        "cost_reference_0_023pct": replace(I3, perp_cost=REALISTIC_PERP_COST),
        "stop_on_mark": replace(I3, stop_on_mark=True),
        "liquidate_on_last_price": replace(I3, liquidate_on_mark=False),
    }
    reference = {
        name: describe(simulate_fast_trend(signal, bars, funding, cfg=c, **kw))
        for name, c in refs.items()
    }
    t2 = _t2_returns()
    pairs = [(t2[d], r) for d, r in zip(main.days, main.returns, strict=True) if d in t2]
    combo = [(a + b) / 2 for a, b in pairs]
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(I3).items()},
        "mark_filled_with_spot": filled,
        "verdict": verdict,
        "i3": rep,
        "reference_only": {
            k: {"full": v["full"], "worst_first": v["worst_first"], "short": v["short"]}
            for k, v in reference.items()
        },
        "with_t2": {
            "daily_return_correlation": statistics.correlation(
                [a for a, _ in pairs], [b for _, b in pairs]
            ),
            "t2_alone": stats(main.days[: len(pairs)], [a for a, _ in pairs]),
            "half_t2_half_i3": stats(main.days[: len(pairs)], combo),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"판정: {verdict['status']}")
    for c in verdict["criteria"]:
        print(f"  {c['name']}: {'통과' if c['passed'] else '실패'}")
    print(f"  기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "check":
        report = check() | {"note": "문서 47 — 45 §2 와 같은 자료 점검. 수익은 계산하지 않았다."}
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        return 0
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
