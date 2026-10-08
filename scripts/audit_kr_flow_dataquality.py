"""국장 통과 2 개(FLOW-KR 142 · FLOW-KQ 154) 자료 품질 점검 — 158 의 G-KR1 오류를 뺐을 때.

사용 예::

    python -m scripts.audit_kr_flow_dataquality --out reports/kr_flow_dq/audit.json

* G-KR1(158): 이웃 거래일 수정 종가 수익이 제한폭(2015-06-15 전 15%, 뒤 30%) + 1%p 를 넘는 날.
  그런 날은 실제 수익이 아니라 수정 계수가 빠진 것으로 보고, **그 하루의 점프만 없앤다**
  (그날 앞의 가격 전부에 같은 배수를 곱함 — 빠진 분할 · 병합 반영과 같은 처리).
* 판정 코드(`simulate_kr` · `flow_picker` · `equal_weight_pit`)를 숫자 하나 안 바꾸고 원래 자료와
  정제 자료로 각각 돌려 연수익 · 샤프 · 낙폭과 (FLOW − EW) 월 차이를 나란히 적는다.
* 판정을 바꾸지 않는다. 결과가 크게 움직이면 '자료 오류에 기댄 통과'로 표시만 한다.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import sys
from collections.abc import Callable, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.audit_dmn_phase0 import newey_west_sd
from scripts.count_kr_patterns import violations
from scripts.run_lt_kr import KrPanel, equal_weight_pit, simulate_kr

REPO = Path(__file__).resolve().parent.parent


def clean_close(days: Sequence[str], px: Sequence[float | None]) -> tuple[list[float | None], int]:
    """G-KR1 위반 날의 점프를 앞 가격 전부에 배수를 곱해 없앤다 (순수). (정제 가격, 고친 날 수)."""
    cal = [d.replace("-", "") for d in days]
    bad = violations(cal, list(px))
    out: list[float | None] = list(px)
    fixes = 0
    for i in range(len(out) - 1, 0, -1):
        if not bad[i]:
            continue
        a, b = out[i - 1], out[i]
        if a is None or b is None or a <= 0:
            continue
        k = b / a
        for j in range(i):
            v = out[j]
            if v is not None:
                out[j] = v * k
        fixes += 1
    return out, fixes


def clean_panel(panel: KrPanel) -> tuple[KrPanel, int]:
    p = copy.copy(panel)
    p.close = {}
    total = 0
    for sym, xs in panel.close.items():
        p.close[sym], n = clean_close(panel.days, xs)
        total += n
    return p, total


def stats(days: Sequence[str], eq: Sequence[float]) -> dict[str, float]:
    rets = [b / a - 1 for a, b in pairwise(eq) if a > 0]
    years = (len(eq) - 1) / 252
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1
    sd = statistics.stdev(rets)
    peak, mdd = 0.0, 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {
        "cagr": round(cagr, 4),
        "sharpe": round(statistics.fmean(rets) / sd * math.sqrt(252), 3),
        "mdd": round(mdd, 4),
    }


def monthly_diff(days: Sequence[str], a: Sequence[float], b: Sequence[float]) -> dict[str, float]:
    last_a: dict[str, float] = {}
    last_b: dict[str, float] = {}
    for d, x, y in zip(days, a, b, strict=True):
        last_a[d[:7]], last_b[d[:7]] = x, y
    keys = sorted(last_a)
    diff = [(last_a[k] / last_a[j] - 1) - (last_b[k] / last_b[j] - 1) for j, k in pairwise(keys)]
    se = newey_west_sd(diff, 3) / math.sqrt(len(diff))
    m = statistics.fmean(diff)
    return {"months": len(diff), "mean_monthly": round(m, 5), "t_nw3": round(m / se, 3)}


def one_market(
    build: Callable[[], tuple[KrPanel, list[bool]]], flows_dir: Path | None, start_date: str
) -> dict[str, Any]:
    from scripts.run_kr_flow import flow_picker, load_flows

    panel, ok = build()
    f = load_flows(panel, flows_dir) if flows_dir else load_flows(panel)
    out: dict[str, Any] = {}
    cleaned, fixes = clean_panel(panel)
    for name, pnl in (("original", panel), ("cleaned", cleaned)):
        fl = simulate_kr(pnl, ok, flow_picker(f), start_date=start_date)
        lt = simulate_kr(pnl, ok, start_date=start_date)
        ew = equal_weight_pit(pnl, lt.universe_log, start_date=start_date)
        start = next(i for i, d in enumerate(pnl.days) if d >= start_date)
        span = pnl.days[start:]
        out[name] = {
            "FLOW": stats(span, fl.equity),
            "EW": stats(span, ew),
            "FLOW_minus_EW": monthly_diff(span, fl.equity, ew),
        }
    out["g_kr1_days_fixed"] = fixes
    o, c = out["original"], out["cleaned"]
    out["change"] = {
        "flow_cagr_pctpt": round(100 * (c["FLOW"]["cagr"] - o["FLOW"]["cagr"]), 2),
        "flow_sharpe": round(c["FLOW"]["sharpe"] - o["FLOW"]["sharpe"], 3),
        "ew_cagr_pctpt": round(100 * (c["EW"]["cagr"] - o["EW"]["cagr"]), 2),
        "t_flow_minus_ew": round(c["FLOW_minus_EW"]["t_nw3"] - o["FLOW_minus_EW"]["t_nw3"], 3),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    from scripts.run_kq_154 import DATA as KQ_DATA
    from scripts.run_kq_154 import FROM as KQ_FROM
    from scripts.run_kq_154 import build_kq_panel
    from scripts.run_lt_kr import build_panel
    from scripts.run_lt_leaders import FROM as KR_FROM

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = {
        "FLOW-KR (142)": one_market(build_panel, None, KR_FROM),
        "FLOW-KQ (154)": one_market(build_kq_panel, KQ_DATA / "flows", KQ_FROM),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
