"""164 — 그때 '주요 테마' 5 개(테마 ETF 거래대금 상위)만 사기 vs 지수 (미장 · 국장).

사용 예::

    python -m scripts.run_theme_top5_164 fetch     # Yahoo 일봉 거래량 받기 (163 자료에 없는 열)
    python -m scripts.run_theme_top5_164 count     # 테마 순위 · 고른 테마 (수익 없음)
    python -m scripts.run_theme_top5_164 verdict   # 1 회 판정

* 사전 등록: docs/research/164-theme-top5-etf.md. 테마 · ETF 는 163 W 목록 그대로(해시 고정).
* 테마 크기: 테마별 시가총액 이력은 무료로 없다(ETF 구성 이력 없음, KRX 일별 파일에 ETF 없음).
  그래서 **그때 알 수 있던 테마 ETF 거래대금**(적격 ETF 의 직전 126 거래일 평균 종가 × 거래량 합)을
  쓴다. 크기라기보다 '돈이 가장 많이 몰린 테마'다.
* 매달 마지막 거래일: 적격 ETF(163 과 같음)가 있는 테마를 거래대금으로 줄 세워 상위 5 테마.
  테마마다 1/5, 테마 안 적격 ETF 고르게. 다음 거래일 종가 체결, 매달 다시 맞춤, 편도 0.10%.
* 판정은 163 과 같다(월 초과 − 2.24 SE > 0 · 두 반쪽 > 0 · 5 년 창 과반 승).
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.research.theme_leaders import nw_se, rolling_horizon  # noqa: E402
from scripts import run_theme_etf_163 as e163  # noqa: E402

VOL = REPO_ROOT / "data" / "research" / "theme_etf_volume"
OUT = REPO_ROOT / "reports" / "theme_top5_164"
TOP = 5
WINDOW = 126
EXPECTED_PICKS_SHA = "ef2bf3abe21d29c03b909a035b40b801eeb607267b95fe0bd47db6881d809a72"
"""count 뒤 고정: 달마다 고른 테마 목록의 해시."""


def fetch() -> None:
    VOL.mkdir(parents=True, exist_ok=True)
    spec = e163._spec()
    syms = sorted(
        {e for v in spec["W"]["US"].values() for e in v}
        | {e for v in spec["W"]["KR"].values() for e in v}
    )
    for s in syms:
        p = VOL / f"{s}.csv"
        if p.exists():
            continue
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=946684800&period2=1791331200&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": e163.UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close", "volume"])
            for t, c, v in zip(res.get("timestamp", []), q["close"], q["volume"], strict=True):
                if c and v is not None:
                    w.writerow([datetime.fromtimestamp(t, UTC).strftime("%Y%m%d"), c, v])
        print(s, flush=True)
        time.sleep(0.3)


def top_themes(value: Mapping[str, float], n: int = TOP) -> list[str]:
    """테마 → 거래대금. 큰 순서 n 개, 동점은 이름 순 (순수)."""
    return sorted(value, key=lambda t: (-value[t], t))[:n]


def _dollar_volume() -> dict[str, tuple[list[str], list[float]]]:
    out = {}
    for p in sorted(VOL.glob("*.csv")):
        with p.open() as f:
            rows = [(r["date"], float(r["close"]) * float(r["volume"])) for r in csv.DictReader(f)]
        out[p.stem] = ([d for d, _ in rows], [v for _, v in rows])
    return out


def run(market: str, spec: Mapping[str, Any], *, count_only: bool = False) -> dict[str, Any]:
    themes: Mapping[str, Sequence[str]] = spec["W"][market]
    syms = sorted({e for v in themes.values() for e in v})
    px = {s: e163._load(s) for s in syms}
    dv = _dollar_volume()
    bpx = e163._load(spec["benchmark"][market])
    cal = [d for d in sorted(bpx) if d <= e163.END]
    pos = {d: i for i, d in enumerate(cal)}
    dates = {s: sorted(p) for s, p in px.items()}

    def last(s: str, d: str) -> float | None:
        ds = dates[s]
        k = bisect.bisect_right(ds, d) - 1
        return px[s][ds[k]] if k >= 0 else None

    def eligible(s: str, t: str) -> bool:
        ds = dates[s]
        k = bisect.bisect_right(ds, t) - 1
        return k >= e163.MIN_HISTORY and pos.get(ds[k], -99) >= pos[t] - 5

    def avg_value(s: str, t: str) -> float:
        ds, vs = dv[s]
        k = bisect.bisect_right(ds, t)
        win = vs[max(0, k - WINDOW) : k]
        return statistics.fmean(win) if win else 0.0

    ends = [cal[i] for i in range(len(cal) - 1) if cal[i][:6] != cal[i + 1][:6]]
    sched: list[tuple[str, dict[str, float], str, list[str]]] = []
    for t in ends:
        el = {th: [e for e in etfs if eligible(e, t)] for th, etfs in themes.items()}
        el = {th: v for th, v in el.items() if v}
        if len(el) < TOP:
            continue
        value = {th: sum(avg_value(e, t) for e in v) for th, v in el.items()}
        picks = top_themes(value)
        w: dict[str, float] = defaultdict(float)
        for th in picks:
            for e in el[th]:
                w[e] += 1 / TOP / len(el[th])
        sched.append((cal[pos[t] + 1], dict(w), t, picks))
    picks_blob = json.dumps([[t, p] for _, _, t, p in sched], ensure_ascii=False).encode("utf-8")
    held = Counter(th for *_, p in sched for th in p)
    info = {
        "first_exec": sched[0][0],
        "months": len(sched),
        "picks_sha": hashlib.sha256(picks_blob).hexdigest(),
        "theme_months": held.most_common(),
        "picks_by_year": {t[:4]: p for _, _, t, p in sched if t[4:6] == "12"},
        "picks_last": sched[-1][3],
    }
    if count_only:
        return info
    days, rb, ri, mb, mi = [], [], [], [], []
    hold: dict[str, float] = {}
    for k, (exd, target, _t, _p) in enumerate(sched):
        tot = sum(hold.values()) or 1.0
        cur = {c: v / tot for c, v in hold.items()}
        turn = (
            1.0
            if k == 0
            else sum(abs(target.get(c, 0) - cur.get(c, 0)) for c in set(target) | set(cur))
        )
        nxt = sched[k + 1][0] if k + 1 < len(sched) else cal[-1]
        hold = dict(target)
        m_b, m_i, first = 1.0 - e163.COST * turn, 1.0, True
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
                r = (1 + r) * (1 - e163.COST * turn) - 1
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
    hz = {h: rolling_horizon(mb, mi, n) for h, n in e163.HORIZONS.items()}
    t1 = m - e163.Z * se > 0
    t2 = statistics.fmean(ex[:half]) > 0 and statistics.fmean(ex[half:]) > 0
    t3 = bool(hz["5y"] and hz["5y"]["a_wins"] > 0.5)
    info.update(
        {
            "verdict": "HISTORICAL_SUPPORT" if t1 and t2 and t3 else "REJECT",
            "T1": t1,
            "T2": t2,
            "T3": t3,
            "excess_mean_month": round(m, 5),
            "se": round(se, 5),
            "lower": round(m - e163.Z * se, 5),
            "halves": [
                round(statistics.fmean(ex[:half]), 5),
                round(statistics.fmean(ex[half:]), 5),
            ],
            "basket": e163._curve(rb),
            "index": e163._curve(ri),
            "dca_irr": [e163._dca_irr(mb), e163._dca_irr(mi)],
            "horizons": hz,
            "by_year": {y: [round(yb[y] - 1, 3), round(yi[y] - 1, 3)] for y in sorted(yb)},
            "years_won": f"{sum(1 for y in yb if yb[y] > yi[y])}/{len(yb)}",
        }
    )
    return info


def main(argv: list[str] | None = None) -> int:
    mode = (argv if argv is not None else sys.argv[1:])[0]
    if mode == "fetch":
        fetch()
        return 0
    spec = e163._spec()
    out = {mk: run(mk, spec, count_only=mode == "count") for mk in ("US", "KR")}
    if mode == "verdict":
        blob = json.dumps({mk: out[mk]["picks_sha"] for mk in out}, sort_keys=True)
        if EXPECTED_PICKS_SHA and hashlib.sha256(blob.encode()).hexdigest() != EXPECTED_PICKS_SHA:
            raise SystemExit("고른 테마 해시 불일치")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{mode}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for k, v in out.items():
        brief = {
            kk: v[kk]
            for kk in v
            if kk not in ("horizons", "by_year", "picks_by_year", "theme_months")
        }
        sys.stdout.write(f"{k} {json.dumps(brief, ensure_ascii=False)}\n")
    if mode == "count":
        blob = json.dumps({mk: out[mk]["picks_sha"] for mk in out}, sort_keys=True)
        sys.stdout.write(f"picks_blob_sha {hashlib.sha256(blob.encode()).hexdigest()}\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
