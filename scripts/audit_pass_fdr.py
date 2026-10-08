"""통과(PASS) 9 개의 '운 좋은 통과' 점검 — 다중검정 보정 (판정은 바꾸지 않는다).

사용 예::

    python -m scripts.audit_pass_fdr --out reports/pass_fdr/audit.json

* 판정이 끝난 규칙을 숫자 하나 안 바꾸고 다시 돌려 '우위 주장' 시계열을 만든다.
  새 규칙 · 새 자료 없음.
* 우위 주장마다 평균 > 0 의 한쪽 검정(Newey–West SE)으로 p 를 구한다:
  - T/v2 단독: 일간 순수익 · O/v1 · O/v2: (O − T/v2) 일간 차이
  - QS: (QS − 매달 QQQ 적립) 단위 NAV 일간 수익 차이
  - FLOW-KR · FLOW-KQ: (FLOW − 같은 종목군 EW) 월 수익 차이
* T(→ T/v2 의 원판)는 판정 문서의 일간 평균 · 2SE 하한에서 t 를 되돌려 쓴다.
* 스테이킹 캐리는 판정 시계열을 다시 만들 수 없어(관찰 기록 기반) 숫자만 적는다. AG · ALLOC 은
  우위가 아니라 생존 · 위험 배분 기준이라 검정 대상이 아니다.
* 보정: 판정 받은 가설 수 m 을 분모로 Bonferroni 와 Benjamini–Hochberg(q 0.05 · 0.10).
  REJECT 의 p 는 모두 통과의 p 보다 크다고 놓는 **통과에 가장 유리한 경우**다 —
  여기서도 못 넘으면 우연일 수 있다.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.audit_dmn_phase0 import newey_west_sd

REPO = Path(__file__).resolve().parent.parent
M_JUDGED = 100  # RESEARCH_HISTORY '한눈에' 2026-10-08


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def t_test(xs: Sequence[float], lag: int) -> dict[str, float]:
    """평균 > 0 한쪽 검정, NW 장기 SD (순수)."""
    n = len(xs)
    m = statistics.fmean(xs)
    se = newey_west_sd(xs, lag) / math.sqrt(n)
    t = m / se if se > 0 else 0.0
    return {"n": n, "mean": m, "se": se, "t": round(t, 3), "p_one_sided": round(1 - phi(t), 5)}


def bh(pvals: Mapping[str, float], m: int, q: float) -> dict[str, bool]:
    """Benjamini–Hochberg, 다른 m − len(pvals) 개 가설의 p 는 모두 더 크다고 놓는다 (순수)."""
    ranked = sorted(pvals.items(), key=lambda kv: kv[1])
    k_max = 0
    for k, (_, p) in enumerate(ranked, start=1):
        if p <= k * q / m:
            k_max = k
    keep = {name for name, _ in ranked[:k_max]}
    return {name: name in keep for name in pvals}


def _rets(eq: Sequence[float]) -> list[float]:
    return [b / a - 1 for a, b in pairwise(eq) if a > 0]


def overlay_series() -> dict[str, list[float]]:
    from scripts.run_o_overlay import FROM_MS, INITIAL, TO_MS, load

    from auto_trading.backtest.overlay import O2_STOP, T2_ALONE, OverlayConfig, simulate_overlay

    spot, perp, funding = load()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    t2 = _rets(simulate_overlay(spot, perp, funding, cfg=T2_ALONE, **kw).equity)
    o1 = _rets(simulate_overlay(spot, perp, funding, cfg=OverlayConfig(), **kw).equity)
    o2 = _rets(simulate_overlay(spot, perp, funding, cfg=O2_STOP, **kw).equity)
    n = min(len(t2), len(o1), len(o2))
    return {
        "T/v2": t2[:n],
        "O/v1 − T/v2": [b - a for a, b in zip(t2[:n], o1[:n], strict=True)],
        "O/v2 − T/v2": [b - a for a, b in zip(t2[:n], o2[:n], strict=True)],
    }


def qs_series() -> list[float]:
    from scripts import run_qs_switch as qsw

    days, qqq, rf = qsw._inputs()
    tqqq = qsw.leveraged(days, qqq, rf, 3.0, qsw.TQQQ_FEE_YEAR)
    sig = qsw.trend_signals(days, qqq)
    end = max(d for d in days if d <= qsw.TO)
    start = min(d for d in days if d >= qsw.FROM)
    a = qsw.simulate(days, qqq, tqqq, sig, rf, mode="A", start=start, end=end)
    qs = qsw.simulate(days, qqq, tqqq, sig, rf, mode="QS", start=start, end=end)
    ra = _rets([v for _, v in a.navs])
    rq = _rets([v for _, v in qs.navs])
    return [b - a_ for a_, b in zip(ra, rq, strict=True)]


def _monthly(days: Sequence[str], eq: Sequence[float]) -> dict[str, float]:
    last: dict[str, float] = {}
    for d, e in zip(days, eq, strict=True):
        last[d[:7]] = e
    keys = sorted(last)
    return {k: last[k] / last[j] - 1 for j, k in pairwise(keys)}


def flow_kr_series() -> list[float]:
    from scripts.run_kr_flow import flow_picker, load_flows
    from scripts.run_lt_kr import build_panel, equal_weight_pit, simulate_kr
    from scripts.run_lt_leaders import FROM

    panel, ok = build_panel()
    f = load_flows(panel)
    fl = simulate_kr(panel, ok, flow_picker(f))
    ew = equal_weight_pit(panel, fl.universe_log)
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    span = panel.days[start:]
    mf = _monthly(span, fl.equity)
    me = _monthly(span, ew)
    return [mf[k] - me[k] for k in sorted(set(mf) & set(me))]


def flow_kq_series() -> list[float]:
    from scripts.run_kq_154 import DATA, FROM, build_kq_panel
    from scripts.run_kr_flow import flow_picker, load_flows
    from scripts.run_lt_kr import equal_weight_pit, simulate_kr

    panel, ok = build_kq_panel()
    f = load_flows(panel, DATA / "flows")
    fl = simulate_kr(panel, ok, flow_picker(f), start_date=FROM)
    lt = simulate_kr(panel, ok, start_date=FROM)
    ew = equal_weight_pit(panel, lt.universe_log, start_date=FROM)
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    span = panel.days[start:]
    mf = _monthly(span, fl.equity)
    me = _monthly(span, ew)
    return [mf[k] - me[k] for k in sorted(set(mf) & set(me))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    tests: dict[str, dict[str, Any]] = {}
    # T (원판): 판정 문서 일간 평균 0.060% · 2SE 하한 +0.017% → SE 0.0215%
    t_v1 = 0.060 / ((0.060 - 0.017) / 2)
    tests["T (원판, 2018-01 ~ 2026-08)"] = {
        "t": round(t_v1, 3),
        "p_one_sided": round(1 - phi(t_v1), 5),
        "source": "판정 문서 숫자",
    }
    for name, xs in overlay_series().items():
        tests[name] = {**t_test(xs, 5), "unit": "일간"}
    tests["QS − 매달 QQQ 적립"] = {**t_test(qs_series(), 5), "unit": "일간 단위 NAV"}
    tests["FLOW-KR − EW"] = {**t_test(flow_kr_series(), 3), "unit": "월"}
    tests["FLOW-KQ − EW"] = {**t_test(flow_kq_series(), 3), "unit": "월"}
    pvals = {k: v["p_one_sided"] for k, v in tests.items()}
    out = {
        "m_judged": M_JUDGED,
        "tests": tests,
        "bonferroni_0.05": {k: p <= 0.05 / M_JUDGED for k, p in pvals.items()},
        "bh_q0.05_best_case": bh(pvals, M_JUDGED, 0.05),
        "bh_q0.10_best_case": bh(pvals, M_JUDGED, 0.10),
        "not_tested": {
            "STAKE-CARRY": "CAGR 3.34 · 3.26% vs 예치 3.08%, 2 년 관찰 — 시계열 재생 불가,"
            " 차이 연 0.2 ~ 0.3%p",
            "AG": "생존(−70% 정리선) · 성장 기준 — 우위 검정 아님",
            "ALLOC": "위험 배분 권고 — 우위 검정 아님",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
