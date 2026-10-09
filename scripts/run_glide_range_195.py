# ruff: noqa: E501 — 결과 사전 줄이 길다
"""문서 195 — Glide 기대 범위 · 배당 단계 도달 시점 · 12 월 공제 실현 효과 (기록, 판정 없음).

사용 예::

    python -m scripts.run_glide_range_195 run   # reports/glide_range_195/result.json

* Glide = 184 틀(TQQQ + SCHD 변동성 비율 · T 브레이크 · 달러 보관 · 세금 250 만 공제 22%).
* A 기대 범위: 과거 일간 수익을 63 거래일 덩어리로 섞어 만든 가상 경로(블록 부트스트랩)로
  1 · 3 · 5 · 10 · 20 · 30 년 뒤 결과 분포를 본다. 출처는 S 합성(1986 ~) · R 실제(2012 ~) 따로.
* B 배당: 가상 경로에서 SCHD 평가액 × 배당률 3.5% × 세후 85% ÷ 12 가 월 10 · 30 · 50 · 100 만 원에
  처음 닿는 해.
* C 공제 실현: 해마다 12-15 뒤 첫 거래일 남은 공제만큼 실현 · 같은 날 되사기, 월 30 · 100 · 300 만 원.
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_brake_183 import data_real, data_synth, signals

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "glide_range_195"
SEED = 195
BLOCK = 63
WARM = 140
YEARS = 30
PATHS = 400
HORIZONS = (1, 3, 5, 10, 20, 30)
DIV_YIELD, DIV_KEEP = 0.035, 0.85
STEPS_KRW = (100_000, 300_000, 500_000, 1_000_000)
AMOUNTS = (300_000.0, 1_000_000.0, 3_000_000.0)


def returns(
    data: Mapping[str, Any], sig_px: Mapping[str, float]
) -> list[tuple[float, float, float, float]]:
    """원자료 → 날마다 (레버리지 · 배당 · 신호 지수 수익, 금리)."""
    days = [d for d in data["days"] if d in sig_px]
    out = []
    for a, b in pairwise(days):
        out.append(
            (
                data["lev"][b] / data["lev"][a] - 1,
                data["div"][b] / data["div"][a] - 1,
                sig_px[b] / sig_px[a] - 1,
                data["rf"].get(a, 0.0),
            )
        )
    return out


def biz_days(n: int) -> list[str]:
    """가상 거래일 — 해마다 평일 중 고르게 몇 날을 빼서 252 일로 맞춘다.

    평일만 쓰면 한 해가 261 일이라 252 일 수익 30 해치가 29.9 해에 끝난다(첫 실행이 30 년 칸에서 멈춘 까닭).
    """
    out: list[str] = []
    year = 2000
    while len(out) < n:
        wk = [
            d
            for d in (date(year, 1, 1) + timedelta(days=i) for i in range(366))
            if d.year == year and d.weekday() < 5
        ]
        drop = {round((k + 0.5) * len(wk) / (len(wk) - 252)) for k in range(len(wk) - 252)}
        out.extend(d.isoformat() for i, d in enumerate(wk) if i not in drop)
        year += 1
    return out[:n]


def make_path(
    src: Sequence[tuple[float, float, float, float]], n: int, rng: random.Random
) -> dict[str, Any]:
    """블록 부트스트랩 가상 경로 (184 simulate 입력 꼴)."""
    rows: list[tuple[float, float, float, float]] = []
    while len(rows) < n:
        i = rng.randrange(0, len(src) - BLOCK)
        rows.extend(src[i : i + BLOCK])
    rows = rows[:n]
    days = biz_days(n + 1)
    lev, div, sig, rf = {days[0]: 100.0}, {days[0]: 100.0}, {days[0]: 100.0}, {days[0]: rows[0][3]}
    for d0, d1, (rl, rd, rs, r) in zip(days[:-1], days[1:], rows, strict=True):
        lev[d1] = max(lev[d0] * (1 + rl), 1e-9)
        div[d1] = div[d0] * (1 + rd)
        sig[d1] = sig[d0] * (1 + rs)
        rf[d1] = r
    vol, trend = signals(days, sig)
    return {
        "days": days,
        "lev": lev,
        "div": div,
        "vol": vol,
        "trend": trend,
        "rf": rf,
        "start": days[WARM],
    }


def path_stats(sim: Mapping[str, Any], monthly: float) -> dict[str, Any]:
    days, navs, dv, vals = sim["nav_days"], sim["navs"], sim["div_vals"], sim["vals"]
    t0 = datetime.fromisoformat(days[0])
    out: dict[str, Any] = {"monthly": monthly, "brake_sales": len(sim["sales"])}
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    out["mdd"] = mdd
    yrs = [(datetime.fromisoformat(d) - t0).days / 365.25 for d in days]
    paid_by: list[float] = []
    paid, flows, k = 0.0, sim["flows"], 0
    for d in days:
        while k < len(flows) and flows[k][0] <= d:
            paid += flows[k][1]
            k += 1
        paid_by.append(paid)
    for h in HORIZONS:
        i = next((j for j, y in enumerate(yrs) if y >= h), None)
        out[f"x{h}"] = vals[i] / paid_by[i] if i is not None and paid_by[i] else None
    # 배당 단계 도달 해 (월 배당 = SCHD 평가 × 3.5% × 85% ÷ 12)
    for s in STEPS_KRW:
        out[f"div{s // 10_000}"] = next(
            (y for y, v in zip(yrs, dv, strict=True) if v * DIV_YIELD * DIV_KEEP / 12 >= s), None
        )
    return out


def pct(xs: Sequence[float], ps: Sequence[float] = (5, 25, 50, 75, 95)) -> dict[str, float]:
    s = sorted(xs)
    return {
        f"p{int(p)}": round(s[min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))], 3)
        for p in ps
    }


def part_a_b(name: str, src: Sequence[tuple[float, float, float, float]]) -> dict[str, Any]:
    rng = random.Random(SEED)
    n = WARM + (YEARS + 1) * 252
    rows = []
    for _ in range(PATHS):
        data = make_path(src, n, rng)
        sim = m184.simulate(data, data["lev"], ref="T", park="cash", monthly_krw=300_000.0)
        rows.append(path_stats(sim, 300_000.0))
    out: dict[str, Any] = {"paths": PATHS, "years": YEARS, "source": name}
    for h in HORIZONS:
        xs = [r[f"x{h}"] for r in rows if r.get(f"x{h}") is not None]
        out[f"{h}y"] = {**pct(xs), "loss_prob": round(sum(x < 1 for x in xs) / len(xs), 3)}
    out["mdd"] = {
        **pct([r["mdd"] for r in rows]),
        "p_gt_50": round(sum(r["mdd"] > 0.5 for r in rows) / len(rows), 3),
    }
    out["brake_sales_30y"] = pct([float(r["brake_sales"]) for r in rows])
    for s in STEPS_KRW:
        k = f"div{s // 10_000}"
        hits = [r[k] for r in rows if r[k] is not None]
        out[k] = {"reach_prob_30y": round(len(hits) / len(rows), 3), **(pct(hits) if hits else {})}
    return out


def part_c() -> dict[str, Any]:
    res: dict[str, Any] = {}
    for name, data in (("R", data_real()), ("S", data_synth())):
        q = m184.qqq_real() if name == "R" else m184.qqq_synth(data["days"], data["rf"])
        row = {}
        for krw in AMOUNTS:
            a = m184.simulate(data, q, ref="T", park="cash", monthly_krw=krw)
            b = m184.simulate(data, q, ref="T", park="cash", monthly_krw=krw, harvest=True)
            ia, ib = (
                m184.irr(a["flows"], a["end"], a["after_tax_liq"]),
                m184.irr(b["flows"], b["end"], b["after_tax_liq"]),
            )
            row[f"{int(krw)}"] = {
                "irr_liq": round(ia, 4),
                "irr_liq_harvest": round(ib, 4),
                "diff_pp": round((ib - ia) * 100, 2),
                "final_x": round(a["after_tax_liq"] / a["contrib"], 3),
                "final_x_harvest": round(b["after_tax_liq"] / b["contrib"], 3),
                "final_gain_pct": round((b["after_tax_liq"] / a["after_tax_liq"] - 1) * 100, 2),
                "tax_paid_x": round(sum(a["taxes"].values()) / a["contrib"], 3),
                "tax_paid_x_harvest": round(sum(b["taxes"].values()) / b["contrib"], 3),
            }
        res[name] = row
    return res


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    s = data_synth()
    from scripts.run_qs_dotcom import _series

    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    src_s = returns(s, ndx)
    r = data_real()
    qqq = m184.qqq_real()
    src_r = returns(r, qqq)
    res = {
        "A_B_synth": part_a_b("S 합성 1986~", src_s),
        "A_B_real": part_a_b("R 실제 2012~", src_r),
        "C_harvest": part_c(),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_glide_range_195 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
