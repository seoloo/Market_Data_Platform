# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 214 — T/v2(부호 방식) 선물 매수의 실행 시점: A 즉시 · B 4 시간 지연 · C 직전 4 시간봉 수익 ≤ 0 이면(최대 24 시간). 후향 파일럿. 주문 없음.

신호 · 변동성 비중은 213 의 TSIGN(T/v2 ``target_weight``, 현물 일봉), 체결 · 보호 · 손익은 210 엔진(선물 1 분봉 · funding · mark).

사용: ``python -m scripts.run_buytiming_214 run`` → reports/buytiming_214/result.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_qb_dc_210 import MIN, Market, Params, _load, block_bootstrap, simulate, summarize
from scripts.run_tcont_213 import build, spot_closes

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "reports" / "buytiming_214"
BASE = Params(decide_hours=24, sig_is_weight=True)
ARMS = {
    "A": replace(BASE, timing="A"),
    "B": replace(BASE, timing="B"),
    "C": replace(BASE, timing="C"),
}
SENS = {
    "main": {},
    "cost_x2": {"cost": 0.0019},
    "fill_worst": {"fill": "worst"},
    "participation_0.1pct": {"participation": 0.001},
    "funding_price_proxy": {"use_mark": False},
    "cost_realistic_ref": {"cost": 0.00023},
}


def timing_report(markets: dict[str, Market], bk: Any) -> dict[str, Any]:
    """매수 필요 · 실행 · 취소 · 미완료, 대기 시간, 주문 시작 → 첫 체결 지연, 시작가 대비 체결가."""
    logs = bk.timing_log
    res = Counter(x["result"] for x in logs)
    execd = [x for x in logs if "exec_i" in x]
    waits = Counter((x["end_j"] - x["start_j"]) * 4 for x in execd)
    fills_by_sym: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for s, i, nt in bk.fill_log:
        if nt > 0:
            fills_by_sym[s].append((i, nt))
    delays, impr, impr_usd, notional = [], [], 0.0, 0.0
    for x in execd:
        s, i0 = x["sym"], x["exec_i"]
        fs = [(i, nt) for i, nt in fills_by_sym[s] if i0 <= i < i0 + 4]
        if not fs:
            continue
        delays.append(fs[0][0] - i0)
        nt = sum(v for _, v in fs)
        qty = sum(v / markets[s].o[i] for i, v in fs)
        avg = nt / qty
        impr.append(
            x["start_px"] / avg - 1
        )  # + 면 대기 시작 시가보다 싸게 삼(피한 하락), − 면 비싸게(놓친 상승)
        impr_usd += qty * (x["start_px"] - avg)
        notional += nt
    return {
        "buy_needs": len(logs),
        "results": dict(res),
        "executed_orders": len(execd),
        "wait_hours_hist": dict(sorted(waits.items())),
        "first_fill_delay_min_hist": dict(sorted(Counter(delays).items())),
        "orders_without_fill": len(execd) - len(delays),
        "start_vs_fill_mean_pct": round(statistics.fmean(impr) * 100, 3) if impr else None,
        "start_vs_fill_notional_weighted_usd": round(impr_usd, 2),
        "filled_buy_notional": round(notional, 2),
    }


def run() -> int:
    mk = _load()
    sigs, _rows = build(mk, spot_closes())
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    books: dict[str, Any] = {}
    for sname, over in SENS.items():
        runs[sname] = {}
        for arm, p in ARMS.items():
            pp = replace(p, **over)
            runs[sname][arm] = summarize(mk, sigs, "TSIGN", pp)
            if sname == "main":
                books[arm] = simulate(mk, sigs, "TSIGN", pp)["book"]
        print(
            sname,
            {
                a: (runs[sname][a]["sharpe"], runs[sname][a]["cagr"], runs[sname][a]["net_pnl"])
                for a in ARMS
            },
            flush=True,
        )

    def diff(x: str, y: str, sname: str = "main") -> dict[str, Any]:
        a = [r for _, r in runs[sname][x]["_returns"]]
        b = [r for _, r in runs[sname][y]["_returns"]]
        d = [u - v for u, v in zip(a, b, strict=True)]
        bs = block_bootstrap(d, a, b)
        days = [dd for dd, _ in runs[sname][x]["_returns"]]
        half = [
            [v for dd, v in zip(days, d, strict=True) if dd <= "2022-12-31"],
            [v for dd, v in zip(days, d, strict=True) if dd > "2022-12-31"],
        ]
        return {
            **bs,
            "mean_diff_daily_bp": round(statistics.fmean(d) * 1e4, 4),
            "halves_mean_daily_bp": [round(statistics.fmean(h) * 1e4, 4) for h in half],
            "p_mean_diff_gt0": None,
        }

    def p_mean(x: str, y: str) -> float:
        """일간 차이 평균의 블록 부트스트랩 양수 비율 (참효과가 양수일 확률이 아님)."""
        import random

        a = [r for _, r in runs["main"][x]["_returns"]]
        b = [r for _, r in runs["main"][y]["_returns"]]
        d = [u - v for u, v in zip(a, b, strict=True)]
        rng, n, pos = random.Random(214), len(d), 0
        for _ in range(2000):
            idx: list[int] = []
            while len(idx) < n:
                st = rng.randrange(n)
                idx.extend((st + t) % n for t in range(20))
            pos += statistics.fmean(d[i] for i in idx[:n]) > 0
        return round(pos / 2000, 3)

    ca, cb, ba = diff("C", "A"), diff("C", "B"), diff("B", "A")
    ca["p_mean_diff_gt0"], cb["p_mean_diff_gt0"], ba["p_mean_diff_gt0"] = (
        p_mean("C", "A"),
        p_mean("C", "B"),
        p_mean("B", "A"),
    )
    sens_ca = {
        s: round(
            (
                statistics.fmean(r for _, r in runs[s]["C"]["_returns"])
                - statistics.fmean(r for _, r in runs[s]["A"]["_returns"])
            )
            * 1e4,
            4,
        )
        for s in SENS
    }
    signs = [v > 0 for k, v in sens_ca.items() if k != "cost_realistic_ref"]
    same = all(signs) or not any(signs)
    h0, h1 = ca["halves_mean_daily_bp"]
    if not same:
        word = "판단 불가"
    elif ca["mean_diff_daily_bp"] > 0 and ca["p_mean_diff_gt0"] >= 0.9 and h0 > 0 and h1 > 0:
        word = (
            "유리"
            if (cb["mean_diff_daily_bp"] > 0 and cb["p_mean_diff_gt0"] >= 0.9)
            else "판단 불가(단순 지연과 구분 안 됨)"
        )
    elif ca["mean_diff_daily_bp"] < 0 and (ca["p_mean_diff_gt0"] <= 0.1 or (h0 < 0 and h1 < 0)):
        word = "불리"
    else:
        word = "판단 불가"
    out: dict[str, Any] = {
        "made": datetime.now(UTC).isoformat(),
        "params": asdict(BASE),
        "pilot": word,
        "C_minus_A": ca,
        "C_minus_B": cb,
        "B_minus_A": ba,
        "sens_C_minus_A_daily_bp": sens_ca,
        "timing": {a: timing_report(mk, books[a]) for a in ARMS},
        "runs": {
            s: {a: {k: v for k, v in d.items() if k != "_returns"} for a, d in vs.items()}
            for s, vs in runs.items()
        },
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    for a in ARMS:
        d = runs["main"][a]
        print(
            a,
            {
                k: d[k]
                for k in (
                    "cagr",
                    "sharpe",
                    "mdd",
                    "net_pnl",
                    "gross_pnl",
                    "cost",
                    "funding",
                    "avg_exposure",
                    "turnover_x_per_year",
                    "trades",
                    "stops",
                )
            },
        )
    print(
        "결론",
        word,
        "C−A",
        {
            k: ca[k]
            for k in (
                "mean_diff_daily_bp",
                "p_mean_diff_gt0",
                "halves_mean_daily_bp",
                "sharpe_diff",
            )
        },
        "C−B",
        {k: cb[k] for k in ("mean_diff_daily_bp", "p_mean_diff_gt0")},
    )
    print(json.dumps(out["timing"], ensure_ascii=False))
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] != ["run"]:
        print(__doc__)
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["MIN", "timing_report"]
