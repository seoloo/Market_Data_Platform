# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 220 — Glide 의 TQQQ 몫을 반은 T 브레이크, 반은 추세 켜짐/꺼짐(AG-US · QS 신호)으로 나누면? (SCHD 규칙 그대로)

사용::

    python -m scripts.run_glide_half_220 check   # G 가 184 의 T 결과를 재현하는지 (손익 비교 없음)
    python -m scripts.run_glide_half_220 run     # 사전 고정(문서 220) 뒤 한 번 → reports/glide_half_220/result.json

* 자료 · 신호 · 세금 · 비용 · 매달 30 만 원 · 매달 양쪽 맞추기 = 184(→ 실거래 Glide) 그대로.
* TQQQ 몫을 두 칸으로 나눈다. 칸마다 보유 수량 · 보관 달러를 따로 세고, 원가 · 세금은 한 계좌(이동평균)로 합친다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_brake_183 import (
    COST,
    CRISES,
    LEV_MAX,
    STAGE1,
    STAGE2,
    STAGE_B,
    VOL_REF,
    data_real,
    data_synth,
)
from scripts.run_brake_184 import MONTHLY_KRW, Book, qqq_real, qqq_synth, summarize, tax_of
from scripts.run_div_mix_180 import DATA_SHA as DIV_SHA
from scripts.run_div_mix_180 import own_hash
from scripts.run_lev_div_181 import LEV_SHA, lev_hash

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "glide_half_220"
ARMS: dict[str, tuple[float, float]] = {"G": (1.0, 0.0), "H": (0.5, 0.5), "Q": (0.0, 1.0)}
"""칸 비중 (T 브레이크 칸, 추세 칸). G = 지금 Glide, H = 반반, Q = 참고(전부 추세)."""
P_MDD = 0.05  # 184 와 같은 낙폭 허용


def simulate(
    data: Mapping[str, Any], qqq: Mapping[str, float], w_t: float, w_q: float
) -> dict[str, Any]:
    """일간 모의 계좌 (순수). 184 ``simulate(ref='T', park='cash')`` 를 두 칸으로 일반화.

    T 칸: TQQQ 고점(마지막 재매수 뒤) 대비 −15 % 면 그 칸 절반, −30 % 면 전부를 팔아 달러로 두고,
          QQQ 추세가 꺼졌다가 다시 켜진 날 그 칸 달러로 되산다(184 그대로, 전날 종가 판단 → 그날 종가 체결).
    추세 칸: QQQ 추세(20 · 60 · 120 일 부호 합 > 0)가 전날 꺼져 있으면 그 칸 TQQQ 를 전부 팔아 달러,
          켜져 있으면 그 칸 달러로 되산다(AG-US · QS 와 같은 신호, 매일 확인).
    매달: 납입 + 맞추기. TQQQ 목표 = 0.6 × min(1, 20 % / 20 일 변동성) × (w_t × 브레이크 단계 배수 + w_q × 추세 칸 켜짐),
          SCHD 목표 = 나머지(184 와 같은 규칙 — TQQQ 가 빠진 동안 새 돈은 SCHD 로). 보관 달러는 맞추기에서 뺀다.
    w_t = 1, w_q = 0 이면 184 의 T 와 같다(검산).
    """
    days, lev, div = data["days"], data["lev"], data["div"]
    vol, trend = data["vol"], data["trend"]
    px = {"L": lev, "D": div, "Q": qqq}
    bk = Book()
    q_t = q_q = 0.0  # 칸별 TQQQ 수량 (합 = bk.q["L"])
    res_t = res_q = 0.0  # 칸별 보관 달러
    units = contrib = 0.0
    stage, off_seen = 0, False
    q_in = True
    navs: list[float] = []
    nav_days: list[str] = []
    l_max = 0.0
    sales: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    ep: dict[str, Any] | None = None
    q_sales: list[dict[str, Any]] = []
    q_eps: list[dict[str, Any]] = []
    q_ep: dict[str, Any] | None = None
    taxes: dict[str, float] = {}
    flows: list[tuple[str, float]] = []
    out_days_q = 0
    i0 = next(i for i, d in enumerate(days) if d >= data["start"] and i > 0)

    def value(d: str) -> float:
        return float(sum(bk.q[s] * px[s][d] for s in ("L", "D") if bk.q[s])) + res_t + res_q

    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        y = d[:4]
        new_month = d[:7] != prev[:7]
        # 0) 6 월 첫 거래일: 지난해 세금 (보유 · 보관 모두 같은 비율로 줄임)
        if new_month and d[5:7] == "06" and str(int(y) - 1) not in taxes and units > 0:
            t = tax_of(bk.gain.get(str(int(y) - 1), 0.0))
            taxes[str(int(y) - 1)] = t
            v = value(d)
            if t > 0 and v > 0:
                f = max(1 - t / v, 0.0)
                bk.scale(f)
                q_t, q_q, res_t, res_q = q_t * f, q_q * f, res_t * f, res_q * f
                units *= f
        # 1) T 칸 브레이크
        if w_t > 0 and units > 0 and q_t > 0 and stage < 2:
            dd = 1 - lev[prev] / l_max if l_max > 0 else 0.0
            sell = 0.0
            if dd >= STAGE2:
                sell, stage = q_t, 2
            elif dd >= STAGE1 and stage == 0:
                sell, stage = q_t / 2, 1
            if sell > 0:
                res_t += bk.sell("L", sell, lev[d], y)
                q_t -= sell
                sales.append({"day": d, "price": lev[d], "dd": round(dd, 4)})
                if ep is None:
                    ep = {"start": d, "usd": 0.0, "qty": 0.0}
                    off_seen = False
                ep["usd"] += sell * lev[d]
                ep["qty"] += sell
        # 2) T 칸 재매수
        if stage > 0 and ep is not None:
            t_on = trend.get(prev)
            if t_on is False:
                off_seen = True
            if t_on and off_seen:
                q_t += bk.buy("L", res_t, lev[d])
                res_t = 0.0
                ep.update(end=d, avg_sale=ep["usd"] / ep["qty"], rebuy=lev[d])
                episodes.append(ep)
                ep, stage = None, 0
                l_max = lev[d]
        # 2b) 추세 칸
        if w_q > 0 and units > 0:
            t_on = trend.get(prev)
            if q_in and t_on is False:
                q_in = False
                if q_q > 0:
                    res_q += bk.sell("L", q_q, lev[d], y)
                    q_sales.append({"day": d, "price": lev[d]})
                    q_ep = {"start": d, "usd": q_q * lev[d], "qty": q_q}
                    q_q = 0.0
            elif not q_in and t_on:
                q_in = True
                if res_q > 0:
                    q_q += bk.buy("L", res_q, lev[d])
                    res_q = 0.0
                if q_ep is not None:
                    q_ep.update(end=d, avg_sale=q_ep["usd"] / q_ep["qty"], rebuy=lev[d])
                    q_eps.append(q_ep)
                    q_ep = None
        if w_q > 0 and not q_in:
            out_days_q += 1
        # 3) 매달 납입 + 맞추기 (보관 달러 제외)
        if new_month:
            v_before = value(d)
            nav_now = v_before / units if units > 0 else 1.0
            units += MONTHLY_KRW / nav_now
            contrib += MONTHLY_KRW
            flows.append((d, MONTHLY_KRW))
            act_t, act_q, act_d = q_t * lev[d], q_q * lev[d], bk.q["D"] * div[d]
            active = act_t + act_q + act_d + MONTHLY_KRW
            v = vol.get(prev)
            base = LEV_MAX * (min(1.0, VOL_REF / v) if v and v > 0 else 1.0)
            tgt_t = base * w_t * STAGE_B[stage] * active
            tgt_q = base * w_q * (1.0 if q_in else 0.0) * active
            tgt_d = active - tgt_t - tgt_q
            cash = MONTHLY_KRW
            if act_t > tgt_t:
                n = (act_t - tgt_t) / lev[d]
                cash += bk.sell("L", n, lev[d], y)
                q_t -= n
            if act_q > tgt_q:
                n = (act_q - tgt_q) / lev[d]
                cash += bk.sell("L", n, lev[d], y)
                q_q -= n
            if act_d > tgt_d:
                cash += bk.sell("D", (act_d - tgt_d) / div[d], div[d], y)
            need_t = max(tgt_t - q_t * lev[d], 0.0)
            need_q = max(tgt_q - q_q * lev[d], 0.0)
            need_d = max(tgt_d - bk.q["D"] * div[d], 0.0)
            tot = need_t + need_q + need_d
            if tot > 0:
                q_t += bk.buy("L", cash * need_t / tot, lev[d])
                q_q += bk.buy("L", cash * need_q / tot, lev[d])
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
    unreal = sum(bk.q[s] * px[s][end] * (1 - COST) - bk.cost[s] for s in ("L", "D") if bk.q[s])
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
        "sales": sales + q_sales,
        "episodes": episodes + q_eps,
        "all_sells": bk.sells,
        "t_sales": len(sales),
        "q_sales": len(q_sales),
        "t_episodes": episodes,
        "q_episodes": q_eps,
        "q_out_share": round(out_days_q / max(len(navs), 1), 4),
    }


def _extra(sim: Mapping[str, Any]) -> dict[str, Any]:
    def loss_share(eps: list[dict[str, Any]]) -> float | None:
        return round(sum(e["rebuy"] > e["avg_sale"] for e in eps) / len(eps), 3) if eps else None

    return {
        "t_brake_sales": sim["t_sales"],
        "trend_sales": sim["q_sales"],
        "t_loss_rebuy_share": loss_share(sim["t_episodes"]),
        "trend_loss_rebuy_share": loss_share(sim["q_episodes"]),
        "trend_half_out_share": sim["q_out_share"],
    }


def run_set(name: str, data: Mapping[str, Any], qqq: Mapping[str, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm, (wt, wq) in ARMS.items():
        sim = simulate(data, qqq, wt, wq)
        out[arm] = {**summarize(sim, CRISES[name]), **_extra(sim)}
    return out


def check() -> dict[str, Any]:
    """G(= 184 T)가 184 결과 파일과 같은지 — 손익 비교 없음, 엔진 검산만."""
    ref = json.loads((REPO / "reports" / "brake_184" / "result.json").read_text(encoding="utf-8"))
    dr, ds = data_real(), data_synth()
    res = {}
    for name, data, qqq in (("R", dr, qqq_real()), ("S", ds, qqq_synth(ds["days"], ds["rf"]))):
        g = summarize(simulate(data, qqq, 1.0, 0.0), CRISES[name])
        t = ref[name]["T"]
        res[name] = {
            k: (g[k], t[k])
            for k in ("cagr", "mdd", "after_tax_liq_x", "irr_liq", "brake_sales", "all_sells")
        }
    return res


def verdict(r: Mapping[str, Any], s: Mapping[str, Any]) -> dict[str, Any]:
    """문서 220 §4 — 결과 전 고정. H 가 R · S 둘 다 세후 최종 ≥ G 이고 낙폭 ≤ G + 5 %p 면 '반반 후보', 아니면 'Glide 유지'."""

    def beats(x: Mapping[str, Any]) -> bool:
        return bool(
            x["H"]["after_tax_liq_x"] >= x["G"]["after_tax_liq_x"]
            and x["H"]["mdd"] <= x["G"]["mdd"] + P_MDD
        )

    ok = [beats(r), beats(s)]
    return {
        "H_beats_G_[R,S]": ok,
        "word": "반반 후보(사용자 판단, 바로 바꾸지 않음)" if all(ok) else "Glide 유지",
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


def main(argv: list[str]) -> int:
    if argv == ["check"]:
        sys.stdout.write(json.dumps(check(), ensure_ascii=False, indent=1) + "\n")
        return 0
    if argv == ["run"]:
        res = run()
        keys = (
            "cagr",
            "mdd",
            "after_tax_liq_x",
            "irr_liq",
            "all_sells",
            "t_brake_sales",
            "trend_sales",
            "trend_half_out_share",
        )
        brief = {k: {a: {x: res[k][a][x] for x in keys} for a in ARMS} for k in ("R", "S")}
        sys.stdout.write(
            json.dumps({"brief": brief, "verdict": res["verdict"]}, ensure_ascii=False, indent=1)
            + "\n"
        )
        return 0
    raise SystemExit("usage: python -m scripts.run_glide_half_220 check|run")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
