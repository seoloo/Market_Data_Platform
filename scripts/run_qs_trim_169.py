"""문서 169 — QS 보완: 지수가 1 년 고점 대비 내릴 때마다 QNDX(1 배)를 조금씩 팔아 TQQQ 씨앗으로.

사용 예::

    python -m scripts.run_qs_trim_169 count   # 판 횟수 · 씨앗이 TQQQ 로 간 횟수 (수익 없음)
    python -m scripts.run_qs_trim_169 run     # 1 회 판정 → reports/qs_trim_169/result.json

* 사전 등록: docs/research/169-qs-trim-seed.md.
* 110 의 QS 에 더하는 것: 전날 종가가 직전 252 거래일 최고 종가 대비 −k% 이하이면
  (아직 안 쓴 k 마다) 오늘 종가에 지수(QNDX 자리) 보유의 f 를 판다. 실현 이익은 그해 세금에 넣는다.
* 판 돈은 '씨앗'으로 따로 두었다가 QS 가 꺼짐 → 켜짐 되는 날 쌓인 현금과 함께 TQQQ 로 산다.
  그 전에 지수가 1 년 고점 −2% 안으로 돌아오면 씨앗으로 지수를 다시 사고 문턱을 모두 다시 쓴다.
* 판 6 개(문턱 3 × 비율 2)를 모두 시험한다. 고르지 않는다.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_qs_confirm import _windows
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import (
    CONTRIBUTION,
    FEE,
    TAX,
    TQQQ_FEE_YEAR,
    Result,
    leveraged,
    simulate,
    trend_signals,
)

REPO = Path(__file__).resolve().parent.parent
XMKT = REPO / "data" / "research" / "qs_bottom_xmkt"
OUT = REPO / "reports" / "qs_trim_169"
THRESHOLDS = {
    "10-20-30": (0.10, 0.20, 0.30),
    "15-25-35": (0.15, 0.25, 0.35),
    "5씩-30까지": (0.05, 0.10, 0.15, 0.20, 0.25, 0.30),
}
FRACTIONS = (0.05, 0.10)
RESET = 0.02
"""고점 −2% 안으로 돌아오면 씨앗을 지수로 되돌리고 문턱을 다시 쓴다(첫 문턱 5% 와 겹치지 않게)."""
TRAIN = ("1987-01", "1997-12")
TEST = ("2008-01", "2016-08")
GAIN = 1.03
FLOOR = 0.90
SHARE = 0.60
DD_SLACK = 0.05
PASS_VARIANTS = 4
XMKT_FROM = "1950-01"
XMKT_RATE = 3.0
XMKT_PASS_MARKETS = 8
XMKT_FILES = {
    "GSPC": "S&P 500",
    "N225": "닛케이 225",
    "FTSE": "FTSE 100",
    "GSPTSE": "캐나다 TSX",
    "GDAXI": "독일 DAX",
    "HSI": "홍콩 항셍",
    "FCHI": "프랑스 CAC 40",
    "AXJO": "호주 ASX 200",
    "BVSP": "브라질 보베스파",
    "KS11": "한국 KOSPI",
    "TWII": "대만 가권",
    "BSESN": "인도 센섹스",
}


def drawdowns(days: Sequence[str], idx: Mapping[str, float]) -> dict[str, float]:
    """날마다 직전 252 거래일(그날 포함) 최고 종가 대비 하락률 (0 ~ 1) (순수)."""
    from collections import deque

    out: dict[str, float] = {}
    q: deque[tuple[int, float]] = deque()
    for i, d in enumerate(days):
        v = idx[d]
        while q and q[-1][1] <= v:
            q.pop()
        q.append((i, v))
        while q[0][0] <= i - 252:
            q.popleft()
        out[d] = 1 - v / q[0][1]
    return out


def simulate_trim(
    days: Sequence[str],
    idx: Mapping[str, float],
    lev: Mapping[str, float],
    signal: Mapping[str, bool | None],
    rf: Mapping[str, float],
    dd: Mapping[str, float],
    *,
    start: str,
    end: str,
    thresholds: Sequence[float],
    frac: float,
) -> tuple[Result, dict[str, int]]:
    """110 QS + 하락 때 지수 일부 팔아 TQQQ 씨앗. ``thresholds`` 가 비면 QS 와 같다 (순수)."""
    span = [d for d in days if start <= d <= end]
    index = {d: i for i, d in enumerate(days)}
    res = Result()
    q_sh = q_cost = l_sh = cash = seed = basis = year_gain = units = peak_nav = 0.0
    fired: set[float] = set()
    stats = {"sells": 0, "seed_to_tqqq": 0, "seed_back": 0}
    prev_state: bool | None = None
    last_month = ""
    for n, d in enumerate(span):
        i = index[d]
        value_before = q_sh * idx[d] + l_sh * lev[d] + cash + seed
        if d[:7] != last_month:
            last_month = d[:7]
            nav = value_before / units if units > 0 else 1.0
            units += CONTRIBUTION / nav
            cash += CONTRIBUTION
            res.invested += CONTRIBUTION
            res.flows.append((d, -CONTRIBUTION))
        prev_d = days[i - 1] if i > 0 else d
        state = bool(signal.get(prev_d)) if i > 0 else False
        if prev_state is None:
            prev_state = state
        # 하락 문턱: 전날 하락률로 오늘 판다
        if thresholds and i > 0:
            ddv = dd[prev_d]
            if ddv <= RESET:
                if seed > 0:  # 씨앗을 못 쓰고 고점 근처로 돌아옴 → 지수 다시 사기
                    res.fees += seed * FEE
                    q_sh += seed * (1 - FEE) / idx[d]
                    q_cost += seed
                    seed = 0.0
                    stats["seed_back"] += 1
                fired.clear()
            else:
                for k in thresholds:
                    if ddv >= k and k not in fired and q_sh > 0:
                        fired.add(k)
                        sell = q_sh * frac
                        gross = sell * idx[d]
                        proceeds = gross * (1 - FEE)
                        cost_part = q_cost * frac
                        res.fees += gross * FEE
                        year_gain += proceeds - cost_part
                        q_sh -= sell
                        q_cost -= cost_part
                        seed += proceeds
                        res.trades += 1
                        stats["sells"] += 1
        if (
            state and not prev_state and (cash > 0 or seed > 0)
        ):  # 꺼짐 → 켜짐: 현금 + 씨앗 전부 TQQQ
            amt = cash + seed
            if seed > 0:
                stats["seed_to_tqqq"] += 1
            res.fees += amt * FEE
            l_sh += amt * (1 - FEE) / lev[d]
            basis += amt
            cash = seed = 0.0
            res.trades += 1
        elif not state and prev_state and l_sh > 0:
            gross = l_sh * lev[d]
            proceeds = gross * (1 - FEE)
            res.fees += gross * FEE
            year_gain += proceeds - basis
            res.round_trips.append(round((proceeds - basis) / basis, 4) if basis > 0 else 0.0)
            cash += proceeds
            l_sh = basis = 0.0
            res.trades += 1
        elif state and cash > 0:
            res.fees += cash * FEE
            q_sh += cash * (1 - FEE) / idx[d]
            q_cost += cash
            cash = 0.0
        prev_state = state
        if not state:
            res.off_days += 1
        last_of_year = n == len(span) - 1 or span[n + 1][:4] != d[:4]
        if last_of_year:
            if year_gain > 0:
                t = TAX * year_gain
                cash -= t
                res.tax_paid += t
                res.taxes_by_year[d[:4]] = round(t, 2)
            year_gain = 0.0
        value = q_sh * idx[d] + l_sh * lev[d] + cash + seed
        nav = value / units if units > 0 else 1.0
        peak_nav = max(peak_nav, nav)
        if peak_nav > 0:
            res.max_drawdown = max(res.max_drawdown, 1 - nav / peak_nav)
        res.days += 1
        res.final = value
    res.flows.append((span[-1], res.final))
    return res, stats


# ------------------------------------------------------------------ 실행


def _nasdaq() -> tuple[list[str], dict[str, float], dict[str, float]]:
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate = _series("DTB3_full.csv", "DTB3")
    days = sorted(d for d in ndx if d <= "2026-08-31")
    rf: dict[str, float] = {}
    last = 0.0
    for d in sorted(set(days) | set(rate)):
        last = rate.get(d, last)
        rf[d] = last
    return days, ndx, rf


def _market(code: str) -> tuple[list[str], dict[str, float], dict[str, float]]:
    with (XMKT / f"{code}.csv").open(encoding="utf-8") as f:
        px = {
            r["date"]: float(r["close"])
            for r in csv.DictReader(f)
            if r["date"] <= "2026-08-31" and float(r["close"]) > 0
        }
    days = sorted(px)
    return days, px, dict.fromkeys(days, XMKT_RATE)


def _variants() -> list[tuple[str, tuple[float, ...], float]]:
    return [(f"{t}·{int(f * 100)}%", th, f) for t, th in THRESHOLDS.items() for f in FRACTIONS]


def _evaluate(
    days: Sequence[str],
    idx: Mapping[str, float],
    rf: Mapping[str, float],
    wins: Sequence[tuple[str, str]],
) -> dict[str, dict[str, float]]:
    sig = trend_signals(days, idx)
    lev = leveraged(days, idx, rf, 3.0, TQQQ_FEE_YEAR)
    dd = drawdowns(days, idx)
    base = [simulate(days, idx, lev, sig, rf, mode="QS", start=s, end=e) for s, e in wins]
    out = {"QS": {"dd_median": round(statistics.median(b.max_drawdown for b in base), 4)}}
    for name, th, f in _variants():
        rel, dds = [], []
        for (s, e), b in zip(wins, base, strict=True):
            r, _ = simulate_trim(days, idx, lev, sig, rf, dd, start=s, end=e, thresholds=th, frac=f)
            rel.append(r.final / b.final)
            dds.append(r.max_drawdown)
        out[name] = {
            "vs_QS_median": round(statistics.median(rel), 4),
            "vs_QS_min": round(min(rel), 4),
            "beats_QS_share": round(sum(v > 1 for v in rel) / len(rel), 4),
            "dd_median": round(statistics.median(dds), 4),
        }
    return out


def count() -> dict[str, Any]:
    days, ndx, rf = _nasdaq()
    sig = trend_signals(days, ndx)
    lev = leveraged(days, ndx, rf, 3.0, TQQQ_FEE_YEAR)
    dd = drawdowns(days, ndx)
    out = {}
    for name, th, f in _variants():
        _r, st = simulate_trim(
            days, ndx, lev, sig, rf, dd, start="1987-01-01", end="2026-08-31", thresholds=th, frac=f
        )
        out[name] = st
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    days, ndx, rf = _nasdaq()
    train = _evaluate(days, ndx, rf, _windows(days, *TRAIN))
    test = _evaluate(days, ndx, rf, _windows(days, *TEST))
    per: dict[str, Any] = {}
    for name, _th, _f in _variants():
        tr, te = train[name], test[name]
        gate = tr["vs_QS_median"] >= GAIN and tr["vs_QS_min"] >= FLOOR
        t1 = te["vs_QS_median"] >= GAIN
        t2 = te["beats_QS_share"] >= SHARE
        t3 = te["dd_median"] <= test["QS"]["dd_median"] + DD_SLACK
        per[name] = {
            "train": tr,
            "test": te,
            "gate": gate,
            "T1": t1,
            "T2": t2,
            "T3": t3,
            "pass": gate and t1 and t2 and t3,
        }
    nas_pass = sum(1 for v in per.values() if v["pass"])
    # 다른 지수 12 개, 1950 년 이후 시작 10 년 창 (석 달마다 시작)
    xm: dict[str, Any] = {}
    for code, label in XMKT_FILES.items():
        mdays, px, mrf = _market(code)
        first = max(XMKT_FROM, mdays[min(len(mdays) - 1, 400)][:7])
        wins = [w for k, w in enumerate(_windows(mdays, first, "2016-08")) if k % 3 == 0]
        if len(wins) < 4:
            continue
        ev = _evaluate(mdays, px, mrf, wins)
        xm[label] = {"windows": len(wins), **ev}
    xm_pass = {
        name: sum(1 for v in xm.values() if v[name]["vs_QS_median"] > 1.0)
        for name, _t, _f in _variants()
    }
    xm_ok = sum(1 for c in xm_pass.values() if c >= XMKT_PASS_MARKETS)
    verdict = (
        "HISTORICAL_SUPPORT" if nas_pass >= PASS_VARIANTS and xm_ok >= PASS_VARIANTS else "REJECT"
    )
    result = {
        "nasdaq": {
            "QS_train_dd": train["QS"]["dd_median"],
            "QS_test_dd": test["QS"]["dd_median"],
            "variants": per,
            "passed": nas_pass,
        },
        "xmkt": {"markets": xm, "markets_beating_QS_by_variant": xm_pass, "variants_ok": xm_ok},
        "verdict": verdict,
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    if mode not in ("count", "run"):
        raise SystemExit("usage: python -m scripts.run_qs_trim_169 count|run")
    res = count() if mode == "count" else run()
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
