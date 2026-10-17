"""문서 259 — 💹 이익 보호 보강: 오른 종목이 되돌아와 −3% 로 손절되는 것을 막는 세 방법. 1회 판정.

사용 예::

    python -m scripts.run_protect_259 run   # 깨끗한 트리 · 141 자료 해시 · 판정 파일 없음일 때만

판 · 진입 · 크기 · 대조는 258 과 같다(256 T 진입, −3% 손절 + 113 느린 나가기 = ARM).
보강만 다르다 (손절가 확인은 미국 장중 저가, 국장 종가 — 258 의 따라 올림과 같은 방식):

* BE10: 종가가 진입가 +10% 에 한 번 닿으면 손절가를 **진입가(본전)** 로 올린다.
* STEP20: 계단식 — 종가가 +20% · +40% · +60% … 에 닿을 때마다 손절가를 한 계단 아래
  (진입가 · +20% · +40% …)로 올린다. 내려가지 않는다.
* MA50: 종가가 +20% 에 한 번 닿은 뒤로는 종가 < 50 일선이면 다음 거래일 종가에 판다.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import ExitRun, Market, _row, kr_market, sma, trade_stats, us_market
from scripts.run_lt_kr import month_end_day
from scripts.run_lt_leaders import FEE, FROM, STOP_FROM_PEAK, trend_off, trend_up
from scripts.run_takeprofit_258 import MAX_POS, STOP, WEIGHT, Pos

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "protect_259"
BE_GAIN = 0.10
STEP = 0.20
MA_GAIN = 0.20
MA_N = 50
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05
ARMS = ("ARM", "BE10", "STEP20", "MA50")
CANDIDATES = ("BE10", "STEP20", "MA50")


def step_floor(entry: float, peak: float) -> float | None:
    """계단식 손절가: 최고 종가가 +20k% (k ≥ 1) 에 닿았으면 진입가 × (1 + 0.2(k−1)). 아니면 None."""
    k = math.floor((peak / entry - 1) / STEP + 1e-9)
    if k < 1:
        return None
    return entry * (1 + STEP * (k - 1))


def simulate(mk: Market, arm: str, start_date: str = FROM) -> ExitRun:
    days = mk.days
    start = next(i for i, d in enumerate(days) if d >= start_date)
    cash = 1.0
    pos: dict[str, Pos] = {}
    run = ExitRun()
    pending_sell: set[str] = set()
    pending_buy: list[str] | None = None

    def px(sym: str, t: int) -> float:
        xs = mk.close[sym]
        while xs[t] is None:
            t -= 1
        return xs[t]  # type: ignore[return-value]

    def sell(sym: str, t: int, price: float, reason: str) -> None:
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
            why = "protect" if p.trailing else "stop"
            if lo is not None:
                if lo <= p.stop:
                    sell(sym, t, op if op is not None and op < p.stop else p.stop, why)
            elif c <= p.stop:
                sell(sym, t, c, why)
        for sym in sorted(pos):
            p = pos[sym]
            c = mk.close[sym][t]
            if c is None:
                continue
            p.peak = max(p.peak, c)
            if t <= p.i0:
                continue
            if arm == "BE10" and c >= p.entry * (1 + BE_GAIN):
                p.trailing = True
                p.stop = max(p.stop, p.entry)
            if arm == "STEP20" and (floor := step_floor(p.entry, p.peak)) is not None:
                p.trailing = True
                p.stop = max(p.stop, floor)
            if arm == "MA50":
                if c >= p.entry * (1 + MA_GAIN):
                    p.trailing = True
                m = sma(mk.close[sym], t, MA_N)
                if p.trailing and m is not None and c < m:
                    pending_sell.add(sym)
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


def passes(x: dict[str, Any], base: dict[str, Any]) -> bool:
    return (
        x["full"]["sharpe"] >= base["full"]["sharpe"] + SHARPE_GAIN
        and all(x[h]["sharpe"] >= base[h]["sharpe"] for h in ("first", "second"))
        and x["full"]["mdd"] <= base["full"]["mdd"] + MDD_SLACK
    )


def judge(us: dict[str, Any], kr: dict[str, Any]) -> dict[str, Any]:
    """사전 등록 §3 (순수, 258 과 같은 틀)."""
    us_ok = [c for c in CANDIDATES if passes(us[c], us["ARM"])]
    both = [c for c in us_ok if kr[c]["full"]["sharpe"] >= kr["ARM"]["full"]["sharpe"]]
    if both:
        best = max(both, key=lambda c: us[c]["full"]["sharpe"])
        return {"verdict": f"ADOPT_{best}", "us_pass": us_ok, "both_pass": both}
    if us_ok:
        return {"verdict": "US_ONLY", "us_pass": us_ok, "both_pass": []}
    return {"verdict": "KEEP_ARM", "us_pass": [], "both_pass": []}


def gave_back(mk: Market, run: ExitRun) -> dict[str, Any]:
    """'오른 뒤 되돌아와 손해로 끝난' 거래: 진입 뒤 최고 종가 ≥ +20% 인데 수익 ≤ 0."""
    n = bad = 0
    for x in run.trades:
        xs = [v for v in mk.close[x["symbol"]][x["entry_i"] : x["exit_i"] + 1] if v is not None]
        if xs and max(xs) >= x["entry_px"] * 1.2:
            n += 1
            bad += x["ret"] <= 0
    return {"up20_trades": n, "up20_then_loss": bad, "share": round(bad / n, 3) if n else None}


def evaluate(mk: Market) -> dict[str, Any]:
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    span = mk.days[start:]
    rows: dict[str, Any] = {}
    for arm in ARMS:
        r = simulate(mk, arm)
        rows[arm] = {**_row(span, r.equity), "trade": trade_stats(mk, r), "gave": gave_back(mk, r)}
    return rows


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    us = evaluate(us_market()[0])
    kr = evaluate(kr_market()[0])
    out = {
        "git": sha,
        **judge(us, kr),
        "us": us,
        "kr": kr,
        "note": "문서 259 1 회 판정. 258 과 같은 판 · 대조(ARM). 미국 주 판정 · 국장 확인.",
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
