"""적립 매수 판단 (docs/research/89) — 종목별 계획, 순수 계산, I/O 없음.

규칙 (사용자 결정 2026-09-26):

* 종목마다 시작일부터 **달력으로 매일 정해진 금액**이 가상으로 쌓인다
  (QNDX 1 만 원·월 30 만 원 · BTQ 1 달러·월 31 달러, :data:`PLANS`).
* 쌓인 금액 ≥ 1 주 가격(+ 수수료 0.25%)이면 "오늘 1 주 살 차례".
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
    "FEE",
    "ORDER_WINDOW",
    "PLANS",
    "Advice",
    "Buy",
    "Order",
    "Plan",
    "State",
    "advise",
    "message",
    "money",
    "reconcile",
    "resolve_orders",
    "should_order",
]

FEE = 0.0025
"""한국투자증권 해외주식 일반 수수료 (사용자 확인 수준, 문서 87)."""
LIMIT_BUFFER = 0.03
"""지정가 = 현재가 × (1 + 3%) — 장 시작 때 체결되게 여유를 둔다(매수 지정가가 시장가보다 높으면
실제 체결은 시장 가격). 밤사이 3% 넘게 오르면 체결되지 않고 다음 날 다시 한다."""
OPEN_ORDER = frozenset({"SENT", "UNKNOWN"})
"""체결·만료가 아직 확인되지 않은 주문 상태."""
ORDER_WINDOW = ("10:00", "22:20")
"""KIS 미국 예약주문 접수 시간 (KST). 끝은 서머타임 22:20 · 평시 23:20 — 1 년 내내 짧은 쪽을 쓴다.
밖에서 보내면 거절되고 그날 주문 1 건을 써 버린다 (아침에 PC 를 켜서 밀린 작업이 돌 때)."""


@dataclass(frozen=True, slots=True)
class Plan:
    symbol: str
    quote_exchange: str
    """시세용 거래소 코드 (NAS·NYS·AMS)."""
    order_exchange: str
    """주문·잔고용 거래소 코드 (NASD·NYSE·AMEX)."""
    currency: str
    """적립 통화: KRW(원화 환산) · USD(달러 그대로)."""
    daily: float
    monthly_cap: float
    max_limit_usd: float
    """지정가 상한 — 설정 실수로 비싼 주문이 나가지 않게."""


PLANS: tuple[Plan, ...] = (
    Plan("QNDX", "NAS", "NASD", "KRW", 10_000.0, 300_000.0, 40.0),
    Plan("BTQ", "NAS", "NASD", "USD", 1.0, 31.0, 6.0),
)
"""2026-09-26: QNDX 약 25.2 USD · BTQ 약 2.74 USD.
월 한도는 QNDX 사용자 지정, BTQ 는 하루 금액 × 31."""


def money(x: float, currency: str) -> str:
    return f"{x:,.0f}원" if currency == "KRW" else f"${x:,.2f}"


@dataclass(frozen=True, slots=True)
class Buy:
    date: str
    """잔고에서 늘어난 것을 발견한 날 (KST)."""
    qty: float
    cost_usd: float
    fx_rate: float
    cost: float
    """적립 통화로 뺀 금액 = cost_usd (× 환율, 원화 계획) × (1 + 수수료).

    환율은 발견한 날 고시 환율(근사)."""


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
        buys = []
        for b in d.get("buys", []):
            b = dict(b)
            if "cost_krw" in b:  # 2026-09-26 첫 판(원화만)의 이름
                b["cost"] = b.pop("cost_krw")
            buys.append(Buy(**b))
        return cls(
            symbol=str(d["symbol"]),
            start_date=str(d["start_date"]),
            baseline_qty=float(d["baseline_qty"]),
            last_qty=float(d["last_qty"]),
            last_cost_usd=float(d["last_cost_usd"]),
            buys=buys,
            orders=[Order(**o) for o in d.get("orders", [])],
        )


@dataclass(frozen=True, slots=True)
class Advice:
    symbol: str
    currency: str
    status: str
    """BUY · WAIT · CAP · PAUSE (문서 118 QS — 추세 꺼짐 또는 TQQQ 살 차례)."""
    today: str
    accrued: float
    price: float
    """1 주 가격 + 수수료 (적립 통화)."""
    last_usd: float
    fx_rate: float
    limit_usd: float
    days_to_go: int
    month_spent: float
    monthly_cap: float
    weekend: bool
    """KST 토·일 밤 — 미국 장이 열리지 않는다."""
    bought_qty: float
    """시작 뒤 적립으로 산 수량 합."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _days(start: str, today: str) -> int:
    return (date.fromisoformat(today) - date.fromisoformat(start)).days + 1


def _in_plan(usd: float, fx_rate: float, plan: Plan) -> float:
    return usd * fx_rate if plan.currency == "KRW" else usd


def reconcile(
    state: State, plan: Plan, today: str, qty: float, avg_usd: float, fx_rate: float
) -> list[str]:
    """잔고(수량·평균 매입가)로 새 매수를 찾는다. 늘면 적립금에서 빼고, 줄면 알리기만 한다."""
    notes: list[str] = []
    cost_total = qty * avg_usd
    dq = qty - state.last_qty
    if dq > 1e-9:
        cost_usd = max(cost_total - state.last_cost_usd, 0.0)
        cost = _in_plan(cost_usd, fx_rate, plan) * (1 + FEE)
        state.buys.append(Buy(today, dq, cost_usd, fx_rate, cost))
        notes.append(
            f"{state.symbol} {dq:g}주 매수 확인 — {cost_usd:,.2f} USD"
            f" ({money(cost, plan.currency)}) 적립금에서 뺌"
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
            notes.append(
                f"{state.symbol} {order.date} 예약 매수는 체결되지 않고 만료 — 오늘 다시 판단"
            )
    return notes


def should_order(
    state: State,
    advice: Advice,
    *,
    trading_enabled: bool,
    halted: str | None,
    retry_confirmed_etp_rejection: bool = False,
    kst_hhmm: str | None = None,
) -> tuple[bool, str]:
    """(오늘 자동 예약 매수를 낼까, 이유). 하루 1 건, 열린 주문이 있으면 내지 않는다.

    ``kst_hhmm`` (지금 KST "HH:MM") 이 주면 :data:`ORDER_WINDOW` 밖에서는 내지 않는다.
    """
    if advice.status != "BUY":
        return False, "살 차례가 아니다"
    if not trading_enabled:
        return False, "자동 매수 꺼짐 (.env KIS_TRADING_ENABLED=true 가 아니다) — 앱에서 직접"
    if halted is not None:
        return False, f"긴급 정지 켜짐 (data/HALT: {halted})"
    if advice.weekend:
        return False, "주말이라 오늘 밤 미국 장이 없다 — 월요일 밤에 자동으로 산다"
    start, end = ORDER_WINDOW
    if kst_hhmm is not None and not start <= kst_hhmm < end:
        later = "오늘 밤 9시 실행에서 산다" if kst_hhmm < start else "내일 다시 판단 (적립 이어짐)"
        return False, f"예약주문 접수 시간({start}~{end}) 밖 — {later}"
    today_orders = [o for o in state.orders if o.date == advice.today]
    retry_allowed = (
        retry_confirmed_etp_rejection
        and state.symbol == "QNDX"
        and len(today_orders) == 1
        and today_orders[0].status == "REJECTED"
        and today_orders[0].code == "APBK1672"
    )
    if today_orders and not retry_allowed:
        return False, "오늘은 이미 주문했다"
    if any(o.status in OPEN_ORDER for o in state.orders):
        return False, "확인되지 않은 주문이 있다 — 다음 날 잔고로 확인한 뒤"
    return True, "예약 매수 1주"


def advise(
    state: State,
    plan: Plan,
    today: str,
    last_usd: float,
    fx_rate: float,
    *,
    weekday: int,
    extra_spent: float = 0.0,
    paused: bool = False,
) -> Advice:
    """``weekday`` 는 KST 오늘의 요일 (월 0 … 일 6).

    문서 118 QS: ``extra_spent`` = TQQQ 에 쓴 돈 − TQQQ 에서 돌아온 돈 (적립금에서 더 뺀다,
    월 한도에는 넣지 않는다), ``paused`` 면 QNDX 를 사지 않는다(PAUSE).
    """
    accrued = (
        plan.daily * _days(state.start_date, today) - sum(b.cost for b in state.buys) - extra_spent
    )
    price = _in_plan(last_usd, fx_rate, plan) * (1 + FEE)
    month_spent = sum(b.cost for b in state.buys if b.date[:7] == today[:7])
    if paused:
        status = "PAUSE"
    elif month_spent + price > plan.monthly_cap:
        status = "CAP"
    elif accrued >= price:
        status = "BUY"
    else:
        status = "WAIT"
    short = max(price - accrued, 0.0)
    return Advice(
        symbol=plan.symbol,
        currency=plan.currency,
        status=status,
        today=today,
        accrued=accrued,
        price=price,
        last_usd=last_usd,
        fx_rate=fx_rate,
        limit_usd=math.floor(last_usd * (1 + LIMIT_BUFFER) * 100) / 100,
        days_to_go=math.ceil(short / plan.daily - 1e-9),
        month_spent=month_spent,
        monthly_cap=plan.monthly_cap,
        weekend=weekday >= 5,
        bought_qty=sum(b.qty for b in state.buys),
    )


def message(advice: Advice, notes: list[str], *, order_line: str | None = None) -> str:
    """종목 한 덩어리. ``order_line`` 이 있으면 자동 매수 결과 줄, 없으면 앱에서 직접 사는 안내."""
    a, c = advice, advice.currency
    head = {
        "BUY": f"🟢 {a.symbol} 오늘 1주 살 차례",
        "WAIT": f"⏳ {a.symbol} 적립 중 — 약 {a.days_to_go}일 뒤 1주",
        "CAP": f"⏸ {a.symbol} 이번 달 한도({money(a.monthly_cap, c)}) — 다음 달에",
        "PAUSE": f"⏸ {a.symbol} 매수 멈춤 (QS) — 적립금은 현금으로 쌓는다",
    }[a.status]
    per_share = f"{a.last_usd:.2f} USD" + (f" × {a.fx_rate:,.2f}원" if c == "KRW" else "")
    lines = [
        head,
        f"적립 {money(a.accrued, c)} / 1주 {money(a.price, c)} ({per_share}, 수수료 포함)",
        f"이번 달 매수 {money(a.month_spent, c)} · 지금까지 적립 매수 {a.bought_qty:g}주",
    ]
    if a.status == "BUY":
        if order_line is not None:
            lines.append(order_line)
        else:
            if a.weekend:
                lines.append("주말이라 오늘 밤 미국 장이 없다 — 월요일 밤에 사면 된다")
            lines.append(
                f"앱: 해외주식 → {a.symbol} → 매수 1주, 지정가 {a.limit_usd:.2f} USD 이하"
                " (장 전이면 예약주문)"
            )
    lines += notes
    return "\n".join(lines)
