"""나무 모의 계좌로 국장 주도주 Paper 따라가기 — 무엇을 몇 주 사고팔지 (I/O 없음).

사용자 (2026-10-15): "모의 투자할 때 기본 금액 700만원 있다고 가정하고 진행해줘" ·
"paper 기준으로해서 모의 계좌로 진행해보면 될 것 같아".

* 따라가는 장부: 국장 그림자 Paper ② 수급 규칙(``reports/kr_shadow/latest.json`` 의 ``flow_rule``).
  Paper 는 '어제 종가로 정한 일 → 오늘 종가 실행' 이라, 모의 계좌는 다음 거래일 장 마감 전
  (15:00 ~ 15:20 KST)에 같은 일을 한다.
* 모의 계좌의 실제 예수금(5 억)은 쓰지 않고 **가상 700 만 원 장부**를 따로 센다
  (:class:`Ledger`). 크기는 Paper 와 같은 비율(한 종목 TARGET 10%, 최대 10 종목).
* 놓친 주문은 다음 날 맞춘다: Paper 가 더 이상 들지 않는 종목은 팔고, Paper 가 들고 있는데
  모의에 없는 종목은 Paper 비중만큼 산다.
* 지정가만(시장가 금지). 매수는 매도 1 호가, 매도는 매수 1 호가 — 바로 닿는 지정가.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["FEE", "MAX_POS", "START_KRW", "TARGET", "Ledger", "Order", "plan"]

START_KRW = 7_000_000
TARGET = 0.10
MAX_POS = 10
FEE = 0.0025
"""Paper 와 같은 편도 비용(수수료 + 세금 + 미끄러짐 가정) — 가상 장부에 그대로 뗀다."""


@dataclass(frozen=True, slots=True)
class Order:
    side: str
    """'buy' · 'sell'."""
    code: str
    qty: int
    price: int
    why: str


@dataclass(slots=True)
class Ledger:
    """가상 700 만 원 장부 — 현금과 체결 기록 (보유 수량은 모의 계좌 잔고가 정본)."""

    cash: float = START_KRW
    fills: list[dict[str, Any]] = field(default_factory=list)
    history: list[list[Any]] = field(default_factory=list)

    def fill(self, day: str, side: str, code: str, qty: int, price: float) -> None:
        amt = qty * price
        self.cash += -amt * (1 + FEE) if side == "buy" else amt * (1 - FEE)
        self.fills.append({"day": day, "side": side, "code": code, "qty": qty, "price": price})

    def equity(self, held: Mapping[str, int], px: Mapping[str, float]) -> float:
        return self.cash + sum(q * px.get(c, 0.0) for c, q in held.items())

    def to_json(self) -> dict[str, Any]:
        return {"cash": self.cash, "fills": self.fills, "history": self.history}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Ledger:
        return cls(
            float(data.get("cash", START_KRW)),
            list(data.get("fills") or []),
            list(data.get("history") or []),
        )


def plan(
    paper: Mapping[str, Any],
    held: Mapping[str, int],
    ask: Mapping[str, int],
    bid: Mapping[str, int],
    ledger: Ledger,
) -> list[Order]:
    """Paper ``flow_rule`` 기록 → 오늘 넣을 지정가 주문 (매도 먼저, 그다음 매수).

    ``held`` 는 모의 계좌 지금 수량, ``ask`` · ``bid`` 는 매도 · 매수 1 호가.
    """
    weights = {str(p["code"]): float(p.get("weight") or 0) for p in paper.get("positions") or []}
    sells_next = set(paper.get("sell_next_close") or {})
    buys_next: Sequence[str] | None = paper.get("buy_next_close")
    keep = [c for c in weights if c not in sells_next]
    new = [c for c in (buys_next or []) if c not in weights and c not in sells_next]
    new = new[: max(0, MAX_POS - len(keep))]
    wanted = set(keep) | set(new)

    orders: list[Order] = []
    cash = ledger.cash
    for code, q in sorted(held.items()):
        if q > 0 and code not in wanted and bid.get(code):
            why = "Paper 매도" if code in sells_next else "Paper 에 없음"
            orders.append(Order("sell", code, q, bid[code], why))
            cash += q * bid[code] * (1 - FEE)

    px = {c: float(bid.get(c) or ask.get(c) or 0) for c in set(held) | wanted}
    equity = cash + sum(q * px[c] for c, q in held.items() if c in wanted)
    targets: list[tuple[str, float, str]] = []
    for code in keep:
        if held.get(code, 0) <= 0:
            targets.append((code, weights[code] * equity, "Paper 보유 맞추기"))
        elif buys_next is not None:
            targets.append((code, TARGET * equity, "판단일 비중 채우기"))
    targets += [(c, TARGET * equity, "Paper 새 매수") for c in new]
    for code, value, why in targets:
        price = ask.get(code)
        if not price:
            continue
        gap = value - held.get(code, 0) * price
        qty = int(min(gap, cash / (1 + FEE)) // price)
        if qty > 0:
            orders.append(Order("buy", code, qty, price, why))
            cash -= qty * price * (1 + FEE)
    return orders
