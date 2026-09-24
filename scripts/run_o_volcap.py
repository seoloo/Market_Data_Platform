"""문서 43 — O/v2 + 변동성 급등 끄기.

사용 예::

    python scripts/run_o_volcap.py measure   # §4 K 선택용 급등 판정 빈도 (수익 미계산)
    python scripts/run_o_volcap.py run       # 사용자 승인 뒤 1회 판정

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

from auto_trading.backtest.overlay import (
    O2_STOP,
    O3_2X,
    T2_ALONE,
    OverlayConfig,
    OverlayResult,
    simulate_overlay,
    vol_spikes,
)
from auto_trading.backtest.trend import target_weight

OUTPUT = Path("reports/o_volcap")
CANDIDATES = (1.5, 2.0)
MAX_SHARE = 0.20
"""K = 1.5 의 판정 비율이 이것을 넘으면 K = 2.0 (43 §2)."""
K: float | None = 1.5
"""§4 측정(2026-09-25)으로 고정: K = 1.5 판정 비율 6.8% ≤ 20%."""
MIN_MDD_CUT = 0.02
MAX_CAGR_GIVEUP = 0.02
_DAY = 86_400_000


def measure() -> dict[str, Any]:
    spot, _, _ = load()
    out: dict[str, Any] = {}
    for ratio in CANDIDATES:
        cfg = replace(O2_STOP, vol_spike_ratio=ratio)
        held = spiked = 0
        by_symbol: dict[str, list[int]] = {}
        spike_dates: dict[str, list[str]] = {}
        for s, bars in spot.items():
            days = sorted(bars)
            closes = [bars[d].close for d in days]
            for i, d in enumerate(days):
                if not FROM_MS <= d + _DAY <= TO_MS:
                    continue  # 판정 d → 체결 d+1 이 구간 안
                history = closes[: i + 1]
                w = target_weight(history, cfg.trend)
                if not w or w <= 0:
                    continue
                held += 1
                hit = s in vol_spikes({s: history}, cfg)
                spiked += hit
                row = by_symbol.setdefault(s, [0, 0])
                row[0] += 1
                row[1] += hit
                if hit:
                    spike_dates.setdefault(s, []).append(
                        datetime.fromtimestamp(d / 1000, UTC).strftime("%Y-%m-%d")
                    )
        out[str(ratio)] = {
            "held_symbol_days": held,
            "spike_symbol_days": spiked,
            "share": round(spiked / held, 4) if held else None,
            "by_symbol_held_spiked": by_symbol,
            "spike_months": {s: sorted({x[:7] for x in dates}) for s, dates in spike_dates.items()},
        }
    share = out["1.5"]["share"]
    out["chosen_K"] = 1.5 if share is not None and share <= MAX_SHARE else 2.0
    out["note"] = "문서 43 §4. 수익·낙폭은 계산하지 않았다."
    return out


def _worst(result: OverlayResult) -> dict[str, Any]:
    eq = result.equity
    worst_day = min(result.returns)
    worst_30 = min(eq[i] / eq[i - 30] - 1 for i in range(30, len(eq)))
    peak, peak_i, dd, span = eq[0], 0, 0.0, (0, 0)
    for i, e in enumerate(eq):
        if e > peak:
            peak, peak_i = e, i
        if 1 - e / peak > dd:
            dd, span = 1 - e / peak, (peak_i, i)

    def date(i: int) -> str:
        return datetime.fromtimestamp(result.days[i] / 1000, UTC).strftime("%Y-%m-%d")

    return {
        "worst_intraday_drawdown": result.worst_intraday_drawdown,
        "worst_day": worst_day,
        "worst_30d": worst_30,
        "mdd": dd,
        "mdd_peak": date(span[0]),
        "mdd_trough": date(span[1]),
    }


def judge_volcap(a: dict[str, Any], b2: dict[str, Any], bv: dict[str, Any]) -> dict[str, Any]:
    o_verdict = judge(a, bv)
    cut = b2["full"]["mdd"] - bv["full"]["mdd"]
    giveup = b2["full"]["cagr"] - bv["full"]["cagr"]
    criteria = [
        {"name": "V1", "passed": o_verdict["status"] == "PASS", "detail": o_verdict["criteria"]},
        {"name": "V2", "passed": cut >= MIN_MDD_CUT, "detail": {"mdd_cut": cut}},
        {"name": "V3", "passed": giveup <= MAX_CAGR_GIVEUP, "detail": {"cagr_giveup": giveup}},
    ]
    return {
        "status": "PASS" if all(c["passed"] for c in criteria) else "REJECT",
        "criteria": criteria,
    }


def run(output: Path) -> int:
    if K is None:
        print("K 가 고정되지 않았다. 43 §4 측정 뒤 커밋하고 실행한다.")
        return 3
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
    bv_cfg = replace(O2_STOP, vol_spike_ratio=K)
    runs: dict[str, tuple[OverlayConfig, OverlayResult]] = {}
    for name, cfg in (
        ("a_t2_alone", T2_ALONE),
        ("b2_o2", O2_STOP),
        ("bv_o2_volcap", bv_cfg),
        ("ref_o3_2x", O3_2X),
        ("ref_o3_2x_volcap", replace(O3_2X, vol_spike_ratio=K)),
    ):
        runs[name] = (cfg, simulate_overlay(spot, perp, funding, cfg=cfg, **kwargs))
    reports: dict[str, Any] = {}
    for name, (_, result) in runs.items():
        rep = describe(result)
        rep["worst_first"] = _worst(result)
        rep["stop_hits"] = result.stop_hits
        rep["spike_days"] = result.spike_days
        reports[name] = rep
    verdict = judge_volcap(reports["a_t2_alone"], reports["b2_o2"], reports["bv_o2_volcap"])
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(bv_cfg).items()},
        "verdict": verdict,
        **reports,
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
    parser.add_argument("stage", choices=("measure", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "measure":
        sys.stdout.write(json.dumps(measure(), ensure_ascii=False, sort_keys=True) + "\n")
        return 0
    return run(args.output)


if __name__ == "__main__":
    sys.exit(main())
