"""NQ Paper — 나스닥 100 상위 10 교체판 (사용자 2026-10-12). 가상 계좌, 주문 없음.

LT(문서 115)의 주도주 점수를 나스닥 100 에 쓰되,
들고 있기 대신 **정해진 날마다 상위 10 으로 갈아탄다.**

* 판단일: 그달 첫 거래일, 그리고 첫 거래일로부터 14 일(달력) 뒤 첫 거래일 — 한 달에 두 번.
  어제 종가로 점수를 매기고 오늘 종가에 사고판다(LT 와 같은 시점).
* 점수: 21 · 63 · 126 거래일 수익의 순위 백분위 평균 (LT 와 같다, 섹터 단계 없음).
* 판단일에 상위 10 밖으로 밀린 보유 종목은 판다. 상위 10 안의 새 종목은 그때 자산의 10% 씩 산다.
  계속 상위 10 인 종목은 그대로 둔다(다시 맞추지 않음). 추세가 꺼진 종목은 사지 않는다.
* 손절: T/v2 와 같은 추세 신호 — 매일 종가로 20 · 60 · 120 거래일 수익 부호의 합이 0 이하이면
  다음 거래일 종가에 판다. 판 돈은 다음 판단일까지 현금으로 둔다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from auto_trading.ops.lt_paper import FEE, LOOKS, Closes, PaperState, Position, _px, _ret, trend_off

TOP_N = 10
SLOT = 1 / TOP_N
SECOND_CHECK_DAYS = 14


def rebalance_days(days: Sequence[str]) -> set[int]:
    """판단일 색인: 그달 첫 거래일과, 그로부터 14 일 뒤 첫 거래일 (같은 달 안)."""
    out: set[int] = set()
    first: date | None = None
    second_done = False
    for t, d in enumerate(days):
        if t == 0 or days[t - 1][:7] != d[:7]:
            first, second_done = date.fromisoformat(d), False
            if t > 0:
                out.add(t)
        elif first is not None and not second_done:
            if date.fromisoformat(d) >= first + timedelta(days=SECOND_CHECK_DAYS):
                out.add(t)
                second_done = True
    return out


def next_rebalance(days: Sequence[str], t: int) -> str:
    """``days[t]`` 다음 판단일. 아직 자료가 없는 날은 평일로 어림한다(휴장이면 다음 거래일)."""
    later = sorted(i for i in rebalance_days(days) if i > t)
    if later:
        return days[later[0]]
    today = date.fromisoformat(days[t])
    first = date.fromisoformat(next(d for d in days if d[:7] == days[t][:7]))
    target = first + timedelta(days=SECOND_CHECK_DAYS)
    if today >= target:
        target = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target.isoformat()


def ranking(closes: Mapping[str, Closes], t: int) -> list[tuple[str, float]]:
    """t 종가 기준 점수 순위 [(종목, 점수)] — 세 기간 수익이 모두 있는 종목만."""
    rets = {
        k: {s: r for s, xs in closes.items() if (r := _ret(xs, t, k)) is not None} for k in LOOKS
    }
    common = set.intersection(*(set(v) for v in rets.values()))
    if len(common) < 2 * TOP_N:
        return []
    score: dict[str, float] = dict.fromkeys(common, 0.0)
    for k in LOOKS:
        ordered = sorted(common, key=lambda s: (rets[k][s], s))
        n = len(ordered)
        for i, s in enumerate(ordered):
            score[s] += i / (n - 1) / len(LOOKS)
    return sorted(((s, round(v, 4)) for s, v in score.items()), key=lambda x: (-x[1], x[0]))


def _equity(state: PaperState, closes: Mapping[str, Closes], t: int) -> float:
    return state.cash + sum(p.qty * _px(closes[s], t) for s, p in state.positions.items())


def _sell(state: PaperState, sym: str, d: str, px: float, reason: str) -> dict[str, Any]:
    pos = state.positions.pop(sym)
    proceeds = pos.qty * px * (1 - FEE)
    state.cash += proceeds
    trade = {
        "symbol": sym,
        "entry": pos.entry,
        "exit": d,
        "ret": round(proceeds / pos.cost - 1, 4),
        "reason": reason,
    }
    state.trades.append(trade)
    return trade


def step(
    state: PaperState,
    days: Sequence[str],
    closes: Mapping[str, Closes],
    t: int,
    rebalance: bool,
) -> dict[str, Any]:
    """하루(t ≥ 1)를 처리한다. ``rebalance`` 면 어제 종가 순위로 오늘 종가에 갈아탄다."""
    d = days[t]
    actions: dict[str, Any] = {"date": d, "rebalance": rebalance, "sold": [], "bought": []}
    # 1) 어제 추세가 꺼져 정해 둔 매도
    for sym, reason in sorted(state.pending_sell.items()):
        if sym in state.positions:
            actions["sold"].append(_sell(state, sym, d, _px(closes[sym], t), reason))
    state.pending_sell = {}
    # 2) 판단일: 상위 10 밖은 팔고, 새로 든 종목은 자산의 10% 씩
    rank = ranking(closes, t - 1)
    top = [s for s, _ in rank[:TOP_N]]
    actions["ranking_as_of"] = days[t - 1]
    actions["top"] = rank[:TOP_N]
    if (rebalance or not state.started) and top:
        for sym in sorted(state.positions):
            if sym not in top:
                actions["sold"].append(_sell(state, sym, d, _px(closes[sym], t), "rank_out"))
        equity = _equity(state, closes, t)
        for sym in top:
            if sym in state.positions or trend_off(closes[sym], t - 1) or state.cash <= 0:
                continue
            amt = min(SLOT * equity, state.cash)
            p = _px(closes[sym], t)
            state.positions[sym] = Position(amt * (1 - FEE) / p, amt, p, d)
            state.cash -= amt
            actions["bought"].append({"symbol": sym, "usd": round(amt, 2), "price": p})
        state.started = True
    # 3) 오늘 종가로 추세가 꺼진 종목은 내일 판다 (T/v2 신호)
    for sym, pos in state.positions.items():
        p = _px(closes[sym], t)
        pos.peak = max(pos.peak, p)
        if trend_off(closes[sym], t):
            state.pending_sell[sym] = "trend_off"
    value = _equity(state, closes, t)
    state.equity.append((d, round(value, 4)))
    state.last_date = d
    actions["equity"] = round(value, 4)
    actions["pending_sell"] = dict(state.pending_sell)
    return actions
