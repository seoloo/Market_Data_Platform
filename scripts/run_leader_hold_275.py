"""문서 275 — 274 GBM 주도주를 6 달 보유(나눠 사기)로: A_EQ · B_IV · C_MKT. 1 회 판정.

사용 예::

    PYTHONPATH="src;.;.venv/Lib/site-packages" build/tcn-venv/Scripts/python.exe \
        -m scripts.run_leader_hold_275 run

고르기는 274 그대로 다시 계산(마지막 고르기가 274 결과와 같은지 확인), 나가기만 새 규칙.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_leader_quality_261 import _spy_closes
from scripts.run_lt_kr import month_end_day
from scripts.run_lt_leaders import FEE, stats

from scripts import run_leader_ml_274 as m274

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "leader_hold_275"
R274 = REPO / "reports" / "leader_ml_274" / "result.json"
START = "2016-01-01"
HALF_END, HALF2 = "2020-12-31", "2021-01-01"
HOLD = 126
SLOTS = 6
VOL_DAYS = 63
ARMS = ("A_EQ", "B_IV", "C_MKT")
PICKERS = ("GBM", "LR", "BASE", "EW")


@dataclass
class HoldRun:
    equity: list[float] = field(default_factory=list)
    trades: list[float] = field(default_factory=list)
    """포지션별 수익(비용 뒤)."""


def _px(xs: Sequence[float | None], t: int) -> float | None:
    while t >= 0 and xs[t] is None:
        t -= 1
    return xs[t] if t >= 0 else None


def simulate_hold(
    days: Sequence[str],
    close: Mapping[str, Sequence[float | None]],
    picks: Mapping[int, Sequence[str]],
    start: str,
    *,
    weights: Callable[[int, Sequence[str]], list[float]] | None = None,
    buy_ok: Callable[[int], bool] = lambda _t: True,
    fee: float = FEE,
    hold: int = HOLD,
    slots: int = SLOTS,
) -> HoldRun:
    """월말 고르기 → 다음 거래일 종가에 (평가액 ÷ slots) 만큼 사서 hold 거래일 뒤 종가에 판다."""
    s0 = next(i for i, d in enumerate(days) if d >= start)
    cash = 1.0
    lots: list[tuple[int, str, float, float]] = []  # (파는 날, 종목, 수량, 산 금액)
    pending: tuple[list[str], list[float]] | None = None
    run = HoldRun()

    def value(t: int) -> float:
        return sum(q * (_px(close[s], t) or 0.0) for _e, s, q, _c in lots)

    for t in range(s0, len(days)):
        keep = []
        for lot in lots:
            exit_t, sym, qty, cost = lot
            if exit_t == t:
                price = _px(close[sym], t) or 0.0
                proceeds = qty * price * (1 - fee)
                cash += proceeds
                run.trades.append(proceeds / cost - 1)
            else:
                keep.append(lot)
        lots = keep
        if pending is not None:
            syms, ws = pending
            budget = min(cash, (cash + value(t)) / slots)
            for sym, w in zip(syms, ws, strict=True):
                c = close[sym][t]
                if c is None or w <= 0:
                    continue
                amt = budget * w
                lots.append((t + hold, sym, amt * (1 - fee) / c, amt))
                cash -= amt
            pending = None
        if t + 1 < len(days) and month_end_day(days, t) and t in picks and buy_ok(t):
            syms = [s for s in picks[t] if close[s][t] is not None]
            if syms:
                ws = weights(t, syms) if weights else [1 / len(syms)] * len(syms)
                pending = (syms, ws)
        run.equity.append(cash + value(t))
    return run


def inverse_vol(
    close: Mapping[str, Sequence[float | None]],
) -> Callable[[int, Sequence[str]], list[float]]:
    def w(t: int, syms: Sequence[str]) -> list[float]:
        vols: dict[str, float] = {}
        for s in syms:
            xs = close[s][t - VOL_DAYS : t + 1]
            if t >= VOL_DAYS and all(x is not None for x in xs):
                c = [x for x in xs if x is not None]
                v = statistics.pstdev(c[i] / c[i - 1] - 1 for i in range(1, len(c)))
                if v > 0:
                    vols[s] = v
        fill = statistics.fmean(vols.values()) if vols else 1.0
        inv = [1 / vols.get(s, fill) for s in syms]
        total = sum(inv)
        return [x / total for x in inv]

    return w


def summarize(days: Sequence[str], run: HoldRun) -> dict[str, Any]:
    s0 = next(i for i, d in enumerate(days) if d >= START)
    span = list(days[s0:])
    eq = run.equity
    yearly: dict[str, float] = {}
    monthly: list[float] = []
    prev_y, prev_m = eq[0], eq[0]
    for i, d in enumerate(span):
        last = i + 1 == len(span) or span[i + 1][:4] != d[:4]
        if last:
            yearly[d[:4]] = eq[i] / prev_y - 1
            prev_y = eq[i]
        if i + 1 == len(span) or span[i + 1][:7] != d[:7]:
            monthly.append(eq[i] / prev_m - 1)
            prev_m = eq[i]
    years = (len(span) - 1) / 252
    return {
        "full": stats(span, eq, START, span[-1]),
        "first": stats(span, eq, START, HALF_END),
        "second": stats(span, eq, HALF2, span[-1]),
        "yearly": {k: round(v, 4) for k, v in yearly.items()},
        "worst_month": round(min(monthly), 4),
        "positions": len(run.trades),
        "positions_per_year": round(len(run.trades) / years, 1),
        "win_rate": round(sum(r > 0 for r in run.trades) / len(run.trades), 4) if run.trades else 0,
        "mean_position_ret": round(statistics.fmean(run.trades), 4) if run.trades else 0,
    }


def judge(res: Mapping[str, Any], arm: str) -> dict[str, Any]:
    """문서 275 §3 (순수)."""

    def ok(u: str) -> dict[str, bool]:
        g, b = res[u]["arms"][arm]["GBM"], res[u]["arms"][arm]["BASE"]
        return {
            "sharpe_gain": g["full"]["sharpe"] >= b["full"]["sharpe"] + 0.10,
            "halves": all(g[h]["sharpe"] >= b[h]["sharpe"] for h in ("first", "second")),
            "mdd": g["full"]["mdd"] <= b["full"]["mdd"] + 0.05,
            "beats_spy": g["full"]["sharpe"] > res[u]["spy"]["full"]["sharpe"],
        }

    checks = {u: ok(u) for u in ("U1", "U2")}
    if all(all(c.values()) for c in checks.values()):
        status = "후속 가치(탐색)"
    elif all(
        res[u]["arms"][arm]["GBM"]["full"]["sharpe"] < res[u]["arms"][arm]["BASE"]["full"]["sharpe"]
        for u in ("U1", "U2")
    ):
        status = "불리"
    else:
        status = "불명"
    return {"status": status, "checks": checks}


def evaluate(universe: str, r274: Mapping[str, Any]) -> dict[str, Any]:
    rows, mk = m274.build_rows(universe)
    prob = m274.walk_forward(rows, mk.days)
    test_t = {r["t"] for r in rows if r["day"] >= START}
    pickers: dict[str, dict[int, list[str]]] = {
        "GBM": m274.model_picks(rows, prob["GBM"]),
        "LR": m274.model_picks(rows, prob["LR"]),
        "BASE": m274.base_picks(rows, mk),
    }
    ew: dict[int, list[str]] = {}
    for r in rows:
        ew.setdefault(r["t"], []).append(r["sym"])
    pickers["EW"] = ew
    pickers = {k: {t: v for t, v in p.items() if t in test_t} for k, p in pickers.items()}
    want = r274["results"][universe]["last_picks"]
    for name in ("GBM", "LR", "BASE"):
        got = {mk.days[t]: pickers[name][t] for t in sorted(pickers[name])[-3:]}
        if got != want[name]:
            raise SystemExit(f"{universe} {name} 고르기가 274 와 다르다")
    iv = inverse_vol(mk.close)
    arms_cfg: dict[str, dict[str, Any]] = {
        "A_EQ": {},
        "B_IV": {"weights": iv},
        "C_MKT": {"buy_ok": lambda t: bool(mk.index_ok[t])},
    }
    out: dict[str, Any] = {"arms": {}, "cost2": {}}
    for arm, cfg in arms_cfg.items():
        out["arms"][arm] = {}
        out["cost2"][arm] = {}
        for name, p in pickers.items():
            run = simulate_hold(mk.days, mk.close, p, START, **cfg)
            out["arms"][arm][name] = summarize(mk.days, run)
            run2 = simulate_hold(mk.days, mk.close, p, START, fee=2 * FEE, **cfg)
            out["cost2"][arm][name] = stats(
                [d for d in mk.days if d >= START], run2.equity, START, mk.days[-1]
            )
        print(json.dumps({"universe": universe, "arm": arm}), flush=True)
    spy = _spy_closes(mk.days)
    s0 = next(i for i, d in enumerate(mk.days) if d >= START)
    spy_eq = [c / spy[s0] * (1 - FEE) for c in spy[s0:]]
    out["spy"] = summarize(mk.days, HoldRun(spy_eq, []))
    out["arm_274"] = {
        k: r274["results"][universe]["account"][k]["full"] for k in ("GBM", "LR", "BASE")
    }
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
        "verdict": {arm: judge(res, arm) for arm in ARMS},
        "results": res,
        "note": "문서 275 1 회. 274 고르기 그대로, 6 달 나눠 사기 · 손절 없음. 생존 편향 자료.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "run":
        r = run()
        print(json.dumps({a: v["status"] for a, v in r["verdict"].items()}, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
