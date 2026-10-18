"""문서 274 — 미장 주도주를 미리 찾는 모델(6 달 뒤 상위 10%): GBM · LR vs 113 기본. 1 회 판정.

사용 예 (numpy · scikit-learn 은 연구 venv, 프로젝트 패키지는 운영 venv 에서)::

    PYTHONPATH="src;.;.venv/Lib/site-packages" build/tcn-venv/Scripts/python.exe \
        -m scripts.run_leader_ml_274 run

종목군 U1 = 113 판(지금 S&P 500, 생존 편향), U2 = U1 중 그 월말에 이미 S&P 500 이던 종목(fja 명단).
고르기만 바꾸고 계좌는 258 ARM 그대로.
"""

from __future__ import annotations

import bisect
import csv
import dataclasses
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import trade_stats, us_market
from scripts.run_leader_quality_261 import EXPECTED_DATA as SEC_SHA
from scripts.run_leader_quality_261 import Fundamentals, pick_in
from scripts.run_leader_quality_261 import data_hash as sec_hash
from scripts.run_lt_kr import month_end_day
from scripts.run_lt_leaders import LOOKS, stats
from scripts.run_sb_breakout import STOCKS, load
from scripts.run_takeprofit_258 import simulate

REPO = Path(__file__).resolve().parent.parent
PIT = REPO / "data" / "research" / "leader_ml_us" / "sp500_historical_components.csv"
PIT_SHA = "36326709d46d6cd25834de5df457b16f5f96fad3a06b9beac28f7b88aa0b0d54"
OUT = REPO / "reports" / "leader_ml_274"
FIRST = "2011-01-01"
TEST_FROM, TEST_YEARS = "2016-01-01", range(2016, 2027)
HALF = ("2016-01-01", "2020-12-31", "2021-01-01")
HORIZON = 126
TOP_FRAC = 0.10
PICKS = 10
NW_LAG = 5
STALE_DAYS, YOY_DAYS = 200, (350, 380)
SEED = 20261018
FEATURES = (
    "r21", "r63", "r126", "r252", "r12_1", "from_high", "vol63", "dollar_vol",
    "score113", "sector113", "rev_yoy", "op_yoy", "fund_missing",
)  # fmt: skip


# ------------------------------------------------------------------ 순수 계산


def pct_rank(values: Mapping[str, float]) -> dict[str, float]:
    """0 (가장 작음) ~ 1 (가장 큼). 같으면 티커 순."""
    order = sorted(values, key=lambda s: (values[s], s))
    n = len(order)
    if n == 1:
        return {order[0]: 0.5}
    return {s: i / (n - 1) for i, s in enumerate(order)}


def top_labels(fwd: Mapping[str, float | None], frac: float = TOP_FRAC) -> dict[str, int | None]:
    """f 가 있는 종목 중 상위 ``frac`` (경계값 포함) = 1. f 없으면 None."""
    known = sorted(v for v in fwd.values() if v is not None)
    if not known:
        return dict.fromkeys(fwd)
    k = max(1, math.ceil(len(known) * frac - 1e-9))
    cut = known[len(known) - k]
    return {s: None if v is None else int(v >= cut) for s, v in fwd.items()}


def growth(qs: Sequence[tuple[str, str, float]], day: str) -> float | None:
    """그날 알 수 있던 가장 최근 분기 ÷ 1 년 전 같은 분기 − 1 (전년 ≤ 0 이면 None)."""
    from scripts.run_leader_quality_261 import _days

    known = [q for q in qs if q[0] <= day]
    if not known:
        return None
    last = max(known, key=lambda q: q[1])
    if _days(last[1], day) > STALE_DAYS:
        return None
    prev = [q for q in known if YOY_DAYS[0] <= _days(q[1], last[1]) <= YOY_DAYS[1]]
    if not prev or prev[0][2] <= 0:
        return None
    return last[2] / prev[0][2] - 1


def trainable(t: int, days: Sequence[str], year: int, horizon: int = HORIZON) -> bool:
    """정답 끝(t + horizon)이 시험 해 첫날 전이면 학습에 쓸 수 있다."""
    return t + horizon < len(days) and days[t + horizon] < f"{year}-01-01"


def caught(top5: Iterable[str], lists: Iterable[Iterable[str]]) -> int:
    seen = {s for xs in lists for s in xs}
    return sum(1 for s in top5 if s in seen)


def nw_t(xs: Sequence[float], lag: int = NW_LAG) -> float:
    n = len(xs)
    m = sum(xs) / n
    c = [x - m for x in xs]
    var = sum(v * v for v in c) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * sum(c[i] * c[i - k] for i in range(k, n)) / n
    se = math.sqrt(max(var, 0.0) / n)
    return m / se if se > 0 else 0.0


# ------------------------------------------------------------------ 자료


def pit_members() -> tuple[list[str], list[set[str]]]:
    if hashlib.sha256(PIT.read_bytes()).hexdigest() != PIT_SHA:
        raise SystemExit("S&P 500 명단 해시가 문서 274 §1 과 다르다")
    csv.field_size_limit(10**8)
    with PIT.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))[1:]
    return [r[0] for r in rows], [set(r[1].split(",")) for r in rows]


def member_at(dates: Sequence[str], sets: Sequence[set[str]], day: str) -> set[str]:
    i = bisect.bisect_right(dates, day) - 1
    return sets[i] if i >= 0 else set()


def _ret(xs: Sequence[float | None], a: int, b: int) -> float | None:
    if a < 0 or xs[a] is None or xs[b] is None:
        return None
    return xs[b] / xs[a] - 1  # type: ignore[operator]


def price_features(
    close: Sequence[float | None], dvol: Sequence[float | None], t: int
) -> dict[str, float] | None:
    if t < 252:
        return None
    w = close[t - 252 : t + 1]
    if any(v is None for v in w):
        return None
    c: list[float] = [v for v in w if v is not None]
    d = dvol[t - 125 : t + 1]
    if any(v is None for v in d):
        return None
    dv: list[float] = [v for v in d if v is not None]
    long_dv = sum(dv) / 126
    rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - 63, len(c))]
    return {
        "r21": c[-1] / c[-22] - 1,
        "r63": c[-1] / c[-64] - 1,
        "r126": c[-1] / c[-127] - 1,
        "r252": c[-1] / c[0] - 1,
        "r12_1": c[-22] / c[0] - 1,
        "from_high": c[-1] / max(c) - 1,
        "vol63": statistics.pstdev(rets),
        "dollar_vol": (sum(dv[-21:]) / 21) / long_dv if long_dv > 0 else 0.0,
    }


def build_rows(universe: str) -> tuple[list[dict[str, Any]], Any]:
    """월말 · 종목별 특징(순위) · 정답. 반환 (행, us_market 판)."""
    mk, _ = us_market()
    days = mk.days
    sector = json.loads((STOCKS / "sectors.json").read_text(encoding="utf-8"))["sectors"]
    dvol: dict[str, list[float | None]] = {}
    for sym in mk.close:
        ser = load(STOCKS / f"{sym}.csv")
        m = {d: c * v for d, c, v in zip(ser.dates, ser.close, ser.volume, strict=True)}
        dvol[sym] = [m.get(d) for d in days]
    fund = Fundamentals()
    pdates, psets = pit_members()
    rows: list[dict[str, Any]] = []
    for t in range(len(days)):
        if days[t] < FIRST or not month_end_day(days, t):
            continue
        day = days[t]
        pool = [s for s in mk.close if mk.close[s][t] is not None]
        if universe == "U2":
            members = member_at(pdates, psets, day)
            pool = [s for s in pool if s in members]
        raw = {s: f for s in pool if (f := price_features(mk.close[s], dvol[s], t)) is not None}
        if len(raw) < 30:
            continue
        score = dict.fromkeys(raw, 0.0)
        for name in ("r21", "r63", "r126"):  # 113 의 LOOKS = 21 · 63 · 126
            for s, v in pct_rank({s: raw[s][name] for s in raw}).items():
                score[s] += v / len(LOOKS)
        by: dict[str, list[float]] = defaultdict(list)
        for s in raw:
            by[sector.get(s, "Unknown")].append(score[s])
        sec_score = {s: statistics.median(by[sector.get(s, "Unknown")]) for s in raw}
        revg = {s: g for s in raw if (g := growth(fund.rev.get(s, []), day)) is not None}
        opg = {s: g for s in raw if (g := growth(fund.op.get(s, []), day)) is not None}
        cols: dict[str, dict[str, float]] = {
            name: pct_rank({s: raw[s][name] for s in raw})
            for name in ("r21", "r63", "r126", "r252", "r12_1", "from_high", "vol63", "dollar_vol")
        }
        cols["score113"] = pct_rank(score)
        cols["sector113"] = pct_rank(sec_score)
        rr, oo = pct_rank(revg) if revg else {}, pct_rank(opg) if opg else {}
        cols["rev_yoy"] = {s: rr.get(s, 0.5) for s in raw}
        cols["op_yoy"] = {s: oo.get(s, 0.5) for s in raw}
        cols["fund_missing"] = {s: float(s not in rr or s not in oo) for s in raw}
        fwd = {
            s: (_ret(mk.close[s], t, t + HORIZON) if t + HORIZON < len(days) else None) for s in raw
        }
        labels = top_labels(fwd)
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
    return rows, mk


# ------------------------------------------------------------------ 학습 · 고르기


def walk_forward(rows: Sequence[dict[str, Any]], days: Sequence[str]) -> dict[str, list[float]]:
    """시험 해마다 다시 학습한 GBM · LR 확률 (행 순서, 시험 전 행은 NaN)."""
    import numpy as np  # type: ignore[import-not-found]
    from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-not-found]
    from sklearn.linear_model import LogisticRegression  # type: ignore[import-not-found]

    x = np.asarray([r["x"] for r in rows], dtype=float)
    y = np.asarray([-1 if r["y"] is None else r["y"] for r in rows])
    out = {"GBM": np.full(len(rows), np.nan), "LR": np.full(len(rows), np.nan)}
    for year in TEST_YEARS:
        tr = np.asarray([trainable(r["t"], days, year) and r["y"] is not None for r in rows])
        te = np.asarray([r["day"][:4] == str(year) for r in rows])
        if not te.any():
            continue
        gbm = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.05, max_depth=3, min_samples_leaf=50,
            l2_regularization=1.0, early_stopping=False, random_state=SEED,
        )  # fmt: skip
        gbm.fit(x[tr], y[tr])
        out["GBM"][te] = gbm.predict_proba(x[te])[:, 1]
        lr = LogisticRegression(C=1.0, max_iter=2000)
        lr.fit(x[tr] - 0.5, y[tr])
        out["LR"][te] = lr.predict_proba(x[te] - 0.5)[:, 1]
    return {k: v.tolist() for k, v in out.items()}


def model_picks(rows: Sequence[dict[str, Any]], prob: Sequence[float]) -> dict[int, list[str]]:
    by: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for r, p in zip(rows, prob, strict=True):
        if not math.isnan(p):
            by[r["t"]].append((-p, r["sym"]))
    return {t: [s for _p, s in sorted(xs)[:PICKS]] for t, xs in by.items()}


def base_picks(rows: Sequence[dict[str, Any]], mk: Any) -> dict[int, list[str]]:
    pools: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        if r["day"] >= TEST_FROM:
            pools[r["t"]].append(r["sym"])
    sector = _sectors()
    return {t: pick_in(mk.close, sector, t, pool) for t, pool in pools.items()}


def _sectors() -> dict[str, str]:
    raw = json.loads((STOCKS / "sectors.json").read_text(encoding="utf-8"))["sectors"]
    return {k: str(v) for k, v in raw.items()}


# ------------------------------------------------------------------ 평가


def precision(rows: Sequence[dict[str, Any]], picks: Mapping[int, list[str]]) -> dict[int, float]:
    lab = {(r["t"], r["sym"]): r["y"] for r in rows}
    labelled = {r["t"] for r in rows if r["y"] is not None}
    out = {}
    for t, xs in picks.items():
        if t in labelled and xs:
            out[t] = sum(1 for s in xs if lab.get((t, s)) == 1) / len(xs)
    return out


def auc_by_year(rows: Sequence[dict[str, Any]], prob: Sequence[float]) -> dict[str, float]:
    from sklearn.metrics import roc_auc_score  # type: ignore[import-not-found]

    out: dict[str, float] = {}
    groups: dict[str, tuple[list[int], list[float]]] = defaultdict(lambda: ([], []))
    for r, p in zip(rows, prob, strict=True):
        if r["y"] is not None and not math.isnan(p):
            for key in (r["day"][:4], "all"):
                groups[key][0].append(r["y"])
                groups[key][1].append(p)
    for key, (ys, ps) in sorted(groups.items()):
        if 0 < sum(ys) < len(ys):
            out[key] = float(roc_auc_score(ys, ps))
    return out


def yearly_catch(
    rows: Sequence[dict[str, Any]], mk: Any, arms: Mapping[str, Mapping[int, list[str]]]
) -> dict[str, Any]:
    days = mk.days
    pools: dict[int, set[str]] = defaultdict(set)
    for r in rows:
        pools[r["t"]].add(r["sym"])
    ends = sorted(pools)
    out: dict[str, Any] = {a: {} for a in arms}
    for year in range(2016, 2026):
        t0 = max(t for t in ends if days[t] < f"{year}-01-01")
        t1 = max(i for i, d in enumerate(days) if d <= f"{year}-12-31")
        perf = {s: v for s in pools[t0] if (v := _ret(mk.close[s], t0, t1)) is not None}
        top5 = sorted(perf, key=lambda s: (-perf[s], s))[:5]
        prior = [
            t for t in ends if days[t][:4] == str(year - 1) and days[t][5:7] in ("10", "11", "12")
        ]
        for a, picks in arms.items():
            out[a][str(year)] = caught(top5, (picks.get(t, []) for t in prior))
    return {a: {"by_year": v, "total": sum(v.values()), "of": 50} for a, v in out.items()}


def account(mk: Any, picks: Mapping[int, list[str]]) -> dict[str, Any]:
    m = dataclasses.replace(mk, pick=lambda t: list(picks.get(t, [])))
    r = simulate(m, "ARM", start_date=TEST_FROM)
    start = next(i for i, d in enumerate(mk.days) if d >= TEST_FROM)
    span = mk.days[start:]
    return {
        "full": stats(span, r.equity, TEST_FROM, span[-1]),
        "first": stats(span, r.equity, TEST_FROM, HALF[1]),
        "second": stats(span, r.equity, HALF[2], span[-1]),
        "trade": trade_stats(m, r),
    }


def judge(res: Mapping[str, Any], model: str = "GBM") -> dict[str, Any]:
    """문서 274 §7 (순수)."""

    def ok(u: str) -> dict[str, bool]:
        r = res[u]
        a, b = r["account"][model], r["account"]["BASE"]
        return {
            "precision_gt_base": r["precision"][model]["mean"] > r["precision"]["BASE"]["mean"],
            "nw_t_ge_2": r["precision_diff"][model]["nw_t"] >= 2.0,
            "sharpe_gain": a["full"]["sharpe"] >= b["full"]["sharpe"] + 0.10,
            "halves": all(a[h]["sharpe"] >= b[h]["sharpe"] for h in ("first", "second")),
            "mdd": a["full"]["mdd"] <= b["full"]["mdd"] + 0.05,
        }

    checks = {u: ok(u) for u in ("U1", "U2")}
    if all(all(c.values()) for c in checks.values()):
        status = "후속 가치(탐색)"
    elif not checks["U1"]["precision_gt_base"] or all(
        res[u]["account"][model]["full"]["sharpe"] < res[u]["account"]["BASE"]["full"]["sharpe"]
        for u in ("U1", "U2")
    ):
        status = "불리"
    else:
        status = "불명"
    return {"status": status, "checks": checks}


def _pool_sizes(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    per: dict[int, int] = defaultdict(int)
    year: dict[int, str] = {}
    for r in rows:
        per[r["t"]] += 1
        year[r["t"]] = r["day"][:4]
    by: dict[str, list[int]] = defaultdict(list)
    for t, n in per.items():
        by[year[t]].append(n)
    return {y: statistics.median(v) for y, v in sorted(by.items())}


def evaluate(universe: str) -> dict[str, Any]:
    rows, mk = build_rows(universe)
    print(json.dumps({"universe": universe, "rows": len(rows)}), flush=True)
    prob = walk_forward(rows, mk.days)
    arms = {"GBM": model_picks(rows, prob["GBM"]), "LR": model_picks(rows, prob["LR"])}
    arms["BASE"] = base_picks(rows, mk)
    test_t = {r["t"] for r in rows if r["day"] >= TEST_FROM}
    arms = {a: {t: v for t, v in p.items() if t in test_t} for a, p in arms.items()}
    prec = {a: precision(rows, p) for a, p in arms.items()}
    common = sorted(set.intersection(*(set(p) for p in prec.values())))
    labelled = [r for r in rows if r["y"] is not None and r["day"] >= TEST_FROM]
    out: dict[str, Any] = {
        "rows": len(rows),
        "pool_size_by_year": _pool_sizes(rows),
        "base_rate_test": sum(r["y"] for r in labelled) / len(labelled),
        "precision": {
            a: {"mean": statistics.fmean(p[t] for t in common), "months": len(common)}
            for a, p in prec.items()
        },
        "precision_diff": {
            a: {
                "mean": statistics.fmean(prec[a][t] - prec["BASE"][t] for t in common),
                "nw_t": nw_t([prec[a][t] - prec["BASE"][t] for t in common]),
            }
            for a in ("GBM", "LR")
        },
        "precision_by_year": {
            a: {
                y: statistics.fmean(v for t, v in p.items() if mk.days[t][:4] == y)
                for y in sorted({mk.days[t][:4] for t in p})
            }
            for a, p in prec.items()
        },
        "auc": {a: auc_by_year(rows, prob[a]) for a in ("GBM", "LR")},
        "yearly_top5": yearly_catch(rows, mk, arms),
        "account": {a: account(mk, p) for a, p in arms.items()},
        "last_picks": {a: {mk.days[t]: p[t] for t in sorted(p)[-3:]} for a, p in arms.items()},
    }
    if universe == "U1":
        base_2011 = simulate(mk, "ARM", start_date=FIRST)
        s0 = next(i for i, d in enumerate(mk.days) if d >= FIRST)
        out["check_261_base"] = stats(mk.days[s0:], base_2011.equity, FIRST, mk.days[-1])
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if sec_hash() != SEC_SHA:
        raise SystemExit("SEC 자료 해시가 261 과 다르다")
    res = {u: evaluate(u) for u in ("U1", "U2")}
    out = {
        "git": sha,
        "completed_utc": datetime.now(UTC).isoformat(),
        "verdict": {"GBM": judge(res, "GBM"), "LR": judge(res, "LR")},
        "results": res,
        "note": "문서 274 1 회. 지금 S&P 500 판(U1) · 그 시점 명단 교집합(U2), 빠진 종목 없음.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "run":
        r = run()
        print(json.dumps(r["verdict"], ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
