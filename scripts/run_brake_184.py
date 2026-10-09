"""문서 184 — M3 브레이크 세후 비교 · 판 돈 보관처(달러 · SCHD · QQQ) · 매달 확인.

사용 예::

    python -m scripts.run_brake_184 run   # 1 회 판정 → reports/brake_184/result.json

* 사전 등록: docs/research/184-m3-brake-after-tax-parking-preregistration.md.
* 183 의 자료 · 신호 · 규칙 그대로.
* 원화(매달 30 만 원) · 이동평균 원가 · 연 250 만 원 공제 22% 세금을 더했다.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_brake_183 import (
    COST,
    CRISES,
    HIGH_DAYS,
    LEV_MAX,
    STAGE1,
    STAGE2,
    STAGE_B,
    VOL_REF,
    _daily,
    data_real,
    data_synth,
    nav_stats,
    window_stats,
)
from scripts.run_div_mix_180 import DATA as DIV_DATA
from scripts.run_div_mix_180 import DATA_SHA as DIV_SHA
from scripts.run_div_mix_180 import own_hash
from scripts.run_lev_div_181 import LEV_SHA, NDX_DIV, lev_hash, total_return_index
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import leveraged

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "brake_184"
MONTHLY_KRW = 300_000.0
DEDUCTION = 2_500_000.0
TAX = 0.22
QQQ_FEE_SYN = 0.002
P_MDD = 0.05
VARIANTS: dict[str, dict[str, Any]] = {
    "M0": {"ref": None},
    "H": {"ref": "H", "park": "cash"},
    "T": {"ref": "T", "park": "cash"},
    "T_SCHD": {"ref": "T", "park": "D"},
    "T_QQQ": {"ref": "T", "park": "Q"},
    "T_MONTH": {"ref": "T", "park": "cash", "monthly": True},
}


class Book:
    """보유 · 이동평균 원가 · 해마다 실현 손익 (원화)."""

    def __init__(self) -> None:
        self.q: dict[str, float] = defaultdict(float)
        self.cost: dict[str, float] = defaultdict(float)
        self.gain: dict[str, float] = defaultdict(float)
        self.sells = 0

    def buy(self, sym: str, krw: float, px: float) -> float:
        if krw <= 0:
            return 0.0
        qty = krw * (1 - COST) / px
        self.q[sym] += qty
        self.cost[sym] += krw
        return qty

    def sell(self, sym: str, qty: float, px: float, year: str) -> float:
        qty = min(qty, self.q[sym])
        if qty <= 1e-12:
            return 0.0
        proceeds = qty * px * (1 - COST)
        avg = self.cost[sym] / self.q[sym]
        self.gain[year] += proceeds - avg * qty
        self.q[sym] -= qty
        self.cost[sym] -= avg * qty
        self.sells += 1
        return proceeds

    def scale(self, f: float) -> None:
        for s in list(self.q):
            self.q[s] *= f
            self.cost[s] *= f


def tax_of(gain: float) -> float:
    return TAX * max(0.0, gain - DEDUCTION)


def simulate(
    data: Mapping[str, Any],
    qqq: Mapping[str, float],
    *,
    ref: str | None,
    park: str = "cash",
    monthly: bool = False,
    early: Mapping[str, bool] | None = None,
    no_sell: frozenset[str] = frozenset(),
    monthly_krw: float = MONTHLY_KRW,
) -> dict[str, Any]:
    """일간 모의 계좌 (순수). 기호: L(TQQQ) · D(SCHD) · Q(QQQ)."""
    days, lev, div = data["days"], data["lev"], data["div"]
    vol, trend = data["vol"], data["trend"]
    px = {"L": lev, "D": div, "Q": qqq}
    bk = Book()
    res_cash = res_qty = units = contrib = 0.0
    stage, off_seen = 0, False
    navs: list[float] = []
    nav_days: list[str] = []
    win_start, l_max = 0, 0.0
    sales: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    ep: dict[str, Any] | None = None
    taxes: dict[str, float] = {}
    flows: list[tuple[str, float]] = []
    i0 = next(i for i, d in enumerate(days) if d >= data["start"] and i > 0)

    def value(d: str) -> float:
        return float(sum(bk.q[s] * px[s][d] for s in ("L", "D", "Q") if bk.q[s])) + res_cash

    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        y = d[:4]
        new_month = d[:7] != prev[:7]
        check = (not monthly) or new_month
        nav_prev = navs[-1] if navs else 1.0
        # 0) 6 월 첫 거래일: 지난해 세금
        if new_month and d[5:7] == "06" and str(int(y) - 1) not in taxes and units > 0:
            t = tax_of(bk.gain.get(str(int(y) - 1), 0.0))
            taxes[str(int(y) - 1)] = t
            v = value(d)
            if t > 0 and v > 0:
                f = max(1 - t / v, 0.0)
                bk.scale(f)
                res_cash *= f
                res_qty *= f
                units *= f
        # 1) 브레이크
        if ref and check and units > 0 and bk.q["L"] > 0 and stage < 2:
            if ref == "H":
                window = navs[max(win_start, len(navs) - HIGH_DAYS) :]
                base = max(contrib / units, max(window) if window else nav_prev)
                dd = 1 - nav_prev / base
            else:
                dd = 1 - lev[prev] / l_max if l_max > 0 else 0.0
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = bk.q["L"], 2
            elif dd >= STAGE1 and stage == 0:
                sell, stage = bk.q["L"] / 2, 1
            if sell > 0:
                cash = bk.sell("L", sell, lev[d], y)
                if park == "cash":
                    res_cash += cash
                else:
                    res_qty += bk.buy(park, cash, px[park][d])
                sales.append({"day": d, "price": lev[d], "dd": round(dd, 4)})
                if ep is None:
                    ep = {"start": d, "usd": 0.0, "qty": 0.0}
                    off_seen = False
                ep["usd"] += sell * lev[d]
                ep["qty"] += sell
        # 2) 재매수
        if stage > 0 and ep is not None and check:
            t_on = trend.get(prev)
            if t_on is False:
                off_seen = True
            # 187: ``early`` 는 날짜 → 조기 재매수 신호 (없으면 184 그대로)
            if (t_on and off_seen) or (early is not None and early.get(prev, False)):
                if park == "cash":
                    cash, res_cash = res_cash, 0.0
                else:
                    cash = bk.sell(park, res_qty, px[park][d], y)
                    res_qty = 0.0
                bk.buy("L", cash, lev[d])
                ep.update(end=d, avg_sale=ep["usd"] / ep["qty"], rebuy=lev[d])
                episodes.append(ep)
                ep, stage = None, 0
                win_start, l_max = len(navs), lev[d]
        elif stage > 0 and monthly and trend.get(prev) is False:
            off_seen = True
        # 3) 매달 납입 + 다시 맞춤 (보관분 제외)
        if new_month:
            v_before = value(d)
            nav_now = v_before / units if units > 0 else 1.0
            units += monthly_krw / nav_now
            contrib += monthly_krw
            flows.append((d, monthly_krw))
            d_active = bk.q["D"] - (res_qty if park == "D" else 0.0)
            act_l, act_d = bk.q["L"] * lev[d], d_active * div[d]
            active = act_l + act_d + monthly_krw
            v = vol.get(prev)
            share = LEV_MAX * (min(1.0, VOL_REF / v) if v and v > 0 else 1.0) * STAGE_B[stage]
            tgt_l = share * active
            cash = monthly_krw
            # 189: ``no_sell`` 종목은 매달 맞추기에서 팔지 않는다 (브레이크 매도는 그대로)
            if act_l > tgt_l and "L" not in no_sell:
                cash += bk.sell("L", (act_l - tgt_l) / lev[d], lev[d], y)
            tgt_d = active - tgt_l
            if act_d > tgt_d and "D" not in no_sell:
                cash += bk.sell("D", (act_d - tgt_d) / div[d], div[d], y)
            need_l = max(tgt_l - bk.q["L"] * lev[d], 0.0)
            need_d = max(tgt_d - (bk.q["D"] - (res_qty if park == "D" else 0.0)) * div[d], 0.0)
            tot = need_l + need_d
            if tot > 0:
                bk.buy("L", cash * need_l / tot, lev[d])
                bk.buy("D", cash * need_d / tot, div[d])
            else:
                bk.buy("D", cash, div[d])
        v = value(d)
        if units > 0:
            navs.append(v / units)
            nav_days.append(d)
            l_max = max(l_max, lev[d])
    end = days[-1]
    last_y = end[:4]
    taxes[last_y] = tax_of(bk.gain.get(last_y, 0.0))
    v_end = value(end) - taxes[last_y]
    unreal = sum(bk.q[s] * px[s][end] * (1 - COST) - bk.cost[s] for s in ("L", "D", "Q") if bk.q[s])
    liq_tax = tax_of(bk.gain.get(last_y, 0.0) + unreal) - taxes[last_y]
    return {
        "navs": navs,
        "nav_days": nav_days,
        "contrib": contrib,
        "flows": flows,
        "end": end,
        "after_tax_hold": v_end,
        "after_tax_liq": v_end - max(liq_tax, 0.0),
        "taxes": taxes,
        "sales": sales,
        "episodes": episodes,
        "all_sells": bk.sells,
    }


def irr(flows: Sequence[tuple[str, float]], end: str, final: float) -> float:
    """매달 납입(양수) → 끝 가치 ``final`` 을 만드는 연 수익률 (이분법, 순수)."""
    e = datetime.fromisoformat(end)
    yrs = [(e - datetime.fromisoformat(d)).days / 365.25 for d, _ in flows]

    def fv(r: float) -> float:
        return float(sum(a * (1 + r) ** t for (_, a), t in zip(flows, yrs, strict=True)))

    lo, hi = -0.99, 3.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if fv(mid) < final:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def yearly(sim: Mapping[str, Any]) -> dict[str, float]:
    last: dict[str, float] = {}
    first: dict[str, float] = {}
    for d, v in zip(sim["nav_days"], sim["navs"], strict=True):
        first.setdefault(d[:4], v)
        last[d[:4]] = v
    out, prev = {}, None
    for y in sorted(last):
        base = prev if prev is not None else first[y]
        out[y] = round(last[y] / base - 1, 4)
        prev = last[y]
    return out


def summarize(sim: Mapping[str, Any], crises: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    eps = sim["episodes"]
    c = sim["contrib"]
    return {
        **nav_stats(sim["navs"], sim["nav_days"]),
        "after_tax_hold_x": round(sim["after_tax_hold"] / c, 3),
        "after_tax_liq_x": round(sim["after_tax_liq"] / c, 3),
        "irr_hold": round(irr(sim["flows"], sim["end"], sim["after_tax_hold"]), 4),
        "irr_liq": round(irr(sim["flows"], sim["end"], sim["after_tax_liq"]), 4),
        "tax_paid_x": round(sum(sim["taxes"].values()) / c, 3),
        "brake_sales": len(sim["sales"]),
        "rebuys": len(eps),
        "loss_rebuy_share": round(sum(e["rebuy"] > e["avg_sale"] for e in eps) / len(eps), 3)
        if eps
        else None,
        "all_sells": sim["all_sells"],
        "crises": {k: window_stats(sim, a, b) for k, (a, b) in crises.items()},
    }


def qqq_real() -> dict[str, float]:
    return _daily(DIV_DATA / "QQQ.csv")


def qqq_synth(days: Sequence[str], rf: Mapping[str, float]) -> dict[str, float]:
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    tr = total_return_index(days, ndx, NDX_DIV)
    return leveraged(days, tr, rf, 1.0, QQQ_FEE_SYN)


def run_set(name: str, data: Mapping[str, Any], qqq: Mapping[str, float]) -> dict[str, Any]:
    sims = {k: simulate(data, qqq, **kw) for k, kw in VARIANTS.items()}
    out: dict[str, Any] = {k: summarize(s, CRISES[name]) for k, s in sims.items()}
    out["period"] = [sims["M0"]["nav_days"][0], sims["M0"]["nav_days"][-1]]
    if name == "R":
        out["yearly"] = {k: yearly(s) for k, s in sims.items()}
    out["contrib_krw"] = sims["M0"]["contrib"]
    return out


def verdict(r: Mapping[str, Any], s: Mapping[str, Any]) -> dict[str, Any]:
    def beats(x: Mapping[str, Any], k: str, strict: bool) -> bool:
        a, b = x[k]["after_tax_liq_x"], x["T"]["after_tax_liq_x"]
        ok = a > b if strict else a >= b
        return bool(ok and x[k]["mdd"] <= x["T"]["mdd"] + P_MDD)

    ok_park = [k for k in ("T_SCHD", "T_QQQ") if beats(r, k, True) and beats(s, k, True)]
    park = (
        max(ok_park, key=lambda k: r[k]["after_tax_liq_x"] + s[k]["after_tax_liq_x"])
        if ok_park
        else "T"
    )
    month = beats(r, "T_MONTH", False) and beats(s, "T_MONTH", False)
    return {
        "parking": {"T": "달러", "T_SCHD": "SCHD", "T_QQQ": "QQQ"}[park],
        "parking_ok": {k: [beats(r, k, True), beats(s, k, True)] for k in ("T_SCHD", "T_QQQ")},
        "check": "매달" if month else "매일",
        "month_ok_[R,S]": [beats(r, "T_MONTH", False), beats(s, "T_MONTH", False)],
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if own_hash() != DIV_SHA or lev_hash() != LEV_SHA:
        raise SystemExit("자료 해시 불일치 (180 · 181)")
    dr, ds = data_real(), data_synth()
    r = run_set("R", dr, qqq_real())
    s = run_set("S", ds, qqq_synth(ds["days"], ds["rf"]))
    res = {
        "R": r,
        "S": s,
        "verdict": verdict(r, s),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


KEYS = (
    "cagr",
    "mdd",
    "after_tax_hold_x",
    "after_tax_liq_x",
    "irr_hold",
    "irr_liq",
    "tax_paid_x",
    "brake_sales",
    "rebuys",
    "loss_rebuy_share",
    "all_sells",
)


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_brake_184 run")
    res = run()
    brief = {k: {n: {x: res[k][n][x] for x in KEYS} for n in VARIANTS} for k in ("R", "S")}
    out = {"brief": brief, "verdict": res["verdict"]}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
