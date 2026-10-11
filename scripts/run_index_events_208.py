"""문서 208 — S&P 500 편입 · 편출 강제 매매 사건 (롱만, 토스로 할 수 있는 형태).

    python -m scripts.run_index_events_208 audit   # 사건 수 · 가격 덮개 · 위약(가짜) 창 SD · 검정력
    python -m scripts.run_index_events_208 run     # 사전 등록 뒤 한 번 → reports/index_events_208/

자료: ``scripts.fetch_sp500_changes_208`` (위키백과 고정 판 · Tiingo)
+ 로컬 ``data/research/stocks``
· ``stocks_sp400``. 벤치마크 SPY (Tiingo 수정 가격).
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from auto_trading.research.index_events import (
    Px,
    Window,
    addition_window,
    by_date_mean,
    deletion_window,
    load_px,
    t_stat,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "sp500_events_208"
LOCAL = (REPO / "data" / "research" / "stocks", REPO / "data" / "research" / "stocks_sp400")
OUT = REPO / "reports" / "index_events_208"
START = date(2008, 1, 1)
END = date(2026, 8, 31)
HOLD_A = 5
COST_SIDE = 0.0012  # 토스 미국 주식 수수료 0.1% + 스프레드 · 미끄럼 0.02% (편도)
PLACEBO_GAP = 60  # 위약 창: 발표 60 거래일 앞에서 끝나는 같은 길이 창


def _price_file(t: str) -> Path | None:
    for name in (t, t.replace(".", "-")):
        for d in (*LOCAL, DATA / "prices"):
            p = d / f"{name}.csv"
            if p.exists():
                return p
    return None


def px(t: str) -> Px | None:
    p = _price_file(t)
    if p is None:
        return None
    with p.open(encoding="utf-8") as fh:
        return load_px(list(csv.DictReader(fh)))


def events() -> list[dict[str, Any]]:
    ev = json.loads((DATA / "events.json").read_text(encoding="utf-8"))
    return [e for e in ev if START <= date.fromisoformat(e["effective"]) <= END]


def additions(spy: Px) -> tuple[list[tuple[dict[str, Any], Window]], dict[str, int]]:
    out = []
    why = {"no_announcement": 0, "no_price": 0, "no_window": 0, "ok": 0}
    for e in events():
        t = e["added"]
        if not t:
            continue
        if not e["announced"]:
            why["no_announcement"] += 1
            continue
        p = px(t)
        if p is None:
            why["no_price"] += 1
            continue
        w = addition_window(
            p, spy, date.fromisoformat(e["announced"]), date.fromisoformat(e["effective"])
        )
        if w is None:
            why["no_window"] += 1
            continue
        why["ok"] += 1
        out.append((e, w))
    return out, why


def deletions(spy: Px) -> tuple[list[tuple[dict[str, Any], Window]], dict[str, int]]:
    """시가총액 사유 편출만 (인수 · 합병 편출은 거래가 끝남)."""
    out = []
    why = {"not_mcap": 0, "no_price": 0, "no_window": 0, "ok": 0}
    for e in events():
        t = e["removed"]
        if not t:
            continue
        if "market cap" not in e["reason"].lower():
            why["not_mcap"] += 1
            continue
        p = px(t)
        if p is None:
            why["no_price"] += 1
            continue
        w = deletion_window(p, spy, date.fromisoformat(e["effective"]), HOLD_A)
        if w is None:
            why["no_window"] += 1
            continue
        why["ok"] += 1
        out.append((e, w))
    return out, why


def placebo(p: Px, spy: Px, start: date, length: int, *, from_open: bool) -> float | None:
    """사건 전 ``PLACEBO_GAP`` 거래일 앞에서 끝나는 같은 길이 창의 초과 수익 (기준선 SD 용)."""
    a = p.first_on_or_after(start)
    if a is None:
        return None
    end_i = a - PLACEBO_GAP
    start_i = end_i - length
    if start_i < 0:
        return None
    s, e = p.days[start_i], p.days[end_i]
    sa, sb = spy.first_on_or_after(s), spy.first_on_or_after(e)
    if sa is None or sb is None or spy.days[sa] != s or spy.days[sb] != e:
        return None
    r = p.close[end_i] / (p.open[start_i] if from_open else p.close[start_i]) - 1
    b = spy.close[sb] / (spy.open[sa] if from_open else spy.close[sa]) - 1
    return r - b


def _length(p: Px, w: Window) -> int:
    return p.days.index(w.end) - p.days.index(w.start)


def audit() -> dict[str, Any]:
    spy = px("SPY")
    assert spy is not None
    adds, why_a = additions(spy)
    dels, why_d = deletions(spy)
    out: dict[str, Any] = {"additions": why_a, "deletions": why_d}
    for name, items, from_open in (("B_additions", adds, True), ("A_deletions", dels, False)):
        pl = []
        lengths = []
        for e, w in items:
            p = px(e["added"] if from_open else e["removed"])
            assert p is not None
            n = _length(p, w)
            lengths.append(n)
            x = placebo(p, spy, w.start, n, from_open=from_open)
            if x is not None:
                pl.append((w.start, x))
        units = by_date_mean(pl)
        sd = statistics.stdev(u for _, u in units) if len(units) > 1 else math.nan
        n_units = len(by_date_mean([(w.start, 0.0) for _, w in items]))
        by_year: dict[int, int] = {}
        for _, w in items:
            by_year[w.start.year] = by_year.get(w.start.year, 0) + 1
        out[name] = {
            "events": len(items),
            "date_units": n_units,
            "window_trading_days_median": statistics.median(lengths) if lengths else None,
            "placebo_units": len(units),
            "placebo_sd_excess": round(sd, 4),
            "mde_80pct_one_sided_2.5pct": round(2.8 * sd / math.sqrt(n_units), 4)
            if n_units
            else None,
            "round_trip_cost": 2 * COST_SIDE,
            "by_year": dict(sorted(by_year.items())),
        }
    return out


# ------------------------------------------------------------------ 판정 (사전 등록 뒤 한 번)


def _summ(units: Sequence[tuple[date, float]]) -> dict[str, Any]:
    xs = [x for _, x in units]
    if len(xs) < 2:
        return {"n": len(xs)}
    m, sd = statistics.fmean(xs), statistics.stdev(xs)
    se = sd / math.sqrt(len(xs))
    return {
        "n": len(xs),
        "mean": round(m, 5),
        "median": round(statistics.median(xs), 5),
        "t": round(t_stat(xs), 2),
        "ci95": [round(m - 1.96 * se, 5), round(m + 1.96 * se, 5)],
        "hit": round(sum(x > 0 for x in xs) / len(xs), 3),
        "worst": round(min(xs), 4),
        "best": round(max(xs), 4),
    }


def judge(units: Sequence[tuple[date, float]], t_needed: float) -> dict[str, Any]:
    """§4. units = 날짜 묶음별 순 초과 수익(비용 뺌)."""
    xs = [x for _, x in units]
    first = [x for d, x in units if d.year <= 2017]
    second = [x for d, x in units if d.year >= 2018]
    recent = [x for d, x in units if d.year >= 2022]
    by_year: dict[int, float] = {}
    for d, x in units:
        by_year[d.year] = by_year.get(d.year, 0.0) + x
    best = max(by_year, key=lambda y: by_year[y])
    rest = [x for d, x in units if d.year != best]
    crit = {
        "C1_mean_pos_t": statistics.fmean(xs) > 0 and t_stat(xs) >= t_needed,
        "C2_halves_pos": bool(first)
        and bool(second)
        and statistics.fmean(first) > 0
        and statistics.fmean(second) > 0,
        "C3_recent_2022_pos": bool(recent) and statistics.fmean(recent) > 0,
        "C4_without_best_year_pos": bool(rest) and statistics.fmean(rest) > 0,
    }
    if all(crit.values()):
        verdict = "PASS"
    elif statistics.fmean(xs) <= 0:
        verdict = "FAIL"
    else:
        verdict = "INCONCLUSIVE"
    return {
        "criteria": crit,
        "verdict": verdict,
        "best_year": best,
        "halves": [
            round(statistics.fmean(first), 5) if first else None,
            round(statistics.fmean(second), 5) if second else None,
        ],
        "recent_2022": round(statistics.fmean(recent), 5) if recent else None,
        "by_year_sum": {str(y): round(v, 4) for y, v in sorted(by_year.items())},
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    spy = px("SPY")
    assert spy is not None
    res: dict[str, Any] = {"computed": datetime.now(UTC).isoformat(timespec="seconds")}
    for name, (items, _why), t_needed in (
        ("B_additions", additions(spy), 2.24),
        ("A_deletions", deletions(spy), 2.0),
    ):
        gross = by_date_mean([(w.start, w.excess) for _, w in items])
        net = [(d, x - 2 * COST_SIDE) for d, x in gross]
        raw = by_date_mean([(w.start, w.ret) for _, w in items])
        r: dict[str, Any] = {
            "gross_excess": _summ(gross),
            "net_excess": _summ(net),
            "raw_return_gross": _summ(raw),
            "judgement": judge(net, t_needed),
        }
        if name == "B_additions":
            # 보고만: 발표 다음 날 시가에 이미 뛴 몫 (발표일 종가 → 다음 날 시가)
            jumps = []
            for e, w in items:
                p = px(e["added"])
                assert p is not None
                k = p.days.index(w.start)
                if k >= 1:
                    jumps.append((w.start, p.open[k] / p.close[k - 1] - 1))
            r["announcement_gap_open"] = _summ(by_date_mean(jumps))
        res[name] = r
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv == ["audit"]:
        res = audit()
    elif argv == ["run"]:
        res = run()
    else:
        raise SystemExit("usage: python -m scripts.run_index_events_208 audit|run")
    OUT.mkdir(parents=True, exist_ok=True)
    if argv == ["audit"]:
        (OUT / "audit.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    sys.stdout.write(json.dumps(res, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
