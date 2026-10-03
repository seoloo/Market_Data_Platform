"""LT Paper (문서 115) — 113 의 주도주 추세 보유 규칙을 앞으로의 자료로 하루씩 적용하는 순수 엔진.

판단은 그날 종가로, 실행은 다음 거래일 종가로 한다(113 과 같다).
- 매도: 어제 종가 기준으로 정해 둔 종목(추세 꺾임 · 고점 −35%)을 오늘 종가에 판다.
- 매수: 오늘이 그달 첫 거래일이면, **어제 종가** 로 고른 주도주(추세 있음 · SPY 200 일선 위)를
  오늘 종가에 산다. 들고 있는 종목은 목표 비중까지 다시 채운다(추매).
  첫 실행은 그달 첫날이 아니어도 산다.
주문은 내지 않는다 — 가상 계좌다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

FEE = 0.0025
MAX_POS, TARGET = 10, 0.10
STOP_FROM_PEAK = 0.35
LOOKS = (21, 63, 126)
TOP_SECTORS, TOP_STOCKS = 3, 10
TREND_LB = (20, 60, 120)
STOCK_SMA, SPY_SMA = 50, 200

Closes = Sequence[float | None]


def _ret(xs: Closes, t: int, k: int) -> float | None:
    if t - k < 0 or xs[t] is None or xs[t - k] is None:
        return None
    a, b = xs[t - k], xs[t]
    assert a is not None and b is not None
    return b / a - 1


def _trend_sum(xs: Closes, t: int) -> int | None:
    total = 0
    for k in TREND_LB:
        r = _ret(xs, t, k)
        if r is None:
            return None
        total += 1 if r > 0 else -1 if r < 0 else 0
    return total


def trend_up(xs: Closes, t: int) -> bool:
    s = _trend_sum(xs, t)
    if s is None or s <= 0 or t + 1 < STOCK_SMA:
        return False
    window = xs[t - STOCK_SMA + 1 : t + 1]
    if any(v is None for v in window):
        return False
    c = xs[t]
    assert c is not None
    return c > sum(v for v in window if v is not None) / STOCK_SMA


def trend_off(xs: Closes, t: int) -> bool:
    s = _trend_sum(xs, t)
    return s is not None and s <= 0


def spy_ok(spy: Closes, t: int) -> bool:
    if t + 1 < SPY_SMA:
        return False
    window = spy[t - SPY_SMA + 1 : t + 1]
    if any(v is None for v in window):
        return False
    c = spy[t]
    assert c is not None
    return c > sum(v for v in window if v is not None) / SPY_SMA


def leaders(
    closes: Mapping[str, Closes], sectors: Mapping[str, str], t: int
) -> tuple[list[tuple[str, float]], list[str]]:
    """(주도주 상위 10 [(종목, 점수)], 주도 섹터 상위 3)."""
    rets: dict[int, dict[str, float]] = {}
    for k in LOOKS:
        rets[k] = {s: r for s, xs in closes.items() if (r := _ret(xs, t, k)) is not None}
    common = set.intersection(*(set(v) for v in rets.values()))
    if len(common) < 50:
        return [], []
    score: dict[str, float] = defaultdict(float)
    for k in LOOKS:
        ordered = sorted(common, key=lambda s: rets[k][s])
        n = len(ordered)
        for i, s in enumerate(ordered):
            score[s] += i / (n - 1) / len(LOOKS)
    by_sector: dict[str, list[float]] = defaultdict(list)
    for s in common:
        by_sector[sectors.get(s, "Unknown")].append(score[s])

    def median(xs: list[float]) -> float:
        ys = sorted(xs)
        m = len(ys) // 2
        return ys[m] if len(ys) % 2 else (ys[m - 1] + ys[m]) / 2

    top = sorted(by_sector, key=lambda x: median(by_sector[x]), reverse=True)[:TOP_SECTORS]
    pool = sorted(
        (s for s in common if sectors.get(s, "Unknown") in top),
        key=lambda s: score[s],
        reverse=True,
    )
    return [(s, round(score[s], 4)) for s in pool[:TOP_STOCKS]], top


@dataclass
class Position:
    qty: float
    cost: float
    peak: float
    entry: str


@dataclass
class PaperState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    pending_sell: dict[str, str] = field(default_factory=dict)
    """종목 → 이유 (trend_off · stop_35). 다음 실행일 종가에 판다."""
    last_date: str | None = None
    started: bool = False
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity: list[tuple[str, float]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["equity"] = [list(x) for x in self.equity]
        return out

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PaperState:
        return cls(
            cash=float(data["cash"]),
            positions={k: Position(**v) for k, v in data["positions"].items()},
            pending_sell=dict(data["pending_sell"]),
            last_date=data["last_date"],
            started=bool(data["started"]),
            trades=list(data["trades"]),
            equity=[(str(d), float(v)) for d, v in data["equity"]],
        )


def _px(xs: Closes, t: int) -> float:
    while t > 0 and xs[t] is None:
        t -= 1
    v = xs[t]
    if v is None:
        raise ValueError("가격 없음")
    return v


def step(
    state: PaperState,
    days: Sequence[str],
    closes: Mapping[str, Closes],
    spy: Closes,
    sectors: Mapping[str, str],
    t: int,
) -> dict[str, Any]:
    """하루(t)를 처리하고 그날 한 일을 돌려준다. ``t`` 는 ``days`` 색인, t ≥ 1."""
    d = days[t]
    actions: dict[str, Any] = {"date": d, "sold": [], "bought": [], "topped_up": []}
    # 1) 어제 정해 둔 매도
    for sym, reason in sorted(state.pending_sell.items()):
        pos = state.positions.pop(sym, None)
        if pos is None:
            continue
        proceeds = pos.qty * _px(closes[sym], t) * (1 - FEE)
        state.cash += proceeds
        trade = {
            "symbol": sym,
            "entry": pos.entry,
            "exit": d,
            "ret": round(proceeds / pos.cost - 1, 4),
            "reason": reason,
        }
        state.trades.append(trade)
        actions["sold"].append(trade)
    sold_today = set(state.pending_sell)
    state.pending_sell = {}
    # 2) 그달 첫 거래일(또는 첫 실행)이면 어제 종가로 고른 주도주를 오늘 종가에
    month_start = days[t - 1][:7] != d[:7]
    lead, top_sectors = leaders(closes, sectors, t - 1)
    actions["leaders_as_of"] = days[t - 1]
    actions["leaders"] = lead
    actions["leading_sectors"] = top_sectors
    actions["spy_above_200"] = spy_ok(spy, t - 1)
    if (month_start or not state.started) and actions["spy_above_200"]:
        equity = state.cash + sum(p.qty * _px(closes[s], t) for s, p in state.positions.items())
        for sym, pos in list(state.positions.items()):
            gap = TARGET * equity - pos.qty * _px(closes[sym], t)
            if gap > 0 and state.cash > 0:
                amt = min(gap, state.cash)
                pos.qty += amt * (1 - FEE) / _px(closes[sym], t)
                pos.cost += amt
                state.cash -= amt
                actions["topped_up"].append({"symbol": sym, "usd": round(amt, 2)})
        for sym, _score in lead:
            if sym in state.positions or sym in sold_today or not trend_up(closes[sym], t - 1):
                continue
            if len(state.positions) >= MAX_POS or state.cash <= 0:
                break
            amt = min(TARGET * equity, state.cash)
            p = _px(closes[sym], t)
            state.positions[sym] = Position(amt * (1 - FEE) / p, amt, p, d)
            state.cash -= amt
            actions["bought"].append({"symbol": sym, "usd": round(amt, 2), "price": p})
        state.started = True
    # 3) 오늘 종가로 내일 팔 종목
    for sym, pos in state.positions.items():
        p = _px(closes[sym], t)
        pos.peak = max(pos.peak, p)
        if p <= (1 - STOP_FROM_PEAK) * pos.peak:
            state.pending_sell[sym] = "stop_35"
        elif trend_off(closes[sym], t):
            state.pending_sell[sym] = "trend_off"
    value = state.cash + sum(p.qty * _px(closes[s], t) for s, p in state.positions.items())
    state.equity.append((d, round(value, 4)))
    state.last_date = d
    actions["equity"] = round(value, 4)
    actions["pending_sell"] = dict(state.pending_sell)
    return actions
