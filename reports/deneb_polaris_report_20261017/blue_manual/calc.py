"""파란 설명서 시안 숫자 — calc_spec.md 그대로 (설명용 회계, 판정 아님).

    .venv/Scripts/python.exe reports/deneb_polaris_report_20261017/blue_manual/calc.py

기존 원장 full_report/calculation.py 와 같은 함수 · 자료 · 비용. 다섯 납입액을 각각 실행한다.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path
from types import FrameType
from typing import Any

import numpy as np

R = Path(__file__).resolve().parents[3]
W = Path(__file__).resolve().parent
sys.path[:0] = [str(R), str(R / "src")]
sys.path.append(str(R / ".venv/Lib/site-packages"))
from scripts import run_brake_183 as b183  # noqa: E402
from scripts import run_brake_184 as b  # noqa: E402
from scripts import run_namu_core_compare as c  # noqa: E402

END = "2026-08-31"
AMOUNTS = [100_000, 200_000, 300_000, 500_000, 1_000_000]
LEDGER = R / "reports/deneb_polaris_report_20261017/full_report/calculation_result.json"
PANELS = [("2012-01", "2015-12"), ("2016-01", "2019-12"), ("2020-01", "2022-12"), ("2023-01", "2026-08")]
CASES = [
    # 이름, 레버리지 종목, 브레이크, 변동성 맞춤, 상한, 원장 이름
    ("Deneb", "TQQQ", "T", True, 0.6, "Deneb"),
    ("변동성 조절만", "TQQQ", None, True, 0.6, "방어 제외"),
    ("TQQQ 60 + SCHD 40 고정", "TQQQ", None, False, 0.6, "3배+SCHD 고정"),
    ("QLD 60 + SCHD 40 고정", "QLD", None, False, 0.6, "2배+SCHD 고정"),
    ("QQQ 60 + SCHD 40 고정", "QQQ", None, False, 0.6, "1배+SCHD 고정"),
    ("QQQ 단독 적립", "QQQ", None, False, 1.0, "나스닥1배"),
    ("QLD 단독 적립", "QLD", None, False, 1.0, "나스닥2배"),
    ("TQQQ 단독 적립", "TQQQ", None, False, 1.0, "나스닥3배"),
    ("S&P500 단독 적립(SPY)", "SPY", None, False, 1.0, "S&P500"),
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def signals(days: list[str], q: dict[str, float]) -> tuple[dict, dict]:
    """full_report/calculation.py 와 같은 식 (183 과 같음을 아래에서 확인)."""
    a = np.array([q[d] for d in days])
    r = a[1:] / a[:-1] - 1
    v = np.full(len(a), np.nan)
    t = np.full(len(a), np.nan)
    v[20:] = np.std(np.lib.stride_tricks.sliding_window_view(r, 20), axis=1) * np.sqrt(252)
    t[120:] = (np.sign(a[120:] - a[100:-20]) + np.sign(a[120:] - a[60:-60]) + np.sign(a[120:] - a[:-120])) > 0
    return (
        dict(zip(days, [None if np.isnan(x) else float(x) for x in v], strict=True)),
        dict(zip(days, [None if np.isnan(x) else bool(x) for x in t], strict=True)),
    )


def capture_pol(px: dict, days: list[str], i0: int, i1: int, pol: str, amount: float) -> dict:
    got: dict[str, Any] = {}

    def prof(f: FrameType, e: str, a: Any) -> None:
        if e == "return" and f.f_code is c.run_policy.__code__:
            got["values"] = list(f.f_locals["vals"])
            got["nav"] = list(f.f_locals["navs"])

    sys.setprofile(prof)
    try:
        res = c.run_policy(px, days, i0, i1, pol, lambda _: float(amount))
    finally:
        sys.setprofile(None)
    return {**res, **got, "dates": days[i0 : i1 + 1]}


def metrics(days: list[str], vals: list[float], nav: list[float], amount: float) -> dict[str, Any]:
    a = np.asarray(nav)
    peak = np.maximum.accumulate(a)
    dd = a / peak - 1
    tr = int(np.argmin(dd))
    pk = int(np.argmax(a[: tr + 1]))
    rec = next((i for i in range(tr, len(a)) if a[i] >= a[pk]), None)
    yrs = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days / 365.25
    months = sorted({d[:7] for d in days})
    paid = len(months) * amount
    # 돈 기준: 일간 평가액 고점 → 이후 저점 최대 감소액
    v = np.asarray(vals)
    vpeak = np.maximum.accumulate(v)
    drop = vpeak - v
    j = int(np.argmax(drop))
    jp = int(np.argmax(v[: j + 1]))
    # 달말 평가액 < 누적 원금
    last = {d[:7]: x for d, x in zip(days, vals, strict=True)}
    under = [m for k, m in enumerate(months) if last[m] < (k + 1) * amount]
    return {
        "final": float(vals[-1]),
        "paid": paid,
        "profit": float(vals[-1] - paid),
        "cagr": float((a[-1] / a[0]) ** (1 / yrs) - 1),
        "mdd": float(dd.min()),
        "peak": days[pk],
        "trough": days[tr],
        "recovery": days[rec] if rec is not None else None,
        "recovery_days": (date.fromisoformat(days[rec]) - date.fromisoformat(days[pk])).days if rec is not None else None,
        "money_drop": float(drop[j]),
        "money_drop_from": days[jp],
        "money_drop_to": days[j],
        "money_drop_peak_value": float(v[jp]),
        "under_paid_months": len(under),
        "under_paid_list": under,
    }


def month_end(days: list[str], vals: list[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d, x in zip(days, vals, strict=True):
        out[d[:7]] = float(x)
    return out


def savings(amount: float, n: int, r: float, tax: float = 0.0) -> float:
    """12 개월 적금 단리 → 만기금 같은 금리 1 년 예금 재예치(연 복리) → 덜 찬 기간 비례 단리."""
    dep = 0.0  # 예금 잔액
    cyc: list[int] = []  # 이번 적금 회차의 납입 달
    for k in range(n + 1):
        if k > 0 and k % 12 == 0:
            interest = sum(amount * r * (k - j) / 12 for j in cyc)
            dep_int = dep * r
            dep = dep + dep_int * (1 - tax) + amount * len(cyc) + interest * (1 - tax)
            cyc = []
        if k < n:
            cyc.append(k)
    part = n % 12
    interest = sum(amount * r * (n - j) / 12 for j in cyc) + dep * r * part / 12
    return dep + amount * len(cyc) + interest * (1 - tax)


def annual(px: dict[str, float], days: list[str]) -> dict[str, float]:
    last: dict[str, float] = {}
    for d in days:
        last[d[:4]] = px[d]
    ys = sorted(last)
    return {y: last[y] / last[p] - 1 for p, y in zip(ys, ys[1:], strict=False) if "2012" <= y}


def main() -> None:
    b.TAX = 0.0
    c.TAX_RATE = 0.0
    c.FEE = 0.0015
    px = {s: c.load(R / f"data/research/div_mix_180/{s}.csv") for s in ["QQQ", "SCHD", "SPY", "IEF", "GLD"]}
    px["TQQQ"] = c.load(R / "data/research/lev_div_181/TQQQ.csv")
    px["QLD"] = c.load(R / "data/research/lev_div_181/QLD.csv")
    common = sorted(d for d in set.intersection(*(set(v) for v in px.values())) if d <= END)
    i0 = next(i for i, d in enumerate(common) if d >= "2012-01-01")
    vol, tr = signals(common, px["QQQ"])
    ov, ot = b183.signals(common, px["QQQ"])
    assert all((vol[d] is None and ov[d] is None) or math.isclose(vol[d], ov[d], abs_tol=1e-12) for d in common)
    assert tr == ot

    def den(sym: str, ref: str | None, vol_on: bool, cap: float, amount: float) -> dict:
        prev = b.LEV_MAX
        b.LEV_MAX = cap
        try:
            return b.simulate(
                {"days": common, "lev": px[sym], "div": px["SCHD"], "vol": vol if vol_on else {}, "trend": tr, "start": "2012-01-01"},
                px["QQQ"],
                ref=ref,
                monthly_krw=amount,
            )
        finally:
            b.LEV_MAX = prev

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))["historical"]
    runs: dict[str, dict[int, dict[str, Any]]] = {}
    monthly10: dict[str, dict[str, float]] = {}
    brake: dict[str, Any] = {}
    for name, sym, ref, v_on, cap, old in CASES:
        runs[name] = {}
        for m in AMOUNTS:
            sim = den(sym, ref, v_on, cap, m)
            runs[name][m] = metrics(sim["nav_days"], sim["vals"], sim["navs"], m)
            if m == 200_000:
                assert math.isclose(runs[name][m]["final"], ledger[old]["metrics"]["final"], rel_tol=1e-9), name
            if m == 100_000:
                monthly10[name] = month_end(sim["nav_days"], sim["vals"])
                if name == "Deneb":
                    eps = sim["episodes"]
                    brake = {
                        "sales": sim["sales"],
                        "episodes": eps,
                        "n_sales": len(sim["sales"]),
                        "n_episodes": len(eps),
                        "rebuy_higher_share": sum(e["rebuy"] > e["avg_sale"] for e in eps) / len(eps),
                    }
    runs["Polaris"] = {}
    for m in AMOUNTS:
        pol = capture_pol(px, common, i0, len(common) - 1, "YEAR", m)
        runs["Polaris"][m] = metrics(pol["dates"], pol["values"], pol["nav"], m)
        if m == 200_000:
            assert math.isclose(runs["Polaris"][m]["final"], ledger["Polaris"]["metrics"]["final"], rel_tol=1e-9)
        if m == 100_000:
            monthly10["Polaris"] = month_end(pol["dates"], pol["values"])

    n = len({d[:7] for d in common[i0:]})
    assert n == 176
    linear = {}
    for name, rr in runs.items():
        base = rr[100_000]["final"] / 100_000
        linear[name] = max(abs(rr[m]["final"] / m / base - 1) for m in AMOUNTS)
        assert linear[name] < 1e-9, (name, linear[name])
        assert all(rr[m]["paid"] == n * m for m in AMOUNTS)
    sav = {
        f"r{int(r * 100)}": {
            str(m): {"pretax": savings(m, n, r), "after_tax": savings(m, n, r, 0.154)} for m in AMOUNTS
        }
        for r in (0.03, 0.04)
    }
    # 5 년 확인: 기존 원장 save() 와 같은 값
    z = 0.0
    for _ in range(5):
        z = z * 1.04 + 12 * 100_000 + 6.5 * 100_000 * 0.04
    assert math.isclose(savings(100_000, 60, 0.04), z, rel_tol=1e-12)

    days = common[i0 - 1 :]
    yearly = {s: annual(px[s], common) for s in ["SPY", "QQQ", "IEF", "GLD", "SCHD", "TQQQ"]}
    out = {
        "spec_sha": sha(W / "calc_spec.md"),
        "period": [common[i0], common[-1]],
        "deposits": n,
        "amounts": AMOUNTS,
        "runs": {k: {str(m): v for m, v in rr.items()} for k, rr in runs.items()},
        "monthly_10": monthly10,
        "panels": PANELS,
        "savings": sav,
        "yearly": yearly,
        "brake_10": brake,
        "checks": {
            "ledger_200k_reproduced": True,
            "linear_max_rel_err": linear,
            "deposits_176": True,
            "savings_5y_matches_ledger_formula": True,
            "first_day": days[1],
            "decision": "설명용 회계 — 새 PASS · OOS · 운영 변경 없음",
        },
    }
    (W / "calc_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, rr in runs.items():
        x = rr[100_000]
        print(f"{name:24s} 끝 {x['final'] / 1e4:9,.0f}만 이익 {x['profit'] / 1e4:9,.0f}만 연 {x['cagr']:.1%} 낙폭 {x['mdd']:.1%} 회복 {x['recovery_days']}일 감소액 {x['money_drop'] / 1e4:,.0f}만 원금하회 {x['under_paid_months']}달")
    print(json.dumps(sav, ensure_ascii=False))
    print("brake", brake["n_sales"], brake["n_episodes"], brake["rebuy_higher_share"])


if __name__ == "__main__":
    main()
