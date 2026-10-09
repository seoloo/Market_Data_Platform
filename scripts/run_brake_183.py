"""문서 183 — M3 매도 브레이크 기준 고점(H 평단 · 12 개월 고점 vs T TQQQ 고점) · 판 돈 보관.

보관은 달러 vs 단기 국채 ETF.

사용 예::

    python -m scripts.run_brake_183 run   # 1 회 판정 → reports/brake_183/result.json

* 사전 등록: docs/research/183-m3-brake-reference-parking-preregistration.md.
* 일간 모의 계좌: 매달 첫 거래일 납입 1 · 목표까지 다시 맞춤.
* 브레이크는 매일(전날 종가로 판단 → 그날 종가 체결).
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_div_mix_180 import DATA as DIV_DATA
from scripts.run_div_mix_180 import DATA_SHA as DIV_SHA
from scripts.run_div_mix_180 import french_hi30, own_hash
from scripts.run_lev_div_181 import FEES, LEV_DATA, LEV_SHA, NDX_DIV, lev_hash, total_return_index
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import leveraged

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "brake_183"
COST = 0.0015
LEV_MAX = 0.6
VOL_REF = 0.20
VOL_DAYS = 20
LOOKBACKS = (20, 60, 120)
STAGE1, STAGE2 = 0.15, 0.30
STAGE_B = {0: 1.0, 1: 0.5, 2: 0.0}
HIGH_DAYS = 252
TBILL_FEE = 0.001
TBILL_TAX = 0.154
END = "2026-08-31"
P_T_MDD, P_T_CAGR = 0.05, 0.01
P_B_MDD, P_B_CAGR = 0.10, 0.02
CRISES = {
    "R": {
        "2018 4분기": ("2018-10-01", "2018-12-31"),
        "2020 코로나": ("2020-02-01", "2020-03-31"),
        "2022 금리 인상": ("2022-01-01", "2022-12-31"),
    },
    "S": {
        "1987 블랙먼데이": ("1987-09-01", "1987-12-31"),
        "2000 ~ 02 닷컴": ("2000-03-01", "2002-09-30"),
        "2008 금융위기": ("2007-11-01", "2009-02-28"),
        "2020 코로나": ("2020-02-01", "2020-03-31"),
        "2022 금리 인상": ("2022-01-01", "2022-12-31"),
    },
}


def signals(
    days: Sequence[str], px: Mapping[str, float]
) -> tuple[dict[str, float | None], dict[str, bool | None]]:
    """날짜 → (그날 종가까지 20 일 변동성, QS 추세 켜짐) (순수)."""
    vol: dict[str, float | None] = {}
    trend: dict[str, bool | None] = {}
    for i, d in enumerate(days):
        p = px[d]
        if i >= max(LOOKBACKS):
            score = sum(
                (1 if p > px[days[i - k]] else -1 if p < px[days[i - k]] else 0) for k in LOOKBACKS
            )
            trend[d] = score > 0
        else:
            trend[d] = None
        if i >= VOL_DAYS:
            rets = [px[days[j]] / px[days[j - 1]] - 1 for j in range(i - VOL_DAYS + 1, i + 1)]
            vol[d] = statistics.pstdev(rets) * math.sqrt(252)
        else:
            vol[d] = None
    return vol, trend


def simulate(
    days: Sequence[str],
    lev: Mapping[str, float],
    div: Mapping[str, float],
    vol: Mapping[str, float | None],
    trend: Mapping[str, bool | None],
    rf: Mapping[str, float],
    *,
    start: str,
    ref: str | None,
    park: str = "cash",
) -> dict[str, Any]:
    """일간 모의 계좌 (순수). ``ref`` None · 'H' · 'T', ``park`` 'cash' · 'tbill'."""
    q_l = q_d = units = reserve = contrib = 0.0
    stage = 0
    off_seen = False
    navs: list[float] = []
    nav_days: list[str] = []
    win_start = 0
    l_max = 0.0
    sales: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    ep: dict[str, Any] | None = None
    reserve_days = 0
    i0 = next(i for i, d in enumerate(days) if d >= start and i > 0)
    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        if reserve > 0:
            reserve_days += 1
            if park == "tbill":
                reserve *= 1 + rf.get(prev, 0.0) / 100 / 252 * (1 - TBILL_TAX)
        nav_prev = navs[-1] if navs else 1.0
        # 1) 브레이크 (전날 종가로 판단)
        if ref and units > 0 and q_l > 0 and stage < 2:
            if ref == "H":
                window = navs[max(win_start, len(navs) - HIGH_DAYS) :]
                base = max(contrib / units, max(window) if window else nav_prev)
                dd = 1 - nav_prev / base
            else:
                dd = 1 - lev[prev] / l_max if l_max > 0 else 0.0
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = q_l, 2
            elif dd >= STAGE1 and stage == 0:
                sell, stage = q_l / 2, 1
            if sell > 0:
                amt = sell * lev[d] * (1 - COST)
                if park == "tbill":
                    amt *= 1 - TBILL_FEE
                q_l -= sell
                reserve += amt
                sales.append({"day": d, "price": lev[d], "qty": sell, "dd": round(dd, 4)})
                if ep is None:
                    ep = {"start": d, "sold_usd": 0.0, "sold_qty": 0.0}
                    off_seen = False
                ep["sold_usd"] += sell * lev[d]
                ep["sold_qty"] += sell
        # 2) 재매수 (추세가 꺼졌다가 다시 켜진 날)
        if stage > 0 and ep is not None:
            t = trend.get(prev)
            if t is False:
                off_seen = True
            elif t and off_seen:
                amt = reserve * (1 - TBILL_FEE if park == "tbill" else 1.0)
                q_l += amt * (1 - COST) / lev[d]
                ep.update(
                    end=d,
                    days=sum(1 for x in days if ep["start"] <= x <= d),
                    avg_sale=ep["sold_usd"] / ep["sold_qty"],
                    rebuy=lev[d],
                    back_usd=amt,
                    rf_avg=statistics.fmean(rf.get(x, 0.0) for x in days if ep["start"] <= x <= d),
                )
                episodes.append(ep)
                ep, reserve, stage = None, 0.0, 0
                win_start = len(navs)
                l_max = lev[d]
        # 3) 매달 첫 거래일: 납입 + 다시 맞춤
        if d[:7] != prev[:7]:
            value = q_l * lev[d] + q_d * div[d] + reserve
            nav_now = value / units if units > 0 else 1.0
            units += 1.0 / nav_now
            contrib += 1.0
            active = q_l * lev[d] + q_d * div[d] + 1.0
            v = vol.get(prev)
            share = LEV_MAX * (min(1.0, VOL_REF / v) if v and v > 0 else 1.0) * STAGE_B[stage]
            tgt_l = share * active
            trade = abs(tgt_l - q_l * lev[d]) + abs((active - tgt_l) - q_d * div[d])
            active -= COST * trade
            q_l = share * active / lev[d]
            q_d = (1 - share) * active / div[d]
        value = q_l * lev[d] + q_d * div[d] + reserve
        if units > 0:
            navs.append(value / units)
            nav_days.append(d)
            l_max = max(l_max, lev[d])
    value = q_l * lev[days[-1]] + q_d * div[days[-1]] + reserve
    return {
        "navs": navs,
        "nav_days": nav_days,
        "final_multiple": value / contrib,
        "sales": sales,
        "episodes": episodes,
        "open_episode": ep,
        "reserve_day_share": reserve_days / max(len(navs), 1),
    }


def nav_stats(navs: Sequence[float], nav_days: Sequence[str]) -> dict[str, float]:
    years = (
        datetime.fromisoformat(nav_days[-1]) - datetime.fromisoformat(nav_days[0])
    ).days / 365.25
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"cagr": round((navs[-1] / navs[0]) ** (1 / years) - 1, 4), "mdd": round(mdd, 4)}


def window_stats(sim: Mapping[str, Any], a: str, b: str) -> dict[str, float]:
    pts = [(d, v) for d, v in zip(sim["nav_days"], sim["navs"], strict=True) if a <= d <= b]
    if len(pts) < 2:
        return {}
    vals = [v for _, v in pts]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"ret": round(vals[-1] / vals[0] - 1, 4), "mdd": round(mdd, 4)}


def summarize(sim: Mapping[str, Any], crises: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    eps = sim["episodes"]
    return {
        **nav_stats(sim["navs"], sim["nav_days"]),
        "final_multiple": round(sim["final_multiple"], 3),
        "sales": len(sim["sales"]),
        "rebuys": len(eps),
        "loss_rebuy_share": round(sum(e["rebuy"] > e["avg_sale"] for e in eps) / len(eps), 3)
        if eps
        else None,
        "reserve_day_share": round(sim["reserve_day_share"], 3),
        "median_episode_days": statistics.median(e["days"] for e in eps) if eps else None,
        "open_episode": bool(sim["open_episode"]),
        "crises": {k: window_stats(sim, a, b) for k, (a, b) in crises.items()},
    }


def parking_episodes(sim: Mapping[str, Any]) -> dict[str, Any]:
    """달러 보관 기록의 보관마다 국채 ETF 였다면 생겼을 손익 비율 (근사, 순수)."""
    out = []
    for e in sim["episodes"]:
        r = e["rf_avg"] / 100 * (1 - TBILL_TAX)
        gain = (1 + r / 252) ** e["days"] * (1 - TBILL_FEE) ** 2 - 1
        out.append(
            {
                "start": e["start"],
                "days": e["days"],
                "rf_avg": round(e["rf_avg"], 2),
                "gain": round(gain, 4),
            }
        )
    return {
        "episodes": out,
        "loss_share": round(sum(x["gain"] < 0 for x in out) / len(out), 3) if out else None,
    }


def breakeven_days(rate_pct: float) -> float:
    r = rate_pct / 100 * (1 - TBILL_TAX)
    return math.log(1 / (1 - TBILL_FEE) ** 2) / math.log(1 + r / 252) if r > 0 else math.inf


def _daily(path: Path) -> dict[str, float]:
    with path.open(encoding="utf-8") as f:
        return {r["date"]: float(r["adjclose"]) for r in csv.DictReader(f)}


def _rf_daily(days: Sequence[str]) -> dict[str, float]:
    raw = _series("DTB3_full.csv", "DTB3")
    out: dict[str, float] = {}
    lv = 0.0
    for d in sorted(set(days) | set(raw)):
        lv = raw.get(d, lv)
        out[d] = lv
    return out


def data_real() -> dict[str, Any]:
    lev, div, qqq = (
        _daily(LEV_DATA / "TQQQ.csv"),
        _daily(DIV_DATA / "SCHD.csv"),
        _daily(DIV_DATA / "QQQ.csv"),
    )
    qd = sorted(d for d in qqq if d <= END)
    vol, trend = signals(qd, qqq)
    days = sorted(d for d in set(lev) & set(div) & set(qqq) if d <= END)
    return {
        "days": days,
        "lev": lev,
        "div": div,
        "vol": vol,
        "trend": trend,
        "rf": _rf_daily(days),
        "start": "2012-01-01",
    }


def data_synth() -> dict[str, Any]:
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    hi = french_hi30()
    days = sorted(d for d in ndx if d <= END and d[:7] in hi)
    rf = _rf_daily(days)
    tr = total_return_index(days, ndx, NDX_DIV)
    lev = leveraged(days, tr, rf, 3.0, FEES[3.0])
    per_month: dict[str, int] = {}
    for d in days:
        per_month[d[:7]] = per_month.get(d[:7], 0) + 1
    div = {days[0]: 100.0}
    for a, b in pairwise(days):
        div[b] = div[a] * (1 + hi[b[:7]]) ** (1 / per_month[b[:7]])
    vol, trend = signals(days, ndx)
    return {
        "days": days,
        "lev": lev,
        "div": div,
        "vol": vol,
        "trend": trend,
        "rf": rf,
        "start": "1986-02-01",
    }


def run_set(name: str, data: Mapping[str, Any]) -> dict[str, Any]:
    args = (data["days"], data["lev"], data["div"], data["vol"], data["trend"], data["rf"])
    sims = {
        "M0": simulate(*args, start=data["start"], ref=None),
        "H": simulate(*args, start=data["start"], ref="H"),
        "T": simulate(*args, start=data["start"], ref="T"),
        "H_tbill": simulate(*args, start=data["start"], ref="H", park="tbill"),
        "T_tbill": simulate(*args, start=data["start"], ref="T", park="tbill"),
    }
    res: dict[str, Any] = {
        "period": [sims["M0"]["nav_days"][0], sims["M0"]["nav_days"][-1]],
        **{k: summarize(s, CRISES[name]) for k, s in sims.items()},
        "parking_H": parking_episodes(sims["H"]),
        "parking_T": parking_episodes(sims["T"]),
        "sales_H": sims["H"]["sales"],
    }
    return res


def verdict(r: Mapping[str, Any], s: Mapping[str, Any]) -> dict[str, Any]:
    def t_wins(x: Mapping[str, Any]) -> bool:
        return bool(
            x["T"]["mdd"] <= x["H"]["mdd"] - P_T_MDD and x["T"]["cagr"] >= x["H"]["cagr"] - P_T_CAGR
        )

    ref = "T" if t_wins(r) and t_wins(s) else "H"

    def brake_ok(x: Mapping[str, Any]) -> bool:
        return bool(
            x[ref]["mdd"] <= x["M0"]["mdd"] - P_B_MDD
            and x[ref]["cagr"] >= x["M0"]["cagr"] - P_B_CAGR
        )

    def tbill_wins(x: Mapping[str, Any]) -> bool:
        return bool(x[f"{ref}_tbill"]["final_multiple"] > x[ref]["final_multiple"])

    return {
        "reference": ref,
        "T_wins_[R,S]": [t_wins(r), t_wins(s)],
        "brake": "BRAKE_SUPPORT" if brake_ok(r) and brake_ok(s) else "BRAKE_WEAK",
        "brake_ok_[R,S]": [brake_ok(r), brake_ok(s)],
        "parking": "TBILL" if tbill_wins(r) and tbill_wins(s) else "CASH",
        "tbill_wins_[R,S]": [tbill_wins(r), tbill_wins(s)],
        "breakeven_days_at_[1,2,4,5]pct": [round(breakeven_days(x), 1) for x in (1, 2, 4, 5)],
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if own_hash() != DIV_SHA:
        raise SystemExit("자료 해시 불일치 (180)")
    if lev_hash() != LEV_SHA:
        raise SystemExit("자료 해시 불일치 (181)")
    r = run_set("R", data_real())
    s = run_set("S", data_synth())
    res = {
        "R": r,
        "S": s,
        "verdict": verdict(r, s),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_brake_183 run")
    res = run()
    brief = {
        k: {
            n: {
                x: res[k][n][x]
                for x in (
                    "cagr",
                    "mdd",
                    "final_multiple",
                    "sales",
                    "rebuys",
                    "loss_rebuy_share",
                    "reserve_day_share",
                    "median_episode_days",
                )
            }
            for n in ("M0", "H", "T", "H_tbill", "T_tbill")
        }
        for k in ("R", "S")
    }
    sys.stdout.write(
        json.dumps({"brief": brief, "verdict": res["verdict"]}, ensure_ascii=False, indent=1) + "\n"
    )
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
