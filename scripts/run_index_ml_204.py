"""문서 204 — 나스닥100 · S&P 500 주간 ML(LR · XGBoost) 판정. 한 번만.

사용 예 (연구 환경 — 운영 .venv 에는 numpy · sklearn · xgboost 가 없다)::

    set PYTHONPATH=.;src
    build\\ml-venv\\Scripts\\python -m scripts.run_index_ml_204 run
    → reports/index_ml_204/result.json

사전 등록: docs/research/204-qqq-ml-preregistration.md. 이미 결과가 있으면 다시 돌지 않는다.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.research.ml_index import (
    FEATURES,
    HOLD,
    daily_exposure,
    features,
    holm,
    label,
    ledger,
    lr_se,
    max_drawdown,
    p_one_sided,
    sharpe,
    t_weight,
    weekly_ends,
)

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "index_ml_204"
END = "2026-08-31"
FILES = {
    "NDX": (
        REPO / "data/research/tradfi/fred/NASDAQ100.csv",
        "311fa922843ed508521899ac9776064f4e7f287eba5897269f2e8255f700faf8",
        ("observation_date", "NASDAQ100"),
        "1986-01-02",
    ),
    "SPX": (
        REPO / "data/research/qs_bottom_xmkt/GSPC.csv",
        "8683a966aece558fe33a5a9968a410194800076530b56c349b9cde772aea37e8",
        ("date", "close"),
        "1950-01-03",
    ),
}
DGS10 = (
    REPO / "data/research/multi_asset/DGS10.csv",
    "076bcb358011a4dd328915c1e52938fae963b5b57fe882009881859d0b803ef9",
)
TEST_YEARS = range(2000, 2027)
COST, STRESS = 0.0005, 0.0010
COMPARISONS = (("A_LR", "A0"), ("A_XGB", "A0"), ("B_LR", "T"), ("B_XGB", "T"))
CRISES = {
    "dotcom": ("2000-03", "2002-10"),
    "gfc": ("2007-10", "2009-03"),
    "covid": ("2020-02", "2020-03"),
    "y2022": ("2022-01", "2022-12"),
}


def _read(path: Path, sha: str, cols: tuple[str, str], start: str = "") -> dict[str, float]:
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise SystemExit(f"{path.name} 해시가 사전 등록과 다르다 — 판정하지 않는다")
    out = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            d, v = row[cols[0]][:10], row[cols[1]].strip()
            if v and v != "." and start <= d <= END:
                out[d] = float(v)
    return out


def _fit_predict(kind: str, x_tr: Any, y_tr: Any, x_te: Any) -> Any:
    import numpy as np  # type: ignore[import-not-found,unused-ignore]

    if kind == "LR":
        from sklearn import linear_model  # type: ignore[import-not-found,unused-ignore]

        mu, sd = x_tr.mean(axis=0), x_tr.std(axis=0)
        sd[sd == 0] = 1.0
        m = linear_model.LogisticRegression(C=1.0, max_iter=2000)
        m.fit((x_tr - mu) / sd, y_tr)
        return np.asarray(m.predict_proba((x_te - mu) / sd)[:, 1])
    from xgboost import XGBClassifier  # type: ignore[import-not-found,unused-ignore]

    m = XGBClassifier(
        n_estimators=200,
        max_depth=2,
        learning_rate=0.05,
        subsample=1.0,
        colsample_bytree=1.0,
        reg_lambda=1.0,
        min_child_weight=1,
        tree_method="hist",
        random_state=11,
        n_jobs=4,
        eval_metric="logloss",
    )
    m.fit(x_tr, y_tr)
    return np.asarray(m.predict_proba(x_te)[:, 1])


def auc(rows: Sequence[tuple[float, int]]) -> float:
    """순위 기반 AUC (동점은 평균 순위)."""
    order = sorted(range(len(rows)), key=lambda k: rows[k][0])
    ranks = [0.0] * len(rows)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and rows[order[j + 1]][0] == rows[order[i]][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    pos = [ranks[k] for k, (_, y) in enumerate(rows) if y == 1]
    n1, n0 = len(pos), len(rows) - len(pos)
    if n1 == 0 or n0 == 0:
        return 0.5
    return (sum(pos) - n1 * (n1 + 1) / 2) / (n1 * n0)


def cls_metrics(rows: Sequence[tuple[float, int]]) -> dict[str, float]:
    ys = [y for _, y in rows]
    base = statistics.fmean(ys)
    eps = 1e-12

    def ll(p: float, y: int) -> float:
        p = min(max(p, eps), 1 - eps)
        return -(y * math.log(p) + (1 - y) * math.log(1 - p))

    return {
        "n": len(rows),
        "base_rate": round(base, 4),
        "mean_p": round(statistics.fmean(p for p, _ in rows), 4),
        "auc": round(auc(rows), 4),
        "logloss": round(statistics.fmean(ll(p, y) for p, y in rows), 5),
        "logloss_base": round(statistics.fmean(ll(base, y) for _, y in rows), 5),
        "brier": round(statistics.fmean((p - y) ** 2 for p, y in rows), 5),
        "brier_base": round(base * (1 - base), 5),
    }


def _t(d: Sequence[float]) -> float:
    se = lr_se(d)
    return statistics.fmean(d) / se if se > 0 else 0.0


def _diff(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [x - y for x, y in zip(a, b, strict=True)]


def market(name: str, yields: dict[str, float]) -> dict[str, Any]:
    import numpy as np  # type: ignore[import-not-found,unused-ignore]

    path, sha, cols, start = FILES[name]
    series = _read(path, sha, cols, start)
    dates = sorted(series)
    px = [series[d] for d in dates]
    n = len(px)
    feats = features(dates, px, yields)
    weeks = [i for i in weekly_ends(dates) if i in feats]
    labs = {i: lab for i in weeks if (lab := label(px, i)) is not None}

    # 1) 걷기 앞으로 예측 — 시험 해마다 그 해 1 월 1 일 전에 정답이 끝난 주만 학습
    probs: dict[str, dict[int, float]] = {}
    base_rate: dict[tuple[str, int], float] = {}
    cls: dict[str, list[tuple[float, int]]] = {}
    train_n: dict[str, list[int]] = {}
    for target, col in (("up", 1), ("dn", 2)):
        for kind in ("LR", "XGB"):
            key = f"{kind}_{target}"
            probs[key], cls[key], train_n[key] = {}, [], []
            for year in TEST_YEARS:
                cut = f"{year}-01-01"
                tr = [i for i in weeks if i in labs and dates[i + 1 + HOLD] < cut]
                te = [i for i in weeks if dates[i][:4] == str(year)]
                if not te:
                    continue
                train_n[key].append(len(tr))
                y_tr = np.array([labs[i][col] for i in tr])
                x_tr = np.array([feats[i] for i in tr])
                p = _fit_predict(kind, x_tr, y_tr, np.array([feats[i] for i in te]))
                base_rate[(key, year)] = float(y_tr.mean())
                for i, v in zip(te, p, strict=True):
                    probs[key][i] = float(v)
                    if i in labs:
                        cls[key].append((float(v), int(labs[i][col])))

    # 2) 전략 — 모두 시험 첫 주부터 같은 출발
    first_test = min(probs["LR_up"])
    test_weeks = [i for i in weeks if i >= first_test]
    t_daily = {i: w for i in range(n) if (w := t_weight(px, i)) is not None}
    week_keys = sorted(probs["LR_dn"])

    def b_plan(key: str) -> dict[int, float]:
        mult = {}
        for i, p in probs[key].items():
            b = base_rate[(key, int(dates[i][:4]))]
            mult[i] = 0.0 if p >= 3 * b else 0.5 if p >= 2 * b else 1.0
        out = {}
        for d, w in t_daily.items():
            j = bisect.bisect_right(week_keys, d) - 1  # 그날까지 나온 주간 배율
            out[d] = w * (mult[week_keys[j]] if j >= 0 else 1.0)
        return out

    plans: dict[str, dict[int, float]] = {
        "BH": dict.fromkeys(test_weeks, 1.0),
        "A0": {i: float(px[i] > px[i - 20]) for i in test_weeks},
        "A_LR": {i: float(p >= 0.5) for i, p in probs["LR_up"].items()},
        "A_XGB": {i: float(p >= 0.5) for i, p in probs["XGB_up"].items()},
        "T": t_daily,
        "B_LR": b_plan("LR_dn"),
        "B_XGB": b_plan("XGB_dn"),
    }
    first_month = dates[first_test][:7]
    monthly: dict[str, dict[str, float]] = {}
    exposure: dict[str, list[float]] = {}
    for k, plan in plans.items():
        exposure[k] = daily_exposure(n, plan)
        for tag, c in (("", COST), ("@10bp", STRESS)):
            led = ledger(dates, px, exposure[k], c)
            monthly[k + tag] = {m: r for m, r in led.items() if m >= first_month}
    months = sorted(monthly["T"])
    half = len(months) // 2

    def series_of(k: str) -> list[float]:
        return [monthly[k][m] for m in months]

    summary = {}
    for k in plans:
        s = series_of(k)
        ex = exposure[k][first_test:]
        summary[k] = {
            "cagr": round(math.prod(1 + r for r in s) ** (12 / len(s)) - 1, 4),
            "sharpe": round(sharpe(s), 3),
            "mdd": round(max_drawdown(s), 4),
            "exposure_mean": round(statistics.fmean(ex), 3),
            "turnover_per_year": round(
                sum(abs(ex[j] - ex[j - 1]) for j in range(1, len(ex))) / (len(ex) / 252), 2
            ),
        }

    def window_return(k: str, lo: str, hi: str) -> float:
        return round(math.prod(1 + monthly[k][m] for m in months if lo <= m <= hi) - 1, 4)

    comps = {}
    for model, base in COMPARISONS:
        d = _diff(series_of(model), series_of(base))
        d10 = _diff(series_of(model + "@10bp"), series_of(base + "@10bp"))
        t = _t(d)
        decades = {
            f"{dec}0s": round(
                statistics.fmean(x for x, m in zip(d, months, strict=True) if m[:3] == dec), 5
            )
            for dec in ("200", "201", "202")
        }
        comps[f"{name}_{model}"] = {
            "vs": base,
            "mean_diff_month": round(statistics.fmean(d), 5),
            "t": round(t, 2),
            "p_one_sided": p_one_sided(t),
            "halves": [round(statistics.fmean(d[:half]), 5), round(statistics.fmean(d[half:]), 5)],
            "mean_diff_10bp": round(statistics.fmean(d10), 5),
            "sharpe_model_vs_base": [summary[model]["sharpe"], summary[base]["sharpe"]],
            "mdd_model_vs_base": [summary[model]["mdd"], summary[base]["mdd"]],
            "decades": decades,
        }
    return {
        "first_test_month": months[0],
        "last_month": months[-1],
        "months": len(months),
        "half_split": months[half],
        "first_feature_date": dates[weeks[0]],
        "train_rows_first_last": {k: [v[0], v[-1]] for k, v in train_n.items()},
        "weeks_scored": {k: len(v) for k, v in probs.items()},
        "summary": summary,
        "classification": {k: cls_metrics(rows) for k, rows in cls.items()},
        "xgb_minus_lr_t": {
            g: round(_t(_diff(series_of(f"{g}_XGB"), series_of(f"{g}_LR"))), 2) for g in ("A", "B")
        },
        "crises": {
            c: {k: window_return(k, lo, hi) for k in plans} for c, (lo, hi) in CRISES.items()
        },
        "comparisons": comps,
    }


def judge(comps: dict[str, dict[str, Any]], xgb_lr_t: dict[str, float]) -> dict[str, str]:
    """사전 등록 §6. Holm 은 8 개 비교 전부(음수 평균은 p > 0.5 로 자연히 탈락)."""
    passed = holm({k: v["p_one_sided"] for k, v in comps.items()})
    verdict = {}
    for k, v in comps.items():
        crit = {
            "P1": v["mean_diff_month"] > 0 and passed[k],
            "P2": all(h > 0 for h in v["halves"]),
            "P3": v["mean_diff_10bp"] > 0,
            "P4": v["sharpe_model_vs_base"][0] >= v["sharpe_model_vs_base"][1],
        }
        if "_B_" in k:
            crit["P5"] = v["mdd_model_vs_base"][0] <= v["mdd_model_vs_base"][1] + 0.02
        v["criteria"] = crit
        verdict[k] = "HISTORICAL_SUPPORT" if all(crit.values()) else "REJECT"
    for k, v in comps.items():
        mkt, grp, kind = k.split("_")
        if kind == "XGB":
            v["xgb_beats_lr"] = (
                verdict[k] == "HISTORICAL_SUPPORT" and xgb_lr_t[f"{mkt}_{grp}"] >= 1.96
            )
    return verdict


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    import numpy  # type: ignore[import-not-found,unused-ignore]
    import sklearn  # type: ignore[import-not-found,unused-ignore]
    import xgboost  # type: ignore[import-not-found,unused-ignore]

    yields = _read(DGS10[0], DGS10[1], ("observation_date", "DGS10"))
    out: dict[str, Any] = {
        "computed": datetime.now(UTC).isoformat(timespec="seconds"),
        "versions": {
            "python": sys.version.split()[0],
            "numpy": numpy.__version__,
            "sklearn": sklearn.__version__,
            "xgboost": xgboost.__version__,
        },
        "features": list(FEATURES),
    }
    comps: dict[str, Any] = {}
    xgb_lr: dict[str, float] = {}
    for name in FILES:
        out[name] = market(name, yields)
        comps.update(out[name]["comparisons"])
        for g, t in out[name]["xgb_minus_lr_t"].items():
            xgb_lr[f"{name}_{g}"] = t
    out["verdict"] = judge(comps, xgb_lr)
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_index_ml_204 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
