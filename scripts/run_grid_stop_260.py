"""문서 260 — 💹 '본전 + 비용 잠금 · 그리드로 손절선 올리기'(사용자 제안). 1회 판정.

사용 예::

    python -m scripts.run_grid_stop_260 run   # 깨끗한 트리 · 자료 해시 · 판정 파일 없음일 때만

판 · 진입 · 크기 · 대조(ARM = −3% 손절 + 추세 꺼짐 · 고점 −35%)는 258 · 259 와 같다.
**주 판정은 S&P 400**(114 자료 — 나가기 연구에 처음 씀), 국장 141 확인, S&P 500(113)은 참고.

* 비용선 = 진입가 × (1 + 0.6%) — 편도 0.25% × 2 + 여유 0.1%. 미국 양도세는 이익에만 붙어
  비용선에서 팔면 세금이 거의 0 이다.
* LOCK5: 종가가 +5% 에 한 번 닿으면 손절가를 비용선으로 올린다(그 뒤로는 안 올림).
* GRID5: 5% 그리드 — 최고 종가가 +5% 면 손절가 = 비용선, +10% 면 +5%, +15% 면 +10% … 한 칸씩.
* GRID10: 10% 그리드 — +10% 면 비용선, +20% 면 +10%, +30% 면 +20% …
* 손절가 확인: 미국 장중 저가(시가가 이미 아래면 시가 — 갭), 국장 종가.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import ExitRun, Market, _row, kr_market, trade_stats, us_market
from scripts.run_lt_kr import month_end_day
from scripts.run_lt_leaders import FEE, FROM, STOP_FROM_PEAK, trend_off, trend_up
from scripts.run_takeprofit_258 import MAX_POS, STOP, WEIGHT, Pos

from scripts import run_exit_242 as e242
from scripts import run_lt_leaders as lt

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "grid_stop_260"
SP400 = REPO / "data" / "research" / "stocks_sp400"
SP400_AUDIT = REPO / "reports" / "lt_sp400" / "audit.json"
SP400_SHA256 = "ef82b7c25fd368cdc7e627388b603db7a458a595942036f3f3a17e8fa4acd232"
"""문서 114 에 고정된 S&P 400 유니버스 해시."""
COST_LINE = 2 * FEE + 0.001
ARMS = ("ARM", "LOCK5", "GRID5", "GRID10")
CANDIDATES = ("LOCK5", "GRID5", "GRID10")
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05


def grid_floor(entry: float, peak: float, step: float) -> float | None:
    """그리드 손절가: 최고 종가가 +step·k (k ≥ 1) 에 닿았으면
    k = 1 은 비용선, k ≥ 2 는 +step·(k−1)."""
    k = math.floor((peak / entry - 1) / step + 1e-9)
    if k < 1:
        return None
    if k == 1:
        return entry * (1 + COST_LINE)
    return entry * (1 + max(COST_LINE, step * (k - 1)))


def floor_for(arm: str) -> Callable[[float, float], float | None]:
    if arm == "LOCK5":
        return lambda e, pk: e * (1 + COST_LINE) if pk >= e * 1.05 - 1e-9 else None
    if arm == "GRID5":
        return lambda e, pk: grid_floor(e, pk, 0.05)
    if arm == "GRID10":
        return lambda e, pk: grid_floor(e, pk, 0.10)
    return lambda _e, _pk: None


def simulate(mk: Market, arm: str, start_date: str = FROM) -> ExitRun:
    days = mk.days
    start = next(i for i, d in enumerate(days) if d >= start_date)
    cash = 1.0
    pos: dict[str, Pos] = {}
    run = ExitRun()
    pending_sell: set[str] = set()
    pending_buy: list[str] | None = None
    floor = floor_for(arm)

    def px(sym: str, t: int) -> float:
        xs = mk.close[sym]
        while xs[t] is None:
            t -= 1
        return xs[t]  # type: ignore[return-value]

    def sell(sym: str, t: int, price: float, reason: str, line: float = 0.0) -> None:
        nonlocal cash
        p = pos.pop(sym)
        proceeds = p.qty * price * (1 - FEE)
        cash += proceeds
        run.trades.append(
            {
                "symbol": sym,
                "entry_i": p.i0,
                "exit_i": t,
                "entry_px": p.entry,
                "exit_px": price,
                "ret": proceeds / p.cost - 1,
                "w": WEIGHT,
                "reason": reason,
                "line": line,
                "peak": p.peak,
            }
        )

    for t in range(start, len(days)):
        for sym in sorted(pending_sell):
            c = mk.close[sym][t]
            if sym in pos and c is not None:
                sell(sym, t, c, "slow")
        pending_sell = {s for s in pending_sell if s in pos}
        equity = cash + sum(p.qty * px(s, t) for s, p in pos.items())
        if pending_buy is not None:
            for sym in pending_buy:
                c = mk.close[sym][t]
                if c is None or sym in pos or len(pos) >= MAX_POS or cash <= 0:
                    continue
                amt = min(WEIGHT * equity, cash)
                pos[sym] = Pos(amt * (1 - FEE) / c, amt, c, t, c, c * (1 - STOP))
                cash -= amt
            pending_buy = None
        for sym in sorted(pos):
            if mk.ends_at(sym, t):
                pending_sell.discard(sym)
                sell(sym, t, px(sym, t), "delisted")
        for sym in sorted(pos):
            p = pos[sym]
            c = mk.close[sym][t]
            if t <= p.i0 or c is None:
                continue
            lo = mk.low[sym][t] if mk.low is not None else None
            op = mk.open[sym][t] if mk.open is not None else None
            why = "grid" if p.trailing else "stop"
            if lo is not None:
                if lo <= p.stop:
                    price = op if op is not None and op < p.stop else p.stop
                    sell(sym, t, price, why, p.stop)
            elif c <= p.stop:
                sell(sym, t, c, why, p.stop)
        for sym in sorted(pos):
            p = pos[sym]
            c = mk.close[sym][t]
            if c is None:
                continue
            p.peak = max(p.peak, c)
            if t <= p.i0:
                continue
            f = floor(p.entry, p.peak)
            if f is not None and f > p.stop:
                p.trailing = True
                p.stop = f
            if trend_off(mk.close[sym], t) or c <= (1 - STOP_FROM_PEAK) * p.peak:
                pending_sell.add(sym)
        if month_end_day(days, t) and mk.index_ok[t]:
            pending_buy = [
                s
                for s in mk.pick(t)
                if s not in pos and s not in pending_sell and trend_up(mk.close[s], t)
            ]
        run.equity.append(cash + sum(p.qty * px(s, t) for s, p in pos.items()))
    return run


def protect_stats(run: ExitRun) -> dict[str, Any]:
    """'+5% 넘게 올랐다가 손해로 끝' · 그리드 매도가 선 아래로 체결(갭)된 경우."""
    up = [x for x in run.trades if x["peak"] >= x["entry_px"] * 1.05]
    grid = [x for x in run.trades if x["reason"] == "grid"]
    gap = [x for x in grid if x["exit_px"] < x["line"] * (1 - 1e-9)]
    return {
        "up5_trades": len(up),
        "up5_then_loss": sum(1 for x in up if x["ret"] <= 0),
        "grid_exits": len(grid),
        "grid_gap_below_line": len(gap),
        "grid_gap_worst": round(min((x["exit_px"] / x["line"] - 1 for x in gap), default=0.0), 4),
        "grid_loss_exits": sum(1 for x in grid if x["ret"] <= 0),
    }


def passes(x: dict[str, Any], base: dict[str, Any]) -> bool:
    return (
        x["full"]["sharpe"] >= base["full"]["sharpe"] + SHARPE_GAIN
        and all(x[h]["sharpe"] >= base[h]["sharpe"] for h in ("first", "second"))
        and x["full"]["mdd"] <= base["full"]["mdd"] + MDD_SLACK
    )


def judge(main: dict[str, Any], kr: dict[str, Any]) -> dict[str, Any]:
    """사전 등록 §3 (순수). main = S&P 400."""
    ok = [c for c in CANDIDATES if passes(main[c], main["ARM"])]
    both = [c for c in ok if kr[c]["full"]["sharpe"] >= kr["ARM"]["full"]["sharpe"]]
    if both:
        best = max(both, key=lambda c: main[c]["full"]["sharpe"])
        return {"verdict": f"ADOPT_{best}", "main_pass": ok, "both_pass": both}
    if ok:
        return {"verdict": "SP400_ONLY", "main_pass": ok, "both_pass": []}
    return {"verdict": "KEEP_ARM", "main_pass": [], "both_pass": []}


def sp400_market() -> Market:
    """113 판 코드를 S&P 400 자료로 (114 와 같은 방식 — 모듈 경로만 바꿨다가 되돌린다)."""
    old = (lt.STOCKS, lt.AUDIT, lt.UNIVERSE_SHA256, getattr(e242, "STOCKS"))  # noqa: B009
    lt.STOCKS, lt.AUDIT, lt.UNIVERSE_SHA256 = SP400, SP400_AUDIT, SP400_SHA256
    setattr(e242, "STOCKS", SP400)  # noqa: B010 — 242 의 시가 · 저가 폴더
    try:
        return us_market()[0]
    finally:
        lt.STOCKS, lt.AUDIT, lt.UNIVERSE_SHA256 = old[:3]
        setattr(e242, "STOCKS", old[3])  # noqa: B010


def evaluate(mk: Market) -> dict[str, Any]:
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    span = mk.days[start:]
    rows: dict[str, Any] = {}
    for arm in ARMS:
        r = simulate(mk, arm)
        rows[arm] = {
            **_row(span, r.equity),
            "trade": trade_stats(mk, r),
            "protect": protect_stats(r),
        }
    return rows


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    sp400 = evaluate(sp400_market())
    kr = evaluate(kr_market()[0])
    sp500 = evaluate(us_market()[0])
    out = {
        "git": sha,
        **judge(sp400, kr),
        "sp400": sp400,
        "kr": kr,
        "sp500": sp500,
        "note": "문서 260 1 회 판정. S&P 400 주 판정 · 국장 확인 · S&P 500 참고(9 후보 본 판).",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv[1:] != ["run"]:
        print(__doc__)
        return 2
    out = run()
    print(json.dumps({"verdict": out["verdict"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
