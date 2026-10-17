"""문서 256 — 추세 밖에서 산 주도주(눌림 · 반등 노리기) vs 추세 안에서 산 주도주. 1회 판정.

사용 예::

    python -m scripts.run_dip_256 run   # 깨끗한 트리 · 141 자료 해시 · 판정 파일 없음일 때만

판 · 크기는 242(113 미국 · 141 국장 주도주, 월말 · 지수 200 일선 위 · 다음 거래일 종가 진입)에
💹 지금 크기(손절 한 번 0.3% ÷ 3% = 한 종목 10%, 최대 10 종목, 추매 없음).

* 진입 T (대조): 이번 월말 주도주 10 중 추세(20 · 60 · 120 부호 평균 > 0 · 50 일선 위)인 종목.
* 진입 D (눌림): 최근 6 번의 월말 주도주 명단에 든 적 있고 **지금 추세가 꺼진**(부호 평균 ≤ 0) 종목.
  최근 명단 · 점수 높은 순.
* 나가기 S3: 진입가 −3% 장중 손절만 (지금 💹).
* 나가기 ARM: S3 + 산 뒤 추세가 한 번 확인되면(그날 종가로 추세 조건) 그때부터 113 느린 나가기
  (추세 꺼짐 또는 진입 뒤 최고 종가 −35% → 다음 거래일 종가). T 진입은 산 날 이미 확인.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import ExitRun, Market, _row, kr_market, trade_stats, us_market
from scripts.run_lt_kr import month_end_day
from scripts.run_lt_leaders import FEE, FROM, STOP_FROM_PEAK, trend_off, trend_up

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "dip_256"
STOP = 0.03
WEIGHT = 0.10
MAX_POS = 10
LOOKBACK_MONTHS = 6
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05
ARMS = (("T", False), ("T", True), ("D", False), ("D", True))


def arm_name(entry: str, slow: bool) -> str:
    return f"{entry}_{'ARM' if slow else 'S3'}"


@dataclass(slots=True)
class Pos:
    qty: float
    cost: float
    entry: float
    i0: int
    peak: float
    armed: bool


def dip_pool(
    history: Sequence[list[str]], close: dict[str, list[float | None]], t: int
) -> list[str]:
    """최근 명단(새 것부터, 점수 순)에서 지금 추세가 꺼진 종목, 겹침 없이."""
    out: list[str] = []
    for names in reversed(history[-LOOKBACK_MONTHS:]):
        for s in names:
            if s not in out and close[s][t] is not None and trend_off(close[s], t):
                out.append(s)
    return out


def simulate(mk: Market, entry: str, slow: bool, start_date: str = FROM) -> ExitRun:
    """242 ``simulate_exit`` 와 같은 순서: 어제 정한 매도 · 매수 → 오늘 종가,
    장중 손절은 진입 다음 거래일부터, 오늘 종가로 내일 할 일."""
    days = mk.days
    start = next(i for i, d in enumerate(days) if d >= start_date)
    cash = 1.0
    pos: dict[str, Pos] = {}
    run = ExitRun()
    pending_sell: set[str] = set()
    pending_buy: list[str] | None = None
    history: list[list[str]] = []

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
                armed = trend_up(mk.close[sym], t)
                pos[sym] = Pos(amt * (1 - FEE) / c, amt, c, t, c, armed)
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
            stop = p.entry * (1 - STOP)
            lo = mk.low[sym][t] if mk.low is not None else None
            op = mk.open[sym][t] if mk.open is not None else None
            if lo is not None:
                if lo <= stop:
                    sell(sym, t, op if op is not None and op < stop else stop, "stop")
            elif c <= stop:
                sell(sym, t, c, "stop")
        for sym, p in pos.items():
            c = mk.close[sym][t]
            if c is None:
                continue
            p.peak = max(p.peak, c)
            if not p.armed and trend_up(mk.close[sym], t):
                p.armed = True
            off = trend_off(mk.close[sym], t) or c <= (1 - STOP_FROM_PEAK) * p.peak
            if slow and p.armed and t > p.i0 and off:
                pending_sell.add(sym)
        if month_end_day(days, t):
            picks = mk.pick(t)
            history.append(picks)
            if mk.index_ok[t]:
                if entry == "T":
                    cand = [s for s in picks if trend_up(mk.close[s], t)]
                else:
                    cand = dip_pool(history, mk.close, t)
                pending_buy = [s for s in cand if s not in pos and s not in pending_sell]
        run.equity.append(cash + sum(p.qty * px(s, t) for s, p in pos.items()))
    return run


def judge(kr: dict[str, Any]) -> str:
    """사전 등록 §4 (순수). 국장: D_ARM vs T_ARM."""
    d, t = kr["D_ARM"], kr["T_ARM"]
    if d["full"]["sharpe"] < t["full"]["sharpe"]:
        return "불리"
    ok = (
        d["full"]["sharpe"] >= t["full"]["sharpe"] + SHARPE_GAIN
        and all(d[h]["sharpe"] >= t[h]["sharpe"] for h in ("first", "second"))
        and d["full"]["mdd"] <= t["full"]["mdd"] + MDD_SLACK
    )
    return "지지" if ok else "불명"


def evaluate(mk: Market) -> dict[str, Any]:
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    span = mk.days[start:]
    rows: dict[str, Any] = {}
    for entry, slow in ARMS:
        r = simulate(mk, entry, slow)
        rows[arm_name(entry, slow)] = {**_row(span, r.equity), "trade": trade_stats(mk, r)}
    return rows


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    kr = evaluate(kr_market()[0])
    us = evaluate(us_market()[0])
    out = {
        "git": sha,
        "verdict": judge(kr),
        "kr": kr,
        "us": us,
        "note": (
            "문서 256 1 회 판정. 국장 = 141 폐지 포함 그 시점 상위 200(판정), "
            "미국 = 113 지금 S&P 500(생존 편향 — 눌림 매수에 유리하게 기울어 참고만)."
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
