"""문서 258 — 💹 개별주 익절: −3% 손절은 그대로 두고 '언제 이익을 챙기나'. 1회 판정.

사용 예::

    python -m scripts.run_takeprofit_258 run   # 깨끗한 트리 · 141 자료 해시 · 판정 파일 없음일 때만

판 · 진입 · 크기는 256 의 T(월말 주도주 10 중 추세 · 지수 200 일선 위 · 다음 거래일 종가 ·
10% · 최대 10). 모든 팔: 진입가 −3% 손절(미국 장중 저가, 국장 종가) + 113 느린 나가기
(추세 꺼짐 · 진입 뒤 최고 종가 −35%, 다음 거래일 종가). 익절만 다르다.

* ARM (대조, 256 T_ARM): 익절 없음.
* TP20: 종가가 진입가 +20% 이상이면 그 종가에 전부 판다.
* HALF20: 종가가 +20% 이상인 첫날 그 종가에 절반, 나머지는 ARM 대로.
* TRAIL20: 종가가 +20% 에 한 번 닿으면 손절가를 '진입 뒤 최고 종가 × 0.85' 로 올리고 따라 올린다
  (미국 장중 저가 · 국장 종가로 확인, 진입가 아래로는 안 내림).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import ExitRun, Market, _row, kr_market, trade_stats, us_market
from scripts.run_lt_kr import month_end_day
from scripts.run_lt_leaders import FEE, FROM, STOP_FROM_PEAK, trend_off, trend_up

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "takeprofit_258"
STOP = 0.03
WEIGHT = 0.10
MAX_POS = 10
GAIN = 0.20
TRAIL = 0.15
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05
ARMS = ("ARM", "TP20", "HALF20", "TRAIL20")
CANDIDATES = ("TP20", "HALF20", "TRAIL20")


@dataclass(slots=True)
class Pos:
    qty: float
    cost: float
    entry: float
    i0: int
    peak: float
    stop: float
    halved: bool = False
    trailing: bool = False


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

    def sell(sym: str, t: int, price: float, reason: str, part: float = 1.0) -> None:
        nonlocal cash
        p = pos[sym]
        qty, cost = p.qty * part, p.cost * part
        proceeds = qty * price * (1 - FEE)
        cash += proceeds
        run.trades.append(
            {
                "symbol": sym,
                "entry_i": p.i0,
                "exit_i": t,
                "entry_px": p.entry,
                "exit_px": price,
                "ret": proceeds / cost - 1,
                "w": WEIGHT * part,
                "reason": reason,
            }
        )
        if part >= 1.0:
            pos.pop(sym)
        else:
            p.qty -= qty
            p.cost -= cost

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
        # 장중 손절 (진입 다음 거래일부터) — 손절가는 −3% 또는 따라 올린 값
        for sym in sorted(pos):
            p = pos[sym]
            c = mk.close[sym][t]
            if t <= p.i0 or c is None:
                continue
            lo = mk.low[sym][t] if mk.low is not None else None
            op = mk.open[sym][t] if mk.open is not None else None
            why = "trail" if p.trailing else "stop"
            if lo is not None:
                if lo <= p.stop:
                    sell(sym, t, op if op is not None and op < p.stop else p.stop, why)
            elif c <= p.stop:
                sell(sym, t, c, why)
        # 오늘 종가로: 익절 · 따라 올림 · 느린 나가기
        for sym in sorted(pos):
            p = pos[sym]
            c = mk.close[sym][t]
            if c is None or t <= p.i0:
                if c is not None:
                    p.peak = max(p.peak, c)
                continue
            p.peak = max(p.peak, c)
            hit = c >= p.entry * (1 + GAIN)
            if arm == "TP20" and hit:
                pending_sell.discard(sym)
                sell(sym, t, c, "tp")
                continue
            if arm == "HALF20" and hit and not p.halved:
                sell(sym, t, c, "tp_half", 0.5)
                p.halved = True
            if arm == "TRAIL20":
                if hit:
                    p.trailing = True
                if p.trailing:
                    p.stop = max(p.stop, p.entry, p.peak * (1 - TRAIL))
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
    """사전 등록 §4 (순수). 미국 통과 + 국장 샤프가 ARM 이상인 후보 중 미국 샤프 최고."""
    us_ok = [c for c in CANDIDATES if passes(us[c], us["ARM"])]
    both = [c for c in us_ok if kr[c]["full"]["sharpe"] >= kr["ARM"]["full"]["sharpe"]]
    if both:
        best = max(both, key=lambda c: us[c]["full"]["sharpe"])
        return {"verdict": f"ADOPT_{best}", "us_pass": us_ok, "both_pass": both}
    if us_ok:
        return {"verdict": "US_ONLY", "us_pass": us_ok, "both_pass": []}
    return {"verdict": "KEEP_ARM", "us_pass": [], "both_pass": []}


def evaluate(mk: Market) -> dict[str, Any]:
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    span = mk.days[start:]
    rows: dict[str, Any] = {}
    for arm in ARMS:
        r = simulate(mk, arm)
        rows[arm] = {**_row(span, r.equity), "trade": trade_stats(mk, r)}
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
        "note": (
            "문서 258 1 회 판정. 미국 = 113 지금 S&P 500(생존 편향 — 오래 들기에 유리), "
            "국장 = 141 폐지 포함(확인). 진입 · 크기는 256 T."
        ),
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
