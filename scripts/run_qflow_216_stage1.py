# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 216 — 신호(A 15 분 경계 · B 5 분 밀린 경계 · C 3 시간 전체 흐름) · 자료 감사 · 1 단계 비용 문턱 점검.

1 단계는 보호 · 포지션 제한 없는 사건 진단이다(운용 계좌 성과 아님). 기준가는 1 분봉 시가이고 체결 보장 가격이 아니다.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import io
import json
import math
import pickle
import random
import statistics
import zipfile
from array import array
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from scripts.run_qb_dc_210 import WARM, Market, _load
from scripts.run_qflow_216 import (
    DAY,
    LAST_MONTH,
    MIN,
    REPORT,
    STORE,
    SYMBOLS,
    VISION,
    WIN,
    _ms,
    months,
)

END = _ms(date(2026, 9, 1))  # 이 시각 이후는 읽지 않는다
EVAL_START = _ms(date(2020, 7, 1))  # 첫 문턱(직전 180 일)이 2020-01 자료로 채워지는 첫 달
MID = _ms(
    date(2023, 8, 1)
)  # 2020-07-01 ~ 2023-07-31 (1,126 일) / 2023-08-01 ~ 2026-08-31 (1,127 일)
PAPER = (_ms(date(2021, 1, 1)), _ms(date(2024, 11, 1)))  # 논문 표본 2021-01-01 ~ 2024-10-31
N_MIN = (END - WARM) // MIN
Q15 = 15 * MIN
LOOKBACK = 12  # 경계 12 개 ≈ 3 시간
C_MIN = 180  # C: 직전 3 시간 1 분봉
TH_DAYS = 180
TH_MIN_SHARE = 0.9  # 문턱 계산에 필요한 점수 비율 (180 일 × 96 의 90 %)
HOLD = 720  # 12 시간 (분)
COST = 0.00095
STRATS = ("A", "B", "C")


# ------------------------------------------------------------------ 자료


def kline_arrays(symbol: str) -> tuple[bytearray, array[float], array[float]]:
    """WARM 부터 분 단위: (1 분봉 있음, 거래량, taker 매수량)."""
    have = bytearray(N_MIN)
    vol, tb = array("d", bytes(8 * N_MIN)), array("d", bytes(8 * N_MIN))
    for z in sorted((VISION / "um" / symbol / "1m").glob("*.zip")):
        if z.stem.split("-", 2)[2] > LAST_MONTH:
            continue
        with zipfile.ZipFile(z) as zf:
            for line in io.TextIOWrapper(zf.open(zf.namelist()[0]), "utf-8"):
                p = line.split(",")
                if not p[0].isdigit():
                    continue
                t = int(p[0])
                if WARM <= t < END:
                    i = (t - WARM) // MIN
                    have[i], vol[i], tb[i] = 1, float(p[5]), float(p[9])
    return have, vol, tb


def load_agg(symbol: str) -> dict[str, dict[str, Any]]:
    out = {}
    for mo in months():
        f = STORE / "agg" / f"{symbol}-{mo}.pkl"
        if f.exists():
            with f.open("rb") as fh:
                out[mo] = pickle.load(fh)
    return out


def _month_bounds(mo: str) -> tuple[int, int]:
    y, m = map(int, mo.split("-"))
    a = _ms(date(y, m, 1))
    b = _ms(date(y + 1, 1, 1)) if m == 12 else _ms(date(y, m + 1, 1))
    return a, b


def window_imbalance(
    agg: dict[str, dict[str, Any]], have: bytearray
) -> tuple[dict[int, float | None], Counter[str]]:
    """매 5 분 경계 W → 첫 10 초 불균형 I, 불완전하면 None.

    완전 조건: 그 달 파일 있음 · 앞 달 마지막 id + 1 = 이 달 첫 id · W 를 걸치는 id 빈 곳(빠짐 · 중복) 없음 · W 분의 공식 1 분봉 있음.
    완전한데 체결이 없으면 I = 0.
    """
    out: dict[int, float | None] = {}
    why: Counter[str] = Counter()
    prev_last: int | None = None
    for mo in months():
        a, b = _month_bounds(mo)
        ex = agg.get(mo)
        cont = ex is not None and (prev_last is None or ex["first_id"] == prev_last + 1)
        gaps = sorted((g[0], g[1]) for g in ex["gaps"]) if ex else []
        g_lo = [g[0] for g in gaps]
        for w in range(a, b, 5 * MIN):
            if not (WARM <= w < END):
                continue
            if ex is None:
                out[w] = None
                why["월 파일 없음"] += 1
                continue
            if not cont:
                out[w] = None
                why["월 경계 id 불연속"] += 1
                continue
            k = bisect.bisect_left(
                g_lo, w + WIN
            )  # 앞 체결 시각 < W + 10 초 인 빈 곳들 중 뒤 체결 시각 > W 인 것
            if any(gaps[j][1] > w for j in range(max(0, k - 50), k)):
                out[w] = None
                why["id 빈 곳 · 중복"] += 1
                continue
            if not have[(w - WARM) // MIN]:
                out[w] = None
                why["1 분봉 없음"] += 1
                continue
            v = ex["windows"].get(w)
            if not v or v[2] == 0:
                out[w] = 0.0
                why["완전 · 체결 없음"] += 1
                continue
            buy, sell = v[0], v[1]
            out[w] = (buy - sell) / (buy + sell)
            why["완전"] += 1
        if ex is not None:
            prev_last = ex["last_id"]
    return out, why


def scores(
    wi: dict[int, float | None], have: bytearray, vol: array[float], tb: array[float]
) -> dict[str, dict[int, float | None]]:
    """전략 → 판단 경계 T(ms) → 점수, 자료 불완전이면 None. A · C 는 매시 00 · 15 · 30 · 45 분, B 는 05 · 20 · 35 · 50 분."""
    out: dict[str, dict[int, float | None]] = {s: {} for s in STRATS}
    # C 용 누적합
    cv, cb, cm = array("d", [0.0]), array("d", [0.0]), array("l", [0])
    for i in range(N_MIN):
        cv.append(cv[-1] + vol[i])
        cb.append(cb[-1] + tb[i])
        cm.append(cm[-1] + (0 if have[i] else 1))
    for t in range(WARM, END, 5 * MIN):
        ph = (t - WARM) % Q15
        if ph not in (0, 5 * MIN):
            continue
        xs = [wi.get(t - k * Q15) for k in range(LOOKBACK)]
        s = None if any(x is None for x in xs) else statistics.fmean(xs)  # type: ignore[arg-type]
        out["A" if ph == 0 else "B"][t] = s
        if ph == 0:
            i1 = (t - WARM) // MIN
            i0 = i1 - C_MIN
            if i0 < 0 or cm[i1] - cm[i0] > 0:
                out["C"][t] = None
            else:
                v = cv[i1] - cv[i0]
                b = cb[i1] - cb[i0]
                out["C"][t] = (2 * b - v) / v if v > 0 else None
    return out


def pct(xs: list[float], q: float) -> float:
    """선형 보간 백분위 (numpy 기본과 같음). xs 는 정렬됨."""
    h = (len(xs) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def thresholds(sc: dict[int, float | None]) -> dict[int, tuple[float, float] | None]:
    """UTC 날짜 시작 ms → (P10, P90). 직전 180 개 완성 일자의 점수만(그날 자료 제외), 00:00 에 고정."""
    by_day: dict[int, list[float]] = {}
    for t, s in sc.items():
        if s is not None:
            by_day.setdefault(t // DAY * DAY, []).append(s)
    need = int(TH_MIN_SHARE * TH_DAYS * 96)
    out: dict[int, tuple[float, float] | None] = {}
    for d in range(EVAL_START - DAY, END, DAY):
        pool: list[float] = []
        for k in range(1, TH_DAYS + 1):
            pool.extend(by_day.get(d - k * DAY, ()))
        if len(pool) < need:
            out[d] = None
            continue
        pool.sort()
        out[d] = (pct(pool, 0.10), pct(pool, 0.90))
    return out


def signals(
    sc: dict[int, float | None], th: dict[int, tuple[float, float] | None]
) -> tuple[list[tuple[int, int]], Counter[str]]:
    """평가 구간 경계의 (T, 방향 +1 롱 / −1 숏). P90 초과 롱, P10 미만 숏."""
    out: list[tuple[int, int]] = []
    why: Counter[str] = Counter()
    for t in sorted(sc):
        if not (EVAL_START <= t < END):
            continue
        s = sc[t]
        if s is None:
            why["점수 자료 불완전"] += 1
            continue
        lim = th.get(t // DAY * DAY)
        if lim is None:
            why["문턱 자료 부족"] += 1
            continue
        if s > lim[1]:
            out.append((t, 1))
        elif s < lim[0]:
            out.append((t, -1))
        else:
            why["문턱 안"] += 1
    return out, why


# ------------------------------------------------------------------ 사건 수익 (1 단계)


def event(m: Market, ft: list[int], t: int, side: int, delay_min: int) -> dict[str, Any] | None:
    """진입 기준 = T + delay 분에 시작하는 1 분봉 시가, 청산 = 그 12 시간 뒤 1 분봉 시가. 체결 보장 가격 아님."""
    i = (t - WARM) // MIN + delay_min
    j = i + HOLD
    if WARM + j * MIN >= END:
        return None
    if m.qv[i] <= 0 or m.qv[j] <= 0:
        return {"drop": True}
    p0, p1 = m.o[i], m.o[j]
    t0, t1 = WARM + i * MIN, WARM + j * MIN
    pay = 0.0
    for k in range(bisect.bisect_right(ft, t0), bisect.bisect_left(ft, t1)):
        f_ms, rate, mk = m.funding[k]
        px = mk if mk is not None else m.c[(f_ms - WARM) // MIN - 1]
        pay += side * rate * px / p0  # 롱: rate > 0 이면 냄 · 숏: 받음
    gross = side * (p1 / p0 - 1)
    fee = COST * (1 + p1 / p0)
    return {
        "t": t,
        "side": side,
        "p0": p0,
        "p1": p1,
        "gross": gross,
        "fee": fee,
        "funding": pay,
        "net": gross - fee - pay,
    }


def summ(ev: list[dict[str, Any]]) -> dict[str, Any]:
    if not ev:
        return {"n": 0}
    net = sorted(e["net"] for e in ev)
    top = sorted(net, reverse=True)[: max(1, math.ceil(0.05 * len(net)))]
    return {
        "n": len(ev),
        "mean_gross_bp": round(statistics.fmean(e["gross"] for e in ev) * 1e4, 2),
        "mean_fee_bp": round(statistics.fmean(e["fee"] for e in ev) * 1e4, 2),
        "mean_funding_bp": round(statistics.fmean(e["funding"] for e in ev) * 1e4, 2),
        "mean_net_bp": round(statistics.fmean(net) * 1e4, 2),
        "median_net_bp": round(statistics.median(net) * 1e4, 2),
        "net_pct_bp": {q: round(pct(net, q / 100) * 1e4, 1) for q in (1, 5, 25, 50, 75, 95, 99)},
        "win_pct": round(sum(1 for x in net if x > 0) / len(net) * 100, 1),
        "sum_net_pct": round(sum(net) * 100, 2),
        "top5pct_sum_net_pct": round(sum(top) * 100, 2),
        "net_without_top5pct_mean_bp": round(
            (sum(net) - sum(top)) / max(1, len(net) - len(top)) * 1e4, 2
        ),
    }


def day_index(t: int) -> int:
    return (t - EVAL_START) // DAY


N_DAYS = (END - EVAL_START) // DAY


def boot_idx(block: int, reps: int = 2000, seed: int = 216) -> list[list[int]]:
    """날짜 블록(원형) 표본 — 모든 종목 · 전략에 같은 날짜 묶음을 쓴다."""
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        idx: list[int] = []
        while len(idx) < N_DAYS:
            st = rng.randrange(N_DAYS)
            idx.extend((st + k) % N_DAYS for k in range(block))
        out.append(idx[:N_DAYS])
    return out


def daily_agg(ev: list[dict[str, Any]]) -> tuple[list[float], list[int]]:
    s, c = [0.0] * N_DAYS, [0] * N_DAYS
    for e in ev:
        d = day_index(e["t"])
        s[d] += e["net"]
        c[d] += 1
    return s, c


def boot_mean(agg: tuple[list[float], list[int]], idx: list[list[int]]) -> list[float]:
    s, c = agg
    out = []
    for ix in idx:
        n = sum(c[k] for k in ix)
        out.append(sum(s[k] for k in ix) / n if n else 0.0)
    return out


def ci(xs: list[float]) -> dict[str, Any]:
    ys = sorted(xs)
    return {
        "ci90_bp": [
            round(ys[int(0.05 * len(ys))] * 1e4, 2),
            round(ys[int(0.95 * len(ys))] * 1e4, 2),
        ],
        "boot_share_gt0": round(sum(1 for x in ys if x > 0) / len(ys), 3),
    }


def nonoverlap(ev: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """참고: 종목별 한 번에 하나(첫 유효 신호에 들어가 12 시간), 2 단계 계좌 규칙과 같은 순서."""
    busy: dict[str, int] = {}
    out = []
    for e in sorted(ev, key=lambda x: x["t"]):
        s = e["sym"]
        if e["t"] < busy.get(s, 0):
            continue
        out.append(e)
        busy[s] = e["t"] + e["delay"] * MIN + HOLD * MIN
    return out


# ------------------------------------------------------------------ 실행


def build() -> dict[str, Any]:
    data: dict[str, Any] = {}
    for s in SYMBOLS:
        have, vol, tb = kline_arrays(s)
        agg = load_agg(s)
        wi, why_w = window_imbalance(agg, have)
        sc = scores(wi, have, vol, tb)
        th = {k: thresholds(v) for k, v in sc.items()}
        sig = {}
        why_s = {}
        for k in STRATS:
            sig[k], why_s[k] = signals(sc[k], th[k])
        data[s] = {
            "agg": agg,
            "wi": wi,
            "why_w": why_w,
            "sc": sc,
            "th": th,
            "sig": sig,
            "why_s": why_s,
            "have": have,
            "vol": vol,
            "tb": tb,
        }
    return data


def audit(data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or build()
    res: dict[str, Any] = {}
    for s in SYMBOLS:
        d = data[s]
        agg = d["agg"]
        mo_list = months()
        cont_breaks = []
        prev = None
        for mo in mo_list:
            ex = agg.get(mo)
            if ex is None:
                continue
            if prev is not None and ex["first_id"] != prev + 1:
                cont_breaks.append((mo, ex["first_id"] - prev - 1))
            prev = ex["last_id"]
        gaps = [(mo, g) for mo, ex in agg.items() for g in ex["gaps"]]
        # 매시 00 분 전체 체결 vs 공식 1 분봉
        rel_v, rel_b, day_v = [], [], {}
        for ex in agg.values():
            for mms, (v, b, _n) in ex["check_min"].items():
                if not (WARM <= mms < END):
                    continue
                i = (mms - WARM) // MIN
                if not d["have"][i] or d["vol"][i] <= 0:
                    continue
                rel_v.append((v - d["vol"][i]) / d["vol"][i])
                if d["tb"][i] > 0:
                    rel_b.append((b - d["tb"][i]) / d["tb"][i])
                day_v.setdefault(mms // DAY, [0.0, 0.0])
                day_v[mms // DAY][0] += v
                day_v[mms // DAY][1] += d["vol"][i]
        ra = sorted(abs(x) for x in rel_v)
        rb = sorted(abs(x) for x in rel_b)
        tot_a = sum(v[0] for v in day_v.values())
        tot_k = sum(v[1] for v in day_v.values())
        sc = d["sc"]
        ev_scores = {k: [v for t, v in sc[k].items() if EVAL_START <= t < END] for k in STRATS}
        res[s] = {
            "months": len(agg),
            "missing_months": [mo for mo in mo_list if mo not in agg],
            "rows": sum(ex["rows"] for ex in agg.values()),
            "header_months": sum(1 for ex in agg.values() if ex["header"]),
            "ts_unit": dict(Counter(ex["ts_unit"] for ex in agg.values())),
            "nonmono": sum(ex["nonmono"] for ex in agg.values()),
            "month_id_breaks": cont_breaks,
            "id_gaps": len(gaps),
            "id_gap_examples": [
                (
                    mo,
                    datetime.fromtimestamp(g[0] / 1000, UTC).isoformat(),
                    datetime.fromtimestamp(g[1] / 1000, UTC).isoformat(),
                    g[2],
                )
                for mo, g in gaps[:10]
            ],
            "windows": dict(d["why_w"]),
            "check_minutes": len(rel_v),
            "check_rel_abs_vol_median_pct": round(ra[len(ra) // 2] * 100, 4) if ra else None,
            "check_rel_abs_vol_p99_pct": round(ra[int(0.99 * len(ra))] * 100, 3) if ra else None,
            "check_rel_abs_takerbuy_median_pct": round(rb[len(rb) // 2] * 100, 4) if rb else None,
            "check_sum_rel_pct": round((tot_a - tot_k) / tot_k * 100, 5) if tot_k else None,
            "score_coverage": {
                k: {"boundaries": len(v), "valid": sum(1 for x in v if x is not None)}
                for k, v in ev_scores.items()
            },
            "score_dist": {
                k: {
                    q: round(pct(sorted(x for x in v if x is not None), q / 100), 4)
                    for q in (1, 10, 50, 90, 99)
                }
                for k, v in ev_scores.items()
            },
            "signals": {
                k: {
                    "long": sum(1 for _, x in d["sig"][k] if x > 0),
                    "short": sum(1 for _, x in d["sig"][k] if x < 0),
                    "skips": dict(d["why_s"][k]),
                }
                for k in STRATS
            },
        }
        # 신호 겹침 (손익 없음)
        sa = dict(d["sig"]["A"])
        sc_ = dict(d["sig"]["C"])
        both = sum(1 for t, x in sa.items() if sc_.get(t) == x)
        pa = [
            (sc["A"][t], sc["C"][t])
            for t in sc["A"]
            if EVAL_START <= t < END and sc["A"][t] is not None and sc["C"].get(t) is not None
        ]
        res[s]["A_C_same_signal"] = both
        res[s]["corr_SA_SC"] = round(
            statistics.correlation([a for a, _ in pa], [c for _, c in pa]), 3
        )
        pb = [
            (sc["A"][t], sc["B"].get(t + 5 * MIN))
            for t in sc["A"]
            if EVAL_START <= t < END
            and sc["A"][t] is not None
            and sc["B"].get(t + 5 * MIN) is not None
        ]
        res[s]["corr_SA_SB_next"] = round(
            statistics.correlation([a for a, _ in pb], [b for _, b in pb]), 3
        )  # type: ignore[misc]
        th = [v for v in d["th"]["A"].values() if v]
        res[s]["A_threshold_avg"] = [
            round(statistics.fmean(x[0] for x in th), 4),
            round(statistics.fmean(x[1] for x in th), 4),
        ]
    return res


def stage1() -> dict[str, Any]:
    data = build()
    mk = _load()
    res: dict[str, Any] = {"audit": audit(data)}
    ev: dict[tuple[str, int], list[dict[str, Any]]] = {}
    drops: Counter[str] = Counter()
    for s in SYMBOLS:
        m = mk[s]
        ft = [f[0] for f in m.funding]
        for k in STRATS:
            for delay in (1, 5):
                lst = ev.setdefault((k, delay), [])
                for t, side in data[s]["sig"][k]:
                    e = event(m, ft, t, side, delay)
                    if e is None:
                        drops[f"{k} 평가 끝 넘김"] += 1
                        continue
                    if e.get("drop"):
                        drops[f"{k} 진입 · 청산 분 1 분봉 없음"] += 1
                        continue
                    e["sym"], e["strat"], e["delay"] = s, k, delay
                    lst.append(e)
    res["drops"] = dict(drops)
    out: dict[str, Any] = {}
    for (k, delay), lst in ev.items():
        key = f"{k}_T+{delay * 60}s"
        g: dict[str, Any] = {}
        for side_name, sd in (("both", 0), ("long", 1), ("short", -1)):
            sub = [e for e in lst if sd == 0 or e["side"] == sd]
            g[side_name] = summ(sub)
            g[side_name]["by_symbol"] = {
                s: summ([e for e in sub if e["sym"] == s]) for s in SYMBOLS
            }
            g[side_name]["halves"] = [
                summ([e for e in sub if e["t"] < MID])["mean_net_bp"]
                if any(e["t"] < MID for e in sub)
                else None,
                summ([e for e in sub if e["t"] >= MID])["mean_net_bp"]
                if any(e["t"] >= MID for e in sub)
                else None,
            ]
            g[side_name]["paper_period_vs_outside_mean_net_bp"] = [
                summ([e for e in sub if PAPER[0] <= e["t"] < PAPER[1]]).get("mean_net_bp"),
                summ([e for e in sub if not (PAPER[0] <= e["t"] < PAPER[1])]).get("mean_net_bp"),
            ]
        g["nonoverlap_ref"] = summ(nonoverlap(lst))
        out[key] = g
    res["events"] = out
    # 블록 부트스트랩 (같은 날짜 묶음)
    boots: dict[str, Any] = {}
    for block in (14, 7, 30):
        idx = boot_idx(block)
        bm = {}
        for k in STRATS:
            for delay in (1, 5):
                bm[(k, delay)] = boot_mean(daily_agg(ev[(k, delay)]), idx)
        bl: dict[str, Any] = {}
        for k in STRATS:
            bl[f"{k}_mean"] = ci(bm[(k, 1)])
        bl["A-B"] = ci([a - b for a, b in zip(bm[("A", 1)], bm[("B", 1)], strict=True)])
        bl["A-C"] = ci([a - c for a, c in zip(bm[("A", 1)], bm[("C", 1)], strict=True)])
        bl["A_T+300s_mean"] = ci(bm[("A", 5)])
        boots[f"block{block}"] = bl
    res["bootstrap"] = boots
    a = out["A_T+60s"]["both"]
    res["diffs_mean_net_bp"] = {
        "A-B": round(a["mean_net_bp"] - out["B_T+60s"]["both"]["mean_net_bp"], 2),
        "A-C": round(a["mean_net_bp"] - out["C_T+60s"]["both"]["mean_net_bp"], 2),
        "A-B_T+300s": round(
            out["A_T+300s"]["both"]["mean_net_bp"] - out["B_T+300s"]["both"]["mean_net_bp"], 2
        ),
        "A-C_T+300s": round(
            out["A_T+300s"]["both"]["mean_net_bp"] - out["C_T+300s"]["both"]["mean_net_bp"], 2
        ),
    }
    passed = a["mean_net_bp"] > 0 and all(h is not None and h > 0 for h in a["halves"])
    res["expand_to_stage2"] = passed
    res["conclusion_stage1"] = (
        "확장 조건 충족 → 2 단계" if passed else "이번 규칙의 비용 문턱 미통과"
    )
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "stage1.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    for (k, delay), lst in ev.items():
        with gzip.open(
            REPORT / f"events_{k}_T+{delay * 60}s.csv.gz", "wt", newline="", encoding="utf-8"
        ) as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "decision_utc",
                    "symbol",
                    "side",
                    "entry_ref",
                    "exit_ref",
                    "gross",
                    "fee",
                    "funding",
                    "net",
                ]
            )
            for e in sorted(lst, key=lambda x: (x["t"], x["sym"])):
                w.writerow(
                    [
                        datetime.fromtimestamp(e["t"] / 1000, UTC).isoformat(),
                        e["sym"],
                        e["side"],
                        e["p0"],
                        e["p1"],
                        round(e["gross"], 7),
                        round(e["fee"], 7),
                        round(e["funding"], 7),
                        round(e["net"], 7),
                    ]
                )
    with gzip.open(REPORT / "scores.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["boundary_utc", "symbol", "strategy", "score", "p10", "p90"])
        for s in SYMBOLS:
            for k in STRATS:
                for t, v in sorted(data[s]["sc"][k].items()):
                    if EVAL_START <= t < END:
                        th = data[s]["th"][k].get(t // DAY * DAY)
                        w.writerow(
                            [
                                datetime.fromtimestamp(t / 1000, UTC).isoformat(),
                                s,
                                k,
                                "" if v is None else round(v, 6),
                                "" if not th else round(th[0], 6),
                                "" if not th else round(th[1], 6),
                            ]
                        )
    return res


__all__ = ["audit", "event", "pct", "scores", "signals", "stage1", "thresholds", "window_imbalance"]
