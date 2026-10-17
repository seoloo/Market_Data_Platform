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
    @property
    def accepted(self) -> bool: ...
    @property
    def code(self) -> str: ...
    @property
    def message(self) -> str: ...


class _Status(Protocol):
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
    def from_dict(cls, d: dict[str, Any]) -> Order:
        return cls(**{k: d[k] for k in cls.__slots__ if k in d})


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
        return is_kr(self.symbol)

    @property
    def sold(self) -> float:
        return sum(o.filled for o in self.sells)

    @property
    def sold_amount(self) -> float:
        return sum(o.amount - o.fee for o in self.sells)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ticket:
        base = {
            k: d[k] for k in cls.__slots__ if k in d and k not in ("buys", "sells", "hold_days")
        }
        t = cls(**base)
        t.hold_days = [str(x) for x in d.get("hold_days") or []]
        t.buys = [Order.from_dict(o) for o in d.get("buys") or []]
        t.sells = [Order.from_dict(o) for o in d.get("sells") or []]
        return t


def new_ticket(a: Advice, now_ms: int, tid: str) -> Ticket:
    return Ticket(
        tid, a.symbol, a.name, a.currency, a.qty, a.price, now_ms, session=a.entry_session
    )


def merge_into(parent: Ticket, add: Ticket, now_ms: int) -> list[str]:
    """체결된 추가 매수(``add``)를 기존 장부에 합친다 — 합친 평균 단가 −3% 로 손절가를 다시 잡는다.

    매수 기록(``buys``)은 추가 매수 장부에 남겨 월간 복기에서 따로 센다."""
    got = add.filled
    held = parent.filled - parent.sold
    cost = held * parent.avg + sum(o.amount + o.fee for o in add.buys)
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


def _f(x: Any) -> float:
    return float(x) if x is not None else 0.0


def refresh(orders: Sequence[Order], trader: TraderLike, now_ms: int, *, live: bool) -> None:
    """주문 상태 갱신, 2 분 넘게 덜 찬 주문은 취소(장이 열려 있을 때만)."""
    for o in orders:
        if o.closed or not o.order_id:
            continue
        st = trader.order_status(o.order_id)
        if st.status not in _OPEN | {"FILLED", "CANCELED", "REJECTED", "EXPIRED"} or not st.status:
            raise OrderStateUnknownError("주문 상태 미확인 — 신규 주문 금지")
        o.status = st.status
        o.filled = _f(st.filled_qty)
        o.amount = _f(st.filled_amount) or o.filled * _f(st.avg_price)
        o.fee = _f(st.commission)
        if st.status not in _OPEN:
            o.closed = True
        elif live and now_ms - o.placed_ms >= REPRICE_MS and o.filled < o.qty:
            trader.cancel(o.order_id)


def _px(v: float, kr: bool) -> str:
    return f"{v:,.0f}원" if kr else f"${v:,.2f}"


def _buy_limit(t: Ticket, ask: float) -> float:
    cap = round_price(t.open_px * (1 + OPEN_CAP), t.kr, up=False)
    return min(round_price(ask * (1 + CHASE), t.kr, up=True), cap)


def _place(t: Ticket, trader: TraderLike, now_ms: int, qty: int, limit: float) -> list[str]:
    cid = f"sz-{t.id}-b{len(t.buys)}"[:36]
    try:
        res = trader.order(
            t.symbol, "BUY", qty, Decimal(str(limit)), client_order_id=cid, time_in_force="DAY"
        )
    except OrderStateUnknownError as exc:
        t.buys.append(Order("", cid, "BUY", qty, limit, now_ms, closed=True, status="UNKNOWN"))
        t.status, t.note = "failed", f"매수 주문 결과 모름 — 토스 앱에서 확인 ({exc})"
        return [f"⚠️ {t.name} {t.note}"]
    except AutoTradingError as exc:
        t.status, t.note = "failed", f"매수 주문 막힘: {exc}"
        return [f"⚠️ {t.name} {t.note}"]
    if not res.accepted:
        t.buys.append(Order("", cid, "BUY", qty, limit, now_ms, closed=True, status=res.code))
        t.note = f"매수 거절 {res.code} {res.message[:60]}"
        return []
    t.buys.append(Order(res.code, cid, "BUY", qty, limit, now_ms))
    return []


def _finish(t: Ticket, now_ms: int) -> list[str]:
    got = sum(o.filled for o in t.buys)
    if got <= 0:
        t.status, t.closed_ms = "canceled", now_ms
        why = f" ({t.note})" if t.note else ""
        return [f"❎ {t.name} 매수 안 됨 — 체결 없이 끝{why}"]
    amount = sum(o.amount + o.fee for o in t.buys)
    t.filled = got
    t.avg = amount / got
    t.status = "holding"
    part = "" if got >= t.qty else f" (목표 {t.qty}주 중 {got:g}주, 나머지 취소)"
    if t.kind == PEAD:
        t.stop = 0.0
        return [
            f"✅ {t.name} {got:g}주 체결 · 평균 {_px(t.avg, t.kr)}{part}\n"
            f"📅 PEAD — 손절 없이 {PEAD_HOLD} 거래일째 장 마감 무렵 자동 매도"
        ]
    t.stop = stop_price(t.avg, t.kr)
    return [
        f"✅ {t.name} {got:g}주 체결 · 평균 {_px(t.avg, t.kr)}{part}\n"
        f"🛡 손절 등록: {_px(t.stop, t.kr)} (−3%) 이하면 자동 매도"
    ]


def step_buy(
    t: Ticket,
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
    if t.status == "reserved":
        if not live:
            return []
        if t.session and t.session != session:
            t.status, t.closed_ms, t.note = "canceled", now_ms, "예약한 장이 지남"
            return [f"❎ {t.name} 예약 만료 — {t.session} 장에 못 샀어요. 다시 추천 받아 주세요"]
        t.session = session
        if block:
            t.status, t.closed_ms, t.note = "canceled", now_ms, block
            return [f"❎ {t.name} 예약 취소 — {block}"]
        if regular_open is None or regular_open <= 0:
            t.note = "정규장 시가 미확인 — 주문하지 않음"
            return []
        t.open_px = regular_open
        asks = trader.asks(t.symbol)
        if not asks:
            t.note = "매도 호가 없음 — 다음 확인 때 다시"
            return []
        ask = float(asks[0][0])
        if t.kind == PEAD:  # 반응일 다음 정규장 시가에 산다 — 추격 방지 없음(240 규칙)
            t.status = "buying"
            return _place(t, trader, now_ms, t.qty, _buy_limit(t, ask))
        if t.open_px > t.ref_price * (1 + GAP_UP_LIMIT):
            t.status, t.closed_ms = "canceled", now_ms
            t.note = f"정규장 시가가 추천 때보다 +{t.open_px / t.ref_price - 1:.1%}"
            return [
                f"❎ {t.name} 사지 않았어요 — {t.note} "
                f"({_px(t.ref_price, t.kr)} → {_px(t.open_px, t.kr)}). "
                "갭 상승 추격 방지, 다시 추천 받아 주세요"
            ]
        t.status = "buying"
        if ask > t.ref_price * (1 + GAP_UP_LIMIT):  # 추천 뒤 이미 3% 넘게 달아남
            t.status, t.closed_ms = "canceled", now_ms
            t.note = f"지금 가격이 추천 때보다 +{ask / t.ref_price - 1:.1%}"
            return [
                f"❎ {t.name} 사지 않았어요 — {t.note} "
                f"({_px(t.ref_price, t.kr)} → {_px(ask, t.kr)}). "
                "추격 매수 방지, 다시 추천 받아 주세요"
            ]
        return _place(t, trader, now_ms, t.qty, _buy_limit(t, ask))
    if t.status != "buying":
        return []
    if block or t.buy_block:
        t.buy_block = t.buy_block or block or "신규 매수 보류"
        t.note = t.buy_block
        # 취소 요청 성공을 취소 완료로 간주하지 않는다. 다음 조회로 종결을 확인한다.
        refresh(t.buys, trader, now_ms, live=False)
        for order in t.buys:
            if not order.closed and order.order_id:
                trader.cancel(order.order_id)
        if any(not order.closed for order in t.buys):
            return []
        return _finish(t, now_ms)
    refresh(t.buys, trader, now_ms, live=live and session == t.session)
    if any(not o.closed for o in t.buys):
        return []
    rem = t.qty - int(sum(o.filled for o in t.buys))
    if rem > 0 and live and session == t.session and len(t.buys) < MAX_BUY_ORDERS:
        asks = trader.asks(t.symbol)
        if asks:
            return _place(t, trader, now_ms, rem, _buy_limit(t, float(asks[0][0])))
        return []
    if rem > 0 and session == t.session and live:
        t.note = t.note or f"{MAX_BUY_ORDERS} 번 내도 다 못 삼"
    return _finish(t, now_ms)
