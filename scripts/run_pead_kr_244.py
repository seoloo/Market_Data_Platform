"""문서 244 — 실적 발표 갭 상승 따라가기를 유가증권시장에서 (240 재현). 1회 판정.

사용 예::

    python -m scripts.run_pead_kr_244 fetch   # DART 잠정실적 공정공시 목록 (이어 받기 · 하루 한도)
    python -m scripts.run_pead_kr_244 audit   # 자료 관문: 해마다 공시 커버리지 (수익 안 봄)
    python -m scripts.run_pead_kr_244 run     # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음일 때만

* 종목: 141 자료의 '그 시점 종목군'(직전 월말 거래대금 상위 200 보통주, 상장폐지 포함).
* 실적 공시: Open DART 공정공시(I002) 중 이름에 '(잠정)실적' 이 든 원본 공시(정정 · 첨부추가 뺌).
  DART 는 접수 **날짜**만 준다 → 반응을 공시일 D 와 다음 날 D+1 두 날로 본다.
* 신호: D−1 종가 → D+1 종가 ≥ +5% 이고 max(D, D+1 거래량) ≥ 직전 20 거래일 평균 × 2.
* 진입 · 보유: D+2 종가에 사서 20 거래일 뒤 종가(국장 자료는 종가뿐 — 141 관례). 왕복 0.5%.
  보유 중 자료가 끊기면(상장폐지) 마지막 거래 종가. 초과 = 종목 − KOSPI(같은 날들).
* 대조: 같은 종목군 · 같은 조건이지만 실적 공시일 ±3 거래일 밖인 날(종목마다 5 거래일 안 겹침 뺌).
"""

from __future__ import annotations

import bisect
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_lt_kr import DATA, EXPECTED_DATA, _iso, data_hash, members, month_end_day
from scripts.run_lt_kr import build_panel as build_kr
from scripts.run_lt_leaders import TO

from auto_trading.research.pead_240 import Event, bootstrap_mean, dedupe

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "data" / "research" / "pead_kr_244"
DISC = ROOT / "disclosures"
OUT = REPO / "reports" / "pead_kr_244"
FROM_DAY, TO_DAY = "20080101", "20260831"
GAP = 0.05
VOL_X = 2.0
HOLD = 20
HOLD_LONG = 60
COST = 0.005
"""왕복 — 141 국장 연구의 편도 0.25% 두 번."""
DEDUPE_DAYS = 5
CONTROL_GAP_DAYS = 3
ENTRY_LAG = 2
"""D+2 종가 진입 (D+1 종가에 신호를 알고 다음 거래일 종가에 산다)."""
COVERAGE_MIN = 0.5
SEED = 244
FIRST_END = "2016-12-31"
EXPECTED_DISC: str | None = None
"""공시 목록 묶음 해시 — audit 뒤 문서 244 에 적고 여기에 고정한다."""


# ------------------------------------------------------------------ 순수


def is_earnings(report_nm: str) -> bool:
    """원본 잠정실적 공정공시인가 (정정 · 첨부추가 등 '[' 로 시작하는 것은 뺀다)."""
    nm = report_nm.strip()
    return not nm.startswith("[") and "(잠정)실적" in nm


def earnings_dates(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({str(r["rcept_dt"]) for r in rows if is_earnings(str(r.get("report_nm", "")))})


def day_index(days: Sequence[str], yyyymmdd: str) -> int | None:
    """공시일 → 그날 또는 다음 거래일 번호 (``days`` 는 ISO)."""
    i = bisect.bisect_left(days, _iso(yyyymmdd))
    return i if i < len(days) else None


def gap2(close: Sequence[float | None], vol: Sequence[float], d: int) -> float | None:
    """D−1 → D+1 종가 수익 (거래량 조건을 넘을 때만). 넘지 못하거나 자료가 없으면 None."""
    if d < 21 or d + 1 >= len(close):
        return None
    c0, c1 = close[d - 1], close[d + 1]
    if c0 is None or c1 is None or c0 <= 0:
        return None
    base = [v for v in vol[d - 20 : d] if v > 0]
    if len(base) < 10:
        return None
    if max(vol[d], vol[d + 1]) < VOL_X * statistics.fmean(base):
        return None
    return c1 / c0 - 1


def outcome(
    days: Sequence[str],
    close: Sequence[float | None],
    kospi: Sequence[float],
    d: int,
    last: int,
    hold: int = HOLD,
) -> tuple[str, float] | None:
    """D → (진입일, 비용 뒤 초과 수익). ``last`` = 그 종목 마지막 거래일 번호."""
    e = d + ENTRY_LAG
    while e <= min(d + ENTRY_LAG + 5, last) and close[e] is None:
        e += 1
    if e > last or close[e] is None:
        return None
    x = e + hold
    if x >= len(days):
        return None
    xi = min(x, last)
    while close[xi] is None:
        xi -= 1
    stock = close[xi] / close[e] - 1 - COST  # type: ignore[operator]
    market = kospi[x] / kospi[e] - 1
    return days[e], stock - market


def judge(main: tuple[float, float, float], halves: Sequence[float], control: float) -> str:
    """240 §3 그대로 (순수)."""
    mean, lo, _hi = main
    if mean <= 0:
        return "불리"
    if lo > 0 and all(h > 0 for h in halves) and mean > control:
        return "지지"
    return "불명"


@dataclass(frozen=True, slots=True)
class Hit:
    symbol: str
    d: int
    gap: float
    earnings: bool


# ------------------------------------------------------------------ 받기


def _jobs() -> list[tuple[str, str]]:
    from scripts.fetch_kr_dart import _corp_codes

    ever = json.loads((DATA / "ever_universe.json").read_text(encoding="utf-8"))
    corps = _corp_codes()
    return [(c, corps[c]) for c in sorted(ever) if c in corps]


def fetch() -> dict[str, Any]:
    import httpx
    from scripts.fetch_kr_dart import API, OVER_LIMIT, PAUSE, Budget, _key
    from scripts.fetch_kr_dart import ROOT as DART_ROOT

    key = _key()
    budget = Budget(DART_ROOT / "budget.json")
    got = skipped = 0
    stopped = ""
    DISC.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60) as client:
        for code, corp in _jobs():
            path = DISC / f"{code}.json"
            if path.exists():
                skipped += 1
                continue
            rows: list[dict[str, Any]] = []
            page, pages, status = 1, 1, ""
            while page <= pages:
                if not budget.spend():
                    stopped = "하루 한도"
                    break
                body = client.get(
                    f"{API}/list.json",
                    params={
                        "crtfc_key": key,
                        "corp_code": corp,
                        "bgn_de": FROM_DAY,
                        "end_de": TO_DAY,
                        "pblntf_detail_ty": "I002",
                        "page_count": "100",
                        "page_no": str(page),
                    },
                ).json()
                time.sleep(PAUSE)
                status = str(body.get("status", ""))
                if status == OVER_LIMIT:
                    stopped = "DART 한도 초과(020)"
                    break
                pages = int(body.get("total_page") or 1)
                rows += body.get("list") or []
                page += 1
            if stopped:
                break
            path.write_text(
                json.dumps({"status": status, "list": rows}, ensure_ascii=False), encoding="utf-8"
            )
            got += 1
    return {"got": got, "skipped": skipped, "stopped": stopped, "used_today": budget.used}


def disc_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = sorted(DISC.glob("*.json"))
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


# ------------------------------------------------------------------ 판


def load_volume(days: Sequence[str], sym: str) -> list[float]:
    at = {d: i for i, d in enumerate(days)}
    out = [0.0] * len(days)
    for line in (DATA / "prices" / f"{sym}.csv").read_text(encoding="utf-8").splitlines()[1:]:
        d, _c, vol, _v = line.split(",")
        i = at.get(_iso(d))
        if i is not None:
            out[i] = float(vol)
    return out


def universe_by_day(panel: Any) -> list[set[str]]:
    """각 거래일 → 직전 월말(그날 전)의 종목군. 월말이 아직 없으면 빈 집합."""
    days = panel.days
    out: list[set[str]] = []
    current: set[str] = set()
    for t in range(len(days)):
        out.append(current)
        if month_end_day(days, t):
            current = set(members(panel, t))
    return out


def collect(panel: Any) -> tuple[list[Hit], dict[str, Any]]:
    days = panel.days
    lo = bisect.bisect_left(days, _iso(FROM_DAY))
    hi = bisect.bisect_right(days, _iso(TO_DAY)) - 1
    uni = universe_by_day(panel)
    hits: list[Hit] = []
    skipped: Counter[str] = Counter()
    for sym in sorted(panel.close):
        path = DISC / f"{sym}.json"
        if not path.exists():
            continue
        close = panel.close[sym]
        vol = load_volume(days, sym)
        rows = json.loads(path.read_text(encoding="utf-8")).get("list") or []
        ed = [i for d in earnings_dates(rows) if (i := day_index(days, d)) is not None]
        ed = dedupe([i for i in ed if lo <= i <= hi])
        near = {j for i in ed for j in range(i - CONTROL_GAP_DAYS, i + CONTROL_GAP_DAYS + 1)}
        for d in ed:
            if sym not in uni[d]:
                skipped["earnings_not_in_universe"] += 1
                continue
            g = gap2(close, vol, d)
            if g is None:
                skipped["earnings_no_volume_or_data"] += 1
                continue
            hits.append(Hit(sym, d, g, True))
        last_ctrl = -(10**9)
        for d in range(lo, hi + 1):
            if d in near or sym not in uni[d] or d - last_ctrl <= DEDUPE_DAYS:
                continue
            g = gap2(close, vol, d)
            if g is not None and abs(g) >= GAP:
                hits.append(Hit(sym, d, g, False))
                last_ctrl = d
    return hits, dict(skipped)


def coverage(panel: Any) -> dict[str, Any]:
    """해마다: 그해 종목군에 있었던 회사 중 잠정실적 공시가 1 건 이상인 비율 (수익 안 봄)."""
    days = panel.days
    uni = universe_by_day(panel)
    per_year: dict[str, set[str]] = {}
    for t, s in enumerate(uni):
        per_year.setdefault(days[t][:4], set()).update(s)
    have: dict[str, set[str]] = {}
    count: Counter[str] = Counter()
    for path in sorted(DISC.glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8")).get("list") or []
        for d in earnings_dates(rows):
            have.setdefault(d[:4], set()).add(path.stem)
            count[d[:4]] += 1
    out = {}
    for y in sorted(per_year):
        if not "2008" <= y <= TO_DAY[:4] or not per_year[y]:
            continue
        mem = per_year[y]
        out[y] = {
            "members": len(mem),
            "with_earnings": len(mem & have.get(y, set())),
            "share": round(len(mem & have.get(y, set())) / len(mem), 3),
            "notices": count[y],
        }
    start = next((y for y, v in out.items() if v["share"] >= COVERAGE_MIN), None)
    return {"by_year": out, "start_year": start}


def summarize(events: Sequence[Event], seed: int = SEED) -> dict[str, Any]:
    if not events:
        return {"n": 0}
    mean, lo, hi = bootstrap_mean(events, seed=seed)
    xs = [e.excess for e in events]
    return {
        "n": len(events),
        "mean": round(mean, 5),
        "ci95": [round(lo, 5), round(hi, 5)],
        "median": round(statistics.median(xs), 5),
        "win_rate": round(sum(1 for x in xs if x > 0) / len(xs), 3),
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if data_hash()[0] != EXPECTED_DATA:
        raise SystemExit("국장 자료 해시가 141 과 다르다")
    if EXPECTED_DISC is None or disc_hash()[0] != EXPECTED_DISC:
        raise SystemExit("공시 목록 해시가 문서 244 고정값과 다르다 — audit 먼저")
    panel, _ok = build_kr(to=TO)
    cov = coverage(panel)
    start = f"{cov['start_year']}-01-01"
    rows = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kmap = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    kospi = [kmap[d] for d in panel.days]
    hits, skipped = collect(panel)

    def events(sel: Sequence[Hit], hold: int = HOLD) -> list[Event]:
        out = []
        for h in sel:
            o = outcome(panel.days, panel.close[h.symbol], kospi, h.d, panel.last[h.symbol], hold)
            if o is not None and o[0] >= start:
                out.append(Event(o[0], h.gap, o[1]))
        return out

    up = [h for h in hits if h.earnings and h.gap >= GAP]
    down = [h for h in hits if h.earnings and h.gap <= -GAP]
    ctrl = [h for h in hits if not h.earnings and h.gap >= GAP]
    main = events(up)
    first = [e for e in main if e.entry_day <= FIRST_END]
    second = [e for e in main if e.entry_day > FIRST_END]
    ctrl_ev = events(ctrl)
    main_s, ctrl_s = summarize(main), summarize(ctrl_ev)
    halves = [summarize(first), summarize(second)]
    verdict = judge(
        (main_s["mean"], *main_s["ci95"]),
        [h.get("mean", 0.0) for h in halves],
        ctrl_s.get("mean", 0.0),
    )
    out = {
        "git": sha,
        "start": start,
        "verdict": verdict,
        "earnings_up_20": main_s,
        "halves": {"first": halves[0], "second": halves[1]},
        "control_up_20": ctrl_s,
        "earnings_down_20": summarize(events(down)),
        "earnings_up_60": summarize(events(up, HOLD_LONG)),
        "control_up_60": summarize(events(ctrl, HOLD_LONG)),
        "gap_5_10": summarize([e for e in main if e.gap < 0.10]),
        "gap_10_plus": summarize([e for e in main if e.gap >= 0.10]),
        "by_year": dict(sorted(Counter(e.entry_day[:4] for e in main).items())),
        "skipped": skipped,
        "coverage": cov,
        "note": (
            "문서 244 1 회 판정. 초과 = 비용(왕복 0.5%) 뒤 종목 − KOSPI, D+2 종가 진입 · 20 거래일."
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def audit() -> dict[str, Any]:
    panel, _ok = build_kr(to=TO)
    h, n = disc_hash()
    cov = coverage(panel)
    jobs = len(_jobs())
    return {"disc_hash": h, "files": n, "jobs": jobs, **cov}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "fetch":
        out = fetch()
    elif cmd == "audit":
        out = audit()
    elif cmd == "run":
        res = run()
        out = {"verdict": res["verdict"], "start": res["start"]}
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
