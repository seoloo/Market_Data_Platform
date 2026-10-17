"""문서 261 — 미장 주도주 고르기: 113 기본 vs + 시가총액 $500억 vs + 시가총액 + 실적 증가. 1회 판정.

사용 예::

    python -m scripts.run_leader_quality_261 coverage   # 날짜별 후보 수만 (수익 안 봄)
    python -m scripts.run_leader_quality_261 run        # 깨끗한 트리 · 자료 해시 · 판정 없을 때만

판 · 진입 · 나가기 · 크기는 258 의 ARM(월말 · SPY 200 일선 위 · 추세 · 다음 거래일 종가 ·
10% · 최대 10, −3% 손절 + 추세 꺼짐 · 고점 −35%). **고르는 법만** 다르다:

* BASE: 113 — 전체에서 1 · 3 · 6 달 수익 순위 점수, 주도 섹터 3 · 주도주 10.
* CAP: 그 시점 시가총액(추정) ≥ $500억 × (SPY 그날 ÷ SPY 2026-08-31) 인 종목 안에서 같은 계산.
* CAP_EARN: CAP 안에서, 주도 섹터 후보를 점수 순으로 보며 **최근 분기 매출 · 영업이익이
  1 년 전 같은 분기보다 모두 늘어난** 종목만 10 개까지.

시가총액 추정 = 공시된 유통 시가총액(``EntityPublicFloat``, 기준일 d) × (수정 종가 t ÷ 수정 종가 d).
공시일(filed) 전 값은 쓰지 않는다. 분기 = 기간 80 ~ 100 일 값, 끝난 지 200 일 넘으면 없음으로 본다.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import Market, trade_stats, us_market
from scripts.run_lt_leaders import LOOKS, TOP_SECTORS, TOP_STOCKS, stats
from scripts.run_lt_leaders import build_panel as build_us
from scripts.run_takeprofit_258 import simulate

REPO = Path(__file__).resolve().parent.parent
FACTS = REPO / "data" / "research" / "sec_facts_261"
OUT = REPO / "reports" / "leader_quality_261"
EXPECTED_DATA: str | None = "5938e5f2afd9a92315d56fb4f937a816048184ec05f66ef05c62eaf9cf273b30"
"""SEC 자료를 받은 뒤 문서 261 §1 에 적고 여기 고정한다."""
CAP_USD = 50e9
CAP_ASOF = "2026-08-31"
MIN_POOL = 30
STALE_DAYS = 200
Q_DAYS = (80, 100)
YOY_DAYS = (350, 380)
FROM = "2011-01-01"
"""SEC XBRL 이 대형주에 자리 잡은 뒤 — §1 의 날짜별 후보 수로 정한다."""
SPLIT = "2019-01-01"
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05
ARMS = ("BASE", "CAP", "CAP_EARN")
CANDIDATES = ("CAP", "CAP_EARN")


# ------------------------------------------------------------------ 자료


def data_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(FACTS.glob("*.json")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def floats(raw: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, float]]:
    """(공시일, 기준일, 달러) — 공시일 순, 같은 기준일은 먼저 공시된 것."""
    seen: dict[str, tuple[str, str, float]] = {}
    for r in raw:
        if r.get("filed") and r.get("end") and r.get("val"):
            key = str(r["end"])
            row = (str(r["filed"]), key, float(r["val"]))
            if key not in seen or row[0] < seen[key][0]:
                seen[key] = row
    return sorted(seen.values())


def quarters(raw: Sequence[Mapping[str, Any]]) -> list[tuple[str, str, float]]:
    """분기(80 ~ 100 일) 값 (공시일, 끝, 값) — 같은 끝은 먼저 공시된 것."""
    seen: dict[str, tuple[str, str, float]] = {}
    for r in raw:
        s, e, v, f = r.get("start"), r.get("end"), r.get("val"), r.get("filed")
        if not (s and e and f) or v is None:
            continue
        if not Q_DAYS[0] <= _days(str(s), str(e)) <= Q_DAYS[1]:
            continue
        row = (str(f), str(e), float(v))
        if str(e) not in seen or row[0] < seen[str(e)][0]:
            seen[str(e)] = row
    return sorted(seen.values())


def grew(qs: Sequence[tuple[str, str, float]], day: str) -> bool | None:
    """``day`` 에 알 수 있던 가장 최근 분기가 1 년 전 같은 분기보다 큰가. 모르면 None."""
    known = [q for q in qs if q[0] <= day]
    if not known:
        return None
    last = max(known, key=lambda q: q[1])
    if _days(last[1], day) > STALE_DAYS:
        return None
    prev = [q for q in known if YOY_DAYS[0] <= _days(q[1], last[1]) <= YOY_DAYS[1]]
    if not prev:
        return None
    return last[2] > prev[0][2]


class Fundamentals:
    """종목 → 유통 시가총액 · 매출 · 영업이익 분기 값."""

    def __init__(self, folder: Path = FACTS) -> None:
        self.float: dict[str, list[tuple[str, str, float]]] = {}
        self.rev: dict[str, list[tuple[str, str, float]]] = {}
        self.op: dict[str, list[tuple[str, str, float]]] = {}
        for p in sorted(folder.glob("*.json")):
            if p.stem.startswith("_"):
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            self.float[p.stem] = floats(d.get("float") or [])
            rev: list[Mapping[str, Any]] = []
            for rows in (d.get("revenue") or {}).values():
                rev.extend(rows)
            self.rev[p.stem] = quarters(rev)
            self.op[p.stem] = quarters(d.get("opinc") or [])

    def cap(
        self, sym: str, day: str, close: Sequence[float | None], days: Sequence[str]
    ) -> float | None:
        """그날 알 수 있던 마지막 유통 시가총액 × 그 뒤 가격 변화."""
        rows = [r for r in self.float.get(sym, []) if r[0] <= day]
        if not rows:
            return None
        _filed, asof, usd = max(rows, key=lambda r: r[1])
        i = bisect.bisect_right(days, asof) - 1
        t = bisect.bisect_right(days, day) - 1
        if i < 0 or t < 0:
            return None
        a, b = close[i], close[t]
        if a is None or b is None or a <= 0:
            return None
        return usd * b / a

    def growing(self, sym: str, day: str) -> bool:
        r, o = grew(self.rev.get(sym, []), day), grew(self.op.get(sym, []), day)
        return bool(r) and bool(o)


# ------------------------------------------------------------------ 고르기


def pick_in(
    close: Mapping[str, Sequence[float | None]],
    sector: Mapping[str, str],
    t: int,
    pool: Sequence[str],
    keep: Callable[[str], bool] | None = None,
    min_pool: int = MIN_POOL,
) -> list[str]:
    """``pool`` 안에서 113 계산(1 · 3 · 6 달 순위 점수 → 주도 섹터 3 → 점수 순),
    ``keep`` 통과만 10 개."""
    rets: dict[int, dict[str, float]] = {}
    for k in LOOKS:
        rets[k] = {}
        for s in pool:
            xs = close[s]
            a, b = xs[t], xs[t - k] if t - k >= 0 else None
            if a is not None and b is not None:
                rets[k][s] = a / b - 1
    common = set.intersection(*(set(v) for v in rets.values())) if rets else set()
    if len(common) < min_pool:
        return []
    score: dict[str, float] = defaultdict(float)
    for k in LOOKS:
        ordered = sorted(common, key=lambda s: (rets[k][s], s))
        n = len(ordered)
        for i, s in enumerate(ordered):
            score[s] += i / (n - 1) / len(LOOKS)
    by: dict[str, list[float]] = defaultdict(list)
    for s in common:
        by[sector.get(s, "Unknown")].append(score[s])
    top = sorted(by, key=lambda x: (statistics.median(by[x]), x), reverse=True)[:TOP_SECTORS]
    ranked = sorted(
        (s for s in common if sector.get(s, "Unknown") in top),
        key=lambda s: (score[s], s),
        reverse=True,
    )
    out = [s for s in ranked if keep is None or keep(s)]
    return out[:TOP_STOCKS]


def build(arm: str, fund: Fundamentals | None = None) -> Market:
    mk, _ = us_market()
    if arm == "BASE":
        return mk
    panel = build_us()
    f = fund or Fundamentals()
    spy_close = _spy_closes(panel.days)
    ref = spy_close[bisect.bisect_right(panel.days, CAP_ASOF) - 1]

    def pick(t: int) -> list[str]:
        day = panel.days[t]
        line = CAP_USD * spy_close[t] / ref
        pool = [
            s
            for s in panel.close
            if (c := f.cap(s, day, panel.close[s], panel.days)) is not None and c >= line
        ]
        keep = (lambda s: f.growing(s, day)) if arm == "CAP_EARN" else None
        return pick_in(panel.close, panel.sector, t, pool, keep)

    mk.pick = pick
    return mk


def _spy_closes(days: Sequence[str]) -> list[float]:
    from scripts.run_sb_breakout import SPY, load

    spy = load(SPY)
    m = dict(zip(spy.dates, spy.close, strict=True))
    return [m[d] for d in days]


# ------------------------------------------------------------------ 판정


def _row(span: Sequence[str], eq: Sequence[float]) -> dict[str, Any]:
    end = span[-1]
    return {
        "full": stats(span, eq, FROM, end),
        "first": stats(span, eq, FROM, "2018-12-31"),
        "second": stats(span, eq, SPLIT, end),
    }


def judge(rows: Mapping[str, Any]) -> str:
    """사전 등록 §3 (순수)."""
    base = rows["BASE"]
    ok = [
        c
        for c in CANDIDATES
        if rows[c]["full"]["sharpe"] >= base["full"]["sharpe"] + SHARPE_GAIN
        and all(rows[c][h]["sharpe"] >= base[h]["sharpe"] for h in ("first", "second"))
        and rows[c]["full"]["mdd"] <= base["full"]["mdd"] + MDD_SLACK
    ]
    if not ok:
        return "KEEP_BASE"
    return "ADOPT_" + max(ok, key=lambda c: rows[c]["full"]["sharpe"])


def coverage() -> dict[str, Any]:
    """해마다 1 월 말: 시총 기준 넘는 종목 수 · 실적 판단 가능 · 증가 수 (수익 안 봄)."""
    panel = build_us()
    f = Fundamentals()
    spy_close = _spy_closes(panel.days)
    ref = spy_close[bisect.bisect_right(panel.days, CAP_ASOF) - 1]
    out = {}
    for y in range(2009, 2027):
        t = bisect.bisect_right(panel.days, f"{y}-01-31") - 1
        day = panel.days[t]
        line = CAP_USD * spy_close[t] / ref
        big = [
            s
            for s in panel.close
            if (c := f.cap(s, day, panel.close[s], panel.days)) is not None and c >= line
        ]
        known = [
            s for s in big if grew(f.rev[s], day) is not None and grew(f.op[s], day) is not None
        ]
        out[day] = {
            "line_bn": round(line / 1e9, 1),
            "big": len(big),
            "earn_known": len(known),
            "growing": sum(1 for s in big if f.growing(s, day)),
            "with_float": sum(
                1 for s in panel.close if any(r[0] <= day for r in f.float.get(s, []))
            ),
        }
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if EXPECTED_DATA is None or data_hash() != EXPECTED_DATA:
        raise SystemExit("SEC 자료 해시가 문서 261 §1 과 다르다")
    f = Fundamentals()
    rows: dict[str, Any] = {}
    picks: dict[str, dict[str, list[str]]] = {}
    for arm in ARMS:
        mk = build(arm, f)
        start = next(i for i, d in enumerate(mk.days) if d >= FROM)
        span = mk.days[start:]
        r = simulate(mk, "ARM", start_date=FROM)
        rows[arm] = {**_row(span, r.equity), "trade": trade_stats(mk, r)}
        picks[arm] = {
            mk.days[t]: mk.pick(t)
            for t in range(len(mk.days) - 2, start, -1)
            if mk.days[t][:7] != mk.days[t + 1][:7]
        }
    out = {
        "git": sha,
        "data": EXPECTED_DATA,
        "verdict": judge(rows),
        "rows": rows,
        "last_picks": {a: dict(list(p.items())[:3]) for a, p in picks.items()},
        "note": "문서 261 1 회 판정. 113 S&P 500 명단(생존 편향), 2011 ~ 2026-08, 258 ARM 나가기.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "coverage":
        print(json.dumps(coverage(), ensure_ascii=False, indent=1))
        return 0
    if cmd == "run":
        print(json.dumps({"verdict": run()["verdict"]}, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
