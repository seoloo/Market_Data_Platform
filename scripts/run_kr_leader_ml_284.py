"""문서 284 — Sirius(274 GBM 주도주 · 275 나눠 사기)를 유가증권시장에: 5 종목 × 6 · 5 · 4 묶음.

1 회 판정.

사용 예 (numpy · scikit-learn 은 연구 venv, 프로젝트 패키지는 운영 venv 에서)::

    PYTHONPATH="src;.;.venv/Lib/site-packages" build/tcn-venv/Scripts/python.exe \
        -m scripts.run_kr_leader_ml_284 run

종목군 = 141 ``members``(그 시점 거래대금 상위 200, 상장폐지 포함). 특징은 274 의 SEC 실적 3 칸을
142 수급 3 칸으로 바꾼 13 개, 모델 · 학습 · 계좌는 274 · 275 그대로.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_kr_flow import EXPECTED_FLOWS, Flows, flow_hash, flow_ratio, load_flows
from scripts.run_leader_hold_275 import HoldRun, simulate_hold
from scripts.run_leader_small_276 import whole_shares
from scripts.run_lt_kr import (
    DATA,
    EXPECTED_DATA,
    KrPanel,
    _iso,
    build_panel,
    data_hash,
    leaders_pit,
    members,
    month_end_day,
)
from scripts.run_lt_leaders import FEE, LOOKS, stats

from scripts import run_leader_ml_274 as m274

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "kr_leader_ml_284"
FIRST = "2009-01-01"
START, TEST_YEARS = "2012-01-01", range(2012, 2027)
HALF_END, HALF2 = "2018-12-31", "2019-01-01"
HORIZON = 126
MIN_PRICE = 1000.0
MIN_POOL = 30
PICKS = 5
PLANS: dict[str, tuple[int, int]] = {"K5x6": (5, 6), "K5x5": (5, 5), "K5x4": (5, 4)}
"""판 → (종목 수, 묶음 K). 보유 = 21 × K 거래일."""
REPORT_PLANS: dict[str, tuple[int, int]] = {"K10x6": (10, 6)}
SHARPE_GAP, MDD_GAP = 0.10, 0.10
CAPITALS_KRW = (5_000_000, 10_000_000, 20_000_000, 30_000_000)
FEAS_FROM, FEAS_TO = "2024-09-01", "2026-08-31"
MISS_MAX, DRAG_MAX = 0.05, 0.15
FEATURES = (
    "r21", "r63", "r126", "r252", "r12_1", "from_high", "vol63", "dollar_vol",
    "score113", "sector113", "flow20", "flow60", "flow_missing",
)  # fmt: skip
PRICE_COLS = FEATURES[:8]


# ------------------------------------------------------------------ 순수 계산


def _px(xs: Sequence[float | None], t: int) -> float | None:
    while t >= 0 and xs[t] is None:
        t -= 1
    return xs[t] if t >= 0 else None


def fwd_ret(xs: Sequence[float | None], t: int, last: int, horizon: int = HORIZON) -> float | None:
    """t 종가 → t + horizon 종가 수익. 그 전에 자료가 끊긴 종목(``last`` < t + horizon)은
    마지막 거래일 종가로, 거래정지로 그날 값이 없으면 그 전 마지막 종가로. 기간 밖이면 None."""
    if t + horizon >= len(xs) or xs[t] is None:
        return None
    end = _px(xs, min(t + horizon, last))
    return None if end is None else end / xs[t] - 1  # type: ignore[operator]


def plan_hold(slots: int) -> int:
    return 21 * slots


def judge_plan(
    gbm: Mapping[str, Any], ew: Mapping[str, Any], kospi: Mapping[str, Any]
) -> dict[str, Any]:
    """문서 284 §5 (순수) — 판 하나."""
    checks = {
        "a_sharpe_gt_kospi": gbm["full"]["sharpe"] > kospi["full"]["sharpe"],
        "b_halves_cagr_gt_kospi": all(
            gbm[h]["cagr"] > kospi[h]["cagr"] for h in ("first", "second")
        ),
        "c_sharpe_ge_ew_plus": gbm["full"]["sharpe"] >= ew["full"]["sharpe"] + SHARPE_GAP,
        "d_mdd_le_kospi_plus": gbm["full"]["mdd"] <= kospi["full"]["mdd"] + MDD_GAP,
    }
    if all(checks.values()):
        status = "후속 가치(탐색)"
    elif (
        gbm["full"]["sharpe"] <= kospi["full"]["sharpe"]
        and gbm["full"]["sharpe"] <= ew["full"]["sharpe"]
    ):
        status = "불리"
    else:
        status = "불명"
    return {"status": status, "checks": checks}


# ------------------------------------------------------------------ 행


def kr_rows(
    panel: KrPanel,
    flows: Flows,
    pools: Mapping[int, Sequence[str]],
) -> list[dict[str, Any]]:
    """판단일 ``t`` 마다 종목군 ``pools[t]`` 의 특징(백분위) · 정답."""
    rows: list[dict[str, Any]] = []
    for t in sorted(pools):
        day = panel.days[t]
        raw: dict[str, dict[str, float]] = {}
        for s in pools[t]:
            c = panel.close[s][t]
            if c is None or c < MIN_PRICE:
                continue
            f = m274.price_features(panel.close[s], panel.value[s], t)
            if f is not None:
                raw[s] = f
        if len(raw) < MIN_POOL:
            continue
        score = dict.fromkeys(raw, 0.0)
        for name in ("r21", "r63", "r126"):  # 113 의 LOOKS = 21 · 63 · 126
            for s, v in m274.pct_rank({s: raw[s][name] for s in raw}).items():
                score[s] += v / len(LOOKS)
        by: dict[str, list[float]] = defaultdict(list)
        for s in raw:
            by[panel.sector[s]].append(score[s])
        sec_score = {s: statistics.median(by[panel.sector[s]]) for s in raw}
        cols: dict[str, dict[str, float]] = {
            name: m274.pct_rank({s: raw[s][name] for s in raw}) for name in PRICE_COLS
        }
        cols["score113"] = m274.pct_rank(score)
        cols["sector113"] = m274.pct_rank(sec_score)
        f20 = {s: r for s in raw if (r := flow_ratio(flows, s, t, 20)) is not None}
        f60 = {s: r for s in raw if (r := flow_ratio(flows, s, t, 60)) is not None}
        r20, r60 = m274.pct_rank(f20) if f20 else {}, m274.pct_rank(f60) if f60 else {}
        cols["flow20"] = {s: r20.get(s, 0.5) for s in raw}
        cols["flow60"] = {s: r60.get(s, 0.5) for s in raw}
        cols["flow_missing"] = {s: float(s not in r20 or s not in r60) for s in raw}
        fwd = {s: fwd_ret(panel.close[s], t, panel.last[s]) for s in raw}
        labels = m274.top_labels(fwd)
        for s in sorted(raw):
            rows.append(
                {
                    "t": t,
                    "day": day,
                    "sym": s,
                    "x": [cols[f][s] for f in FEATURES],
                    "y": labels[s],
                    "fwd": fwd[s],
                }
            )
    return rows


def decision_days(panel: KrPanel) -> list[int]:
    return [
        t for t in range(len(panel.days)) if panel.days[t] >= FIRST and month_end_day(panel.days, t)
    ]


# ------------------------------------------------------------------ 학습 · 고르기


def walk_forward(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> dict[str, list[float]]:
    """274 ``walk_forward`` 와 같고 시험 해만 2012 ~ 2026."""
    import numpy as np  # type: ignore[import-not-found]
    from sklearn.linear_model import LogisticRegression  # type: ignore[import-not-found]

    x = np.asarray([r["x"] for r in rows], dtype=float)
    y = np.asarray([-1 if r["y"] is None else r["y"] for r in rows])
    out = {"GBM": np.full(len(rows), np.nan), "LR": np.full(len(rows), np.nan)}
    for year in TEST_YEARS:
        tr = np.asarray([m274.trainable(r["t"], days, year) and r["y"] is not None for r in rows])
        te = np.asarray([r["day"][:4] == str(year) for r in rows])
        if not te.any():
            continue
        gbm = m274.gbm_model()
        gbm.fit(x[tr], y[tr])
        out["GBM"][te] = gbm.predict_proba(x[te])[:, 1]
        lr = LogisticRegression(C=1.0, max_iter=2000)
        lr.fit(x[tr] - 0.5, y[tr])
        out["LR"][te] = lr.predict_proba(x[te] - 0.5)[:, 1]
    return {k: v.tolist() for k, v in out.items()}


def top_picks(
    rows: Sequence[dict[str, Any]], prob: Sequence[float], n: int
) -> dict[int, list[str]]:
    by: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for r, p in zip(rows, prob, strict=True):
        if not math.isnan(p):
            by[r["t"]].append((-p, r["sym"]))
    return {t: [s for _p, s in sorted(xs)[:n]] for t, xs in by.items()}


# ------------------------------------------------------------------ 평가


def summarize(days: Sequence[str], run: HoldRun) -> dict[str, Any]:
    s0 = next(i for i, d in enumerate(days) if d >= START)
    span = list(days[s0:])
    eq = run.equity
    yearly: dict[str, float] = {}
    monthly: list[float] = []
    prev_y, prev_m = eq[0], eq[0]
    for i, d in enumerate(span):
        if i + 1 == len(span) or span[i + 1][:4] != d[:4]:
            yearly[d[:4]] = eq[i] / prev_y - 1
            prev_y = eq[i]
        if i + 1 == len(span) or span[i + 1][:7] != d[:7]:
            monthly.append(eq[i] / prev_m - 1)
            prev_m = eq[i]
    return {
        "full": stats(span, eq, START, span[-1]),
        "first": stats(span, eq, START, HALF_END),
        "second": stats(span, eq, HALF2, span[-1]),
        "yearly": {k: round(v, 4) for k, v in yearly.items()},
        "worst_month": round(min(monthly), 4),
        "positions": len(run.trades),
        "win_rate": round(sum(r > 0 for r in run.trades) / len(run.trades), 4) if run.trades else 0,
        "mean_position_ret": round(statistics.fmean(run.trades), 4) if run.trades else 0,
    }


def precision(rows: Sequence[dict[str, Any]], picks: Mapping[int, list[str]]) -> dict[int, float]:
    lab = {(r["t"], r["sym"]): r["y"] for r in rows}
    labelled = {r["t"] for r in rows if r["y"] is not None}
    return {
        t: sum(1 for s in xs if lab.get((t, s)) == 1) / len(xs)
        for t, xs in picks.items()
        if t in labelled and xs
    }


def feasibility(
    picks: Mapping[int, Sequence[str]],
    days: Sequence[str],
    close: Mapping[str, Sequence[float | None]],
    slots: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sel = [t for t in sorted(picks) if FEAS_FROM <= days[t] <= FEAS_TO and picks[t]]
    for krw in CAPITALS_KRW:
        miss = total = 0
        drags = []
        for t in sel:
            prices = [c for s in picks[t] if (c := close[s][t]) is not None]
            m, d = whole_shares(float(krw), slots, prices)
            miss += m
            total += len(prices)
            drags.append(d)
        out[str(krw)] = {
            "missed_share": miss / total if total else None,
            "cash_drag_mean": statistics.fmean(drags) if drags else None,
            "cash_drag_worst": max(drags) if drags else None,
            "decisions": len(sel),
        }
    ok = [
        int(k)
        for k, v in out.items()
        if v["missed_share"] is not None
        and v["missed_share"] <= MISS_MAX
        and v["cash_drag_mean"] <= DRAG_MAX
    ]
    out["needed_krw"] = min(ok) if ok else None
    return out


def _kospi(panel: KrPanel, name: str = "kospi.csv", col: int = 1) -> dict[str, float]:
    rows = (DATA / name).read_text(encoding="utf-8").splitlines()[1:]
    return {_iso(r.split(",")[0]): float(r.split(",")[col]) for r in rows}


def evaluate() -> dict[str, Any]:
    panel, index_ok = build_panel()
    flows = load_flows(panel)
    days = panel.days
    pools = {t: members(panel, t) for t in decision_days(panel)}
    rows = kr_rows(panel, flows, pools)
    print(json.dumps({"rows": len(rows)}), flush=True)
    prob = walk_forward(rows, days)
    test_t = sorted({r["t"] for r in rows if r["day"] >= START})
    ew: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        ew[r["t"]].append(r["sym"])
    base10 = {t: [s for s, _ in leaders_pit(panel, t, pools[t])] for t in test_t}

    def pickers(n: int) -> dict[str, dict[int, list[str]]]:
        p = {
            "GBM": top_picks(rows, prob["GBM"], n),
            "LR": top_picks(rows, prob["LR"], n),
            "BASE": {t: v[:n] for t, v in base10.items()},
            "EW": dict(ew),
        }
        return {k: {t: list(v[t]) for t in test_t if v.get(t)} for k, v in p.items()}

    out: dict[str, Any] = {"plans": {}, "cost2": {}, "feasibility": {}}
    for plan, (n, k) in {**PLANS, **REPORT_PLANS}.items():
        out["plans"][plan], out["cost2"][plan] = {}, {}
        for name, p in pickers(n).items():
            run = simulate_hold(days, panel.close, p, START, hold=plan_hold(k), slots=k)
            out["plans"][plan][name] = summarize(days, run)
            run2 = simulate_hold(
                days, panel.close, p, START, hold=plan_hold(k), slots=k, fee=2 * FEE
            )
            out["cost2"][plan][name] = stats(
                [d for d in days if d >= START], run2.equity, START, days[-1]
            )
        if plan in PLANS:
            out["feasibility"][plan] = feasibility(pickers(n)["GBM"], days, panel.close, k)
        print(json.dumps({"plan": plan}), flush=True)
    p5all = pickers(PICKS)
    g5 = p5all["GBM"]
    mkt = simulate_hold(
        days, panel.close, g5, START, hold=plan_hold(6), slots=6, buy_ok=lambda t: index_ok[t]
    )
    out["report_k5x6_g200"] = summarize(days, mkt)

    kospi = _kospi(panel)
    s0 = next(i for i, d in enumerate(days) if d >= START)
    k_eq = [kospi[d] / kospi[days[s0]] * (1 - FEE) for d in days[s0:]]
    out["kospi"] = summarize(days, HoldRun(k_eq, []))
    tr = _kospi(panel, "kospi_tr.csv", 2)
    tr_days = [d for d in days if d in tr and d >= "2015-01-02"]
    out["kospi_tr_vs_price_2015"] = {
        "tr": stats(tr_days, [tr[d] for d in tr_days], tr_days[0], tr_days[-1]),
        "price": stats(tr_days, [kospi[d] for d in tr_days], tr_days[0], tr_days[-1]),
    }
    out["verdict"] = {
        plan: judge_plan(out["plans"][plan]["GBM"], out["plans"][plan]["EW"], out["kospi"])
        for plan in PLANS
    }

    p5 = {a: precision(rows, p5all[a]) for a in ("GBM", "LR", "BASE")}
    common = sorted(set.intersection(*(set(v) for v in p5.values())))
    labelled = [r for r in rows if r["y"] is not None and r["day"] >= START]
    out["precision"] = {
        "base_rate": sum(r["y"] for r in labelled) / len(labelled),
        "months": len(common),
        **{a: statistics.fmean(v[t] for t in common) for a, v in p5.items()},
        "gbm_minus_base_nw_t": m274.nw_t([p5["GBM"][t] - p5["BASE"][t] for t in common]),
    }
    out["auc"] = {a: m274.auc_by_year(rows, prob[a]) for a in ("GBM", "LR")}
    pool_n: dict[str, list[int]] = defaultdict(list)
    per_t: dict[int, int] = defaultdict(int)
    for r in rows:
        per_t[r["t"]] += 1
    for t, n_ in per_t.items():
        pool_n[days[t][:4]].append(n_)
    out["pool_size_by_year"] = {y: statistics.median(v) for y, v in sorted(pool_n.items())}
    out["delisted"] = {
        "in_rows": len({r["sym"] for r in rows if r["sym"] in panel.delisted}),
        "gbm5_picked": sorted({s for v in g5.values() for s in v if s in panel.delisted}),
    }
    info = json.loads((DATA / "info.json").read_text(encoding="utf-8"))
    out["last_picks"] = {
        a: {
            days[t]: [f"{s} {info.get(s, {}).get('name', '')}" for s in p5all[a][t]]
            for t in test_t[-3:]
            if t in p5all[a]
        }
        for a in ("GBM", "BASE")
    }
    out["rows"] = len(rows)
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if data_hash()[0] != EXPECTED_DATA or flow_hash()[0] != EXPECTED_FLOWS:
        raise SystemExit("국장 자료 해시가 문서 284 §1 과 다르다")
    res = evaluate()
    out = {
        "git": sha,
        "completed_utc": datetime.now(UTC).isoformat(),
        "verdict": res.pop("verdict"),
        "results": res,
        "note": "문서 284 1 회. 유가증권시장 그 시점 거래대금 상위 200(상장폐지 포함), "
        "5 종목 × 6 · 5 · 4 묶음.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) > 1 and argv[1] == "run":
        r = run()
        print(json.dumps({p: v["status"] for p, v in r["verdict"].items()}, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
