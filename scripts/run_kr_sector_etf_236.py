"""236 — 섹터 ETF 수급: 외국인 + 기관이 사 모으는 섹터를 KOSPI 200 섹터 ETF 로 (연금저축용 질문).

    python -m scripts.run_kr_sector_etf_236 fetch   # ETF 일봉 받기 (조회만)
    python -m scripts.run_kr_sector_etf_236 audit   # 자료 점검 (수익 계산 없음)
    python -m scripts.run_kr_sector_etf_236 run     # 1 회 판정 — 깨끗한 트리 · 해시 확인

규칙 · 판정 기준: docs/research/236-kr-sector-etf-flow-preregistration.md.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.run_kr_flow import flow_scores, load_flows  # noqa: E402
from scripts.run_lt_kr import build_panel, members  # noqa: E402
from scripts.run_lt_leaders import trend_off, trend_up  # noqa: E402

DATA = REPO / "data" / "research" / "kr_sector_etf"
OUT = REPO / "reports" / "kr_sector_etf_236"
STOCKS = REPO / "data" / "research" / "kr_stocks"
LO, HI = "20110401", "20260831"
"""ETF 받는 기간 — 2026-09-01 이후 자료는 읽지 않는다(연구 규칙)."""
FROM = "2011-10-04"
"""판정 시작: 첫 ETF 상장(2011-04-06) 뒤 120 거래일 — 추세 규칙(T 120)을 낼 수 있는 첫 무렵."""
TO = "2026-08-31"
HALF = "2019-01-01"

ETFS: dict[str, str] = {
    "139220": "건설",
    "139230": "중공업",
    "139240": "철강소재",
    "139250": "에너지화학",
    "139260": "IT",
    "139270": "금융",
    "139290": "경기소비재",
    "227540": "헬스케어",
    "227550": "산업재",
    "227560": "생활소비재",
    "315270": "커뮤니케이션",
}
"""TIGER 200 섹터 ETF (KOSPI 200 섹터 지수 추종) — 섹터 이름."""
BENCH = "069500"
"""KODEX 200 — 지수 보유 비교."""

KSIC_TO_SECTOR: dict[int, str] = {
    **dict.fromkeys((41, 42), "건설"),
    **dict.fromkeys((29, 31), "중공업"),
    **dict.fromkeys((17, 23, 24, 25), "철강소재"),
    **dict.fromkeys((19, 20, 22), "에너지화학"),
    **dict.fromkeys((26, 27), "IT"),
    **dict.fromkeys((64, 65, 66), "금융"),
    **dict.fromkeys((13, 14, 15, 30, 32, 33, 45, 47, 55, 56, 85, 90, 91), "경기소비재"),
    21: "헬스케어",
    **dict.fromkeys((28, 46, 49, 50, 51, 52, 69, 70, 71, 72, 73, 74, 75), "산업재"),
    **dict.fromkeys((1, 2, 3, 10, 11, 12), "생활소비재"),
    **dict.fromkeys((58, 59, 60, 61, 62, 63), "커뮤니케이션"),
}
"""KSIC 중분류 → KOSPI 200 섹터 (문서 236 §2 — 실제 지수 구성과 다를 수 있음, 약점으로 적음).
27(의료 · 정밀 · 광학)은 소분류 271(의료용 기기)만 헬스케어, 나머지는 IT."""

TOP = 3
MIN_MEMBERS = 3
FEE = 0.0025
FEE_REF = 0.0005


def sector_of(ksic: str) -> str | None:
    if len(ksic) < 6 or not ksic[2:4].isdigit():
        return None
    mid = int(ksic[2:4])
    if mid == 27 and ksic[4:6] == "01":
        return "헬스케어"
    return KSIC_TO_SECTOR.get(mid)


# ------------------------------------------------------------------ 자료


def fetch() -> int:
    from scripts.fetch_kr_stocks import _kis, _pages

    DATA.mkdir(parents=True, exist_ok=True)
    kis = _kis()
    try:
        for code in [*ETFS, BENCH]:
            rows = _pages(kis._get, code, LO, HI)
            text = "date,close,volume,value\n" + "".join(
                f"{d},{c:g},{v:g},{a:g}\n" for d, c, v, a in rows
            )
            (DATA / f"{code}.csv").write_text(text, encoding="utf-8", newline="\n")
            print(code, len(rows), rows[0][0] if rows else None, rows[-1][0] if rows else None)
    finally:
        kis.close()
    return 0


def data_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.glob("*.csv")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


EXPECTED_DATA: str | None = "189e631cfa9f2059c2c1a2d71beddbef377ebf621ba07b1a9cbb5188275194f4"
"""ETF 자료 해시 — audit 뒤 문서 §7 에 적고 여기에 고정한다."""


def load_etf(code: str, days: Sequence[str]) -> list[float | None]:
    """패널 날짜(ISO)에 맞춘 ETF 종가 — 상장 전 · 기록 없는 날은 None.

    거래량 0 인 날도 종가를 쓴다: ETF 는 유동성 공급자(LP)가 호가를 내므로 그 값 근처에서
    살 · 팔 수 있다고 본다(문서 236 §1 약점). 주식(141)은 거래량 0 이면 None 이었다."""
    at = {d: i for i, d in enumerate(days)}
    xs: list[float | None] = [None] * len(days)
    for line in (DATA / f"{code}.csv").read_text(encoding="utf-8").splitlines()[1:]:
        d, c, _vol, _val = line.split(",")
        i = at.get(f"{d[:4]}-{d[4:6]}-{d[6:]}")
        if i is not None and float(c) > 0:
            xs[i] = float(c)
    return xs


def audit() -> dict[str, object]:
    panel, _ok = build_panel(STOCKS, to=TO)
    info = json.loads((STOCKS / "info.json").read_text(encoding="utf-8"))
    out: dict[str, object] = {"data_hash": data_hash()}
    for code, name in [*ETFS.items(), (BENCH, "KODEX 200")]:
        xs = load_etf(code, panel.days)
        have = [i for i, v in enumerate(xs) if v is not None]
        first = have[0] if have else None
        gaps = sum(1 for i in range(first or 0, len(xs)) if xs[i] is None) if have else None
        zero = sum(
            1
            for line in (DATA / f"{code}.csv").read_text(encoding="utf-8").splitlines()[1:]
            if float(line.split(",")[2]) <= 0
        )
        out[code] = {
            "name": name,
            "zero_volume_days": zero,
            "first": panel.days[first] if first is not None else None,
            "last": panel.days[have[-1]] if have else None,
            "missing_after_first": gaps,
        }
    # 월말마다 섹터별 종목군 안 종목 수 (수익 없음)
    counts: dict[str, list[int]] = defaultdict(list)
    for t in range(len(panel.days) - 1):
        if panel.days[t] < FROM or panel.days[t + 1][:7] == panel.days[t][:7]:
            continue
        pool = members(panel, t)
        c: dict[str, int] = defaultdict(int)
        for s in pool:
            sec = sector_of(str(info.get(s, {}).get("ksic", "")))
            if sec:
                c[sec] += 1
        for name in ETFS.values():
            counts[name].append(c.get(name, 0))
    out["members_per_month"] = {
        k: {"min": min(v), "median": statistics.median(v), "max": max(v)} for k, v in counts.items()
    }
    return out


# ------------------------------------------------------------------ 규칙


@dataclass(slots=True)
class Result:
    equity: list[float] = field(default_factory=list)
    days: list[str] = field(default_factory=list)
    trades: int = 0
    picks: dict[str, list[str]] = field(default_factory=dict)
    exposure: list[float] = field(default_factory=list)


def sector_scores(
    score: dict[str, float], sector: dict[str, str | None]
) -> dict[str, tuple[float, int]]:
    """섹터별 (종목 수급 점수 중앙값, 종목 수) — 142 의 섹터 고르기와 같은 중앙값."""
    by: dict[str, list[float]] = defaultdict(list)
    for s, v in score.items():
        sec = sector.get(s)
        if sec:
            by[sec].append(v)
    return {k: (statistics.median(v), len(v)) for k, v in by.items()}


def simulate(
    days: Sequence[str],
    etf: dict[str, list[float | None]],
    decide: dict[int, list[str]],
    index_ok: Sequence[bool],
    fee: float,
    *,
    top: int = TOP,
    use_trend: bool = True,
) -> Result:
    """판단일(월말) t 의 목록 → t + 1 종가에 같은 비중(1 / ``top``)으로 맞춤.

    ``decide[t]`` 는 그날 고른 ETF(점수 순). 판단일에 KOSPI 가 200 일선 아래면 목록이 비어
    **모두 판다**(현금, 이자 0). ``use_trend`` 면 추세(T 20·60·120 · 50 일선)일 때만 사고,
    매일 종가로 추세가 꺾이면 다음 날 판다.
    """
    start = next(i for i, d in enumerate(days) if d >= FROM)
    end = max(i for i, d in enumerate(days) if d <= TO)
    cash = 1.0
    qty: dict[str, float] = {}
    res = Result()
    pending: list[str] | None = None
    sell_next: set[str] = set()

    def px(c: str, t: int) -> float | None:
        return etf[c][t]

    def value(t: int) -> float:
        total = cash
        for c, q in qty.items():
            k = t
            while etf[c][k] is None:
                k -= 1
            total += q * etf[c][k]  # type: ignore[operator]
        return total

    for t in range(start, end + 1):
        for c in sorted(sell_next):
            p = px(c, t)
            if c in qty and p is not None:
                cash += qty.pop(c) * p * (1 - fee)
                res.trades += 1
        sell_next = {c for c in sell_next if c in qty}
        if pending is not None:
            eq = value(t)
            want = [c for c in pending if px(c, t) is not None][:top]
            for c in sorted(qty):  # 목록에서 빠진 것 팔기
                p = px(c, t)
                if c not in want and p is not None:
                    cash += qty.pop(c) * p * (1 - fee)
                    res.trades += 1
            for c in want:
                p = px(c, t)
                assert p is not None
                gap = eq / top - qty.get(c, 0.0) * p
                if gap > 0:
                    amt = min(gap, cash)
                    if amt > 0:
                        qty[c] = qty.get(c, 0.0) + amt * (1 - fee) / p
                        cash -= amt
                        res.trades += 1
                elif gap < 0:
                    sell = -gap
                    qty[c] -= sell / p
                    cash += sell * (1 - fee)
                    res.trades += 1
            pending = None
        if use_trend:
            for c in qty:
                if trend_off(etf[c], t):
                    sell_next.add(c)
        if t in decide and t < end:
            chosen = decide[t]
            pending = (
                [c for c in chosen if c not in sell_next and (not use_trend or trend_up(etf[c], t))]
                if index_ok[t]
                else []
            )
            res.picks[days[t]] = pending
        eq = value(t)
        res.equity.append(eq)
        res.days.append(days[t])
        res.exposure.append(1 - cash / eq if eq else 0.0)
    return res


def stats(eq: Sequence[float], days: Sequence[str], lo: str, hi: str) -> dict[str, float]:
    idx = [i for i, d in enumerate(days) if lo <= d < hi]
    xs = [eq[i] for i in idx]
    rets = [xs[i] / xs[i - 1] - 1 for i in range(1, len(xs))]
    years = len(rets) / 252
    cagr = (xs[-1] / xs[0]) ** (1 / years) - 1 if years > 0 else 0.0
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = statistics.fmean(rets) / sd * 252**0.5 if sd else 0.0
    peak, mdd = xs[0], 0.0
    for v in xs:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"cagr": cagr, "sharpe": sharpe, "mdd": mdd, "end": xs[-1] / xs[0]}


def month_ends(days: Sequence[str]) -> list[int]:
    return [t for t in range(len(days) - 1) if days[t + 1][:7] != days[t][:7]]


def build(fee: float = FEE) -> dict[str, Any]:
    """FLOW(수급 상위 3 섹터) · EW-G(모든 섹터 같은 비중) · B-G(KODEX 200) — 200 일선 조건 같음.

    참고 줄: EW(조건 없이 모든 섹터) · KODEX 200 그냥 보유."""
    panel, index_ok = build_panel(STOCKS, to=TO)
    info = json.loads((STOCKS / "info.json").read_text(encoding="utf-8"))
    flows = load_flows(panel)
    days = panel.days
    etf = {c: load_etf(c, days) for c in [*ETFS, BENCH]}
    code_of = {name: c for c, name in ETFS.items()}
    sector = {s: sector_of(str(info.get(s, {}).get("ksic", ""))) for s in panel.close}

    flow_pick: dict[int, list[str]] = {}
    all_pick: dict[int, list[str]] = {}
    bench_pick: dict[int, list[str]] = {}
    for t in month_ends(days):
        if days[t] < FROM or days[t] > TO:
            continue
        live = [c for c in ETFS if _listed_days(etf[c], t) >= 120]
        all_pick[t] = live
        bench_pick[t] = [BENCH]
        pool = members(panel, t)
        sc = sector_scores(flow_scores(flows, t, pool), sector)
        ranked = sorted(
            (k for k, (_m, n) in sc.items() if n >= MIN_MEMBERS and code_of.get(k) in live),
            key=lambda k: (sc[k][0], k),
            reverse=True,
        )
        flow_pick[t] = [code_of[k] for k in ranked]

    return {
        "days": days,
        "flow": simulate(days, etf, flow_pick, index_ok, fee),
        "ew": simulate(days, etf, all_pick, index_ok, fee, top=len(ETFS), use_trend=False),
        "bench_gated": simulate(days, etf, bench_pick, index_ok, fee, top=1, use_trend=False),
        "ew_plain": simulate(
            days, etf, all_pick, [True] * len(days), fee, top=len(ETFS), use_trend=False
        ),
        "bench": etf[BENCH],
        "ranked": {days[t]: v for t, v in flow_pick.items()},
    }


def _listed_days(xs: Sequence[float | None], t: int) -> int:
    return sum(1 for v in xs[: t + 1] if v is not None)


def _hold(xs: Sequence[float | None], days: Sequence[str]) -> tuple[list[float], list[str]]:
    idx = [i for i, d in enumerate(days) if FROM <= d <= TO and xs[i] is not None]
    return [float(xs[i]) for i in idx], [days[i] for i in idx]  # type: ignore[arg-type]


def _yearly(eq: Sequence[float], days: Sequence[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    prev: float | None = None
    for v, d in zip(eq, days, strict=True):
        y = d[:4]
        if y not in first:
            first[y] = prev if prev is not None else v
        last[y] = v
        prev = v
    for y in first:
        out[y] = round(last[y] / first[y] - 1, 4)
    return out


def _summary(eq: Sequence[float], days: Sequence[str]) -> dict[str, Any]:
    return {
        "all": stats(eq, days, FROM, "9999"),
        "half1": stats(eq, days, FROM, HALF),
        "half2": stats(eq, days, HALF, "9999"),
        "yearly": _yearly(eq, days),
    }


def judge(flow: dict[str, Any], ew: dict[str, Any]) -> dict[str, Any]:
    """S1 샤프 ≥ EW-G + 0.20 · S2 두 반쪽 연수익 > EW-G · S3 낙폭 ≤ max(EW-G 낙폭, 40%)."""
    s1 = flow["all"]["sharpe"] >= ew["all"]["sharpe"] + 0.20
    s2 = all(flow[h]["cagr"] > ew[h]["cagr"] for h in ("half1", "half2"))
    s3 = flow["all"]["mdd"] <= max(ew["all"]["mdd"], 0.40)
    return {"S1": s1, "S2": s2, "S3": s3, "verdict": "PASS" if s1 and s2 and s3 else "REJECT"}


def run() -> dict[str, object]:
    if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout:
        raise SystemExit("깨끗한 트리에서만 판정한다")
    if EXPECTED_DATA is None or data_hash() != EXPECTED_DATA:
        raise SystemExit("ETF 자료 해시가 고정 값과 다르다")
    verdict = OUT / "verdict.json"
    if verdict.exists():
        raise SystemExit("이미 판정했다 — 다시 돌리지 않는다")
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    report: dict[str, Any] = {"commit": commit, "data_hash": EXPECTED_DATA}
    for label, fee in (("fee_025", FEE), ("fee_005_ref", FEE_REF)):
        b = build(fee)
        flow, ew, bg = b["flow"], b["ew"], b["bench_gated"]
        hold_eq, hold_days = _hold(b["bench"], b["days"])
        part: dict[str, Any] = {
            "flow": _summary(flow.equity, flow.days),
            "ew_gated": _summary(ew.equity, ew.days),
            "kodex200_gated": _summary(bg.equity, bg.days),
            "ew_plain": _summary(b["ew_plain"].equity, b["ew_plain"].days),
            "kodex200_hold": _summary(hold_eq, hold_days),
            "trades": {"flow": flow.trades, "ew_gated": ew.trades},
            "exposure_mean": {
                "flow": statistics.fmean(flow.exposure),
                "ew_gated": statistics.fmean(ew.exposure),
            },
        }
        part["judge"] = judge(part["flow"], part["ew_gated"])
        if fee == FEE:
            counts: dict[str, int] = defaultdict(int)
            for picks in flow.picks.values():
                for c in picks[:TOP]:
                    counts[ETFS[c]] += 1
            part["held_months_by_sector"] = dict(sorted(counts.items(), key=lambda x: -x[1]))
            part["months_with_no_buy"] = sum(1 for v in flow.picks.values() if not v)
            part["last_picks"] = {d: [ETFS[c] for c in v] for d, v in list(flow.picks.items())[-6:]}
        report[label] = part
    report["verdict"] = report["fee_025"]["judge"]["verdict"]
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    verdict.write_text(text, encoding="utf-8")
    report["verdict_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return report


def main(argv: list[str]) -> int:
    stages = {"fetch": fetch, "audit": audit, "run": run}
    if len(argv) != 1 or argv[0] not in stages:
        raise SystemExit(f"usage: python -m scripts.run_kr_sector_etf_236 {'|'.join(stages)}")
    out = stages[argv[0]]()
    if isinstance(out, dict):
        sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
