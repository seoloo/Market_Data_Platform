"""문서 245 — 주도주 추가 매수(불타기) 규칙: 눌림 뒤 회복 · 진입가 +10% · 추가 없음. 1회 판정.

사용 예::

    python -m scripts.run_addon_245 run   # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음일 때만

고르는 법 · 진입 · 나가기는 141(국장) · 113(미국) 그대로 — 월말 주도 섹터 3 · 주도주 10 · 추세 ·
지수 200 일선 위 · 다음 거래일 종가 · 추세 꺾임 / 고점 −35% 면 다음 거래일 종가 매도 · 편도 0.25% ·
최대 10 종목. 141 의 '매달 목표까지 추매' 는 하지 않고 **추가 매수 규칙만** 바꾼다.

* FULL (대조): 진입 때 10% 다 산다, 추가 없음.
* PULL (후보): 진입 때 5%, 그 뒤 종가가 20 일선 아래로 내려갔다가 다시 위로 올라선 첫날 →
  다음 거래일 종가에 5% 추가.
* UP10 (후보): 진입 때 5%, 종가가 진입가 × 1.10 을 처음 넘은 날 → 다음 거래일 종가에 5% 추가.
* HALF (참고): 진입 때 5%, 추가 없음.
추가는 종목마다 한 번, 그날 계좌의 5% (현금 안), 매도 예약이 걸린 종목은 하지 않는다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_exit_242 import FIRST_END, Market, kr_market, sma, us_market
from scripts.run_lt_kr import month_end_day
from scripts.run_lt_leaders import FEE, FROM, SPLIT, STOP_FROM_PEAK, stats, trend_off, trend_up

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "addon_245"
MAX_POS = 10
FULL_W = 0.10
HALF_W = 0.05
MA = 20
UP = 0.10
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05
CANDIDATES = ("PULL", "UP10")


@dataclass(frozen=True, slots=True)
class Arm:
    name: str
    first: float
    add: str | None
    """None · 'pull' · 'up'."""


ARMS = (
    Arm("FULL", FULL_W, None),
    Arm("PULL", HALF_W, "pull"),
    Arm("UP10", HALF_W, "up"),
    Arm("HALF", HALF_W, None),
)


@dataclass(slots=True)
class Pos:
    qty: float
    cost: float
    entry: float
    i0: int
    peak: float
    added: bool = False
    dipped: bool = False
    """진입 뒤 종가가 20 일선 아래로 내려간 적이 있나 (PULL 용)."""


@dataclass(slots=True)
class AddRun:
    equity: list[float] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    adds: int = 0


def add_signal(arm: Arm, p: Pos, close: Sequence[float | None], t: int) -> bool:
    """오늘 종가로 추가 매수 신호인가 (순수). PULL 은 ``p.dipped`` 를 갱신한다."""
    c = close[t]
    if arm.add is None or p.added or c is None or t <= p.i0:
        return False
    if arm.add == "up":
        return c > p.entry * (1 + UP)
    m = sma(close, t, MA)
    if m is None:
        return False
    if c < m:
        p.dipped = True
        return False
    return p.dipped


def simulate_add(mk: Market, arm: Arm, start_date: str = FROM) -> AddRun:
    days = mk.days
    start = next(i for i, d in enumerate(days) if d >= start_date)
    cash = 1.0
    pos: dict[str, Pos] = {}
    run = AddRun()
    pending_sell: set[str] = set()
    pending_add: set[str] = set()
    pending_buy: list[str] | None = None

    def px(sym: str, t: int) -> float:
        xs = mk.close[sym]
        while xs[t] is None:
            t -= 1
        return xs[t]  # type: ignore[return-value]

    def sell(sym: str, t: int, reason: str) -> None:
        nonlocal cash
        p = pos.pop(sym)
        proceeds = p.qty * px(sym, t) * (1 - FEE)
        cash += proceeds
        run.trades.append(
            {"symbol": sym, "ret": proceeds / p.cost - 1, "added": p.added, "reason": reason}
        )

    for t in range(start, len(days)):
        for sym in sorted(pending_sell):
            if sym in pos and mk.close[sym][t] is not None:
                sell(sym, t, "rule")
        pending_sell = {s for s in pending_sell if s in pos}
        equity = cash + sum(p.qty * px(s, t) for s, p in pos.items())
        for sym in sorted(pending_add):
            c = mk.close[sym][t]
            if sym not in pos or c is None or cash <= 0 or sym in pending_sell:
                continue
            amt = min(HALF_W * equity, cash)
            p = pos[sym]
            p.qty += amt * (1 - FEE) / c
            p.cost += amt
            p.added = True
            cash -= amt
            run.adds += 1
        pending_add = {s for s in pending_add if s in pos and not pos[s].added}
        if pending_buy is not None:
            for sym in pending_buy:
                c = mk.close[sym][t]
                if c is None or sym in pos or len(pos) >= MAX_POS or cash <= 0:
                    continue
                amt = min(arm.first * equity, cash)
                pos[sym] = Pos(amt * (1 - FEE) / c, amt, c, t, c)
                cash -= amt
            pending_buy = None
        for sym in sorted(pos):
            if mk.ends_at(sym, t):
                pending_sell.discard(sym)
                pending_add.discard(sym)
                sell(sym, t, "delisted")
        for sym, p in pos.items():
            c = mk.close[sym][t]
            if c is None:
                continue
            p.peak = max(p.peak, c)
            if t > p.i0 and (trend_off(mk.close[sym], t) or c <= (1 - STOP_FROM_PEAK) * p.peak):
                pending_sell.add(sym)
                continue
            if add_signal(arm, p, mk.close[sym], t):
                pending_add.add(sym)
        if month_end_day(days, t) and mk.index_ok[t]:
            pending_buy = [
                s
                for s in mk.pick(t)
                if s not in pos and s not in pending_sell and trend_up(mk.close[s], t)
            ]
        run.equity.append(cash + sum(p.qty * px(s, t) for s, p in pos.items()))
    return run


def _row(span: Sequence[str], run: AddRun) -> dict[str, Any]:
    end = span[-1]
    tr = run.trades
    added = [x["ret"] for x in tr if x["added"]]
    return {
        "full": stats(span, run.equity, FROM, end),
        "first": stats(span, run.equity, FROM, FIRST_END),
        "second": stats(span, run.equity, SPLIT, end),
        "trades": len(tr),
        "adds": run.adds,
        "avg_ret": round(sum(x["ret"] for x in tr) / len(tr), 4) if tr else None,
        "avg_ret_added": round(sum(added) / len(added), 4) if added else None,
    }


def evaluate(mk: Market) -> dict[str, Any]:
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    span = mk.days[start:]
    return {arm.name: _row(span, simulate_add(mk, arm)) for arm in ARMS}


def passes(x: dict[str, Any], base: dict[str, Any]) -> bool:
    return (
        x["full"]["sharpe"] >= base["full"]["sharpe"] + SHARPE_GAIN
        and all(x[h]["sharpe"] >= base[h]["sharpe"] for h in ("first", "second"))
        and x["full"]["mdd"] <= base["full"]["mdd"] + MDD_SLACK
    )


def judge(kr: dict[str, Any]) -> str:
    """사전 등록 §4 (순수). 통과 후보 중 샤프 최고, 없으면 NONE."""
    ok = [c for c in CANDIDATES if passes(kr[c], kr["FULL"])]
    if not ok:
        return "NONE"
    return "ADOPT_" + max(ok, key=lambda c: kr[c]["full"]["sharpe"])


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
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
        "us_reference": us,
        "note": (
            "문서 245 1 회 판정. 국장 판정(폐지 포함), "
            "미국은 113 자료(생존 편향 · 이미 본 판)라 참고."
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv[1:] != ["run"]:
        print(__doc__)
        return 2
    print(json.dumps({"verdict": run()["verdict"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
