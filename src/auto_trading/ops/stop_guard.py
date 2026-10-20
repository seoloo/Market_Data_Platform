"""비중 추천 종목 손절 감시 — 산 값보다 −3% 면 등록 수량만 지정가로 판다 (토스).

사용자 (2026-10-15): "3% 이상 손해를 보면 자동으로 매도" — 3% 는 매수한 종목 기준.

* 토스 API 에 서버 손절 주문이 없어서 봇이 정규장에서 2 분마다 가격을 보고, 손절가 이하면 판다.
* 매도는 지정가(매수 호가를 위에서부터 쌓아 다 차는 가격, 최우선 호가 −1% 아래로는 안 감).
  2 분 안 차면 취소하고 지금 호가로 다시, 최대 12 번. 시장가는 쓰지 않는다.
* **장부에 등록한 수량만** 판다 — 같은 종목을 다른 곳(앱 · 다른 전략)에서 더 갖고 있어도
  그 몫은 두고, 팔 수 있는 수량(``sellable``)보다 많이 내지 않는다.
* 지킬 수 없는 것: 밤사이 · 장 시작 갭(장이 열린 뒤 첫 확인 가격에 판다),
  프리 · 애프터마켓(감시 안 함), PC 꺼짐, 확인 사이 최대 2 분.
* PEAD 장부(``kind == "pead"``)는 손절이 없다. 보유 중 지나간 정규장을 세어 ``PEAD_HOLD`` 번째 장의
  마감 무렵(매수 창이 닫힌 뒤 · 매도 창 안 — 미국 마감 5 ~ 2 분 전)에 같은 지정가 매도로 판다.
  그 창에 다 못 팔면 다음 장에 이어서 판다.

언제 쓰나: 텔레그램 봇이 2 분마다 부르는 ``scripts/size_guard.tick``.
읽고 쓰는 것: 없음 — 받은 장부(:class:`~auto_trading.ops.size_order.Ticket`)를 고치고,
              주문은 넣어 준 창구(``TraderLike``)로만.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.ops.liquidate import choose_price
from auto_trading.ops.position_size import STOP, is_kr, round_price, stop_price
from auto_trading.ops.size_order import PEAD, PEAD_HOLD, Order, Ticket, TraderLike, refresh

__all__ = [
    "MAX_SELL_ORDERS",
    "check_hold_exit",
    "check_stop",
    "register_app",
    "status_lines",
    "step_sell",
]

MAX_SELL_ORDERS = 12


def _px(value: float, kr: bool) -> str:
    """[쓰임] 가격 글 — 국내 '67,900원' · 미국 '$175.13'."""
    return f"{value:,.0f}원" if kr else f"${value:,.2f}"


def _won(value: float) -> str:
    """[쓰임] 원화 금액 글 — 1 만 원 넘으면 '4.5만 원'."""
    return f"{value / 10_000:,.1f}만 원" if abs(value) >= 10_000 else f"{value:,.0f}원"


def _money(pnl: float, kr: bool, fx: float) -> str:
    """종목 통화 손익 → '-$31.80 (약 -4.5만 원)' · 국내는 원만."""
    if kr:
        return _won(pnl)
    sign = "-" if pnl < 0 else "+"
    return f"{sign}${abs(pnl):,.2f} (약 {_won(pnl * fx)})"


def _loss_line(pnl: float, pct: float, kr: bool, fx: float, *, done: bool) -> str:
    """손절 알림의 손해 줄 (사용자 2026-10-16 '손절했을 때 얼마 손해 보는지도 알림에')."""
    head = ("💸 손해" if pnl < 0 else "💰 손익") if done else "💸 예상 손해 약"
    return f"{head}: {_money(pnl, kr, fx)} · {pct:+.1%}"


def register_app(tid: str, symbol: str, name: str, qty: float, avg: float, now_ms: int) -> Ticket:
    """앱에서 산 종목에 손절만 건다 — 토스 평균 단가 기준."""
    ticket = Ticket(
        tid,
        symbol,
        name,
        "KRW" if is_kr(symbol) else "USD",
        int(qty),
        avg,
        now_ms,
        status="holding",
        source="app",
    )
    ticket.filled, ticket.avg = float(int(qty)), avg
    ticket.stop = stop_price(avg, ticket.kr)
    return ticket


def check_stop(ticket: Ticket, last: float | None) -> list[str]:
    """보유 중이고 지금 가격이 손절가 이하면 ``selling`` 으로. PEAD 장부는 손절 없음."""
    if (
        ticket.kind == PEAD
        or ticket.status != "holding"
        or last is None
        or last <= 0
        or last > ticket.stop
    ):
        return []
    ticket.status = "selling"
    ticket.note = f"손절 — {_px(last, ticket.kr)} ≤ {_px(ticket.stop, ticket.kr)}"
    return []


def check_hold_exit(ticket: Ticket, session: str, *, sell_live: bool, closing: bool) -> list[str]:
    """PEAD 보유 중 정규장(``sell_live``)이면 그 장을 세고, ``PEAD_HOLD`` 번째 장의 마감 무렵
    (``closing``)이면 ``selling`` 으로."""
    if ticket.kind != PEAD or ticket.status != "holding" or not sell_live:
        return []
    if session not in ticket.hold_days:
        ticket.hold_days.append(session)
    if len(ticket.hold_days) >= PEAD_HOLD and closing:
        ticket.status = "selling"
        ticket.note = f"PEAD {PEAD_HOLD} 거래일 보유 끝"
    return []


def step_sell(
    ticket: Ticket,
    trader: TraderLike,
    now_ms: int,
    *,
    live: bool,
    sellable: float,
    fx: float = 1.0,
) -> list[str]:
    """한 번 확인 (``selling`` 만). ``live`` = 팔 수 있는 시간, ``sellable`` = 판매 가능 수량."""
    if ticket.status != "selling":
        return []
    refresh(ticket.sells, trader, now_ms, live=live)
    rem = int(ticket.filled - ticket.sold)
    if rem <= 0:
        return _done(ticket, now_ms, fx)
    if any(not sell_order.closed for sell_order in ticket.sells) or not live:
        return []
    if len(ticket.sells) >= MAX_SELL_ORDERS:
        ticket.status = "failed"
        ticket.note = f"{MAX_SELL_ORDERS} 번 내도 다 못 팖 — 토스 앱에서 확인"
        return [f"⚠️ {ticket.name} 손절 매도 {ticket.note} (남은 {rem}주)"]
    qty = min(rem, int(sellable))
    if qty <= 0:
        ticket.note = "팔 수 있는 수량 0 — 앱에서 판 것 아닌지 확인"
        return []
    price = choose_price(
        [
            (Decimal(str(price_level)), Decimal(str(volume)))
            for price_level, volume in trader.bids(ticket.symbol)
        ],
        Decimal(qty),
    )
    if price is None:
        ticket.note = "매수 호가 없음 — 다음 확인 때 다시"
        return []
    limit = round_price(float(price), ticket.kr, up=False)
    cid = f"sz-{ticket.id}-s{len(ticket.sells)}"[:36]
    first = not ticket.sells
    try:
        res = trader.order(
            ticket.symbol,
            "SELL",
            qty,
            Decimal(str(limit)),
            client_order_id=cid,
            time_in_force="DAY",
        )
    except OrderStateUnknownError as exc:
        ticket.sells.append(
            Order("", cid, "SELL", qty, limit, now_ms, closed=True, status="UNKNOWN")
        )
        ticket.status, ticket.note = "failed", f"손절 매도 결과 모름 — 토스 앱에서 확인 ({exc})"
        return [f"⚠️ {ticket.name} {ticket.note}"]
    except AutoTradingError as exc:
        ticket.status, ticket.note = "failed", f"손절 매도 막힘: {exc}"
        return [f"⚠️ {ticket.name} {ticket.note}"]
    if not res.accepted:
        ticket.sells.append(
            Order("", cid, "SELL", qty, limit, now_ms, closed=True, status=res.code)
        )
        ticket.note = f"매도 거절 {res.code} {res.message[:60]} — 다음 확인 때 다시"
        return [f"⚠️ {ticket.name} {ticket.note}"] if first else []
    ticket.sells.append(Order(res.code, cid, "SELL", qty, limit, now_ms))
    if first and ticket.note.startswith("매도 신호"):  # 월간 추천의 매도 신호 버튼 (stock_reco)
        est = (limit - ticket.avg) * rem
        return [
            f"🧹 {ticket.name} {ticket.note} — {qty}주 지정가 {_px(limit, ticket.kr)} 매도 주문\n"
            + _loss_line(est, limit / ticket.avg - 1, ticket.kr, fx, done=False).replace(
                "예상 손해 약", "예상 손익"
            )
            + f" ({rem}주, 평균 {_px(ticket.avg, ticket.kr)} 기준, 수수료 전)"
        ]
    if first and ticket.kind == PEAD:
        est = (limit - ticket.avg) * rem
        return [
            f"📅 {ticket.name} {ticket.note} — {qty}주 지정가 {_px(limit, ticket.kr)} 매도 주문\n"
            + _loss_line(est, limit / ticket.avg - 1, ticket.kr, fx, done=False).replace(
                "예상 손해 약", "예상 손익"
            )
            + f" ({rem}주, 평균 {_px(ticket.avg, ticket.kr)} 기준, 수수료 전)"
        ]
    if first:
        est = (limit - ticket.avg) * rem
        return [
            f"🛑 {ticket.name} 손절 시작 — {ticket.note}\n"
            f"{qty}주 지정가 {_px(limit, ticket.kr)} 매도 주문\n"
            + _loss_line(est, limit / ticket.avg - 1, ticket.kr, fx, done=False)
            + f" ({rem}주, 평균 {_px(ticket.avg, ticket.kr)} 기준, 수수료 전)"
        ]
    return []


def _done(ticket: Ticket, now_ms: int, fx: float) -> list[str]:
    """[쓰임] 다 팔았다 — 장부를 ``closed`` 로, 평균 매도가 · 손익 알림 한 통.
    [이유] 손절 · 매도 신호 · PEAD 보유 끝마다 머리말만 다르고 손익 셈은 같다."""
    ticket.status, ticket.closed_ms = "closed", now_ms
    sold = ticket.sold
    avg = ticket.sold_amount / sold if sold > 0 else 0.0
    pnl = ticket.sold_amount - ticket.avg * sold
    pct = pnl / (ticket.avg * sold) if sold > 0 and ticket.avg > 0 else 0.0
    head = (
        f"📅 {ticket.name} PEAD 매도 끝"
        if ticket.kind == PEAD
        else f"🧹 {ticket.name} 매도 신호 매도 끝"
        if ticket.note.startswith("매도 신호")
        else f"🛑 {ticket.name} 손절 매도 끝"
    )
    return [
        f"{head} · {sold:g}주 · 평균 {_px(avg, ticket.kr)} "
        f"({avg / ticket.avg - 1:+.1%})\n" + _loss_line(pnl, pct, ticket.kr, fx, done=True)
    ]


def status_lines(tickets: Sequence[Ticket], last: dict[str, float]) -> list[str]:
    """'📋 지금 포지션' 에 붙일 손절 현황 한 줄씩."""
    word = {"reserved": "⏳ 장 시작에 매수", "buying": "🔄 매수 중", "selling": "🛑 손절 매도 중"}
    out = []
    for ticket in tickets:
        if not ticket.active:
            continue
        if ticket.status == "holding" and ticket.kind == PEAD:
            last_price = last.get(ticket.symbol)
            now = f" · 지금 {last_price / ticket.avg - 1:+.1%}" if last_price and ticket.avg else ""
            out.append(
                f"📅 {ticket.name} {ticket.filled:g}주 · 평균 {_px(ticket.avg, ticket.kr)}{now}"
                f" · PEAD {len(ticket.hold_days)}/{PEAD_HOLD} 거래일 (손절 없음)"
            )
        elif ticket.kind == PEAD and ticket.status == "selling":
            out.append(f"📅 PEAD {PEAD_HOLD}일 매도 중 {ticket.name} {ticket.qty}주")
        elif ticket.status == "holding":
            last_price = last.get(ticket.symbol)
            if last_price:
                out.append(
                    f"🛡 {ticket.name} {ticket.filled:g}주 · 평균 {_px(ticket.avg, ticket.kr)}"
                    f" · 지금 {last_price / ticket.avg - 1:+.1%}"
                    f" · 손절 {_px(ticket.stop, ticket.kr)}까지 {ticket.stop / last_price - 1:+.1%}"
                )
            else:
                out.append(
                    f"🛡 {ticket.name} {ticket.filled:g}주 · 손절 {_px(ticket.stop, ticket.kr)}"
                )
        else:
            out.append(f"{word.get(ticket.status, ticket.status)} {ticket.name} {ticket.qty}주")
    if out:
        out.append(f"(손절 = 산 값 −{STOP:.0%}, 정규장에서 2분마다 확인)")
    return out
