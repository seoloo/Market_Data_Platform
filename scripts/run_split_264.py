"""문서 264 — 적립 배분 Deneb · Vega · Altair 80 · 10 · 10 vs 70 · 20 · 10 (사전 등록 7de34be).

사용 예::

    python -m scripts.run_split_264   # 한 번 → reports/split_264/result.json

기존 엔진 그대로: Deneb = 184 T, Vega = 197 T/v2(김프 없는 가격, 200 ``pair``),
Altair = 235 AG4 + 숏 2 배 BASE(232 OWN). 세 곳 사이 돈을 옮기지 않고 새 돈만 나눈다. 세전.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_lev_235 import account_with_leverage
from auto_trading.research.ag_short_own_gate_232 import account_class
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as wdata
from scripts.run_brake_183 import data_real
from scripts.run_cruise_kimp_200 import pair
from scripts.run_cruise_range_197 import combo_stats, month_end, paid_by, simulate_cruise

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "split_264"
TOTAL = 1_000_000.0
START, END = "2020-06-01", "2026-08-31"
MIXES = {
    "A 80·10·10": (0.8, 0.1, 0.1),
    "B 70·20·10": (0.7, 0.2, 0.1),
    "Deneb만": (1.0, 0.0, 0.0),
    "Vega만": (0.0, 1.0, 0.0),
    "Altair만": (0.0, 0.0, 1.0),
}

Part = tuple[dict[str, float], dict[str, float]]


def altair_monthly() -> dict[str, float]:
    """AG4 + 숏 2 배 BASE 한 번 넣은 계좌 → 달 수익률."""
    if base_run.INPUT_ID != "96dc97701d133ab3f4ec261534ed55c4129fd09507b535b884ff0e1c16107235":
        raise SystemExit("Altair 엔진 입력 ID 다름")
    manifest, features = base_run.source()
    selected = tuple(
        c for c in p.cases() if c.exposure == 4 and c.mode == "SHORT_MAX" and c.scenario.name == "BASE"
    )
    assert len(selected) == 1
    with account_class(account_with_leverage(Decimal(2))):
        runs = p.simulate(
            wdata.blocks(base_run.SOURCE / "aligned", manifest, features, progress=False),
            end_ms=base_run.END,
            selected=selected,
        )
    run = json.loads(json.dumps(runs[selected[0].key], default=base_run.serialize))
    trace = run["trace"]
    eq: dict[str, float] = {}
    for i, pt in enumerate(trace):
        v = float(Decimal(pt["equity"]))
        if i == len(trace) - 1:
            v -= float(Decimal(run["terminal_cost_reserve"]))
        month = datetime.fromtimestamp((pt["at_ms"] - 1) / 1000, UTC).strftime("%Y-%m")
        eq[month] = v
    out, prev = {}, float(p.INITIAL)
    for m in sorted(eq):
        out[m] = eq[m] / prev - 1
        prev = eq[m]
    return out


def altair_part(rets: dict[str, float], monthly: float) -> Part:
    v, paid = 0.0, 0.0
    vals, paids = {}, {}
    for m in sorted(rets):
        if not START[:7] <= m <= END[:7]:
            continue
        v = (v + monthly) * (1 + rets[m])
        paid += monthly
        vals[m], paids[m] = v, paid
    return vals, paids


def deneb_part(monthly: float) -> Part:
    dr = {**data_real(), "start": START}
    s = m184.simulate(dr, m184.qqq_real(), ref="T", park="cash", monthly_krw=monthly)
    return month_end(s["nav_days"], s["vals"]), month_end(s["nav_days"], paid_by(s["nav_days"], s["flows"]))


def vega_part(px: dict[str, dict[str, float]], monthly: float) -> Part:
    sub = {c: {d: v for d, v in s.items() if d <= END} for c, s in px.items()}
    days = sorted(next(iter(sub.values())))
    s = simulate_cruise(days, sub, start=START, monthly=monthly, tax=False)
    return month_end(s["nav_days"], s["vals"]), month_end(s["nav_days"], paid_by(s["nav_days"], s["flows"]))


def stats(parts: list[Part]) -> dict[str, Any]:
    st = combo_stats(parts)
    months = sorted(set.intersection(*(set(v) for v, _ in parts)))
    val = {m: sum(v[m] for v, _ in parts) for m in months}
    paid = {m: sum(pp[m] for _, pp in parts) for m in months}
    # 해마다 단위 가치 수익
    units, nav, prev_paid, navs = 0.0, {}, 0.0, {}
    for m in months:
        flow = paid[m] - prev_paid
        before = (val[m] - flow) / units if units > 0 else 1.0
        units += flow / before if before > 0 else 0.0
        navs[m] = val[m] / units
        prev_paid = paid[m]
    yearly, last = {}, 1.0
    for y in sorted({m[:4] for m in months}):
        end = navs[max(m for m in months if m[:4] == y)]
        yearly[y] = end / last - 1
        last = end
    nav = navs
    peak, dd22 = 0.0, 0.0
    for m in months:
        if m[:4] == "2022":
            peak = max(peak, nav[m]) if peak else nav[m]
            dd22 = min(dd22, nav[m] / peak - 1)
    lastm = months[-1]
    return {
        "months": [months[0], lastm],
        "paid": paid[lastm],
        "final": val[lastm],
        "x": val[lastm] / paid[lastm],
        "mdd": st["mdd"],
        "sharpe": st.get("sharpe"),
        "yearly": yearly,
        "dd_2022": dd22,
        "sleeves_final": [v[lastm] for v, _ in parts],
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    m184.TAX = 0.0
    alt = altair_monthly()
    _, n_e = pair(("BTC", "ETH"))
    _, n_s = pair(("BTC", "ETH", "SOL"))
    out: dict[str, Any] = {"prereg": "7de34be", "altair_input": base_run.INPUT_ID, "E": {}, "SOL": {}}
    for key, px in (("E", n_e), ("SOL", n_s)):
        for name, (wd, wv, wa) in MIXES.items():
            if key == "SOL" and name not in ("A 80·10·10", "B 70·20·10"):
                continue
            parts: list[Part] = []
            for w, fn in ((wd, lambda x: deneb_part(x)), (wv, lambda x: vega_part(px, x)),
                          (wa, lambda x: altair_part(alt, x))):
                parts.append(fn(TOTAL * w) if w else ({}, {}))
            used = [pt for pt in parts if pt[0]]
            res = stats(used)
            res["sleeves_final"] = [pt[0][max(pt[0])] if pt[0] else 0.0 for pt in parts]
            out[key][name] = res
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> int:
    out = run()
    for key in ("E", "SOL"):
        print("== Vega", "BTC+ETH" if key == "E" else "BTC+ETH+SOL")
        for name, r in out[key].items():
            yr = " ".join(f"{y[2:]} {v:+.0%}" for y, v in r["yearly"].items())
            sl = " / ".join(f"{v / 1e4:,.0f}" for v in r["sleeves_final"])
            print(
                f"{name:10} {r['months']} 원금 {r['paid'] / 1e4:,.0f}만 끝 {r['final'] / 1e4:,.0f}만"
                f" ({r['x']:.2f}배) 낙폭 {r['mdd']:.1%} 샤프 {r['sharpe']:.2f} 22 {r['dd_2022']:.0%}"
                f" | 곳별 {sl} | {yr}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
