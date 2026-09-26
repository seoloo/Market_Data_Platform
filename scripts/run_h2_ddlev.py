"""문서 83 — H2: 계좌 낙폭 연동 배수 (고점 2.5배 → 낙폭 10% 에서 현물만). 사용자 승인 뒤 1회 판정.

사용 예::

    python -m scripts.run_h2_ddlev run

``run`` 은 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
판정 기준은 O 의 틀(24 §3 O1~O5)을 쓰되 **대조가 O/v2** 다: 정지 0 · 청산 0 · MDD ≤ 25% ·
CAGR > O/v2 · 두 절반 각각 > O/v2. 참고 줄(판정 아님): 현실 비용, 같은 배수(2.5배) 낙폭 연동 없음.
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
from scripts.run_o_volcap import _worst

from auto_trading.backtest.overlay import (
    H2_DDLEV,
    O2_STOP,
    T2_ALONE,
    OverlayResult,
    simulate_overlay,
)
from auto_trading.backtest.trend import realistic_costs

OUTPUT = Path("reports/h2_ddlev")


def _throttle_days(result: OverlayResult, zero: float) -> dict[str, int]:
    """종가 낙폭으로 본 배수 구간별 날 수 (규칙이 실제로 얼마나 작동했나 — 보고만)."""
    peak = result.equity[0]
    out = {"full": 0, "partial": 0, "spot_only": 0}
    for e in result.equity:
        peak = max(peak, e)
        d = 1 - e / peak
        out["full" if d == 0 else "spot_only" if d >= zero else "partial"] += 1
    return out


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
    configs = {
        "a_t2_alone": T2_ALONE,
        "b2_o2": O2_STOP,
        "h2": H2_DDLEV,
        "ref_h2_without_throttle": replace(H2_DDLEV, dd_throttle_zero=None),
        "ref_realistic_h2": replace(H2_DDLEV, trend=realistic_costs(H2_DDLEV.trend)),
        "ref_realistic_b2_o2": replace(O2_STOP, trend=realistic_costs(O2_STOP.trend)),
    }
    runs = {n: simulate_overlay(spot, perp, funding, cfg=c, **kwargs) for n, c in configs.items()}
    reports: dict[str, Any] = {}
    for name, result in runs.items():
        rep = describe(result)
        rep["worst_first"] = _worst(result)
        rep["stop_hits"] = result.stop_hits
        reports[name] = rep
    reports["h2"]["throttle_days"] = _throttle_days(runs["h2"], H2_DDLEV.dd_throttle_zero or 1.0)
    verdict = judge(reports["b2_o2"], reports["h2"])  # 대조 = O/v2
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config_h2": {k: str(v) for k, v in asdict(H2_DDLEV).items()},
        "comparator": "O/v2 (O2_STOP)",
        "verdict_h2": verdict,
        **reports,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"H2: {verdict['status']}")
    for c in verdict["criteria"]:
        print(f"  {c['name']}: {'통과' if c['passed'] else '실패'} {c['detail']}")
    print(f"  기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("run",))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
