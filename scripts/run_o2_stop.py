"""O/v2(O/v1 + 선물 비상 스톱 −20%)를 사전등록(docs/research/27)대로 1회 판정한다.

사용 예::

    python scripts/run_o2_stop.py run    # 2020-03-01 ~ 2026-08-31 1회 판정

작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_o_overlay import FROM_MS, INITIAL, TO_MS, describe, judge, load

from auto_trading.backtest.overlay import T2_ALONE, OverlayConfig, simulate_overlay

OUTPUT = Path("reports/o2")
STOP = 0.20
"""2026-09-24 사용자 선택 (27 §2)."""
MAX_CAGR_GIVEUP = 0.02
"""보험 비용 상한: O/v1 대비 연환산 순수익 감소 ≤ 2%p (27 §3)."""


def o2_config() -> OverlayConfig:
    return replace(OverlayConfig(), catastrophe_stop=STOP)


def judge_o2(a: dict[str, Any], b1: dict[str, Any], b2: dict[str, Any]) -> dict[str, Any]:
    o_verdict = judge(a, b2)
    giveup = b1["full"]["cagr"] - b2["full"]["cagr"]
    criteria = [
        {
            "name": "S1",
            "passed": o_verdict["status"] == "PASS",
            "detail": {"o_criteria": o_verdict["criteria"]},
        },
        {
            "name": "S2",
            "passed": giveup <= MAX_CAGR_GIVEUP,
            "detail": {
                "cagr_o1": b1["full"]["cagr"],
                "cagr_o2": b2["full"]["cagr"],
                "giveup": giveup,
                "threshold": MAX_CAGR_GIVEUP,
            },
        },
    ]
    return {
        "status": "PASS" if all(c["passed"] for c in criteria) else "REJECT",
        "criteria": criteria,
    }


def run(output: Path) -> int:
    path = output / "run" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    spot, perp, funding = load()
    kwargs: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    a = simulate_overlay(spot, perp, funding, cfg=T2_ALONE, **kwargs)
    b1 = simulate_overlay(spot, perp, funding, cfg=OverlayConfig(), **kwargs)
    b2 = simulate_overlay(spot, perp, funding, cfg=o2_config(), **kwargs)
    ra, r1, r2 = describe(a), describe(b1), describe(b2)
    for report, result in ((r1, b1), (r2, b2)):
        report["stop_hits"] = result.stop_hits
    verdict = judge_o2(ra, r1, r2)
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(o2_config()).items()},
        "verdict": verdict,
        "a_t2_alone": ra,
        "b1_o1": r1,
        "b2_o2": r2,
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
    print(f"  발동 {b2.stop_hits}회 · 기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("run",))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return run(parser.parse_args().output)


if __name__ == "__main__":
    sys.exit(main())
