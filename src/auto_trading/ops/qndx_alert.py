"""QNDX 적립 알림·자동 매수 판단 (docs/research/89) — 순수 계산, I/O 없음.

규칙 (사용자 결정 2026-09-26):

* 시작일부터 **달력으로 매일 1 만 원**이 가상으로 쌓인다. 월 한도 30 만 원.
* 쌓인 금액 ≥ 1 주 가격(원화 환산 + 수수료 0.25%)이면 "오늘 1 주 살 차례".
  자동 매수가 켜져 있으면 그날 밤 **예약 매수 1 주**(:func:`should_order`), 아니면 알림만.
* 체결은 다음 실행에서 잔고 수량이 늘어난 것으로 확인하고 실제 매입 금액만큼 적립금에서 뺀다.
  예약주문은 당일만 유효하므로, 다음 날까지 수량이 늘지 않았으면 그 주문은 만료로 본다.
  시작할 때 이미 가진 수량은 적립에 넣지 않는다(기준 수량).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

__all__ = [
    "DAILY_KRW",
    "FEE",
    "MONTHLY_CAP_KRW",
    "Advice",
    "Buy",
    "Order",
    "State",
    "advise",
    "message",
    "reconcile",
    "resolve_orders",
    "should_order",
]

DAILY_KRW = 10_000.0
MONTHLY_CAP_KRW = 300_000.0
FEE = 0.0025
"""한국투자증권 해외주식 일반 수수료 (사용자 확인 수준, 문서 87)."""
LIMIT_BUFFER = 0.03
"""지정가 = 현재가 × (1 + 3%) — 장 시작 때 체결되게 여유를 둔다(매수 지정가가 시장가보다 높으면
실제 체결은 시장 가격). 밤사이 3% 넘게 오르면 체결되지 않고 다음 날 다시 한다."""
OPEN_ORDER = frozenset({"SENT", "UNKNOWN"})
"""체결·만료가 아직 확인되지 않은 주문 상태."""


@dataclass(frozen=True, slots=True)
class Buy:
    date: str
    """잔고에서 늘어난 것을 발견한 날 (KST)."""
    qty: float
    cost_usd: float
    fx_rate: float
    cost_krw: float
    """cost_usd × 환율 × (1 + 수수료) — 환율은 발견한 날 KIS 고시 환율(근사)."""


@dataclass(slots=True)
class Order:
    date: str
    """주문을 낸 날 (KST)."""
    limit_usd: float
    status: str
    """SENT(접수) · UNKNOWN(전송 결과 모름) · REJECTED · FILLED · EXPIRED."""
    code: str = ""
    message: str = ""


@dataclass(slots=True)
class State:
    symbol: str
    start_date: str
    baseline_qty: float
    last_qty: float
    last_cost_usd: float
    buys: list[Buy] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> State:
        return cls(
            symbol=str(d["symbol"]),
            start_date=str(d["start_date"]),
            baseline_qty=float(d["baseline_qty"]),
            last_qty=float(d["last_qty"]),
            last_cost_usd=float(d["last_cost_usd"]),
            buys=[Buy(**b) for b in d.get("buys", [])],
            orders=[Order(**o) for o in d.get("orders", [])],
        )


@dataclass(frozen=True, slots=True)
class Advice:
    status: str
    """BUY · WAIT · CAP."""
    today: str
    accrued_krw: float
    price_krw: float
    """1 주 원화 가격 + 수수료."""
    last_usd: float
    fx_rate: float
    limit_usd: float
    days_to_go: int
    month_spent_krw: float
    weekend: bool
    """KST 토·일 밤 — 미국 장이 열리지 않는다."""
    bought_qty: float
    """시작 뒤 적립으로 산 수량 합."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _days(start: str, today: str) -> int:
    return (date.fromisoformat(today) - date.fromisoformat(start)).days + 1


def reconcile(state: State, today: str, qty: float, avg_usd: float, fx_rate: float) -> list[str]:
    """잔고(수량·평균 매입가)로 새 매수를 찾는다. 늘면 적립금에서 빼고, 줄면 알리기만 한다."""
    notes: list[str] = []
    cost_total = qty * avg_usd
    dq = qty - state.last_qty
    if dq > 1e-9:
        cost_usd = max(cost_total - state.last_cost_usd, 0.0)
        cost_krw = cost_usd * fx_rate * (1 + FEE)
        state.buys.append(Buy(today, dq, cost_usd, fx_rate, cost_krw))
        notes.append(
            f"{state.symbol} {dq:g}주 매수 확인 — {cost_usd:,.2f} USD"
            f" ≈ {cost_krw:,.0f}원 적립금에서 뺌"
        )
    elif dq < -1e-9:
        notes.append(f"{state.symbol} 보유가 {-dq:g}주 줄었다(매도?) — 적립금에는 반영하지 않는다")
    state.last_qty, state.last_cost_usd = qty, cost_total
    return notes


def resolve_orders(state: State, today: str, bought_now: bool) -> list[str]:
    """지난날 낸 예약주문(당일 유효)을 정리한다.

    이번 확인에서 수량이 늘었으면 FILLED, 아니면 EXPIRED.

    :func:`reconcile` 바로 뒤에 부른다 (``bought_now`` = 이번 확인에서 새 매수가 있었나).
    """
    notes: list[str] = []
    open_past = [o for o in state.orders if o.status in OPEN_ORDER and o.date < today]
    for k, order in enumerate(open_past):
        filled = bought_now and k == len(open_past) - 1
        order.status = "FILLED" if filled else "EXPIRED"
        if not filled:
            notes.append(f"{order.date} 예약 매수는 체결되지 않고 만료 — 오늘 다시 판단한다")
    return notes


def should_order(
    state: State, advice: Advice, *, trading_enabled: bool, halted: str | None
) -> tuple[bool, str]:
    """(오늘 자동 예약 매수를 낼까, 이유). 하루 1 건, 열린 주문이 있으면 내지 않는다."""
    if advice.status != "BUY":
        return False, "살 차례가 아니다"
    if not trading_enabled:
        return False, "자동 매수 꺼짐 (.env KIS_TRADING_ENABLED=true 가 아니다) — 앱에서 직접"
    if halted is not None:
        return False, f"긴급 정지 켜짐 (data/HALT: {halted})"
    if advice.weekend:
        return False, "주말이라 오늘 밤 미국 장이 없다 — 월요일 밤에 자동으로 산다"
    if any(o.date == advice.today for o in state.orders):
        return False, "오늘은 이미 주문했다"
    if any(o.status in OPEN_ORDER for o in state.orders):
        return False, "확인되지 않은 주문이 있다 — 다음 날 잔고로 확인한 뒤"
    return True, "예약 매수 1주"


def advise(state: State, today: str, last_usd: float, fx_rate: float, *, weekday: int) -> Advice:
    """``weekday`` 는 KST 오늘의 요일 (월 0 … 일 6)."""
    accrued = DAILY_KRW * _days(state.start_date, today) - sum(b.cost_krw for b in state.buys)
    price_krw = last_usd * fx_rate * (1 + FEE)
    month = today[:7]
    month_spent = sum(b.cost_krw for b in state.buys if b.date[:7] == month)
    if month_spent + price_krw > MONTHLY_CAP_KRW:
        status = "CAP"
    elif accrued >= price_krw:
        status = "BUY"
    else:
        status = "WAIT"
    short = max(price_krw - accrued, 0.0)
    return Advice(
        status=status,
        today=today,
        accrued_krw=accrued,
        price_krw=price_krw,
        last_usd=last_usd,
        fx_rate=fx_rate,
        limit_usd=math.floor(last_usd * (1 + LIMIT_BUFFER) * 100) / 100,
        days_to_go=math.ceil(short / DAILY_KRW),
        month_spent_krw=month_spent,
        weekend=weekday >= 5,
        bought_qty=sum(b.qty for b in state.buys),
    )


def message(advice: Advice, symbol: str, notes: list[str], *, order_line: str | None = None) -> str:
    """``order_line`` 이 있으면 자동 매수 결과 줄, 없으면 앱에서 직접 사는 안내."""
    a = advice
    head = {
        "BUY": f"🟢 {symbol} 오늘 1주 살 차례",
        "WAIT": f"⏳ {symbol} 적립 중 — 약 {a.days_to_go}일 뒤 1주",
        "CAP": f"⏸ {symbol} 이번 달 한도({MONTHLY_CAP_KRW:,.0f}원) — 다음 달에",
    }[a.status]
    lines = [
        head,
        f"적립 {a.accrued_krw:,.0f}원 / 1주 {a.price_krw:,.0f}원"
        f" ({a.last_usd:.2f} USD × {a.fx_rate:,.2f}원, 수수료 포함)",
        f"이번 달 매수 {a.month_spent_krw:,.0f}원 · 지금까지 적립 매수 {a.bought_qty:g}주",
    ]
    if a.status == "BUY":
        if order_line is not None:
            lines.append(order_line)
        else:
            if a.weekend:
                lines.append("주말이라 오늘 밤 미국 장이 없다 — 월요일 밤에 사면 된다")
            lines.append(
                f"앱: 해외주식 → {symbol} → 매수 1주, 지정가 {a.limit_usd:.2f} USD 이하"
                " (장 전이면 예약주문)"
            )
    lines += notes
    return "\n".join(lines)
