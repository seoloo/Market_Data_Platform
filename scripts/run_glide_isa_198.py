# ruff: noqa: E501 — 결과 사전 줄이 길다
"""문서 198 — Glide 의 SCHD 몫을 ISA 국내 미국배당 ETF 로 옮기면 (기록, 판정 없음).

사용 예::

    python -m scripts.run_glide_isa_198 run   # reports/glide_isa_198/result.json

* A(지금): 토스 한 계좌에 TQQQ + SCHD — 184 T 규칙 그대로(양쪽 매매 차익 합산 250 만 공제 22%).
* 나눔: TQQQ · 브레이크 보관 달러는 토스, 배당 몫은 ISA(TIGER 미국배당다우존스 같은 국내 ETF).
  - F: 비율 맞추기 때 ISA 를 팔아 토스로 옮길 수 있음(중도인출, 납입 원금 안).
  - C: ISA 에서 빼지 않음 — 새 돈 · TQQQ 판 돈만 ISA 로 들어감, 배당 몫이 넘치면 새 돈을 TQQQ 로.
  - LONG: ISA 하나를 끝까지(끝에 한 번 9.9%) · 3Y: 3 년마다 만기 → 세금 → 새 ISA 에 다시 넣음.
* ISA 세금: (평가 − 순납입) − 비과세 200 만(서민형 400 만) 의 9.9%. 국내 ETF 추가 비용 연 0.2%(기준) · 0.65%(스트레스).
* 배당 원천징수 15% (배당률 3.5% 가정 → 연 0.525%) 는 토스 SCHD · ISA ETF 모두 똑같이 뺀다.
  ISA 만기 때 외국납부세액 크레딧은 넣지 않았다(ISA 에 불리한 쪽).
* 한도: 연 2,000 만 · 총 1 억 (현행법). 넘는 해를 센다(넘어도 계산은 그대로 — 실제로는 못 하는 구간).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_brake_183 import (
    COST,
    LEV_MAX,
    STAGE1,
    STAGE2,
    STAGE_B,
    VOL_REF,
    data_real,
    data_synth,
)

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "glide_isa_198"
DIV_DRAG = 0.035 * 0.15
ISA_EXTRA = 0.002
ISA_EXTRA_STRESS = 0.0065
ISA_TAX = 0.099
ISA_FREE = 2_000_000.0
ISA_FREE_LOW = 4_000_000.0
ISA_YEAR_CAP = 20_000_000.0
ISA_TOTAL_CAP = 100_000_000.0
AMOUNTS = (300_000.0, 1_000_000.0, 3_000_000.0)


def dragged(days: Sequence[str], px: Mapping[str, float], drag: float) -> dict[str, float]:
    """가격에 연 ``drag`` 만큼 날마다 덜 오르게 (252 거래일 기준, 순수)."""
    f = (1 - drag) ** (1 / 252)
    out, k = {}, 1.0
    for i, d in enumerate(days):
        if i:
            k *= f
        out[d] = px[d] * k
    return out


def simulate_split(
    data: Mapping[str, Any],
    d_isa: Mapping[str, float],
    *,
    monthly_krw: float,
    withdraw: bool,
    term_years: int | None,
    free: float = ISA_FREE,
) -> dict[str, Any]:
    """TQQQ · 보관 달러는 토스, 배당 몫은 ISA (순수). 브레이크 · 비율 규칙은 184 T 와 같다."""
    days, lev = data["days"], data["lev"]
    vol, trend = data["vol"], data["trend"]
    tb = m184.Book()
    q_isa = 0.0
    res_cash = units = contrib = 0.0
    stage, off_seen = 0, False
    l_max = 0.0
    ep: dict[str, Any] | None = None
    taxes: dict[str, float] = {}
    isa_taxes: list[tuple[str, float]] = []
    flows: list[tuple[str, float]] = []
    navs: list[float] = []
    principal = 0.0
    dep_year: dict[str, float] = {}
    dep_total = withdrawn = 0.0
    over_withdraw = 0
    opened: str | None = None
    i0 = next(i for i, d in enumerate(days) if d >= data["start"] and i > 0)

    def toss_value(d: str) -> float:
        return float(tb.q["L"] * lev[d]) + res_cash

    def value(d: str) -> float:
        return toss_value(d) + q_isa * d_isa[d]

    def deposit(d: str, krw: float) -> None:
        nonlocal q_isa, principal, dep_total, opened
        if krw <= 0:
            return
        q_isa += krw * (1 - COST) / d_isa[d]
        principal += krw
        dep_total += krw
        dep_year[d[:4]] = dep_year.get(d[:4], 0.0) + krw
        if opened is None:
            opened = d

    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        y = d[:4]
        new_month = d[:7] != prev[:7]
        # 0) 6 월: 토스 지난해 세금 (토스 쪽만 줄임, 184 처럼 단위도 함께 줄여 NAV 는 그대로)
        if new_month and d[5:7] == "06" and str(int(y) - 1) not in taxes and units > 0:
            t = m184.tax_of(tb.gain.get(str(int(y) - 1), 0.0))
            taxes[str(int(y) - 1)] = t
            tv, v = toss_value(d), value(d)
            if t > 0 and tv > 0:
                f = max(1 - t / tv, 0.0)
                tb.scale(f)
                res_cash *= f
                units *= max(1 - t / v, 0.0)
        # 1) T 브레이크 (토스 TQQQ)
        if units > 0 and tb.q["L"] > 0 and stage < 2:
            dd = 1 - lev[prev] / l_max if l_max > 0 else 0.0
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = tb.q["L"], 2
            elif dd >= STAGE1 and stage == 0:
                sell, stage = tb.q["L"] / 2, 1
            if sell > 0:
                res_cash += tb.sell("L", sell, lev[d], y)
                if ep is None:
                    ep = {"start": d}
                    off_seen = False
        # 2) 재매수
        if stage > 0 and ep is not None:
            t_on = trend.get(prev)
            if t_on is False:
                off_seen = True
            if t_on and off_seen:
                cash, res_cash = res_cash, 0.0
                tb.buy("L", cash, lev[d])
                ep, stage = None, 0
                l_max = lev[d]
        # 3) 매달 납입 + 비율 맞추기
        if new_month:
            nav_now = value(d) / units if units > 0 else 1.0
            units += monthly_krw / nav_now
            contrib += monthly_krw
            flows.append((d, monthly_krw))
            act_l, act_d = tb.q["L"] * lev[d], q_isa * d_isa[d]
            active = act_l + act_d + monthly_krw
            v = vol.get(prev)
            share = LEV_MAX * (min(1.0, VOL_REF / v) if v and v > 0 else 1.0) * STAGE_B[stage]
            tgt_l = share * active
            tgt_d = active - tgt_l
            pool = monthly_krw
            if act_l > tgt_l:
                pool += tb.sell("L", (act_l - tgt_l) / lev[d], lev[d], y)
            if act_d > tgt_d and withdraw:
                q = (act_d - tgt_d) / d_isa[d]
                got = q * d_isa[d] * (1 - COST)
                q_isa -= q
                principal -= got
                withdrawn += got
                if withdrawn > dep_total:
                    over_withdraw += 1
                pool += got
            need_l = max(tgt_l - tb.q["L"] * lev[d], 0.0)
            need_d = max(tgt_d - q_isa * d_isa[d], 0.0)
            tot = need_l + need_d
            to_l = pool * need_l / tot if tot > 0 else 0.0
            tb.buy("L", to_l, lev[d])
            deposit(d, pool - to_l)
        # 4) ISA 만기 (3Y): 세금 → 새 ISA 에 다시 넣음(팔고 사는 비용)
        if term_years and opened is not None and q_isa > 0:
            o = date.fromisoformat(opened)
            due = date(o.year + term_years, o.month, min(o.day, 28)).isoformat()
            if d >= due:
                val = q_isa * d_isa[d] * (1 - COST)
                t = ISA_TAX * max(0.0, val - principal - free)
                isa_taxes.append((d, t))
                units *= max(1 - t / value(d), 0.0)
                q_isa, principal, opened = 0.0, 0.0, None
                deposit(d, val - t)
        v_now = value(d)
        if units > 0:
            navs.append(v_now / units)
            l_max = max(l_max, lev[d])
    end = days[-1]
    last_y = end[:4]
    taxes[last_y] = m184.tax_of(tb.gain.get(last_y, 0.0))
    unreal = tb.q["L"] * lev[end] * (1 - COST) - tb.cost["L"] if tb.q["L"] else 0.0
    liq_tax = max(m184.tax_of(tb.gain.get(last_y, 0.0) + unreal) - taxes[last_y], 0.0)
    toss_end = toss_value(end) - taxes[last_y] - liq_tax
    # 184 처럼 끝 평가액에서 파는 비용은 빼지 않고, 세금 계산에만 쓴다
    val = q_isa * d_isa[end]
    t_end = ISA_TAX * max(0.0, val * (1 - COST) - principal - free)
    isa_taxes.append((end, t_end))
    over_year = sorted(yy for yy, x in dep_year.items() if x > ISA_YEAR_CAP)
    return {
        "navs": navs,
        "flows": flows,
        "end": end,
        "contrib": contrib,
        "after_tax_liq": toss_end + val - t_end,
        "toss_tax": sum(taxes.values()) + liq_tax,
        "isa_tax": sum(t for _, t in isa_taxes),
        "isa_end_value": val,
        "isa_share_end": val / (toss_end + val) if toss_end + val > 0 else 0.0,
        "dep_year_max": max(dep_year.values()) if dep_year else 0.0,
        "years_over_cap": over_year,
        "dep_total": dep_total,
        "over_withdraw_months": over_withdraw,
    }


def irr_of(sim: Mapping[str, Any]) -> float:
    return m184.irr(sim["flows"], sim["end"], sim["after_tax_liq"])


def mdd(navs: Sequence[float]) -> float:
    peak, out = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        out = max(out, 1 - v / peak)
    return out


def row_a(data: Mapping[str, Any], d_toss: Mapping[str, float], krw: float) -> dict[str, Any]:
    dd = {**data, "div": d_toss}
    s = m184.simulate(dd, dd["lev"], ref="T", park="cash", monthly_krw=krw)
    liq_tax = s["after_tax_hold"] - s["after_tax_liq"]
    return {
        "irr_liq": round(irr_of(s), 4),
        "final_x": round(s["after_tax_liq"] / s["contrib"], 3),
        "mdd": round(mdd(s["navs"]), 4),
        "tax_x": round((sum(s["taxes"].values()) + liq_tax) / s["contrib"], 3),
    }


def row_split(sim: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    c = sim["contrib"]
    irr = irr_of(sim)
    return {
        "irr_liq": round(irr, 4),
        "diff_pp": round((irr - base["irr_liq"]) * 100, 2),
        "final_x": round(sim["after_tax_liq"] / c, 3),
        "final_vs_A_pct": round((sim["after_tax_liq"] / c / base["final_x"] - 1) * 100, 2),
        "mdd": round(mdd(sim["navs"]), 4),
        "toss_tax_x": round(sim["toss_tax"] / c, 3),
        "isa_tax_x": round(sim["isa_tax"] / c, 3),
        "isa_share_end": round(sim["isa_share_end"], 3),
        "dep_year_max": round(sim["dep_year_max"]),
        "first_year_over_2000man": sim["years_over_cap"][0] if sim["years_over_cap"] else None,
        "dep_total_over_1eok": sim["dep_total"] > ISA_TOTAL_CAP,
        "over_withdraw_months": sim["over_withdraw_months"],
    }


VARIANTS: dict[str, dict[str, Any]] = {
    "F_LONG": {"withdraw": True, "term_years": None},
    "F_3Y": {"withdraw": True, "term_years": 3},
    "C_LONG": {"withdraw": False, "term_years": None},
    "C_3Y": {"withdraw": False, "term_years": 3},
}


def run_path(data: Mapping[str, Any], amounts: Sequence[float]) -> dict[str, Any]:
    days = data["days"]
    d_toss = dragged(days, data["div"], DIV_DRAG)
    d_isa = dragged(days, data["div"], DIV_DRAG + ISA_EXTRA)
    d_isa_s = dragged(days, data["div"], DIV_DRAG + ISA_EXTRA_STRESS)
    dd = {**data, "div": d_toss}
    out: dict[str, Any] = {}
    for krw in amounts:
        base = row_a(data, d_toss, krw)
        row: dict[str, Any] = {"A": base}
        for k, kw in VARIANTS.items():
            row[k] = row_split(simulate_split(dd, d_isa, monthly_krw=krw, **kw), base)
        row["F_3Y_seominhyeong"] = row_split(
            simulate_split(
                dd, d_isa, monthly_krw=krw, withdraw=True, term_years=3, free=ISA_FREE_LOW
            ),
            base,
        )
        row["F_LONG_stress065"] = row_split(
            simulate_split(dd, d_isa_s, monthly_krw=krw, withdraw=True, term_years=None), base
        )
        row["C_3Y_stress065"] = row_split(
            simulate_split(dd, d_isa_s, monthly_krw=krw, withdraw=False, term_years=3), base
        )
        out[f"{int(krw)}"] = row
    return out


def rolling(data: Mapping[str, Any], years: int, krw: float) -> dict[str, Any]:
    """합성 경로에서 해마다 1 월 시작 ``years`` 년 창 — A 대비 차이 분포."""
    days = data["days"]
    diffs: dict[str, list[float]] = {"F_3Y": [], "C_3Y": [], "F_LONG": []}
    starts = []
    for y0 in range(int(days[0][:4]) + 1, int(days[-1][:4]) - years + 1):
        a, b = f"{y0}-01-01", f"{y0 + years}-01-01"
        i = next(k for k, d in enumerate(days) if d >= a)
        j = next(k for k, d in enumerate(days) if d >= b)
        sl = days[i - 1 : j]
        sub = {**data, "days": sl, "start": a}
        res = run_path_one(sub, krw)
        starts.append(y0)
        for k in diffs:
            diffs[k].append(res[k]["final_vs_A_pct"])
    out: dict[str, Any] = {
        "years": years,
        "monthly": krw,
        "starts": [starts[0], starts[-1]],
        "n": len(starts),
    }
    for k, xs in diffs.items():
        s = sorted(xs)
        out[k] = {
            "min": round(s[0], 2),
            "median": round(s[len(s) // 2], 2),
            "max": round(s[-1], 2),
            "share_better": round(sum(x > 0 for x in xs) / len(xs), 3),
        }
    return out


def run_path_one(data: Mapping[str, Any], krw: float) -> dict[str, Any]:
    days = data["days"]
    d_toss = dragged(days, data["div"], DIV_DRAG)
    d_isa = dragged(days, data["div"], DIV_DRAG + ISA_EXTRA)
    dd = {**data, "div": d_toss}
    base = row_a(data, d_toss, krw)
    return {
        k: row_split(simulate_split(dd, d_isa, monthly_krw=krw, **VARIANTS[k]), base)
        for k in ("F_3Y", "C_3Y", "F_LONG")
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    dr, ds = data_real(), data_synth()
    res = {
        "R": run_path(dr, AMOUNTS),
        "S": run_path(ds, AMOUNTS),
        "S_rolling10_30man": rolling(ds, 10, 300_000.0),
        "S_rolling10_100man": rolling(ds, 10, 1_000_000.0),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_glide_isa_198 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
