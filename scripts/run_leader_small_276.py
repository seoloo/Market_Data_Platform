"""문서 276 — 미장 주도주 6 달 보유를 소액 · 1 주 단위에 맞게 줄이기 (R10x6 · R5x6 · R5x3 · R3x3).

사용 예::

    PYTHONPATH="src;.;.venv/Lib/site-packages" build/tcn-venv/Scripts/python.exe \
        -m scripts.run_leader_small_276 run
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_leader_hold_275 import START, HoldRun, simulate_hold, summarize
from scripts.run_leader_quality_261 import _spy_closes
from scripts.run_lt_leaders import FEE

from scripts import run_leader_ml_274 as m274

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "leader_small_276"
R274 = REPO / "reports" / "leader_ml_274" / "result.json"
PLANS: dict[str, tuple[int, int]] = {
    "R10x6": (10, 1),
    "R5x6": (5, 1),
    "R5x3": (5, 2),
    "R3x3": (3, 2),
}
"""판 → (고르는 수, 몇 달마다 사나). 동시 묶음 = 6 ÷ 간격."""
SMALL = ("R5x6", "R5x3", "R3x3")
KRW_PER_USD = 1400.0
CAPITALS_KRW = (10_000_000, 20_000_000, 30_000_000, 50_000_000, 80_000_000)
FEAS_FROM, FEAS_TO = "2024-09-01", "2026-08-31"
MISS_MAX, DRAG_MAX = 0.05, 0.15
KEEP_GAP = 0.15


def buys_this_month(day: str, every: int) -> bool:
    """두 달마다면 홀수 달(1 · 3 · 5 …) 말에만."""
    return every == 1 or int(day[5:7]) % every == 1


def thin(
    picks: Mapping[int, Sequence[str]], days: Sequence[str], n: int, every: int
) -> dict[int, list[str]]:
    return {t: list(p[:n]) for t, p in picks.items() if buys_this_month(days[t], every)}


def whole_shares(capital_usd: float, slots: int, prices: Sequence[float]) -> tuple[int, float]:
    """한 번 사는 몫 = 자본 ÷ slots, 종목마다 같은 목표. (못 사는 종목 수, 남는 돈 비율)."""
    budget = capital_usd / slots
    target = budget / len(prices)
    spent, missed = 0.0, 0
    for p in prices:
        q = math.floor(target / p)
        missed += q == 0
        spent += q * p
    return missed, 1 - spent / budget


def feasibility(
    picks: Mapping[int, Sequence[str]],
    days: Sequence[str],
    close: Mapping[str, Sequence[float | None]],
    slots: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sel = [t for t in sorted(picks) if FEAS_FROM <= days[t] <= FEAS_TO and picks[t]]
    for krw in CAPITALS_KRW:
        miss = total = 0
        drags = []
        for t in sel:
            prices = [c for s in picks[t] if (c := close[s][t]) is not None]
            m, d = whole_shares(krw / KRW_PER_USD, slots, prices)
            miss += m
            total += len(prices)
            drags.append(d)
        out[str(krw)] = {
            "missed_share": miss / total if total else None,
            "cash_drag_mean": statistics.fmean(drags) if drags else None,
            "cash_drag_worst": max(drags) if drags else None,
            "decisions": len(sel),
        }
    ok = [
        int(k)
        for k, v in out.items()
        if v["missed_share"] is not None
        and v["missed_share"] <= MISS_MAX
        and v["cash_drag_mean"] <= DRAG_MAX
    ]
    out["needed_krw"] = min(ok) if ok else None
    return out


def judge(res: Mapping[str, Any]) -> dict[str, Any]:
    """문서 276 §4 (순수)."""

    def ok(u: str, plan: str) -> dict[str, bool]:
        g, b = res[u]["plans"][plan]["GBM"], res[u]["plans"][plan]["BASE"]
        full = res[u]["plans"]["R10x6"]["GBM"]["full"]["sharpe"]
        return {
            "sharpe_gain": g["full"]["sharpe"] >= b["full"]["sharpe"] + 0.10,
            "halves": all(g[h]["sharpe"] >= b[h]["sharpe"] for h in ("first", "second")),
            "beats_spy": g["full"]["sharpe"] > res[u]["spy"]["full"]["sharpe"],
            "keeps_r10x6": g["full"]["sharpe"] >= full - KEEP_GAP,
        }

    checks = {p: {u: ok(u, p) for u in ("U1", "U2")} for p in SMALL}
    passed = [p for p in SMALL if all(all(c.values()) for c in checks[p].values())]

    def need(p: str) -> float:
        v = res["U2"]["feasibility"][p]["needed_krw"]
        return float("inf") if v is None else float(v)

    pick = min(passed, key=lambda p: (need(p), -PLANS[p][0] * 6 // PLANS[p][1])) if passed else None
    return {
        "status": f"시범 후보 {pick}" if pick else "축소 불가",
        "passed": passed,
        "checks": checks,
    }


def evaluate(universe: str, r274: Mapping[str, Any]) -> dict[str, Any]:
    rows, mk = m274.build_rows(universe)
    prob = m274.walk_forward(rows, mk.days)
    test_t = {r["t"] for r in rows if r["day"] >= START}
    pickers = {
        "GBM": m274.model_picks(rows, prob["GBM"]),
        "BASE": m274.base_picks(rows, mk),
    }
    pickers = {k: {t: v for t, v in p.items() if t in test_t} for k, p in pickers.items()}
    want = r274["results"][universe]["last_picks"]
    for name in ("GBM", "BASE"):
        got = {mk.days[t]: pickers[name][t] for t in sorted(pickers[name])[-3:]}
        if got != want[name]:
            raise SystemExit(f"{universe} {name} 고르기가 274 와 다르다")
    out: dict[str, Any] = {"plans": {}, "feasibility": {}}
    for plan, (n, every) in PLANS.items():
        slots = 6 // every
        out["plans"][plan] = {}
        for name, p in pickers.items():
            sel = thin(p, mk.days, n, every)
            run = simulate_hold(mk.days, mk.close, sel, START, slots=slots)
            out["plans"][plan][name] = summarize(mk.days, run)
        out["feasibility"][plan] = feasibility(
            thin(pickers["GBM"], mk.days, n, every), mk.days, mk.close, slots
        )
        print(json.dumps({"universe": universe, "plan": plan}), flush=True)
    spy = _spy_closes(mk.days)
    s0 = next(i for i, d in enumerate(mk.days) if d >= START)
    out["spy"] = summarize(mk.days, HoldRun([c / spy[s0] * (1 - FEE) for c in spy[s0:]], []))
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    r274 = json.loads(R274.read_text(encoding="utf-8"))
    res = {u: evaluate(u, r274) for u in ("U1", "U2")}
    out = {
        "git": sha,
        "completed_utc": datetime.now(UTC).isoformat(),
        "verdict": judge(res),
        "results": res,
        "note": "문서 276 1 회. 274 고르기 × 6 달 보유, 소액 축소판 · 1 주 단위 필요 금액.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "run":
        r = run()
        print(json.dumps(r["verdict"]["status"], ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
