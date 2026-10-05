"""AI 주도 테마 장기 적립 그림자 장부 (문서 147) — 순수 함수. 주문하지 않는다.

매달 마지막 거래일의 목록(AI 주도주 · 가격 주도주 · 수급 주도주 · 종목군 전체)에 **같은 금액**을
똑같이 나눠 다음 거래일부터 종가에 사고, **팔지 않고 계속 들고 간다**(BTQ·WQTM 적립처럼 몇 년 보유).
그 날 거래가 없는 종목은 5 거래일 안 첫 거래일에 산다. 끝내 못 산 몫은 현금으로 남는다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

__all__ = ["AccBook", "accumulate", "cohort_returns", "irr"]

FEE = 0.0025
BUY_WAIT = 5


@dataclass(slots=True)
class AccBook:
    contributed: float = 0.0
    cash: float = 0.0
    qty: dict[str, float] = field(default_factory=dict)
    equity: list[float] = field(default_factory=list)
    """날마다 평가액(현금 + 보유 × 마지막 종가)."""
    flows: list[tuple[int, float]] = field(default_factory=list)
    """(넣은 날 색인, 금액) — IRR 용."""
    baskets: list[dict[str, object]] = field(default_factory=list)
    """달마다: 신호일 색인 · 종목 · 산 수량."""


def _last(xs: Sequence[float | None], t: int) -> float | None:
    while t >= 0:
        if xs[t] is not None:
            return xs[t]
        t -= 1
    return None


def accumulate(
    days: Sequence[str],
    close: Mapping[str, Sequence[float | None]],
    baskets: Mapping[int, Sequence[str]],
    *,
    monthly: float,
    start: int,
    fee: float = FEE,
    keep: Callable[[str, int], bool] | None = None,
) -> AccBook:
    """``baskets[t]`` = 월말 ``t`` 의 목록. ``t >= start`` 인 달마다 ``monthly`` 를 넣는다.

    ``keep`` 이 있으면 그 조건(예: 추세 위)을 만족한 종목만 산다 — 못 산 몫은 현금으로 남아
    다음 달 목록에 함께 쓴다. 목록이 비면 그달 돈은 현금으로 남는다."""
    book = AccBook()
    pending: list[tuple[str, float, int]] = []  # 종목 · 금액 · 신호일
    for t in range(start, len(days)):
        still: list[tuple[str, float, int]] = []
        for sym, amt, t0 in pending:
            px = close.get(sym, [None] * len(days))[t]
            if px is not None:
                book.qty[sym] = book.qty.get(sym, 0.0) + amt * (1 - fee) / px
            elif t - t0 <= BUY_WAIT:
                still.append((sym, amt, t0))
            else:
                book.cash += amt
        pending = still
        if t in baskets:
            book.contributed += monthly
            book.flows.append((t, monthly))
            names = [s for s in baskets[t] if keep is None or keep(s, t)]
            budget = monthly + book.cash
            if names:
                each = budget / len(names)
                pending.extend((s, each, t) for s in names)
                book.cash = 0.0
            else:
                book.cash = budget
            book.baskets.append({"t": t, "names": list(names)})
        value = book.cash + sum(amt for _s, amt, _t in pending)
        for sym, q in book.qty.items():
            px = _last(close[sym], t)
            value += q * px if px is not None else 0.0
        book.equity.append(value)
    return book


def irr(flows: Sequence[tuple[int, float]], final_t: int, final_value: float) -> float | None:
    """돈 넣은 날·금액과 마지막 평가액으로 연 수익률(거래일 252 기준, 이분법)."""
    if not flows or final_value <= 0:
        return None

    def npv(r: float) -> float:
        total = final_value
        for t, amt in flows:
            total -= amt * (1 + r) ** ((final_t - t) / 252)
        return total

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def cohort_returns(
    close: Mapping[str, Sequence[float | None]],
    names: Sequence[str],
    t0: int,
    t1: int,
) -> float | None:
    """월말 ``t0`` 목록을 다음 거래일 종가에 똑같이 나눠 사서 ``t1`` 종가까지의 수익(비용 뺌).

    거래가 없던 종목은 직전 종가로 평가한다. 산 날 값이 없는 종목은 뺀다."""
    rets = []
    for s in names:
        xs = close.get(s)
        if xs is None or t0 + 1 >= len(xs):
            continue
        a, b = xs[t0 + 1], _last(xs, t1)
        if a is None or b is None:
            continue
        rets.append(b / a * (1 - FEE) - 1)
    return sum(rets) / len(rets) if rets else None
