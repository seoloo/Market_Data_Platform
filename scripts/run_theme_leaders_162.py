"""162 — 테마 주도주만 들고 1 등이 바뀌면 갈아타기 vs 같은 테마 고르게 들기 (국장 · 미장).

사용 예::

    python -m scripts.run_theme_leaders_162 momentum   # A: 1 · 3 · 6 달 수익 순위 (10-08 판정 재현)
    python -m scripts.run_theme_leaders_162 mcap       # B: 시가총액 1 등 (1 회 판정)

* 사전 등록: docs/research/162-theme-leader-rotation.md. 테마 · 종목은
  ``docs/research/theme-leaders-162.json`` (2026-10-08 고정, 해시 ``SPEC_SHA``).
* 신호: 매달 마지막 거래일 t. 적격 = t 에 종가가 있고(최근 5 거래일 안 거래), 종가 127 개 이상,
  국장은 직전 130 거래일에 가격제한폭 밖 하루 수익이 없을 것. 테마 활성 = 적격 3 개 이상.
  B 는 그날 시가총액도 있어야 적격이다(국장 KRX ``MKTCAP``, 미장 SEC 주식 수 × 종가).
* LEADER: 활성 테마마다 1/N, 그 테마 1 등 하나. BASKET: 활성 테마마다 1/N, 적격 종목 고르게.
  다음 거래일 종가 체결, 매달 목표 비중으로 다시 맞춘다. 비용 편도 국장 0.25% · 미장 0.10%.
* 판정(시장마다): 월 초과 평균 − 2.24 · SE(Newey-West 3) > 0, 두 반쪽 평균 > 0.
  B 는 여기에 **5 년(60 달) 보유 창의 절반 넘게 LEADER 가 이김**을 더한다
  (사용자: 1 년 ~ 20 년으로 봐야 한다).
* 보유 기간 표(1 · 3 · 5 · 10 년 · 전체): 매달 시작한 창마다 LEADER ÷ BASKET − 1.
  겹친 창이라 기술 통계.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.research.theme_leaders import (  # noqa: E402
    leader_mcap,
    leader_momentum,
    nw_se,
    rolling_horizon,
    us_mktcap,
)

R = REPO_ROOT / "data" / "research"
SPEC = REPO_ROOT / "docs" / "research" / "theme-leaders-162.json"
SPEC_SHA = "ff37583bdd4563706e095be30561ebdb9907d6ec1b1d43c9e84053ebb17db4dd"
Z = 2.24
COST = {"KR": 0.0025, "US": 0.0010}
START = {
    ("momentum", "KR"): "20100101",
    ("momentum", "US"): "20080101",
    ("mcap", "KR"): "20150101",  # 유가증권시장 KRX 시가총액 파일이 2015-01 부터
    ("mcap", "US"): "20100101",  # SEC XBRL 주식 수가 대체로 2009 ~ 2011 부터
}
END = "20260831"
HORIZONS = {"1y": 12, "3y": 36, "5y": 60, "10y": 120}


def _load_kr(code: str, folder: str) -> dict[str, float]:
    with (R / folder / "prices" / f"{code}.csv").open() as f:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(f) if float(r["close"]) > 0}


def _load_us(sym: str, folder: str) -> dict[str, float]:
    with (R / folder / f"{sym}.csv").open() as f:
        return {
            r["date"].replace("-", ""): float(r["close"])
            for r in csv.DictReader(f)
            if float(r["close"]) > 0
        }


def _calendar(market: str) -> dict[str, float]:
    if market == "KR":
        with (R / "kr_stocks" / "kospi.csv").open() as f:
            return {r["date"]: float(r["close"]) for r in csv.DictReader(f)}
    with (R / "tradfi" / "SPY_adj.csv").open() as f:
        return {r["date"].replace("-", ""): float(r["close"]) for r in csv.DictReader(f)}


def _curve(daily: list[float]) -> dict[str, float]:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in daily:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    yrs = len(daily) / 252
    sd = statistics.pstdev(daily) * math.sqrt(252) if len(daily) > 1 else 0.0
    mu = statistics.fmean(daily) * 252 if daily else 0.0
    return {
        "cagr": round(eq ** (1 / yrs) - 1, 4) if yrs > 0 else 0.0,
        "sharpe": round(mu / sd, 2) if sd else 0.0,
        "mdd": round(mdd, 4),
        "final": round(eq, 3),
    }


class CapBook:
    """B 용 그날 시가총액. 국장은 KRX 월말 값, 미장은 SEC 주식 수 × Yahoo 분할 조정 종가."""

    def __init__(self, market: str) -> None:
        self.market = market
        self.kr: dict[str, dict[str, float]] = defaultdict(dict)
        self.us: dict[str, dict[str, Any]] = {}
        self.us_dates: dict[str, list[str]] = {}
        self.backfilled = Counter[str]()
        if market == "KR":
            with (R / "theme_leaders" / "kr_mktcap.csv").open() as f:
                for r in csv.DictReader(f):
                    self.kr[r["code"]][r["date"]] = float(r["mktcap"])
            self.kr_dates = {c: sorted(v) for c, v in self.kr.items()}
        else:
            for p in sorted((R / "theme_leaders" / "us").glob("*.json")):
                d = json.loads(p.read_text(encoding="utf-8"))
                sh = d.get("dei:EntityCommonStockSharesOutstanding") or d.get(
                    "us-gaap:CommonStockSharesOutstanding", []
                )
                closes = {
                    datetime.fromtimestamp(ts, UTC).strftime("%Y%m%d"): c
                    for ts, c in d.get("close_split_adj", [])
                }
                self.us[d["symbol"]] = {
                    "shares": [(e, f, float(v)) for e, f, v in sh],
                    "splits": [
                        (datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d"), r)
                        for ts, r in d.get("splits", [])
                    ],
                    "close": closes,
                }
                self.us_dates[d["symbol"]] = sorted(closes)

    def cap(self, c: str, t: str, cal_pos: Mapping[str, int]) -> tuple[float, bool] | None:
        if self.market == "KR":
            ds = self.kr_dates.get(c, [])
            k = bisect.bisect_right(ds, t) - 1
            if k < 0 or cal_pos.get(ds[k], -99) < cal_pos[t] - 5:
                return None
            return self.kr[c][ds[k]], False
        u = self.us.get(c)
        if u is None:
            return None
        ds = self.us_dates[c]
        k = bisect.bisect_right(ds, t) - 1
        if k < 0 or cal_pos.get(ds[k], -99) < cal_pos[t] - 5:
            return None
        tt = f"{t[:4]}-{t[4:6]}-{t[6:]}"
        v = us_mktcap(u["shares"], u["splits"], u["close"][ds[k]], tt)
        return (v.value, v.backfilled) if v else None


def run(rule: str, market: str, themes: Mapping[str, list[list[str]]]) -> dict[str, Any]:
    idx = _calendar(market)
    cal = [d for d in sorted(idx) if START[(rule, market)] <= d <= END]
    loader = _load_kr if market == "KR" else _load_us
    px: dict[str, dict[str, float]] = {}
    for members in themes.values():
        for code, _name, folder in members:
            if code not in px:
                px[code] = loader(code, folder)
    names = {c: n for ms in themes.values() for c, n, _ in ms}
    dates = {c: sorted(p) for c, p in px.items()}
    pos = {d: i for i, d in enumerate(cal)}
    caps = CapBook(market) if rule == "mcap" else None

    def last(c: str, d: str) -> tuple[str, float] | None:
        ds = dates[c]
        k = bisect.bisect_right(ds, d) - 1
        return (ds[k], px[c][ds[k]]) if k >= 0 else None

    def ret_back(c: str, d: str, n: int) -> float:
        ds = dates[c]
        k = bisect.bisect_right(ds, d) - 1
        return px[c][ds[k]] / px[c][ds[k - n]] - 1

    bad: dict[str, list[str]] = defaultdict(list)
    if market == "KR":
        for c, ds in dates.items():
            for i in range(1, len(ds)):
                lim = 0.155 if ds[i] < "20150615" else 0.305
                if abs(px[c][ds[i]] / px[c][ds[i - 1]] - 1) > lim:
                    bad[c].append(ds[i])

    def eligible(c: str, t: str) -> bool:
        ds = dates[c]
        k = bisect.bisect_right(ds, t) - 1
        if k < 126 or (ds[k] != t and pos.get(ds[k], -99) < pos[t] - 5):
            return False
        if market == "KR":
            lo = ds[max(0, k - 130)]
            if any(lo <= b <= t for b in bad[c]):
                return False
        return True

    ends = [cal[i] for i in range(len(cal) - 1) if cal[i][:6] != cal[i + 1][:6]]
    timeline: dict[str, list[tuple[str, str]]] = defaultdict(list)
    sched: list[
        tuple[str, dict[str, float], dict[str, float], dict[str, tuple[str, list[str]]]]
    ] = []
    backfilled_leader_months = 0
    for t in ends:
        active: dict[str, tuple[str, list[str]]] = {}
        for th, members in themes.items():
            el = list(dict.fromkeys(c for c, _, _ in members if eligible(c, t)))
            if rule == "mcap":
                assert caps is not None
                cv = {c: caps.cap(c, t, pos) for c in el}
                el = [c for c in el if cv[c] is not None]
            if len(el) < 3:
                continue
            if rule == "momentum":
                leader = leader_momentum(
                    {c: (ret_back(c, t, 21), ret_back(c, t, 63), ret_back(c, t, 126)) for c in el}
                )
            else:
                leader = leader_mcap({c: cv[c][0] for c in el})  # type: ignore[index]
                backfilled_leader_months += int(cv[leader][1])  # type: ignore[index]
            active[th] = (leader, el)
            if not timeline[th] or timeline[th][-1][1] != leader:
                timeline[th].append((t, leader))
        if not active:
            continue
        n = len(active)
        lw: dict[str, float] = defaultdict(float)
        bw: dict[str, float] = defaultdict(float)
        for leader, el in active.values():
            lw[leader] += 1 / n
            for c in el:
                bw[c] += 1 / n / len(el)
        sched.append((cal[pos[t] + 1], dict(lw), dict(bw), active))

    def simulate(which: int, cost: float) -> tuple[list[str], list[float], list[tuple[str, float]]]:
        days, rets, months = [], [], []
        hold: dict[str, float] = {}
        for k, (ex, lw, bw, _act) in enumerate(sched):
            target = lw if which == 0 else bw
            tot = sum(hold.values()) or 1.0
            cur = {c: v / tot for c, v in hold.items()}
            turn = (
                1.0
                if k == 0
                else sum(abs(target.get(c, 0) - cur.get(c, 0)) for c in set(target) | set(cur))
            )
            nxt = sched[k + 1][0] if k + 1 < len(sched) else cal[-1]
            hold = dict(target)
            m_ret = 1.0 - cost * turn
            first = True
            for i in range(pos[ex] + 1, pos[nxt] + 1):
                d0, d1 = cal[i - 1], cal[i]
                val0 = sum(hold.values())
                new = {}
                for c, v in hold.items():
                    p0, p1 = last(c, d0), last(c, d1)
                    new[c] = v * (p1[1] / p0[1]) if p0 and p1 else v
                val1 = sum(new.values())
                r = val1 / val0 - 1
                if first:
                    r = (1 + r) * (1 - cost * turn) - 1
                    first = False
                days.append(d1)
                rets.append(r)
                hold = new
                m_ret *= val1 / val0
            months.append((ex, m_ret - 1))
        return days, rets, months

    res: dict[str, Any] = {"first_exec": sched[0][0], "last_exec": sched[-1][0]}
    monthly: dict[str, tuple[list[float], list[float]]] = {}
    for label, cost in (("base", COST[market]), ("cost2x", 2 * COST[market])):
        d_l, r_l, m_l = simulate(0, cost)
        _d_b, r_b, m_b = simulate(1, cost)
        a, b = [x[1] for x in m_l], [x[1] for x in m_b]
        monthly[label] = (a, b)
        ex = [x - y for x, y in zip(a, b, strict=True)]
        half = len(ex) // 2
        m, se = statistics.fmean(ex), nw_se(ex)
        res[label] = {
            "months": len(ex),
            "excess_mean_month": round(m, 5),
            "se": round(se, 5),
            "lower": round(m - Z * se, 5),
            "first_half": round(statistics.fmean(ex[:half]), 5),
            "second_half": round(statistics.fmean(ex[half:]), 5),
            "split": m_l[half][0],
            "leader": _curve(r_l),
            "basket": _curve(r_b),
            "win_months": round(sum(1 for x in ex if x > 0) / len(ex), 3),
        }
        if label == "base":
            ir = [idx[d_l[i]] / idx[d_l[i - 1]] - 1 for i in range(1, len(d_l))]
            res["index"] = _curve(ir)
            yl: dict[str, float] = defaultdict(lambda: 1.0)
            yb: dict[str, float] = defaultdict(lambda: 1.0)
            for (d, x), (_, y) in zip(m_l, m_b, strict=True):
                yl[d[:4]] *= 1 + x
                yb[d[:4]] *= 1 + y
            res["by_year"] = {y: [round(yl[y] - 1, 3), round(yb[y] - 1, 3)] for y in sorted(yl)}
    a, b = monthly["base"]
    res["horizons"] = {h: rolling_horizon(a, b, n) for h, n in HORIZONS.items()}
    res["horizons"]["all"] = rolling_horizon(a, b, len(a))
    # 테마별 (그 테마만, 비용 전 월 수익 차)
    per_theme = {}
    for th in themes:
        ex = []
        for k, (exd, _lw, _bw, act) in enumerate(sched):
            if th not in act:
                continue
            t_end = sched[k + 1][0] if k + 1 < len(sched) else cal[-1]
            leader, el = act[th]

            def r(c: str, s: str = exd, e: str = t_end) -> float:
                p0, p1 = last(c, s), last(c, e)
                return p1[1] / p0[1] - 1 if p0 and p1 else 0.0

            ex.append(r(leader) - statistics.fmean(r(c) for c in el))
        if ex:
            held = Counter[str]()
            for _exd, _lw, _bw, act in sched:
                if th in act:
                    held[names[act[th][0]]] += 1
            per_theme[th] = {
                "months": len(ex),
                "excess_mean_month": round(statistics.fmean(ex), 4),
                "se": round(nw_se(ex), 4) if len(ex) > 5 else None,
                "switches": len(timeline[th]) - 1,
                "most_held": held.most_common(4),
                "timeline": [[d, names[c]] for d, c in timeline[th]],
            }
    res["per_theme"] = per_theme
    base = res["base"]
    ok = base["lower"] > 0 and base["first_half"] > 0 and base["second_half"] > 0
    if rule == "mcap":
        h5 = res["horizons"]["5y"]
        res["T3_5y_windows_leader_wins_gt_half"] = bool(h5 and h5["a_wins"] > 0.5)
        ok = ok and res["T3_5y_windows_leader_wins_gt_half"]
        res["backfilled_leader_months"] = backfilled_leader_months
    res["verdict"] = "HISTORICAL_SUPPORT" if ok else "REJECT"
    res["bad_tick_stocks"] = {names[c]: len(v) for c, v in bad.items() if v}
    return res


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    rule = args[0]
    if rule not in ("momentum", "mcap"):
        raise SystemExit("momentum | mcap")
    raw = SPEC.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SPEC_SHA:
        raise SystemExit("테마 목록 해시 불일치")
    themes = json.loads(raw.decode("utf-8"))
    out = {m: run(rule, m, themes[m]) for m in ("KR", "US")}
    path = REPO_ROOT / "reports" / "theme_leaders_162"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{rule}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for m, r in out.items():
        sys.stdout.write(f"{m} {r['verdict']} {json.dumps(r['base'], ensure_ascii=False)}\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
