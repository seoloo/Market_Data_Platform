"""문서 121 — LD 주도주 눌림목 추매(적립형) vs LT-C 고르게 추매. 113 규칙 그대로, 추매 위치만 다름.

사용 예::

    python -m scripts.run_ld_dipadd run   # 판정 한 번 (트리 깨끗 · 결과 파일 없음)
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_qs_switch import irr
from scripts.run_sb_breakout import QQQ, SPY, _sma, load

from scripts import run_lt_leaders as lt
from scripts import run_lt_sp400 as lt400

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "ld_dipadd"
CONTRIBUTION = 1_000.0
DIP1, W1 = 0.15, 0.15
DIP2, W2 = 0.25, 0.20
HALVES = (("2008-06-01", "2016-12-31"), ("2017-01-01", lt.TO))
MDD_MARGIN = 0.05


@dataclass(slots=True)
class Res:
    final: float = 0.0
    invested: float = 0.0
    flows: list[tuple[str, float]] = field(default_factory=list)
    mdd: float = 0.0
    dip_adds: int = 0
    dip_add_syms: list[tuple[str, str]] = field(default_factory=list)
    exits: dict[str, str] = field(default_factory=dict)
    tax_final: float = 0.0


def simulate(
    panel: lt.Panel,
    spy_ok: Sequence[bool],
    lo: str,
    hi: str,
    *,
    dip: bool,
    stop: bool = True,
    tax: bool = False,
) -> Res:
    days = panel.days
    idx = [i for i, d in enumerate(days) if lo <= d <= hi]
    res = Res()
    cash = 0.0
    pos: dict[str, list[float]] = {}  # sym -> [수량, 원가, 진입 뒤 최고, 진입 색인]
    pending_sell: set[str] = set()
    pending_buy: list[str] | None = None
    units = 0.0
    peak_nav = 0.0
    last_month = ""
    year_gain = 0.0
    adds_open: dict[str, str] = {}  # 눌림목 추매를 받은 종목 → 처음 받은 날

    def px(sym: str, t: int) -> float:
        xs = panel.close[sym]
        while xs[t] is None:
            t -= 1
        return xs[t]  # type: ignore[return-value]

    def holdings(t: int) -> float:
        return sum(q * px(s, t) for s, (q, *_r) in pos.items())

    for n, t in enumerate(idx):
        d = days[t]
        if d[:7] != last_month:  # 매달 첫 거래일 입금
            last_month = d[:7]
            value = cash + holdings(t)
            nav = value / units if units > 0 else 1.0
            units += CONTRIBUTION / nav
            cash += CONTRIBUTION
            res.invested += CONTRIBUTION
            res.flows.append((d, -CONTRIBUTION))
        for sym in sorted(pending_sell):
            if sym in pos:
                q, cost, _pk, _i0 = pos.pop(sym)
                proceeds = q * px(sym, t) * (1 - lt.FEE)
                cash += proceeds
                year_gain += proceeds - cost
                if sym in adds_open:
                    res.exits[f"{sym}@{adds_open.pop(sym)}"] = "stop" if proceeds < cost else "gain"
        pending_sell = set()
        equity = cash + holdings(t)
        if pending_buy is not None:
            held = list(pos)
            weights = dict.fromkeys(held, lt.TARGET)
            if dip:
                for s in held:
                    p, peak = px(s, t), pos[s][2]
                    if p <= (1 - DIP2) * peak:
                        weights[s] = W2
                    elif p <= (1 - DIP1) * peak:
                        weights[s] = W1
                held.sort(key=lambda s: px(s, t) / pos[s][2])  # 많이 빠진 순
            for s in held:
                q, cost, peak, i0 = pos[s]
                gap = weights[s] * equity - q * px(s, t)
                if gap > 0 and cash > 0:
                    amt = min(gap, cash)
                    pos[s] = [q + amt * (1 - lt.FEE) / px(s, t), cost + amt, peak, i0]
                    cash -= amt
                    if weights[s] > lt.TARGET:
                        res.dip_adds += 1
                        res.dip_add_syms.append((s, d))
                        adds_open.setdefault(s, d)
            for s in pending_buy:
                if s not in pos and len(pos) < lt.MAX_POS and cash > 0:
                    amt = min(lt.TARGET * equity, cash)
                    p = px(s, t)
                    pos[s] = [amt * (1 - lt.FEE) / p, amt, p, t]
                    cash -= amt
            pending_buy = None
        for sym, rec in pos.items():
            p = px(sym, t)
            rec[2] = max(rec[2], p)
            if (stop and p <= (1 - lt.STOP_FROM_PEAK) * rec[2]) or lt.trend_off(
                panel.close[sym], t
            ):
                pending_sell.add(sym)
        month_end = t + 1 < len(days) and days[t + 1][:7] != d[:7]
        if month_end and spy_ok[t]:
            lead = lt.leaders(panel, t)
            pending_buy = [
                s
                for s, _ in lead
                if s not in pos and s not in pending_sell and lt.trend_up(panel.close[s], t)
            ]
        if tax and (n == len(idx) - 1 or days[idx[n + 1]][:4] != d[:4]):
            if year_gain > 0:
                cash -= lt.TAX * year_gain
            year_gain = 0.0
        value = cash + holdings(t)
        nav = value / units if units > 0 else 1.0
        peak_nav = max(peak_nav, nav)
        res.mdd = max(res.mdd, 1 - nav / peak_nav)
        res.final = value
    res.flows.append((days[idx[-1]], res.final))
    return res


def qqq_dca(days: Sequence[str], lo: str, hi: str) -> float | None:
    q = load(QQQ)
    m = dict(zip(q.dates, q.close, strict=True))
    span = [d for d in days if lo <= d <= hi and d in m]
    sh, flows, last = 0.0, [], ""
    for d in span:
        if d[:7] != last:
            last = d[:7]
            sh += CONTRIBUTION * (1 - lt.FEE) / m[d]
            flows.append((d, -CONTRIBUTION))
    flows.append((span[-1], sh * m[span[-1]]))
    return irr(flows)


def _spy_ok(panel: lt.Panel) -> list[bool]:
    spy = load(SPY)
    m = dict(zip(spy.dates, spy.close, strict=True))
    c = [m[d] for d in panel.days]
    sma = _sma(c, lt.SPY_SMA)
    return [s is not None and x > s for x, s in zip(c, sma, strict=True)]


def _pair(panel: lt.Panel, ok: list[bool], lo: str, hi: str) -> dict[str, Any]:
    base = simulate(panel, ok, lo, hi, dip=False)
    ld = simulate(panel, ok, lo, hi, dip=True)
    adds = ld.exits
    return {
        "LT_C": {"irr": irr(base.flows), "final": round(base.final, 2), "mdd": round(base.mdd, 4)},
        "LD": {
            "irr": irr(ld.flows),
            "final": round(ld.final, 2),
            "mdd": round(ld.mdd, 4),
            "dip_adds": ld.dip_adds,
            "dip_positions_closed": len(adds),
            "dip_positions_closed_at_loss": sum(v == "stop" for v in adds.values()),
        },
        "invested": base.invested,
        "QQQ_DCA_irr": qqq_dca(panel.days, lo, hi),
    }


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    _sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    p500 = lt.build_panel()
    ok500 = _spy_ok(p500)
    full = _pair(p500, ok500, lt.FROM, lt.TO)
    halves = [_pair(p500, ok500, lo, hi) for lo, hi in HALVES]
    nostop = simulate(p500, ok500, lt.FROM, lt.TO, dip=True, stop=False)
    taxed = {
        "LT_C": irr(simulate(p500, ok500, lt.FROM, lt.TO, dip=False, tax=True).flows),
        "LD": irr(simulate(p500, ok500, lt.FROM, lt.TO, dip=True, tax=True).flows),
    }
    lt400._point_to_sp400()
    lt.UNIVERSE_SHA256 = lt400.UNIVERSE_SHA256
    p400 = lt.build_panel()
    rep = _pair(p400, _spy_ok(p400), lt.FROM, lt.TO)

    def better(x: dict[str, Any]) -> bool:
        return float(x["LD"]["irr"]) > float(x["LT_C"]["irr"])

    c1 = better(full)
    c2 = all(better(h) for h in halves)
    c3 = full["LD"]["mdd"] <= full["LT_C"]["mdd"] + MDD_MARGIN
    c4 = float(rep["LD"]["irr"]) >= float(rep["LT_C"]["irr"])
    result = {
        "verdict": "PASS" if c1 and c2 and c3 and c4 else "REJECT",
        "C1": c1,
        "C2": c2,
        "C3": c3,
        "C4": c4,
        "sp500_full": full,
        "sp500_halves": halves,
        "sp400_full": rep,
        "report_only": {
            "LD_no_stop_irr": irr(nostop.flows),
            "LD_no_stop_mdd": round(nostop.mdd, 4),
            "taxed_irr": taxed,
        },
        "computed": date.today().isoformat(),  # noqa: DTZ011
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_ld_dipadd run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
