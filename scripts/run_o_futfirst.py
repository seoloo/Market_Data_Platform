"""문서 64 — O + 선물부터 줄이기 (T/v2 세 추세 신호가 모두 상승일 때만 선물 추가분).

사용 예::

    python -m scripts.run_o_futfirst measure   # 선물이 꺼지는 (종목, 날) 비율 (수익 미계산)
    python -m scripts.run_o_futfirst run       # 사용자 승인 뒤 1회 판정

``run`` 은 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
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
    O2_STOP,
    O3_2X,
    T2_ALONE,
    OverlayConfig,
    OverlayResult,
    simulate_overlay,
    weak_trends,
)
from auto_trading.backtest.trend import realistic_costs, target_weight

OUTPUT = Path("reports/o_futfirst")
F2 = replace(O2_STOP, futures_full_trend_only=True)
F3 = replace(O3_2X, futures_full_trend_only=True)
MIN_MDD_CUT = 0.02
MAX_CAGR_GIVEUP = 0.02
_DAY = 86_400_000


def measure() -> dict[str, Any]:
    spot, _, _ = load()
    held = off = 0
    by_symbol: dict[str, list[int]] = {}
    by_year: dict[str, list[int]] = {}
    for s, bars in spot.items():
        days = sorted(bars)
        closes = [bars[d].close for d in days]
        for i, d in enumerate(days):
            if not FROM_MS <= d + _DAY <= TO_MS:
                continue  # 판정 d → 체결 d+1 이 구간 안
            history = closes[: i + 1]
            w = target_weight(history, F2.trend)
            if not w or w <= 0:
                continue
            held += 1
            hit = s in weak_trends({s: history}, F2)
            off += hit
            year = datetime.fromtimestamp(d / 1000, UTC).strftime("%Y")
            for key, table in ((s, by_symbol), (year, by_year)):
                row = table.setdefault(key, [0, 0])
                row[0] += 1
                row[1] += hit
    return {
        "held_symbol_days": held,
        "futures_off_symbol_days": off,
        "share": round(off / held, 4) if held else None,
        "by_symbol_held_off": by_symbol,
        "by_year_held_off": by_year,
        "note": "문서 64 §4. T/v2 목표 > 0 중 세 신호가 모두 상승은 아닌 비율. 수익 미계산.",
    }


def judge_f2(a: dict[str, Any], b2: dict[str, Any], f2: dict[str, Any]) -> dict[str, Any]:
    o_verdict = judge(a, f2)
    cut = b2["full"]["mdd"] - f2["full"]["mdd"]
    giveup = b2["full"]["cagr"] - f2["full"]["cagr"]
    criteria = [
        {"name": "W1", "passed": o_verdict["status"] == "PASS", "detail": o_verdict["criteria"]},
        {"name": "W2", "passed": cut >= MIN_MDD_CUT, "detail": {"mdd_cut": cut}},
        {"name": "W3", "passed": giveup <= MAX_CAGR_GIVEUP, "detail": {"cagr_giveup": giveup}},
    ]
    return {
        "status": "PASS" if all(c["passed"] for c in criteria) else "REJECT",
        "criteria": criteria,
    }


def judge_f3(a: dict[str, Any], b2: dict[str, Any], f3: dict[str, Any]) -> dict[str, Any]:
    o_verdict = judge(a, f3)
    criteria = [
        {"name": "X1", "passed": o_verdict["status"] == "PASS", "detail": o_verdict["criteria"]},
        {
            "name": "X2",
            "passed": f3["full"]["cagr"] > b2["full"]["cagr"],
            "detail": {"cagr_f3": f3["full"]["cagr"], "cagr_o2": b2["full"]["cagr"]},
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
    configs: dict[str, OverlayConfig] = {
        "a_t2_alone": T2_ALONE,
        "b2_o2": O2_STOP,
        "f2_o2_futfirst": F2,
        "b3_o3": O3_2X,
        "f3_o3_futfirst": F3,
    }
    for name in ("b2_o2", "f2_o2_futfirst", "b3_o3", "f3_o3_futfirst"):
        cfg = configs[name]
        configs[f"ref_realistic_{name}"] = replace(cfg, trend=realistic_costs(cfg.trend))
    runs: dict[str, OverlayResult] = {
        name: simulate_overlay(spot, perp, funding, cfg=cfg, **kwargs)
        for name, cfg in configs.items()
    }
    reports: dict[str, Any] = {}
    for name, result in runs.items():
        rep = describe(result)
        rep["worst_first"] = _worst(result)
        rep["stop_hits"] = result.stop_hits
        rep["futures_off_symbol_days"] = result.spike_days
        reports[name] = rep
    a, b2 = reports["a_t2_alone"], reports["b2_o2"]
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config_f2": {k: str(v) for k, v in asdict(F2).items()},
        "config_f3": {k: str(v) for k, v in asdict(F3).items()},
        "verdict_f2": judge_f2(a, b2, reports["f2_o2_futfirst"]),
        "verdict_f3": judge_f3(a, b2, reports["f3_o3_futfirst"]),
        **reports,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for key in ("verdict_f2", "verdict_f3"):
        verdict = record[key]
        print(f"{key}: {verdict['status']}")
        for c in verdict["criteria"]:
            print(f"  {c['name']}: {'통과' if c['passed'] else '실패'}")
    print(f"  기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("measure", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "measure":
        sys.stdout.write(json.dumps(measure(), ensure_ascii=False, sort_keys=True) + "\n")
        return 0
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
