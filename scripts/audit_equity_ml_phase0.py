"""IDEAS 21 · 20 (주식 ML) Phase 0 감사 — 자료 · 미래 정보 · 용량 · 검정력. 학습·후보 성과 없음.

사용 예::

    python -m scripts.audit_equity_ml_phase0 --out reports/equity_ml_phase0/audit.json

* 검정력은 후보를 학습하지 않고, 141 · 142 에서 **이미 본** 유가증권시장 종목 선택 규칙들의
  월 수익(``simulate_kr`` · ``equal_weight_pit`` 그대로)으로 '종목 선택 규칙 둘의 월 차이' 의
  크기를 어림한다(새 노출 없음). RF − Ridge(21) · 비용 고려 − 무시(20) 차이의 실측이 아니다.
* 가용 월수는 실적 신호가 처음 가능한 2018-04 와 12 개월 학습 창으로 센다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.audit_dmn_phase0 import mde, newey_west_sd, required_months
from scripts.run_kr_flow import flow_picker, load_flows
from scripts.run_lt_kr import build_panel, equal_weight_pit, simulate_kr

REPO = Path(__file__).resolve().parent.parent
FROM = "2018-04-01"
Z80 = 0.8416
EFFECTS_ANNUAL = (0.03, 0.05, 0.10, 0.20)
FIRST_SIGNAL, LAST_SIGNAL, TRAIN_MONTHS = "2018-04", "2026-07", 12
RF = {"trees": 100, "max_depth": 3, "min_samples_leaf": 100, "features": 8}


def months_between(a: str, b: str) -> int:
    """'YYYY-MM' a ~ b 포함 달 수 (순수)."""
    ya, ma = int(a[:4]), int(a[5:])
    yb, mb = int(b[:4]), int(b[5:])
    return (yb - ya) * 12 + (mb - ma) + 1


def monthly(days: Sequence[str], equity: Sequence[float]) -> dict[str, float]:
    last: dict[str, float] = {}
    for d, e in zip(days, equity, strict=True):
        last[d[:7]] = e
    keys = sorted(last)
    return {k: last[k] / last[j] - 1 for j, k in pairwise(keys)}


def power_proxies() -> dict[str, Any]:
    panel, ok = build_panel()
    f = load_flows(panel)
    flow = simulate_kr(panel, ok, flow_picker(f), start_date=FROM)
    lt = simulate_kr(panel, ok, start_date=FROM)
    ew = equal_weight_pit(panel, lt.universe_log, start_date=FROM)
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    span = panel.days[start:]
    m = {
        "FLOW": monthly(span, flow.equity),
        "LT": monthly(span, lt.equity),
        "EW": monthly(span, ew),
    }
    keys = sorted(set(m["FLOW"]) & set(m["LT"]) & set(m["EW"]))
    out: dict[str, Any] = {"months": len(keys), "from": keys[0], "to": keys[-1], "pairs": {}}
    for a, b in (("FLOW", "LT"), ("FLOW", "EW"), ("LT", "EW")):
        d = [m[a][k] - m[b][k] for k in keys]
        out["pairs"][f"{a} − {b}"] = {
            "sd_lr_lag3": round(newey_west_sd(d, 3), 4),
            "sd_lr_lag6_audit": round(newey_west_sd(d, 6), 4),
        }
    return out


def power_table(sds: Sequence[float], n_avail: int) -> list[dict[str, Any]]:
    """차이 SD 별 · 효과 별 필요 월수와 가용 월수의 최소 검출 효과 (순수)."""
    rows = []
    for sd in sds:
        rows.append(
            {
                "sd_monthly": sd,
                "needed_months": {
                    f"{int(e * 100)}%/yr": required_months(sd, e / 12, Z80) for e in EFFECTS_ANNUAL
                },
                "mde_annual_arith_at_available": round(12 * mde(sd, n_avail, Z80), 3),
            }
        )
    return rows


def capacity() -> dict[str, Any]:
    rows_per_window = 200 * TRAIN_MONTHS
    max_leaves = 2 ** RF["max_depth"]
    return {
        "rf": RF,
        "rows_per_train_window_max": rows_per_window,
        "independent_months_per_window": TRAIN_MONTHS,
        "max_leaves_per_tree": max_leaves,
        "rows_per_leaf_at_full_window": rows_per_window // max_leaves,
        "ridge_coefficients": RF["features"] + 1,
        "libraries": {
            m: importlib.util.find_spec(m) is not None
            for m in ("numpy", "sklearn", "scipy", "cvxpy")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    signal_months = months_between(FIRST_SIGNAL, LAST_SIGNAL)
    oos = signal_months - TRAIN_MONTHS
    proxies = power_proxies()
    sds = sorted({0.01, 0.02, 0.03, *(p["sd_lr_lag3"] for p in proxies["pairs"].values())})
    result = {
        "months": {
            "signal_months": signal_months,
            "first_oos_month_after_12m_train": oos,
            "note": "계획용·역사 확인으로 나누면 각 구간은 이보다 짧다",
        },
        "capacity": capacity(),
        "power_proxies_seen_141_142": proxies,
        "power_table": power_table(sds, oos),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
