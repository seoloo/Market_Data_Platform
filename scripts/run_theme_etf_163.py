"""163 — 테마 ETF 고르게 들기 vs 지수 (미장 SPY · 국장 KODEX 200).

사용 예::

    python -m scripts.run_theme_etf_163 fetch     # Yahoo 일봉(배당 조정 종가) 받기
    python -m scripts.run_theme_etf_163 count     # 테마 · ETF 가용 개월 수 (수익 없음)
    python -m scripts.run_theme_etf_163 verdict   # 1 회 판정

* 사전 등록: docs/research/163-theme-etf-vs-index.md. 목록 ``docs/research/theme-etf-163.json``.
* 162 에서 종목을 지금 골라 생긴 사후 선택을 없애려고, **그 당시 운용사가 구성한 테마 ETF** 를 쓴다.
  테마 이름을 지금 고른 효과는 남는다 → U(사용자 테마)와 W(유행이 지난 테마까지 넓게)를 나란히 본다.
* 매달 마지막 거래일 신호 → 다음 거래일 종가 체결, 매달 다시 맞춤. 적격 ETF = 그날까지
  배당 조정 종가 126 개 이상 · 최근 5 거래일 안 거래. U 는 테마마다 우선순위 첫 적격 ETF 하나,
  W 는 테마 안 적격 ETF 고르게. 테마끼리 고르게. 활성 테마 3 개 이상인 달부터.
* 비용 편도 0.10%(ETF), 지수는 사서 들고 있기(비용 0).
* 판정(트랙 · 시장마다): 월 초과(바스켓 − 지수) 평균 − 2.24 · SE(NW 3) > 0, 두 반쪽 > 0,
  5 년 보유 창의 절반 넘게 바스켓 승. 셋 다 → HISTORICAL_SUPPORT.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.research.theme_leaders import nw_se, rolling_horizon  # noqa: E402

SPEC = REPO_ROOT / "docs" / "research" / "theme-etf-163.json"
SPEC_SHA = "72a93f3a1f5cacafa5596830ac73b7ffc8b86193372f801b078d6127aed2a66a"
"""count 뒤 고정(줄 끝 LF 기준)."""
DATA = REPO_ROOT / "data" / "research" / "theme_etf"
OUT = REPO_ROOT / "reports" / "theme_etf_163"
COST = 0.0010
Z = 2.24
MIN_HISTORY = 126
END = "20260831"
HORIZONS = {"1y": 12, "3y": 36, "5y": 60, "10y": 120}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def _spec() -> dict[str, Any]:
    raw = SPEC.read_bytes().replace(b"\r\n", b"\n")
    if SPEC_SHA and hashlib.sha256(raw).hexdigest() != SPEC_SHA:
        raise SystemExit("목록 해시 불일치")
    return dict(json.loads(raw.decode("utf-8")))


def _symbols(spec: Mapping[str, Any]) -> list[str]:
    out = set(spec["benchmark"].values())
    for track in ("U", "W"):
        for mk in ("US", "KR"):
            for etfs in spec[track][mk].values():
                out.update(etfs)
    return sorted(out)


def fetch() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for s in _symbols(_spec()):
        p = DATA / f"{s}.csv"
        if p.exists():
            continue
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=946684800"
            "&period2=1791331200&interval=1d&events=div,splits"
        )
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())["chart"]["result"][0]
        ts = res.get("timestamp", [])
        q = res["indicators"]["quote"][0]["close"]
        adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose", q)
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close", "adjclose"])
            for t, c, a in zip(ts, q, adj, strict=True):
                if c and a:
                    w.writerow([datetime.fromtimestamp(t, UTC).strftime("%Y%m%d"), c, a])
        print(s, len(ts), flush=True)
        time.sleep(0.3)


def _load(s: str) -> dict[str, float]:
    with (DATA / f"{s}.csv").open() as f:
        return {
            r["date"]: float(r["adjclose"]) for r in csv.DictReader(f) if float(r["adjclose"]) > 0
        }


def basket_weights(
    themes: Mapping[str, Sequence[str]], eligible: Mapping[str, bool], track: str
) -> dict[str, float]:
    """활성 테마 고르게 · U 는 테마마다 첫 적격 ETF, W 는 테마 안 적격 ETF 고르게 (순수).

    활성 테마가 3 개 미만이면 빈 dict."""
    picks: dict[str, list[str]] = {}
    for th, etfs in themes.items():
        ok = [e for e in etfs if eligible.get(e, False)]
        if ok:
            picks[th] = ok[:1] if track == "U" else ok
    if len(picks) < 3:
        return {}
    w: dict[str, float] = defaultdict(float)
    for etfs in picks.values():
        for e in etfs:
            w[e] += 1 / len(picks) / len(etfs)
    return dict(w)


def _curve(daily: Sequence[float]) -> dict[str, float]:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in daily:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    yrs = len(daily) / 252
    sd = statistics.pstdev(daily) * math.sqrt(252)
    return {
        "cagr": round(eq ** (1 / yrs) - 1, 4),
        "sharpe": round(statistics.fmean(daily) * 252 / sd, 2) if sd else 0.0,
        "mdd": round(mdd, 4),
    }


def _dca_irr(monthly: Sequence[float]) -> float:
    v = 0.0
    for r in monthly:
        v = (v + 1) * (1 + r)
    n, lo, hi = len(monthly), -0.05, 0.1
    for _ in range(100):
        mid = (lo + hi) / 2
        fv = sum((1 + mid) ** (n - k) for k in range(n))
        lo, hi = (mid, hi) if fv < v else (lo, mid)
    return round((1 + mid) ** 12 - 1, 4)


def run(
    track: str, market: str, spec: Mapping[str, Any], *, count_only: bool = False
) -> dict[str, Any]:
    themes = spec[track][market]
    bench = spec["benchmark"][market]
    syms = sorted({e for v in themes.values() for e in v})
    px = {s: _load(s) for s in syms}
    bpx = _load(bench)
    cal = [d for d in sorted(bpx) if d <= END]
    pos = {d: i for i, d in enumerate(cal)}
    dates = {s: sorted(p) for s, p in px.items()}

    def last(s: str, d: str) -> float | None:
        ds = dates[s]
        k = bisect.bisect_right(ds, d) - 1
        return px[s][ds[k]] if k >= 0 else None

    def eligible(s: str, t: str) -> bool:
        ds = dates[s]
        k = bisect.bisect_right(ds, t) - 1
        return k >= MIN_HISTORY and pos.get(ds[k], -99) >= pos[t] - 5

    ends = [cal[i] for i in range(len(cal) - 1) if cal[i][:6] != cal[i + 1][:6]]
    sched = []
    for t in ends:
        w = basket_weights(themes, {s: eligible(s, t) for s in syms}, track)
        if w:
            sched.append((cal[pos[t] + 1], w, t))
    if count_only:
        n_active = [
            sum(1 for th, v in themes.items() if any(e in w for e in v)) for _, w, _ in sched
        ]
        return {
            "first_exec": sched[0][0] if sched else None,
            "months": len(sched),
            "themes_active_min_med_max": [
                min(n_active),
                int(statistics.median(n_active)),
                max(n_active),
            ]
            if n_active
            else None,
            "etfs": {s: [dates[s][0], len(dates[s])] for s in syms},
        }
    days, rb, ri, mb, mi = [], [], [], [], []
    hold: dict[str, float] = {}
    for k, (exd, target, _t) in enumerate(sched):
        tot = sum(hold.values()) or 1.0
        cur = {c: v / tot for c, v in hold.items()}
        turn = (
            1.0
            if k == 0
            else sum(abs(target.get(c, 0) - cur.get(c, 0)) for c in set(target) | set(cur))
        )
        nxt = sched[k + 1][0] if k + 1 < len(sched) else cal[-1]
        hold = dict(target)
        m_b, m_i, first = 1.0 - COST * turn, 1.0, True
        for i in range(pos[exd] + 1, pos[nxt] + 1):
            d0, d1 = cal[i - 1], cal[i]
            v0 = sum(hold.values())
            new = {}
            for c, v in hold.items():
                p0, p1 = last(c, d0), last(c, d1)
                new[c] = v * (p1 / p0) if p0 and p1 else v
            v1 = sum(new.values())
            r = v1 / v0 - 1
            if first:
                r = (1 + r) * (1 - COST * turn) - 1
                first = False
            rbi = bpx[d1] / bpx[d0] - 1
            days.append(d1)
            rb.append(r)
            ri.append(rbi)
            hold = new
            m_b *= v1 / v0
            m_i *= 1 + rbi
        mb.append(m_b - 1)
        mi.append(m_i - 1)
    ex = [a - b for a, b in zip(mb, mi, strict=True)]
    half = len(ex) // 2
    m, se = statistics.fmean(ex), nw_se(ex)
    yb: dict[str, float] = defaultdict(lambda: 1.0)
    yi: dict[str, float] = defaultdict(lambda: 1.0)
    for d, a, b in zip(days, rb, ri, strict=True):
        yb[d[:4]] *= 1 + a
        yi[d[:4]] *= 1 + b
    hz = {h: rolling_horizon(mb, mi, n) for h, n in HORIZONS.items()}
    t1, t2 = m - Z * se > 0, statistics.fmean(ex[:half]) > 0 and statistics.fmean(ex[half:]) > 0
    t3 = bool(hz["5y"] and hz["5y"]["a_wins"] > 0.5)
    return {
        "verdict": "HISTORICAL_SUPPORT" if t1 and t2 and t3 else "REJECT",
        "T1": t1,
        "T2": t2,
        "T3": t3,
        "period": [sched[0][0], cal[-1]],
        "months": len(ex),
        "excess_mean_month": round(m, 5),
        "se": round(se, 5),
        "lower": round(m - Z * se, 5),
        "halves": [round(statistics.fmean(ex[:half]), 5), round(statistics.fmean(ex[half:]), 5)],
        "split": sched[half][0],
        "basket": _curve(rb),
        "index": _curve(ri),
        "dca_irr": [_dca_irr(mb), _dca_irr(mi)],
        "horizons": hz,
        "by_year": {y: [round(yb[y] - 1, 3), round(yi[y] - 1, 3)] for y in sorted(yb)},
        "years_won": f"{sum(1 for y in yb if yb[y] > yi[y])}/{len(yb)}",
    }


def main(argv: list[str] | None = None) -> int:
    mode = (argv if argv is not None else sys.argv[1:])[0]
    if mode == "fetch":
        fetch()
        return 0
    spec = _spec()
    out = {
        f"{tr}-{mk}": run(tr, mk, spec, count_only=mode == "count")
        for tr in ("U", "W")
        for mk in ("US", "KR")
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{mode}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for k, v in out.items():
        brief = {kk: v[kk] for kk in v if kk not in ("etfs", "horizons", "by_year")}
        sys.stdout.write(f"{k} {json.dumps(brief, ensure_ascii=False)}\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
