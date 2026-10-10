# ruff: noqa: E501 — 결과 사전 줄이 길다
"""문서 197 — Cruise 기대 범위 · Glide 와 합친 계좌 범위 (기록, 판정 없음).

사용 예::

    python -m scripts.run_cruise_range_197 check   # 틀 점검(190 업비트판과 비교)만
    python -m scripts.run_cruise_range_197 run     # reports/cruise_range_197/result.json

* Cruise 규칙 = :mod:`auto_trading.ops.t2_upbit` (T/v2 + 세 종목 합 축소), 밴드 5%p, 마감 종가 체결,
  편도 0.10%, 매달 1 일 30 만 원, 코인 과세(250 만 공제 22%, 이듬해 5 월)를 처음부터.
* A: 코인 일간 수익 91 일 덩어리 부트스트랩 400 경로 × 10 년. 자료 E(BTC + ETH 2017-09 ~) · S3(+ SOL 2021-10 ~).
* B: 같은 91 일 달력 창을 Glide(184 T, 실제 TQQQ · SCHD) · Cruise 에 함께 붙여 합친 계좌 범위.
"""

from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_brake_183 import data_real, signals
from scripts.run_glide_range_195 import biz_days, pct

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "cruise_range_197"
SRC = REPO / "data" / "research" / "upbit_daily"
SEED = 197
WINDOW = 91
PATHS = 400
YEARS = 10
HORIZONS = (1, 3, 5, 10)
START = "2000-08-01"
LOOKBACKS = (20, 60, 120)
VOL_DAYS = 60
TARGET_VOL = 0.15
MAX_W = 0.5
BAND = 0.05
FEE = 0.001
MIN_KRW = 5000.0
DEDUCTION = 2_500_000.0
TAX = 0.22
DD_STOP = 0.30
MONTHLY = 300_000.0
END_CRYPTO = "2026-10-09"
END_GLIDE = "2026-08-31"


def load(coin: str) -> dict[str, float]:
    with (SRC / f"KRW-{coin}.csv").open(encoding="utf-8") as f:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(f) if r["date"] <= END_CRYPTO}


def weights(hist: Mapping[str, Sequence[float]]) -> dict[str, float]:
    """코인 → 목표 비중 (t2_upbit.targets 와 같은 계산, float · 순수). 이력이 모자라면 0."""
    out: dict[str, float] = {}
    for coin, closes in hist.items():
        if len(closes) < max(LOOKBACKS) + 1:
            out[coin] = 0.0
            continue
        last = closes[-1]
        signs = [
            (1 if last > closes[-1 - k] else -1 if last < closes[-1 - k] else 0) for k in LOOKBACKS
        ]
        direction = sum(signs) / len(signs)
        steps = [math.log(closes[-k] / closes[-k - 1]) for k in range(1, VOL_DAYS + 1)]
        mean = sum(steps) / VOL_DAYS
        vol = math.sqrt(sum((x - mean) ** 2 for x in steps) / (VOL_DAYS - 1)) * math.sqrt(365)
        size = MAX_W if vol <= 0 else min(TARGET_VOL / vol, MAX_W)
        out[coin] = max(direction, 0.0) * size
    total = sum(out.values())
    if total > 1:
        out = {c: w / total for c, w in out.items()}
    return out


def simulate_cruise(
    days: Sequence[str],
    px: Mapping[str, Mapping[str, float]],
    *,
    start: str,
    monthly: float = MONTHLY,
    lump: float = 0.0,
    tax: bool = True,
    lag: int = 0,
) -> dict[str, Any]:
    """일간 모의 Cruise 계좌 (순수). ``lag`` 1 이면 종가 d 목표를 종가 d+1 에 체결(190 틀 점검용)."""
    coins = list(px)
    qty = dict.fromkeys(coins, 0.0)
    cost = dict.fromkeys(coins, 0.0)
    cash = 0.0
    gain: dict[str, float] = {}
    taxes: dict[str, float] = {}
    hist: dict[str, list[float]] = {c: [] for c in coins}
    units = 0.0
    paid = 0.0
    flows: list[tuple[str, float]] = []
    navs: list[float] = []
    vals: list[float] = []
    nav_days: list[str] = []
    trades = 0
    pending: dict[str, float] | None = None
    peak, dd_hit = 0.0, None

    def value(d: str) -> float:
        return cash + sum(qty[c] * px[c][d] for c in coins if qty[c])

    for i, d in enumerate(days):
        for c in coins:
            if d in px[c]:
                hist[c].append(px[c][d])
        if d < start:
            continue
        prev = days[i - 1] if i else d
        new_month = (not navs) or d[:7] != prev[:7]
        # 세금: 지난해 실현 손익 → 5 월 첫날 낸다 (현금 먼저, 모자라면 보유를 비율로 줄임)
        if tax and new_month and d[5:7] == "05" and units > 0:
            y = str(int(d[:4]) - 1)
            if y not in taxes:
                t = TAX * max(0.0, gain.get(y, 0.0) - DEDUCTION)
                taxes[y] = t
                v = value(d)
                if t > 0 and v > 0:
                    f = max(1 - t / v, 0.0)
                    cash *= f
                    for c in coins:
                        qty[c] *= f
                        cost[c] *= f
        if new_month and (monthly or (lump and not navs)):
            amt = lump if (lump and not navs) else monthly
            nav_now = value(d) / units if units > 0 else 1.0
            units += amt / nav_now
            cash += amt
            paid += amt
            flows.append((d, amt))
        w_now = weights(hist)
        w = w_now if lag == 0 else pending
        pending = w_now
        if w is not None:
            eq = value(d)
            plan = []
            for c in coins:
                if d not in px[c]:
                    continue
                cur = qty[c] * px[c][d] / eq if eq > 0 else 0.0
                want = w.get(c, 0.0)
                if abs(want - cur) < BAND and (want > 0) == (cur > 0):
                    continue
                plan.append((want < cur, c, want, cur))
            for is_sell, c, want, cur in sorted(plan, key=lambda x: (not x[0], x[1])):
                p = px[c][d]
                if is_sell:
                    q = qty[c] if want == 0 else min(qty[c], (cur - want) * eq / p)
                    if q * p < MIN_KRW:
                        continue
                    proceeds = q * p * (1 - FEE)
                    avg = cost[c] / qty[c]
                    gain[d[:4]] = gain.get(d[:4], 0.0) + proceeds - avg * q
                    cost[c] -= avg * q
                    qty[c] -= q
                    cash += proceeds
                else:
                    krw = min((want - cur) * eq, cash)
                    if krw < MIN_KRW:
                        continue
                    qty[c] += krw * (1 - FEE) / p
                    cost[c] += krw
                    cash -= krw
                trades += 1
        v = value(d)
        if units > 0:
            nav = v / units
            navs.append(nav)
            vals.append(v)
            nav_days.append(d)
            peak = max(peak, nav)
            if dd_hit is None and nav <= peak * (1 - DD_STOP):
                dd_hit = d
    return {
        "nav_days": nav_days,
        "navs": navs,
        "vals": vals,
        "flows": flows,
        "paid": paid,
        "taxes": taxes,
        "trades": trades,
        "dd_hit": dd_hit,
    }


def mdd(xs: Sequence[float]) -> float:
    peak, out = xs[0], 0.0
    for v in xs:
        peak = max(peak, v)
        out = max(out, 1 - v / peak)
    return out


def paid_by(days: Sequence[str], flows: Sequence[tuple[str, float]]) -> list[float]:
    out, paid, k = [], 0.0, 0
    for d in days:
        while k < len(flows) and flows[k][0] <= d:
            paid += flows[k][1]
            k += 1
        out.append(paid)
    return out


def horizon_x(
    days: Sequence[str], vals: Sequence[float], paid: Sequence[float]
) -> dict[str, float | None]:
    t0 = datetime.fromisoformat(days[0])
    yrs = [(datetime.fromisoformat(d) - t0).days / 365.25 for d in days]
    out: dict[str, float | None] = {}
    for h in HORIZONS:
        i = next((j for j, y in enumerate(yrs) if y >= h), None)
        out[f"x{h}"] = vals[i] / paid[i] if i is not None and paid[i] else None
    return out


def cal_days(n: int) -> list[str]:
    d0 = date(2000, 1, 1)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def crypto_rows(
    px: Mapping[str, Mapping[str, float]], a: str, b: str
) -> tuple[list[str], list[dict[str, float]]]:
    """[a, b] 의 날마다 코인 수익 (모든 코인이 있는 날만, 순수)."""
    days = sorted(d for d in next(iter(px.values())) if a <= d <= b and all(d in px[c] for c in px))
    rows = [{c: px[c][q] / px[c][p] - 1 for c in px} for p, q in pairwise(days)]
    return days[1:], rows


def build_crypto(
    rows: Sequence[Mapping[str, float]], coins: Sequence[str]
) -> tuple[list[str], dict[str, dict[str, float]]]:
    days = cal_days(len(rows) + 1)
    px: dict[str, dict[str, float]] = {c: {days[0]: 100.0} for c in coins}
    for d0, d1, r in zip(days[:-1], days[1:], rows, strict=True):
        for c in coins:
            px[c][d1] = max(px[c][d0] * (1 + r[c]), 1e-9)
    return days, px


def summarize_paths(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"paths": len(rows)}
    for h in HORIZONS:
        xs = [r[f"x{h}"] for r in rows if r.get(f"x{h}") is not None]
        if xs:
            out[f"{h}y"] = {**pct(xs), "loss_prob": round(sum(x < 1 for x in xs) / len(xs), 3)}
    out["mdd"] = {
        **pct([r["mdd"] for r in rows]),
        "p_gt_30": round(sum(r["mdd"] > 0.3 for r in rows) / len(rows), 3),
        "p_gt_50": round(sum(r["mdd"] > 0.5 for r in rows) / len(rows), 3),
    }
    if all("dd_year" in r for r in rows):
        for h in HORIZONS:
            out[f"halt30_within_{h}y"] = round(
                sum(r["dd_year"] is not None and r["dd_year"] < h for r in rows) / len(rows), 3
            )
    return out


def part_a(name: str, px: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    coins = list(px)
    a = max(min(v) for v in px.values())
    _, src = crypto_rows(px, a, END_CRYPTO)
    rng = random.Random(SEED)
    n = (date(2000 + YEARS + 1, 1, 1) - date(2000, 1, 1)).days
    out_rows = []
    for _ in range(PATHS):
        rows: list[dict[str, float]] = []
        while len(rows) < n:
            i = rng.randrange(0, len(src) - WINDOW)
            rows.extend(src[i : i + WINDOW])
        days, sp = build_crypto(rows[:n], coins)
        sim = simulate_cruise(days, sp, start=START)
        paid = paid_by(sim["nav_days"], sim["flows"])
        t0 = datetime.fromisoformat(sim["nav_days"][0])
        out_rows.append(
            {
                **horizon_x(sim["nav_days"], sim["vals"], paid),
                "mdd": mdd(sim["navs"]),
                "dd_year": None
                if sim["dd_hit"] is None
                else (datetime.fromisoformat(sim["dd_hit"]) - t0).days / 365.25,
                "trades_per_year": sim["trades"] / YEARS,
            }
        )
    res = summarize_paths(out_rows)
    res["source"] = f"{name} {a} ~ {END_CRYPTO}"
    res["trades_per_year"] = pct([r["trades_per_year"] for r in out_rows])
    return res


def glide_rows(
    dr: Mapping[str, Any], qqq: Mapping[str, float], a: str, b: str
) -> list[tuple[str, tuple[float, float, float, float]]]:
    days = [d for d in dr["days"] if d in qqq and d <= END_GLIDE]
    out = []
    for p, q in pairwise(days):
        if a <= q <= b:
            out.append(
                (
                    q,
                    (
                        dr["lev"][q] / dr["lev"][p] - 1,
                        dr["div"][q] / dr["div"][p] - 1,
                        qqq[q] / qqq[p] - 1,
                        dr["rf"].get(p, 0.0),
                    ),
                )
            )
    return out


def build_glide(rows: Sequence[tuple[float, float, float, float]]) -> dict[str, Any]:
    days = biz_days(len(rows) + 1)
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
        "start": START,
    }


def month_end(days: Sequence[str], vals: Sequence[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d, v in zip(days, vals, strict=True):
        out[d[:7]] = v
    return out


def combo_stats(parts: Sequence[tuple[dict[str, float], dict[str, float]]]) -> dict[str, Any]:
    """[(달말 평가액, 달말까지 넣은 돈)] 합 → 배수 · 낙폭 (단위 가치, 순수)."""
    months = sorted(set.intersection(*(set(v) for v, _ in parts)))
    val = [sum(v[m] for v, _ in parts) for m in months]
    paid = [sum(p[m] for _, p in parts) for m in months]
    units, navs, prev_paid = 0.0, [], 0.0
    for v, p in zip(val, paid, strict=True):
        flow = p - prev_paid
        nav_before = (v - flow) / units if units > 0 else 1.0
        units += flow / nav_before if nav_before > 0 else 0.0
        navs.append(v / units if units > 0 else 1.0)
        prev_paid = p
    out: dict[str, Any] = {"mdd": mdd(navs)}
    for h in HORIZONS:
        k = h * 12 - 1
        out[f"x{h}"] = val[k] / paid[k] if k < len(months) and paid[k] else None
    return out


def part_b(name: str, px: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    coins = list(px)
    a = max(min(v) for v in px.values())
    dr = data_real()
    qqq = m184.qqq_real()
    starts = [
        d
        for d in (date.fromisoformat(a) + timedelta(days=i) for i in range(4000))
        if d.isoformat() <= END_GLIDE
    ]
    starts = [d for d in starts if (d + timedelta(days=WINDOW - 1)).isoformat() <= END_GLIDE]
    g_all = glide_rows(dr, qqq, a, END_GLIDE)
    _, c_src = crypto_rows(px, a, END_GLIDE)
    c_days, _ = crypto_rows(px, a, END_GLIDE)
    c_index = {d: i for i, d in enumerate(c_days)}
    rng = random.Random(SEED + 1)
    n_c = (date(2000 + YEARS + 1, 1, 1) - date(2000, 1, 1)).days
    res_rows: dict[str, list[dict[str, Any]]] = {
        "combo": [],
        "glide60": [],
        "cruise60": [],
        "glide30": [],
        "cruise30": [],
    }
    corr_m: list[tuple[float, float]] = []
    for _ in range(PATHS):
        g_rows: list[tuple[float, float, float, float]] = []
        c_rows: list[dict[str, float]] = []
        while len(c_rows) < n_c:
            s = rng.choice(starts)
            e = (s + timedelta(days=WINDOW - 1)).isoformat()
            g_rows.extend(r for d, r in g_all if s.isoformat() <= d <= e)
            c_rows.extend(c_src[c_index[d]] for d in c_days if s.isoformat() <= d <= e)
        c_rows = c_rows[:n_c]
        gd = build_glide(g_rows)
        cd, cpx = build_crypto(c_rows, coins)
        out_g, out_c = {}, {}
        for amt in (MONTHLY, 2 * MONTHLY):
            gs = m184.simulate(gd, gd["lev"], ref="T", park="cash", monthly_krw=amt)
            cs = simulate_cruise(cd, cpx, start=START, monthly=amt)
            out_g[amt] = (
                month_end(gs["nav_days"], gs["vals"]),
                month_end(gs["nav_days"], paid_by(gs["nav_days"], gs["flows"])),
            )
            out_c[amt] = (
                month_end(cs["nav_days"], cs["vals"]),
                month_end(cs["nav_days"], paid_by(cs["nav_days"], cs["flows"])),
            )
        res_rows["combo"].append(combo_stats([out_g[MONTHLY], out_c[MONTHLY]]))
        res_rows["glide60"].append(combo_stats([out_g[2 * MONTHLY]]))
        res_rows["cruise60"].append(combo_stats([out_c[2 * MONTHLY]]))
        res_rows["glide30"].append(combo_stats([out_g[MONTHLY]]))
        res_rows["cruise30"].append(combo_stats([out_c[MONTHLY]]))
        # 달마다 단위 가치 수익 상관 (거치 대리: 30 만 원 경로의 달말 평가액 변화율에서 납입 뺌)
        gv, gp = out_g[MONTHLY]
        cv, cp = out_c[MONTHLY]
        ms = sorted(set(gv) & set(cv))
        for m0, m1 in pairwise(ms):
            if gv[m0] > 0 and cv[m0] > 0:
                corr_m.append(
                    (
                        (gv[m1] - (gp[m1] - gp[m0])) / gv[m0] - 1,
                        (cv[m1] - (cp[m1] - cp[m0])) / cv[m0] - 1,
                    )
                )
    out: dict[str, Any] = {"source": f"{name} 창 {a} ~ {END_GLIDE}"}
    for k, rows in res_rows.items():
        out[k] = summarize_paths(rows)
    xs, ys = zip(*corr_m, strict=True)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in corr_m)
    out["monthly_corr"] = round(
        cov / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)), 3
    )
    return out


def check() -> dict[str, Any]:
    """틀 점검: 실제 역사 2020-03 ~ 2026-08, BTC + ETH, 거치식, 세금 없음, 다음 날 종가 체결 (190 과 비교)."""
    px = {c: load(c) for c in ("BTC", "ETH")}
    days = sorted(d for d in px["BTC"] if d in px["ETH"] and d <= "2026-08-31")
    out = {}
    for lag in (1, 0):
        sim = simulate_cruise(
            days, px, start="2020-03-01", monthly=0.0, lump=10_000_000.0, tax=False, lag=lag
        )
        yrs = (
            datetime.fromisoformat(sim["nav_days"][-1]) - datetime.fromisoformat(sim["nav_days"][0])
        ).days / 365.25
        out[f"lag{lag}"] = {
            "cagr": round((sim["navs"][-1] / sim["navs"][0]) ** (1 / yrs) - 1, 4),
            "mdd": round(mdd(sim["navs"]), 4),
            "trades": sim["trades"],
        }
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    e = {c: load(c) for c in ("BTC", "ETH")}
    s3 = {c: load(c) for c in ("BTC", "ETH", "SOL")}
    res = {
        "check": check(),
        "A_E": part_a("E BTC + ETH", e),
        "A_S3": part_a("S3 BTC + ETH + SOL", s3),
        "B_E": part_b("E BTC + ETH", e),
        "B_S3": part_b("S3 BTC + ETH + SOL", s3),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv == ["check"]:
        sys.stdout.write(json.dumps(check(), ensure_ascii=False, indent=1) + "\n")
        return 0
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_cruise_range_197 check|run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
