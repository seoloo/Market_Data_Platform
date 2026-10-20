"""💰 미국 자동 적립 판단 (docs/research/89) — 종목별 계획, 계산만.

언제 쓰나: 평일 21:00 ``scripts/toss_accumulate.py`` (토스 · 2026-10-04 한국투자에서 옮김).
읽고 쓰는 것: 없음 — 장부 ``data/toss/<기호>.json`` 모양(:class:`State`)과 계산만.

규칙 (사용자 결정 2026-09-26):

* 종목마다 시작일부터 **달력으로 매일 정해진 금액**이 가상으로 쌓인다
  (QNDX 1 만 원·월 30 만 원 · BTQ 1 달러·월 31 달러, :data:`PLANS`).
* 쌓인 금액 ≥ 1 주 가격(+ 수수료)이면 "오늘 1 주 살 차례".
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

from auto_trading.notify.style import SUB, sub

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
    """적립 종목 하나의 계획 (하루 금액 · 월 한도 · 지정가 상한)."""

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
    changes: tuple[tuple[str, float], ...] = ()
    """하루 금액 바꾸기 (그날부터, 새 하루 금액). 그 전까지 쌓인 적립금은 그대로 이어진다."""

    def rate_on(self, day: str) -> float:
        """[쓰임] 그날의 하루 금액 (바뀐 기록 중 그날까지 가장 늦은 것)."""
        rate = self.daily
        for start, new_rate in sorted(self.changes):
            if day >= start:
                rate = new_rate
        return rate

    def accrual(self, start: str, today: str) -> float:
        """[쓰임] ``start`` ~ ``today`` (양 끝 포함) 하루 금액의 합 — 바뀐 날부터는 새 금액.
        [이유] 하루 금액을 바꿔도 지난 날은 그때 금액으로 센다."""
        total = 0.0
        changes_inside = sorted(change for change in self.changes if start < change[0] <= today)
        segment_start, rate = start, self.rate_on(start)
        for change_day, new_rate in changes_inside:
            total += rate * (_days(segment_start, change_day) - 1)
            segment_start, rate = change_day, new_rate
        return total + rate * _days(segment_start, today)


PLANS: tuple[Plan, ...] = (
    Plan("QNDX", "NAS", "NASD", "KRW", 10_000.0, 300_000.0, 40.0),
    Plan("BTQ", "NAS", "NASD", "USD", 1.0, 31.0, 6.0),
)
"""2026-09-26: QNDX 약 25.2 USD · BTQ 약 2.74 USD.
월 한도는 QNDX 사용자 지정, BTQ 는 하루 금액 × 31."""


def money(amount: float, currency: str) -> str:
    """[쓰임] 금액 글자 — 원화는 '12,345원', 달러는 '$12.34'."""
    return f"{amount:,.0f}원" if currency == "KRW" else f"${amount:,.2f}"


@dataclass(frozen=True, slots=True)
class Buy:
    """적립으로 산 것 하나 (잔고가 늘어난 것을 발견한 날 기록)."""

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
    """적립 주문 하나 (장부 ``orders`` 한 줄 — 키 이름은 파일에 저장돼 바꾸지 않는다)."""

    date: str
    """주문을 낸 날 (KST)."""
    limit_usd: float
    status: str
    """SENT(접수) · UNKNOWN(전송 결과 모름) · REJECTED · FILLED · EXPIRED."""
    code: str = ""
    message: str = ""


@dataclass(slots=True)
class State:
    """적립 종목 장부 (``data/toss/<기호>.json``)."""

    symbol: str
    start_date: str
    baseline_qty: float
    """시작할 때 이미 가진 수량 — 적립에 넣지 않는다(기준 수량)."""
    last_qty: float
    """지난 확인 때 실제 보유 수량."""
    last_cost_usd: float
    """지난 확인 때 실제 보유 원가 (수량 × 평균가)."""
    buys: list[Buy] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        buys = []
        for raw_buy in data.get("buys", []):
            buy = dict(raw_buy)
            if "cost_krw" in buy:  # 2026-09-26 첫 판(원화만)의 이름
                buy["cost"] = buy.pop("cost_krw")
            buys.append(Buy(**buy))
        return cls(
            symbol=str(data["symbol"]),
            start_date=str(data["start_date"]),
            baseline_qty=float(data["baseline_qty"]),
            last_qty=float(data["last_qty"]),
            last_cost_usd=float(data["last_cost_usd"]),
            buys=buys,
            orders=[Order(**order) for order in data.get("orders", [])],
        )


@dataclass(frozen=True, slots=True)
class Advice:
    """오늘 판단 (알림 · 보고에 그대로 쓴다)."""

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
    """[쓰임] 두 날짜 사이 날수 — 양 끝 포함."""
    return (date.fromisoformat(today) - date.fromisoformat(start)).days + 1


def _in_plan(usd: float, fx_rate: float, plan: Plan) -> float:
    """[쓰임] 달러 금액을 그 계획의 적립 통화로 (원화 계획이면 × 환율)."""
    return usd * fx_rate if plan.currency == "KRW" else usd


def reconcile(
    state: State,
    plan: Plan,
    today: str,
    qty: float,
    avg_usd: float,
    fx_rate: float,
    *,
    fee: float = FEE,
) -> list[str]:
    """[쓰임] 실제 잔고(수량 · 평균 매입가)로 새 매수를 찾는다.
    늘면 적립금에서 빼고, 줄면 알리기만 한다.
    [이유] 체결 금액은 주문 상세가 아니라 잔고 원가 차이로 잰다
           (한국투자 판에서 쓰던 방식 그대로)."""
    notes: list[str] = []
    cost_total = qty * avg_usd
    qty_change = qty - state.last_qty
    if qty_change > 1e-9:
        cost_usd = max(cost_total - state.last_cost_usd, 0.0)
        cost = _in_plan(cost_usd, fx_rate, plan) * (1 + fee)
        state.buys.append(Buy(today, qty_change, cost_usd, fx_rate, cost))
        notes.append(
            f"{state.symbol} {qty_change:g}주 매수 확인 — {cost_usd:,.2f} USD"
            f" ({money(cost, plan.currency)}) 적립금에서 뺌"
        )
    elif qty_change < -1e-9:
        notes.append(
            f"{state.symbol} 보유가 {-qty_change:g}주 줄었다(매도?) — 적립금에는 반영하지 않는다"
        )
    state.last_qty, state.last_cost_usd = qty, cost_total
    return notes


def resolve_orders(state: State, today: str, bought_now: bool) -> list[str]:
    """[쓰임] 지난날 낸 예약주문(당일 유효)을 정리한다 — 이번 확인에서 수량이 늘었으면 마지막 것은
    FILLED, 나머지는 EXPIRED.

    :func:`reconcile` 바로 뒤에 부른다 (``bought_now`` = 이번 확인에서 새 매수가 있었나)."""
    notes: list[str] = []
    open_past = [
        order for order in state.orders if order.status in OPEN_ORDER and order.date < today
    ]
    for index, order in enumerate(open_past):
        filled = bought_now and index == len(open_past) - 1
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
    """[쓰임] (오늘 자동 매수를 낼까, 이유).
    [이유] 하루 1 건, 확인 안 된 주문이 있으면 내지 않는다 — 같은 1 주를 두 번 사지 않게.

    ``kst_hhmm`` (지금 KST "HH:MM") 을 주면 :data:`ORDER_WINDOW` 밖에서는 내지 않는다.
    (안내 문구의 ``KIS_TRADING_ENABLED`` 는 한국투자 때 이름 — 문구는 바꾸지 않았다.)"""
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
    today_orders = [order for order in state.orders if order.date == advice.today]
    retry_allowed = (
        retry_confirmed_etp_rejection
        and state.symbol == "QNDX"
        and len(today_orders) == 1
        and today_orders[0].status == "REJECTED"
        and today_orders[0].code == "APBK1672"
    )
    if today_orders and not retry_allowed:
        return False, "오늘은 이미 주문했다"
    if any(order.status in OPEN_ORDER for order in state.orders):
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
    fee: float = FEE,
) -> Advice:
    """[쓰임] 오늘 판단 — 쌓인 적립금(쓴 돈 뺌)이 1 주 값(+ 수수료)이면 BUY, 월 한도면 CAP,
    QS 멈춤이면 PAUSE, 아니면 WAIT.

    ``weekday`` 는 KST 오늘의 요일 (월 0 … 일 6).
    문서 118 QS: ``extra_spent`` = TQQQ 에 쓴 돈 − TQQQ 에서 돌아온 돈 (적립금에서 더 뺀다,
    월 한도에는 넣지 않는다), ``paused`` 면 QNDX 를 사지 않는다(PAUSE)."""
    spent = sum(buy.cost for buy in state.buys)
    accrued = plan.accrual(state.start_date, today) - spent - extra_spent
    price = _in_plan(last_usd, fx_rate, plan) * (1 + fee)
    month_spent = sum(buy.cost for buy in state.buys if buy.date[:7] == today[:7])
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
        days_to_go=math.ceil(short / plan.rate_on(today) - 1e-9),
        month_spent=month_spent,
        monthly_cap=plan.monthly_cap,
        weekend=weekday >= 5,
        bought_qty=sum(buy.qty for buy in state.buys),
    )


def message(advice: Advice, notes: list[str], *, order_line: str | None = None) -> str:
    """[쓰임] 종목 한 덩어리 알림 글.

    ``order_line`` 이 있으면 자동 매수 결과 줄, 없으면 앱에서 직접 사는 안내."""
    currency = advice.currency
    head = {
        "BUY": f"🟢 {advice.symbol}  오늘 1주 살 차례",
        "WAIT": f"⏳ {advice.symbol}  적립 중 · 약 {advice.days_to_go}일 뒤 1주",
        "CAP": (
            f"⏸ {advice.symbol}  이번 달 한도({money(advice.monthly_cap, currency)}) · 다음 달에"
        ),
        "PAUSE": f"⏸ {advice.symbol}  매수 멈춤 (QS) · 적립금은 현금으로 쌓는다",
    }[advice.status]
    per_share = f"{advice.last_usd:.2f} USD"
    if currency == "KRW":
        per_share += f" × {advice.fx_rate:,.2f}원"
    lines = [
        head,
        f"{SUB}적립 {money(advice.accrued, currency)} / 1주 {money(advice.price, currency)}",
        sub(f"{per_share} · 수수료 포함"),
        f"{SUB}이번 달 {money(advice.month_spent, currency)}"
        f" · 적립 매수 누적 {advice.bought_qty:g}주",
    ]
    if advice.status == "BUY":
        if order_line is not None:
            lines.append(SUB + order_line)
        else:
            if advice.weekend:
                lines.append(f"{SUB}주말이라 오늘 밤 미국 장이 없다 — 월요일 밤에 사면 된다")
            lines.append(f"{SUB}📱 앱: 해외주식 → {advice.symbol} → 매수 1주")
            lines.append(sub(f"지정가 {advice.limit_usd:.2f} USD 이하 (장 전이면 예약주문)"))
    lines += [SUB + note for note in notes]
    return "\n".join(lines)
