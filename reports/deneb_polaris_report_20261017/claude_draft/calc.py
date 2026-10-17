"""Claude 보고서 시안 6쪽 계산 — calc_spec.md (cb5c3f4) 그대로. 보고서 전용, 매매 코드 불변.

    .venv/Scripts/python.exe reports/deneb_polaris_report_20261017/claude_draft/calc.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

import scripts.run_brake_184 as rb  # noqa: E402
import scripts.run_namu_core_compare as nc  # noqa: E402

START, END = "2012-01-01", "2026-08-31"
MONTHLY = 200_000.0
COST = 0.0015
MARKS = {"5y": "2016-12-31", "10y": "2021-12-31", "end": END}
CRISES = {
    "2018 4분기": ("2018-10-01", "2018-12-31"),
    "2020 코로나": ("2020-02-01", "2020-04-30"),
    "2022 금리 인상": ("2022-01-01", "2022-12-31"),
}


# ── Deneb (184 simulate, TQQQ 비중 기록만 덧붙임) ──────────────────────────
class RecBook(rb.Book):
    current: RecBook | None = None

    def __init__(self) -> None:
        super().__init__()
        RecBook.current = self


class Snap(dict):  # type: ignore[type-arg]
    """SCHD 가격 dict — 하루 마지막 조회 때 TQQQ 수량을 적는다(그날 끝 상태)."""

    lq: dict[str, float]

    def __getitem__(self, d: str) -> float:
        bk = RecBook.current
        if bk is not None:
            self.lq[d] = bk.q["L"]
        return dict.__getitem__(self, d)


def deneb_runs() -> dict[str, dict[str, Any]]:
    rb.Book = RecBook  # type: ignore[misc]
    rb.TAX = 0.0
    data = rb.data_real()
    qqq = rb.qqq_real()
    out: dict[str, dict[str, Any]] = {}
    for name, kw, vol in (
        ("Deneb", {"ref": "T", "park": "cash"}, data["vol"]),
        ("변동성 맞춤만", {"ref": None}, data["vol"]),
        ("고정 60/40", {"ref": None}, {}),
    ):
        div = Snap(data["div"])
        div.lq = {}
        d2 = {**data, "div": div, "vol": vol}
        sim = rb.simulate(d2, qqq, monthly_krw=MONTHLY, **kw)
        days = sim["nav_days"]
        paid, acc, fl = [], 0.0, dict(sim["flows"])
        for d in days:
            acc += fl.get(d, 0.0)
            paid.append(acc)
        lev_share = [div.lq.get(d, 0.0) * data["lev"][d] / v if v else 0.0 for d, v in zip(days, sim["vals"], strict=True)]
        cash_share = [
            1 - ls - dv / v if v else 0.0
            for ls, dv, v in zip(lev_share, sim["div_vals"], sim["vals"], strict=True)
        ]
        out[name] = {
            "days": days,
            "vals": sim["vals"],
            "navs": sim["navs"],
            "paid": paid,
            "lev_share": lev_share,
            "cash_share": cash_share,
            "sells": sim["all_sells"],
            "brake_sales": len(sim["sales"]),
            "episodes": [{k: e[k] for k in ("start", "end")} for e in sim["episodes"]],
        }
    return out


def deneb_check() -> float:
    """184 R T 의 세전 CAGR 35.59% 재현 확인(원래 30 만 원 · 세금 그대로)."""
    data = rb.data_real()
    sim = rb.simulate(data, rb.qqq_real(), ref="T", park="cash")
    return float(rb.summarize(sim, rb.CRISES["R"])["cagr"])


# ── Polaris (run_policy YEAR 복사 + 경로) ──────────────────────────────────
def polaris_policy(
    px: Mapping[str, nc.Series], days: list[str], i0: int, i1: int, policy: str,
    deposit: Callable[[int], float], fee: float, tax_rate: float,
) -> dict[str, Any]:
    target, stocks = nc.TARGET, nc.STOCKS
    q = dict.fromkeys(target, 0.0)
    basis = dict.fromkeys(target, 0.0)
    cash = units = paid = 0.0
    realized: dict[str, float] = {}
    vals: list[float] = []
    navs: list[float] = []
    paids: list[float] = []
    weights: list[dict[str, float]] = []
    trades: list[dict[str, Any]] = []
    k = -1
    for i in range(i0, i1 + 1):
        d = days[i]
        price = {s: px[s][d] for s in target}
        value = cash + sum(q[s] * price[s] for s in target)
        if i > i0 and d[:4] != days[i - 1][:4]:
            gain = realized.get(days[i - 1][:4], 0.0)
            tax = max(0.0, gain - nc.TAX_FREE) * tax_rate
            if tax > 0 and value > 0:
                scale = (value - tax) / value
                q = {s: v * scale for s, v in q.items()}
                basis = {s: v * scale for s, v in basis.items()}
                cash *= scale
                value -= tax
        if i == i0 or d[:7] != days[i - 1][:7]:
            k += 1
            add = deposit(k)
            nav = value / units if units else 1.0
            units += add / nav
            cash += add
            paid += add
            value += add
            w = {s: q[s] * price[s] / value for s in target} if value else dict(target)
            full = (
                policy == "MONTH"
                or (policy == "YEAR" and d[5:7] == "12" and days[i - 1][5:7] != "12")
                or (policy == "BAND" and any(abs(w[s] - t) > nc.BAND for s, t in target.items()))
            ) and sum(q.values()) > 0
            if full:
                want = {s: value * t / price[s] for s, t in target.items()}
                turn = sum(abs(want[s] - q[s]) * price[s] for s in target)
                trades.append({
                    "day": d,
                    "before": {s: q[s] * price[s] / value for s in target},
                    "sell": {s: (q[s] - want[s]) * price[s] for s in target if want[s] < q[s]},
                    "buy": {s: (want[s] - q[s]) * price[s] for s in target if want[s] >= q[s]},
                    "fee": turn * fee,
                })
                for s in target:
                    if want[s] < q[s]:
                        sold = q[s] - want[s]
                        avg = basis[s] / q[s] if q[s] else 0.0
                        realized[d[:4]] = realized.get(d[:4], 0.0) + sold * (price[s] - avg)
                        basis[s] -= sold * avg
                    else:
                        basis[s] += (want[s] - q[s]) * price[s]
                scale = (value - turn * fee) / value
                q = {s: want[s] * scale for s in target}
                cash = 0.0
                value -= turn * fee
            elif cash > 0:
                spend = cash / (1 + fee)
                need = {s: max(0.0, value * t - q[s] * price[s]) for s, t in target.items()}
                tot = sum(need.values())
                if tot >= spend:
                    buy = {s: spend * need[s] / tot for s in target}
                else:
                    buy = {s: need[s] + (spend - tot) * t for s, t in target.items()}
                for s in target:
                    q[s] += buy[s] / price[s]
                    basis[s] += buy[s]
                value -= cash - spend
                cash = 0.0
        vals.append(value)
        navs.append(value / units if units else 1.0)
        paids.append(paid)
        weights.append({s: q[s] * price[s] / value if value else 0.0 for s in target})
    _ = stocks
    return {"days": days[i0 : i1 + 1], "vals": vals, "navs": navs, "paid": paids,
            "weights": weights, "trades": trades, "realized": realized}


def polaris_runs() -> dict[str, Any]:
    px = {s: nc.load(nc.DATA / "div_mix_180" / f"{s}.csv") for s in nc.TARGET}
    days = sorted(set.intersection(*(set(px[s]) for s in nc.TARGET)))
    days = [d for d in days if "2011-12-01" <= d <= END]
    i0 = next(k for k, d in enumerate(days) if d >= START)
    i1 = len(days) - 1
    dep = lambda k: MONTHLY  # noqa: E731
    orig = nc.run_policy(px, days, i0, i1, "YEAR", dep)
    mine = polaris_policy(px, days, i0, i1, "YEAR", dep, nc.FEE, nc.TAX_RATE)
    check = abs(orig["final"] - mine["vals"][-1])
    assert check < 1e-6, check
    res = polaris_policy(px, days, i0, i1, "YEAR", dep, COST, 0.0)
    res["copy_check_abs_diff"] = check
    return res


# ── 적금 ────────────────────────────────────────────────────────────────────
def savings(rate: float, months: list[str]) -> list[float]:
    """t 번째 달 초 납입, 달 말 평가(발생 이자 포함, 세전). 12 달마다 적금 만기 → 예금 이월(연 복리)."""
    mat = sum(MONTHLY * (1 + rate * (12 - j) / 12) for j in range(12))
    out = []
    for t in range(len(months)):
        full = (t + 1) // 12
        bank = 0.0
        for _ in range(full):
            bank = bank * (1 + rate) + mat
        m = (t + 1) - 12 * full
        cur = sum(MONTHLY * (1 + rate * (t + 1 - j) / 12) for j in range(12 * full, t + 1))
        out.append(bank * (1 + rate * m / 12) + cur)
    return out


# ── 지표 ────────────────────────────────────────────────────────────────────
def metrics(days: list[str], vals: list[float], navs: list[float], paid: list[float]) -> dict[str, Any]:
    peak, mdd, pi, ti, best_peak = navs[0], 0.0, 0, 0, 0
    for i, n in enumerate(navs):
        if n > navs[best_peak]:
            best_peak = i
        dd = n / navs[best_peak] - 1
        if dd < mdd:
            mdd, pi, ti = dd, best_peak, i
    rec = next((i for i in range(ti, len(navs)) if navs[i] >= navs[pi]), None)

    def months(a: str, b: str) -> int:
        return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])

    gap = [v - p for v, p in zip(vals, paid, strict=True)]
    gi = min(range(len(gap)), key=gap.__getitem__)
    crises = {}
    for name, (a, b) in CRISES.items():
        idx = [i for i, d in enumerate(days) if a <= d <= b]
        pk, worst = navs[idx[0]], 0.0
        for i in idx:
            pk = max(pk, navs[i])
            worst = min(worst, navs[i] / pk - 1)
        crises[name] = worst
    at = {}
    for k, d in MARKS.items():
        i = max(j for j, x in enumerate(days) if x <= d)
        at[k] = {"day": days[i], "paid": paid[i], "value": vals[i], "profit": vals[i] - paid[i]}
    return {
        "mdd": mdd, "mdd_peak": days[pi], "mdd_trough": days[ti],
        "recovered": days[rec] if rec is not None else None,
        "recovery_months_from_peak": months(days[pi], days[rec]) if rec is not None else None,
        "worst_gap": gap[gi], "worst_gap_day": days[gi],
        "days_below_paid": sum(1 for g in gap if g < 0),
        "crises": crises, "at": at,
    }


def month_end(days: list[str], *series: list[float]) -> tuple[list[str], list[list[float]]]:
    idx = [i for i in range(len(days)) if i == len(days) - 1 or days[i][:7] != days[i + 1][:7]]
    return [days[i][:7] for i in idx], [[s[i] for i in idx] for s in series]


def main() -> None:
    target = HERE / "calc_result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    cagr184 = deneb_check()
    dn = deneb_runs()
    po = polaris_runs()
    res: dict[str, Any] = {"spec_commit": "cb5c3f4", "check_184_T_cagr": cagr184,
                           "polaris_copy_check": po["copy_check_abs_diff"], "models": {}}
    for name, r in dn.items():
        m = metrics(r["days"], r["vals"], r["navs"], r["paid"])
        months, (v, p, ls, cs) = month_end(r["days"], r["vals"], r["paid"], r["lev_share"], r["cash_share"])
        res["models"][name] = {**m, "sells": r["sells"], "brake_sales": r["brake_sales"],
                               "episodes": r["episodes"],
                               "monthly": {"month": months, "value": v, "paid": p,
                                           "tqqq_share": ls, "cash_share": cs}}
    m = metrics(po["days"], po["vals"], po["navs"], po["paid"])
    months, (v, p) = month_end(po["days"], po["vals"], po["paid"])
    _, ws = month_end(po["days"], *[[w[s] for w in po["weights"]] for s in nc.TARGET])
    res["models"]["Polaris"] = {**m, "trades": po["trades"],
                                "monthly": {"month": months, "value": v, "paid": p,
                                            "weights": dict(zip(nc.TARGET, ws, strict=True))}}
    for r in (0.03, 0.04):
        sv = savings(r, months)
        res["models"][f"적금 {int(r * 100)}%"] = {
            "monthly": {"month": months, "value": sv, "paid": p},
            "at": {k: {"value": sv[max(i for i, x in enumerate(months) if x <= d[:7])],
                       "paid": p[max(i for i, x in enumerate(months) if x <= d[:7])]}
                   for k, d in MARKS.items()},
        }
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("184 T CAGR", round(cagr184, 4), "polaris copy diff", po["copy_check_abs_diff"])
    for k, x in res["models"].items():
        a = x["at"]["end"]
        print(f"{k:10} 끝 {a['value'] / 1e4:,.0f}만 (원금 {a['paid'] / 1e4:,.0f}만)",
              f"MDD {x['mdd']:.1%}" if "mdd" in x else "")


if __name__ == "__main__":
    main()
