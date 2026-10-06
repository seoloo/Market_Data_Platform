"""국장 월간 재평가 계좌 — 지정가 일봉 체결 대리 모형 (순수 함수, 초안 §5).

docs/research/FUNDAMENTAL_KR_PREREGISTRATION_DRAFT.md §5 를 옮긴다:

* 월말 종가에 정한 목표 비중 → **다음 거래일부터** 주문. 롱·현금, 차입 없음, 정수 주식(내림).
* 매수 한도 = 전날 원래(비수정) 종가 × (1 + 0.5%) 를 호가 단위로 **내림**, 매도 한도 = × (1 − 0.5%)
  를 **올림**. 하루 유효, 다음 날 전날 종가로 다시 계산, 매수는 최대 5 거래일(그 뒤 잔여는 다음
  재평가까지 현금), 매도는 날마다 다시.
* 체결: 매수는 그날 종가 < 한도, 매도는 종가 > 한도, 거래량 > 0 일 때만. 값은 매수 min(한도, 고가),
  매도 max(한도, 저가). 수량 ≤ 그날 거래량 × 0.1%, 그달 주문 ≤ 직전 63 일 평균 거래대금 × 0.1%.
* 같은 날 같은 종목 매수·매도 동시 금지. 매수는 그날 시작 때 쓸 수 있는 현금만(매도 대금은
  T+2 결제일부터, 배당은 지급일부터). 현금이 모자라면 그날 매수 의도에 비례해 줄인다.
* 비용: ``cost(day, side)`` (기간별 비용표). 분할·병합·증자: 수정 계수(원래 ÷ 수정 종가)가
  바뀐 날 주식 수를 계수 비율로 바꾼다. 상장폐지: 마지막 거래일 종가에 정리(따로 센다).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "Book",
    "Result",
    "StockDays",
    "buy_limit",
    "prev_px",
    "sell_limit",
    "simulate",
    "tick_size",
]

LIMIT_PCT = 0.005
MAX_BUY_TRIES = 5
VOL_CAP = 0.001
ADV_CAP = 0.001
ADV_DAYS = 63
SETTLE = 2


def tick_size(price: float, day: str) -> int:
    """유가증권시장 호가 단위. 2023-01-25 부터 개편 단위(코스피·코스닥 통일)."""
    if day >= "20230125":
        steps = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100),
                 (500_000, 500))  # fmt: skip
    else:
        steps = ((1_000, 1), (5_000, 5), (10_000, 10), (50_000, 50), (100_000, 100),
                 (500_000, 500))  # fmt: skip
    for hi, t in steps:
        if price < hi:
            return t
    return 1_000


def buy_limit(prev_close: float, day: str) -> float:
    raw = prev_close * (1 + LIMIT_PCT)
    t = tick_size(raw, day)
    return math.floor(raw / t + 1e-9) * t  # 10,000 × 1.005 = 10,049.999… 오차


def sell_limit(prev_close: float, day: str) -> float:
    raw = prev_close * (1 - LIMIT_PCT)
    t = tick_size(raw, day)
    return math.ceil(raw / t - 1e-9) * t


@dataclass(frozen=True, slots=True)
class StockDays:
    """한 종목의 거래일 축 자료 (계좌 날짜 축에 맞춘 길이). 거래 없는 날은 volume 0."""

    open: Sequence[float]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    volume: Sequence[float]
    value: Sequence[float]
    factor: Sequence[float]
    """원래 ÷ 수정 종가 — 바뀌면 분할·병합·증자."""
    last: int
    """마지막 거래일 색인 (상장폐지면 그날, 아니면 축 끝)."""
    delisted: bool
    dividends: Mapping[int, tuple[float, int]] = field(default_factory=dict)
    """권리 마지막 매수일 색인 → (주당 배당금, 지급일 색인)."""


@dataclass(slots=True)
class Book:
    cash: float
    shares: dict[str, float] = field(default_factory=dict)
    pending: list[tuple[int, float]] = field(default_factory=list)
    """(가용 날 색인, 금액) — 결제 전 매도 대금·지급 전 배당."""


@dataclass(slots=True)
class Result:
    equity: list[float]
    fills: int = 0
    unfilled_buy_days: int = 0
    delist_exits: int = 0
    turnover: float = 0.0
    costs: float = 0.0
    dividends: float = 0.0
    exposure: list[float] = field(default_factory=list)


def _adv(s: StockDays, t: int) -> float:
    lo = max(0, t - ADV_DAYS)
    w = s.value[lo:t]
    return sum(w) / len(w) if w else 0.0


def simulate(
    days: Sequence[str],
    stocks: Mapping[str, StockDays],
    targets: Mapping[int, Mapping[str, float]],
    cost: Callable[[str, str], float],
    capital: float,
    *,
    start: int,
    end: int,
    delay: int = 0,
) -> Result:
    """``targets``{월말 색인: {종목: 목표 비중}} 을 ``start`` ~ ``end`` 거래일에 굴린다.

    월말 t 의 목표는 t + 1 + ``delay`` 거래일부터 주문한다 (K5 의 지연 시험은 delay=1)."""
    book = Book(capital)
    res = Result([])
    goal: dict[str, float] = {}
    buy_left: dict[str, float] = {}
    sell_left: dict[str, float] = {}
    tries: dict[str, int] = {}
    month_budget: dict[str, float] = {}
    for t in range(start, end + 1):
        day = days[t]
        # 1) 결제·지급된 현금
        keep = []
        for at, amt in book.pending:
            if at <= t:
                book.cash += amt
            else:
                keep.append((at, amt))
        book.pending = keep
        # 2) 분할·병합 — 수정 계수 바뀜
        for sym in list(book.shares):
            s = stocks[sym]
            if t > 0 and s.factor[t] > 0 and s.factor[t - 1] > 0 and s.factor[t] != s.factor[t - 1]:
                ratio = s.factor[t - 1] / s.factor[t]  # 5:1 분할이면 계수 5 → 1, 주식 5 배
                book.shares[sym] *= ratio
                if sym in sell_left:
                    sell_left[sym] *= ratio
                if sym in buy_left:
                    buy_left[sym] = math.floor(buy_left[sym] * ratio)
        # 3) 새 목표 → 첫 세션에 주문 수량(주)을 정한다. 그 뒤엔 남은 수량만 다시 낸다.
        src = t - 1 - delay
        if src in targets:
            goal = dict(targets[src])
            value = book.cash + sum(a for _, a in book.pending)
            value += sum(q * prev_px(stocks[sym], t) for sym, q in book.shares.items())
            buy_left, sell_left, tries, month_budget = {}, {}, {}, {}
            for sym in sorted(set(goal) | set(book.shares)):
                if sym not in stocks or t > stocks[sym].last:
                    continue
                prev = prev_px(stocks[sym], t)
                if prev <= 0:
                    continue
                want = math.floor(goal.get(sym, 0.0) * value / prev)
                have = book.shares.get(sym, 0.0)
                if want == 0 and have > 0:
                    sell_left[sym] = have
                elif want < math.floor(have):
                    sell_left[sym] = math.floor(have) - want
                elif want > math.floor(have):
                    buy_left[sym] = want - math.floor(have)
        # 4) 오늘 주문 — 한도는 전날 종가(분할 반영)로만
        for sym in sorted(sell_left):
            s = stocks[sym]
            if t > s.last:
                sell_left.pop(sym)
                continue
            lim = sell_limit(prev_px(s, t), day)
            if not (s.volume[t] > 0 and s.close[t] > lim):
                continue
            q = min(sell_left[sym], book.shares.get(sym, 0.0), math.floor(s.volume[t] * VOL_CAP))
            if q <= 0:
                continue
            px = max(lim, s.low[t])
            gross = q * px
            c = gross * cost(day, "sell")
            book.shares[sym] = book.shares.get(sym, 0.0) - q
            if book.shares[sym] <= 1e-9:
                book.shares.pop(sym)
            book.pending.append((t + SETTLE, gross - c))
            sell_left[sym] -= q
            if sell_left[sym] <= 1e-9 or sym not in book.shares:
                sell_left.pop(sym)
            res.fills += 1
            res.turnover += gross
            res.costs += c
        plans: list[tuple[str, float, float]] = []
        for sym in sorted(buy_left):
            s = stocks[sym]
            if tries.get(sym, 0) >= MAX_BUY_TRIES or t > s.last:
                buy_left.pop(sym)
                continue
            tries[sym] = tries.get(sym, 0) + 1
            lim = buy_limit(prev_px(s, t), day)
            if not (s.volume[t] > 0 and s.close[t] < lim):
                res.unfilled_buy_days += 1
                continue
            px = min(lim, s.high[t])
            budget = month_budget.setdefault(sym, _adv(s, t) * ADV_CAP)
            q = min(buy_left[sym], math.floor(s.volume[t] * VOL_CAP), math.floor(budget / px))
            if q > 0:
                plans.append((sym, q, px))
        need = sum(q * px * (1 + cost(day, "buy")) for _, q, px in plans)
        scale = min(1.0, book.cash / need) if need > 0 else 0.0
        for sym, q, px in plans:
            q2 = math.floor(q * scale)
            if q2 <= 0:
                continue
            gross = q2 * px
            c = gross * cost(day, "buy")
            book.cash -= gross + c
            book.shares[sym] = book.shares.get(sym, 0.0) + q2
            month_budget[sym] -= gross
            buy_left[sym] -= q2
            if buy_left[sym] <= 0:
                buy_left.pop(sym)
            res.fills += 1
            res.turnover += gross
            res.costs += c
        # 5) 배당 권리 (오늘이 권리 마지막 매수일이면 종가에 들고 있는 수량)
        for sym, q in book.shares.items():
            ev = stocks[sym].dividends.get(t)
            if ev is not None and q > 0:
                amt = q * ev[0]
                book.pending.append((max(ev[1], t + 1), amt))
                res.dividends += amt
        # 6) 상장폐지 — 마지막 거래일 종가에 정리
        for sym in list(book.shares):
            s = stocks[sym]
            if s.delisted and t == s.last:
                q = book.shares.pop(sym)
                gross = q * s.close[t]
                c = gross * cost(day, "sell")
                book.pending.append((t + SETTLE, gross - c))
                res.delist_exits += 1
                res.costs += c
        eq = _mark(book, stocks, t)
        res.equity.append(eq)
        invested = eq - book.cash - sum(a for _, a in book.pending)
        res.exposure.append(invested / eq if eq > 0 else 0.0)
    return res


def prev_px(s: StockDays, t: int) -> float:
    """전날 원래 종가를 오늘 주식 수 기준으로 (분할·병합이 있었으면 계수 비율만큼)."""
    if t <= 0:
        return 0.0
    k = min(t - 1, s.last)
    while k > 0 and s.close[k] <= 0:
        k -= 1
    px = s.close[k]
    f0, f1 = s.factor[k], s.factor[min(t, s.last)]
    return px * f1 / f0 if f0 > 0 and f1 > 0 else px


def _mark(book: Book, stocks: Mapping[str, StockDays], t: int) -> float:
    v = book.cash + sum(a for _, a in book.pending)
    for sym, q in book.shares.items():
        s = stocks[sym]
        k = min(t, s.last)
        while k > 0 and s.close[k] <= 0:
            k -= 1
        v += q * s.close[k]
    return v
