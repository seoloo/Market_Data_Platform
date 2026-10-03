"""QS Paper (문서 116) — 110 QS · 비교 A · 112 AG-US 를 앞으로의 자료로 하루씩 적용하는 엔진.

QQQ T 규칙(20·60·120 거래일 부호 평균 > 0 = 켜짐)을 **전날 종가로** 계산해 오늘 종가에 실행.
- QS: 매달 첫 처리일에 입금. 켜짐이면 QQQ 매수, 꺼짐이면 현금. 꺼짐 → 켜짐 날 현금 전액 TQQQ,
  켜짐 → 꺼짐 날 TQQQ 전부 매도. QQQ 는 팔지 않는다. 해마다 실현 이익 > 0 이면 22% (110 과 같다).
- A: 같은 입금으로 매달 QQQ 만 산다 (지금 적립 방식, 110 의 비교 대상).
- AG-US: 처음 한 번만 입금. 켜짐이면 전액 TQQQ, 꺼짐이면 현금. 공제(1,786 USD) 넘는 실현 이익 22%.
주문은 내지 않는다 — 가상 장부다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

FEE = 0.0025
TAX = 0.22
AG_EXEMPT_USD = 1_786.0
LOOKBACKS = (20, 60, 120)


def trend_on(closes: Sequence[float], t: int) -> bool:
    """t 일 종가까지로 계산한 T 규칙 (부호 평균 > 0). 이력이 모자라면 꺼짐."""
    if t < max(LOOKBACKS):
        return False
    total = 0
    for k in LOOKBACKS:
        r = closes[t] / closes[t - k] - 1
        total += 1 if r > 0 else -1 if r < 0 else 0
    return total > 0


@dataclass
class Book:
    """장부 하나. ``kind`` = "QS" · "A" · "AG"."""

    kind: str
    deposit: float
    qqq: float = 0.0
    tqqq: float = 0.0
    cash: float = 0.0
    basis: float = 0.0
    year_gain: float = 0.0
    invested: float = 0.0
    tax_paid: float = 0.0
    prev_on: bool | None = None
    last_month: str = ""
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity: list[tuple[str, float]] = field(default_factory=list)

    def value(self, qqq_px: float, tqqq_px: float) -> float:
        return self.qqq * qqq_px + self.tqqq * tqqq_px + self.cash


@dataclass
class QsState:
    books: dict[str, Book]
    last_date: str | None = None

    @classmethod
    def new(cls, deposit: float = 1_000.0) -> QsState:
        return cls({k: Book(k, deposit) for k in ("QS", "A", "AG")})

    def to_json(self) -> dict[str, Any]:
        return {
            "last_date": self.last_date,
            "books": {
                k: {**asdict(b), "equity": [list(x) for x in b.equity]}
                for k, b in self.books.items()
            },
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> QsState:
        books = {}
        for k, b in data["books"].items():
            b = dict(b)
            b["equity"] = [(str(d), float(v)) for d, v in b["equity"]]
            books[k] = Book(**b)
        return cls(books, data["last_date"])


def settle_year(state: QsState) -> None:
    """해가 바뀐 첫 처리일 앞에 부른다 — 12 월 마지막 날을 다음 해 자료 없이 처리했으면
    정산이 남는다.

    이미 정산했으면(실현 이익 0) 아무 일도 없다.
    """
    for book in state.books.values():
        _year_end(book)


def _year_end(book: Book) -> None:
    if book.kind == "QS" and book.year_gain > 0:
        book.cash -= TAX * book.year_gain
        book.tax_paid += TAX * book.year_gain
    elif book.kind == "AG" and book.year_gain > AG_EXEMPT_USD:
        t = TAX * (book.year_gain - AG_EXEMPT_USD)
        book.cash -= t
        book.tax_paid += t
    book.year_gain = 0.0


def step(
    state: QsState,
    days: Sequence[str],
    qqq: Sequence[float],
    tqqq: Sequence[float],
    t: int,
    *,
    last_of_year: bool,
) -> dict[str, Any]:
    """하루(t ≥ 1) 처리. ``last_of_year`` 는 오늘이 그해 마지막 처리일인지 (세금 계산용)."""
    d = days[t]
    on = trend_on(qqq, t - 1)
    qp, tp = qqq[t], tqqq[t]
    actions: dict[str, Any] = {
        "date": d,
        "trend_on_as_of": days[t - 1],
        "trend_on": on,
        "books": {},
    }
    for book in state.books.values():
        did: list[str] = []
        new_month = d[:7] != book.last_month
        if new_month and (book.kind != "AG" or not book.last_month):
            book.cash += book.deposit
            book.invested += book.deposit
            did.append(f"입금 {book.deposit:,.0f}")
        book.last_month = d[:7]
        if book.kind == "A":
            if book.cash > 0:
                book.qqq += book.cash * (1 - FEE) / qp
                did.append(f"QQQ 매수 {book.cash:,.2f}")
                book.cash = 0.0
        elif book.kind == "QS":
            if book.prev_on is None:
                book.prev_on = on
            if on and not book.prev_on and book.cash > 0:
                book.tqqq += book.cash * (1 - FEE) / tp
                book.basis += book.cash
                did.append(f"TQQQ 매수 {book.cash:,.2f} (추세 회복)")
                book.trades.append({"date": d, "side": "BUY_TQQQ", "usd": round(book.cash, 2)})
                book.cash = 0.0
            elif not on and book.prev_on and book.tqqq > 0:
                gross = book.tqqq * tp
                proceeds = gross * (1 - FEE)
                book.year_gain += proceeds - book.basis
                book.trades.append(
                    {
                        "date": d,
                        "side": "SELL_TQQQ",
                        "usd": round(proceeds, 2),
                        "ret": round(proceeds / book.basis - 1, 4),
                    }
                )
                did.append(f"TQQQ 매도 {proceeds:,.2f} (추세 꺾임)")
                book.cash += proceeds
                book.tqqq = book.basis = 0.0
            elif on and book.cash > 0:
                book.qqq += book.cash * (1 - FEE) / qp
                did.append(f"QQQ 매수 {book.cash:,.2f}")
                book.cash = 0.0
            book.prev_on = on
        else:  # AG
            if on and book.tqqq == 0 and book.cash > 0:
                book.tqqq = book.cash * (1 - FEE) / tp
                book.basis = book.cash
                book.trades.append({"date": d, "side": "BUY_TQQQ", "usd": round(book.cash, 2)})
                did.append(f"TQQQ 매수 {book.cash:,.2f}")
                book.cash = 0.0
            elif not on and book.tqqq > 0:
                proceeds = book.tqqq * tp * (1 - FEE)
                book.year_gain += proceeds - book.basis
                book.trades.append(
                    {
                        "date": d,
                        "side": "SELL_TQQQ",
                        "usd": round(proceeds, 2),
                        "ret": round(proceeds / book.basis - 1, 4),
                    }
                )
                did.append(f"TQQQ 매도 {proceeds:,.2f}")
                book.cash += proceeds
                book.tqqq = book.basis = 0.0
        if last_of_year:
            _year_end(book)
        value = book.value(qp, tp)
        book.equity.append((d, round(value, 4)))
        actions["books"][book.kind] = {"actions": did, "value": round(value, 2)}
    state.last_date = d
    return actions
