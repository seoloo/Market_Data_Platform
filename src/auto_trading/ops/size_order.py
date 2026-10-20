"""비중 추천 매수 — 예약 장부 · 정규장 시작 직후 지정가 매수 (토스, 주문 객체는 밖에서 받음).

사용자 (2026-10-15): "봇 버튼으로 매수하도록 하자. 단, 반드시 장 시작하면 매수하도록 해줘."

* 장 밖에서 누르면 예약 → **다음 정규장 시작 직후** 주문. 장중에 누르면 바로. 예약은 그 장 1 번만.
* 지정가 = min(매도 1호가 + 0.3%, 장 시작 첫 가격 + 1%). 2 분 안 차면 취소하고 다시, 최대 3 번.
  남은 수량은 취소하고 알린다. 시장가는 쓰지 않는다(사용자 규칙).
* 장 시작 가격이 추천 때보다 3% 넘게 높으면 사지 않고 다시 묻는다(갭 상승 추격 방지).
* 체결되면 실제 평균 단가 −3% 를 손절가로 적는다(손절 감시는 :mod:`stop_guard`).
* 이미 들고 있는 종목은 **추가 매수** 장부(``add_to``)로 사고, 체결되면 기존 장부에 합쳐
  **합친 평균 단가 −3%** 로 손절가를 다시 잡는다(2026-10-16 사용자 "이미 보유하고 있어도 추매").
* **PEAD 장부**(``kind == "pead"``, 2026-10-17 사용자 — 연구 240 · 241): 실적 갭 10%+ 알림의
  버튼으로 산 종목. 손절 없이 20 거래일 보유 뒤 장 마감 무렵 판다
  (:func:`stop_guard.check_hold_exit`).
  반응일 다음 정규장 시가에 사는 규칙이라 '추천 때보다 3% 넘게 오름' 추격 방지는 쓰지 않는다.

언제 쓰나: 텔레그램 봇이 2 분마다 부르는 ``scripts/size_guard.tick`` · 예약 직후.
읽고 쓰는 것: 없음 — 장부 모양(:class:`Ticket`, 파일 ``data/size_guard/state.json``)과 계산만.
              주문은 넣어 준 창구(:class:`TraderLike`, 실제는 ``exchange/toss.TossTrader``)로만.

디자인 패턴: 창구 모양(``Protocol``)을 밖에서 넣는 **의존성 주입** — 시험은 가짜 토스를 넣는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.ops.position_size import (
    GAP_UP_LIMIT,
    Advice,
    is_kr,
    round_price,
    stop_price,
)

__all__ = [
    "ACTIVE",
    "CHASE",
    "MAX_BUY_ORDERS",
    "OPEN_CAP",
    "PEAD",
    "PEAD_HOLD",
    "REPRICE_MS",
    "Order",
    "Ticket",
    "TraderLike",
    "merge_into",
    "new_ticket",
    "refresh",
    "step_buy",
]

CHASE = 0.003
"""매도 1호가 위로 얹는 폭."""
OPEN_CAP = 0.01
"""장 시작 첫 가격보다 이만큼 위로는 사지 않는다."""
REPRICE_MS = 2 * 60_000
MAX_BUY_ORDERS = 3
PEAD = "pead"
PEAD_HOLD = 20
"""PEAD 장부 보유 정규장 수 — 산 날을 1 로 세어 20 번째 장 마감 무렵 판다
(240: R+1 시가 → R+20 종가)."""
ACTIVE = frozenset({"reserved", "buying", "holding", "selling"})
_OPEN = {"PENDING", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIAL_FILLED", ""}
"""토스 주문이 아직 살아 있는 상태 (liquidate 와 같은 기준). 빈 값은 모름이라 살아 있다고 본다."""


class _Result(Protocol):
    """주문 · 취소 결과 모양 (받았나 · 주문 번호 또는 오류 코드 · 메시지)."""

    @property
    def accepted(self) -> bool: ...
    @property
    def code(self) -> str: ...
    @property
    def message(self) -> str: ...


class _Status(Protocol):
    """주문 상태 모양 (상태 · 체결 수량 · 평균가 · 체결 금액 · 수수료)."""

    @property
    def status(self) -> str: ...
    @property
    def filled_qty(self) -> Any: ...
    @property
    def avg_price(self) -> Any: ...
    @property
    def filled_amount(self) -> Any: ...
    @property
    def commission(self) -> Any: ...


class TraderLike(Protocol):
    """주문 창구 모양 — 호가 · 주문 · 상태 조회 · 취소 (``exchange/toss.TossTrader`` 가 맞는다)."""

    def asks(self, symbol: str) -> list[tuple[Any, Any]]: ...
    def bids(self, symbol: str) -> list[tuple[Any, Any]]: ...
    def order(
        self,
        symbol: str,
        side: str,
        qty: int,
        limit: Any,
        *,
        client_order_id: str,
        time_in_force: str = "CLS",
    ) -> _Result: ...
    def order_status(self, order_id: str) -> _Status: ...
    def cancel(self, order_id: str) -> _Result: ...


@dataclass(slots=True)
class Order:
    """장부의 주문 한 줄 — 칸 이름은 파일에 저장돼 바꾸지 않는다.
    ``cid`` = 우리가 붙인 주문 번호, ``order_id`` = 토스 주문 번호."""

    order_id: str
    cid: str
    side: str
    qty: int
    price: float
    placed_ms: int
    closed: bool = False
    status: str = ""
    filled: float = 0.0
    amount: float = 0.0
    fee: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Order:
        """[쓰임] 장부 JSON 한 줄 → 주문 (모르는 칸은 버린다)."""
        return cls(**{key: data[key] for key in cls.__slots__ if key in data})


@dataclass(slots=True)
class Ticket:
    """비중 추천으로 산 (또는 앱에서 사고 손절만 건) 한 종목."""

    id: str
    symbol: str
    name: str
    currency: str
    qty: int
    """사려는 수량 (앱 등록이면 등록 수량)."""
    ref_price: float
    """추천 때 가격."""
    created_ms: int
    status: str = "reserved"
    """reserved → buying → holding → selling → closed · canceled · failed · released · merged."""
    source: str = "bot"
    """bot (봇 버튼으로 매수) · app (앱에서 사고 손절만 등록)."""
    session: str = ""
    """매수를 시작한 정규장 (예: ``US:2026-10-15``)."""
    open_px: float = 0.0
    buy_block: str = ""
    """차단 후 같은 예약으로 다시 사지 않는다. 남은 주문 취소 확인까지 유지."""
    buys: list[Order] = field(default_factory=list)
    sells: list[Order] = field(default_factory=list)
    filled: float = 0.0
    avg: float = 0.0
    stop: float = 0.0
    note: str = ""
    closed_ms: int = 0
    override: bool = False
    """사람이 규칙 밖에서 수량을 정해 산 것('그래도 매수') — 주문 직전엔 못 넘는 이유만 본다."""
    add_to: str = ""
    """추가 매수면 합칠 기존 장부 번호. 체결 뒤 그 장부에 합치고 이 장부는 ``merged``."""
    kind: str = ""
    """'' (💹 비중 추천 · −3% 손절) · ``pead`` (손절 없이 ``PEAD_HOLD`` 정규장 보유)."""
    hold_days: list[str] = field(default_factory=list)
    """PEAD: 보유 중 지나간 정규장 이름 (``US:2026-10-19``)."""

    @property
    def kr(self) -> bool:
        """[쓰임] 국내 종목인가."""
        return is_kr(self.symbol)

    @property
    def sold(self) -> float:
        """[쓰임] 지금까지 판 수량."""
        return sum(order.filled for order in self.sells)

    @property
    def sold_amount(self) -> float:
        """[쓰임] 판 금액 합 (수수료 뺌)."""
        return sum(order.amount - order.fee for order in self.sells)

    @property
    def active(self) -> bool:
        """[쓰임] 아직 할 일이 남았나 (예약 · 매수 중 · 보유 · 매도 중)."""
        return self.status in ACTIVE

    def to_dict(self) -> dict[str, Any]:
        """[쓰임] 장부 JSON 으로 (칸 이름은 파일에 저장돼 바꾸지 않는다)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ticket:
        """[쓰임] 장부 JSON → 장부 (옛 파일에 없는 칸은 기본값)."""
        base = {
            key: data[key]
            for key in cls.__slots__
            if key in data and key not in ("buys", "sells", "hold_days")
        }
        ticket = cls(**base)
        ticket.hold_days = [str(day) for day in data.get("hold_days") or []]
        ticket.buys = [Order.from_dict(order) for order in data.get("buys") or []]
        ticket.sells = [Order.from_dict(order) for order in data.get("sells") or []]
        return ticket


def new_ticket(advice: Advice, now_ms: int, tid: str) -> Ticket:
    """[쓰임] 추천 → 예약 장부 하나 (추천 때 가격 · 수량 · 들어갈 정규장)."""
    return Ticket(
        tid,
        advice.symbol,
        advice.name,
        advice.currency,
        advice.qty,
        advice.price,
        now_ms,
        session=advice.entry_session,
    )


def merge_into(parent: Ticket, add: Ticket, now_ms: int) -> list[str]:
    """체결된 추가 매수(``add``)를 기존 장부에 합친다 — 합친 평균 단가 −3% 로 손절가를 다시 잡는다.

    매수 기록(``buys``)은 추가 매수 장부에 남겨 월간 복기에서 따로 센다."""
    got = add.filled
    held = parent.filled - parent.sold
    cost = held * parent.avg + sum(order.amount + order.fee for order in add.buys)
    old = parent.stop
    parent.filled += got
    parent.qty += int(got)
    parent.avg = cost / (held + got)
    parent.stop = stop_price(parent.avg, parent.kr)
    add.status, add.closed_ms = "merged", now_ms
    add.note = f"{parent.id} 에 합침"
    part = "" if got >= add.qty else f" (목표 {add.qty}주 중 {got:g}주, 나머지 취소)"
    return [
        f"✅ {add.name} {got:g}주 추가 체결 · 평균 {_px(add.avg, add.kr)}{part}\n"
        f"📦 합계 {held + got:g}주 · 평균 {_px(parent.avg, parent.kr)}\n"
        f"🛡 손절선 바꿈: {_px(old, parent.kr)} → {_px(parent.stop, parent.kr)} (평균 −3%)"
    ]


def _to_float(value: Any) -> float:
    """[쓰임] 토스 값(Decimal · None) → float (None 이면 0)."""
    return float(value) if value is not None else 0.0


def refresh(orders: Sequence[Order], trader: TraderLike, now_ms: int, *, live: bool) -> None:
    """주문 상태 갱신, 2 분 넘게 덜 찬 주문은 취소(장이 열려 있을 때만)."""
    for order in orders:
        if order.closed or not order.order_id:
            continue
        reply = trader.order_status(order.order_id)
        if (
            reply.status not in _OPEN | {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
            or not reply.status
        ):
            raise OrderStateUnknownError("주문 상태 미확인 — 신규 주문 금지")
        order.status = reply.status
        order.filled = _to_float(reply.filled_qty)
        order.amount = _to_float(reply.filled_amount) or order.filled * _to_float(reply.avg_price)
        order.fee = _to_float(reply.commission)
        if reply.status not in _OPEN:
            order.closed = True
        elif live and now_ms - order.placed_ms >= REPRICE_MS and order.filled < order.qty:
            trader.cancel(order.order_id)


def _px(value: float, kr: bool) -> str:
    """[쓰임] 가격 글 — 국내 '70,000원' · 미국 '$100.30'."""
    return f"{value:,.0f}원" if kr else f"${value:,.2f}"


def _buy_limit(ticket: Ticket, ask: float) -> float:
    """[쓰임] 매수 지정가 = min(매도 1 호가 + 0.3%, 정규장 시가 + 1%).
    [이유] 바로 체결되게 조금 얹되, 시가보다 많이 비싸게는 사지 않게."""
    cap = round_price(ticket.open_px * (1 + OPEN_CAP), ticket.kr, up=False)
    return min(round_price(ask * (1 + CHASE), ticket.kr, up=True), cap)


def _place(ticket: Ticket, trader: TraderLike, now_ms: int, qty: int, limit: float) -> list[str]:
    """[쓰임] 매수 주문 하나 내고 장부에 적는다 (당일 지정가).
    [이유] 결과를 모르면 장부를 ``failed`` 로 두고 다시 내지 않는다 — 두 번 사지 않게."""
    cid = f"sz-{ticket.id}-b{len(ticket.buys)}"[:36]
    try:
        res = trader.order(
            ticket.symbol, "BUY", qty, Decimal(str(limit)), client_order_id=cid, time_in_force="DAY"
        )
    except OrderStateUnknownError as exc:
        ticket.buys.append(Order("", cid, "BUY", qty, limit, now_ms, closed=True, status="UNKNOWN"))
        ticket.status, ticket.note = "failed", f"매수 주문 결과 모름 — 토스 앱에서 확인 ({exc})"
        return [f"⚠️ {ticket.name} {ticket.note}"]
    except AutoTradingError as exc:
        ticket.status, ticket.note = "failed", f"매수 주문 막힘: {exc}"
        return [f"⚠️ {ticket.name} {ticket.note}"]
    if not res.accepted:
        ticket.buys.append(Order("", cid, "BUY", qty, limit, now_ms, closed=True, status=res.code))
        ticket.note = f"매수 거절 {res.code} {res.message[:60]}"
        return []
    ticket.buys.append(Order(res.code, cid, "BUY", qty, limit, now_ms))
    return []


def _finish(ticket: Ticket, now_ms: int) -> list[str]:
    """[쓰임] 매수 끝 — 체결 수량 · 평균가를 적고 손절가(평균 −3%)를 잡는다.
    체결이 없으면 ``canceled``, PEAD 는 손절 없음."""
    got = sum(order.filled for order in ticket.buys)
    if got <= 0:
        ticket.status, ticket.closed_ms = "canceled", now_ms
        why = f" ({ticket.note})" if ticket.note else ""
        return [f"❎ {ticket.name} 매수 안 됨 — 체결 없이 끝{why}"]
    amount = sum(order.amount + order.fee for order in ticket.buys)
    ticket.filled = got
    ticket.avg = amount / got
    ticket.status = "holding"
    part = "" if got >= ticket.qty else f" (목표 {ticket.qty}주 중 {got:g}주, 나머지 취소)"
    if ticket.kind == PEAD:
        ticket.stop = 0.0
        return [
            f"✅ {ticket.name} {got:g}주 체결 · 평균 {_px(ticket.avg, ticket.kr)}{part}\n"
            f"📅 PEAD — 손절 없이 {PEAD_HOLD} 거래일째 장 마감 무렵 자동 매도"
        ]
    ticket.stop = stop_price(ticket.avg, ticket.kr)
    return [
        f"✅ {ticket.name} {got:g}주 체결 · 평균 {_px(ticket.avg, ticket.kr)}{part}\n"
        f"🛡 손절 등록: {_px(ticket.stop, ticket.kr)} (−3%) 이하면 자동 매도"
    ]


def step_buy(
    ticket: Ticket,
    trader: TraderLike,
    now_ms: int,
    *,
    session: str,
    live: bool,
    block: str | None = None,
    regular_open: float | None = None,
) -> list[str]:
    """한 번 확인. ``session`` = 지금(또는 다음) 정규장 이름, ``live`` = 지금 살 수 있는 시간.

    ``block`` 은 각 매수 전 다시 본 이유. 취소 확인·부분 체결 보호를 먼저 한다.
    ``regular_open`` 은 실제 첫 정규장 1분봉 시가이며 최초 관측 호가로 대체하지 않는다."""
    if ticket.status == "reserved":
        if not live:
            return []
        if ticket.session and ticket.session != session:
            ticket.status, ticket.closed_ms, ticket.note = "canceled", now_ms, "예약한 장이 지남"
            return [
                f"❎ {ticket.name} 예약 만료 — {ticket.session} 장에 못 샀어요."
                " 다시 추천 받아 주세요"
            ]
        ticket.session = session
        if block:
            ticket.status, ticket.closed_ms, ticket.note = "canceled", now_ms, block
            return [f"❎ {ticket.name} 예약 취소 — {block}"]
        if regular_open is None or regular_open <= 0:
            ticket.note = "정규장 시가 미확인 — 주문하지 않음"
            return []
        ticket.open_px = regular_open
        asks = trader.asks(ticket.symbol)
        if not asks:
            ticket.note = "매도 호가 없음 — 다음 확인 때 다시"
            return []
        ask = float(asks[0][0])
        if ticket.kind == PEAD:  # 반응일 다음 정규장 시가에 산다 — 추격 방지 없음(240 규칙)
            ticket.status = "buying"
            return _place(ticket, trader, now_ms, ticket.qty, _buy_limit(ticket, ask))
        if ticket.open_px > ticket.ref_price * (1 + GAP_UP_LIMIT):
            ticket.status, ticket.closed_ms = "canceled", now_ms
            ticket.note = f"정규장 시가가 추천 때보다 +{ticket.open_px / ticket.ref_price - 1:.1%}"
            return [
                f"❎ {ticket.name} 사지 않았어요 — {ticket.note} "
                f"({_px(ticket.ref_price, ticket.kr)} → {_px(ticket.open_px, ticket.kr)}). "
                "갭 상승 추격 방지, 다시 추천 받아 주세요"
            ]
        ticket.status = "buying"
        if ask > ticket.ref_price * (1 + GAP_UP_LIMIT):  # 추천 뒤 이미 3% 넘게 달아남
            ticket.status, ticket.closed_ms = "canceled", now_ms
            ticket.note = f"지금 가격이 추천 때보다 +{ask / ticket.ref_price - 1:.1%}"
            return [
                f"❎ {ticket.name} 사지 않았어요 — {ticket.note} "
                f"({_px(ticket.ref_price, ticket.kr)} → {_px(ask, ticket.kr)}). "
                "추격 매수 방지, 다시 추천 받아 주세요"
            ]
        return _place(ticket, trader, now_ms, ticket.qty, _buy_limit(ticket, ask))
    if ticket.status != "buying":
        return []
    if block or ticket.buy_block:
        ticket.buy_block = ticket.buy_block or block or "신규 매수 보류"
        ticket.note = ticket.buy_block
        # 취소 요청 성공을 취소 완료로 간주하지 않는다. 다음 조회로 종결을 확인한다.
        refresh(ticket.buys, trader, now_ms, live=False)
        for order in ticket.buys:
            if not order.closed and order.order_id:
                trader.cancel(order.order_id)
        if any(not order.closed for order in ticket.buys):
            return []
        return _finish(ticket, now_ms)
    refresh(ticket.buys, trader, now_ms, live=live and session == ticket.session)
    if any(not buy_order.closed for buy_order in ticket.buys):
        return []
    rem = ticket.qty - int(sum(buy_order.filled for buy_order in ticket.buys))
    if rem > 0 and live and session == ticket.session and len(ticket.buys) < MAX_BUY_ORDERS:
        asks = trader.asks(ticket.symbol)
        if asks:
            return _place(ticket, trader, now_ms, rem, _buy_limit(ticket, float(asks[0][0])))
        return []
    if rem > 0 and session == ticket.session and live:
        ticket.note = ticket.note or f"{MAX_BUY_ORDERS} 번 내도 다 못 삼"
    return _finish(ticket, now_ms)
