"""비중 추천 종목 손절 감시 — 산 값보다 −3% 면 등록 수량만 지정가로 판다 (토스).

사용자 (2026-10-15): "3% 이상 손해를 보면 자동으로 매도" — 3% 는 매수한 종목 기준.

* 토스 API 에 서버 손절 주문이 없어서 봇이 정규장에서 2 분마다 가격을 보고, 손절가 이하면 판다.
* 매도는 지정가(매수 호가를 위에서부터 쌓아 다 차는 가격, 최우선 호가 −1% 아래로는 안 감).
  2 분 안 차면 취소하고 지금 호가로 다시, 최대 12 번. 시장가는 쓰지 않는다.
* **장부에 등록한 수량만** 판다 — 같은 종목을 다른 곳(앱 · 다른 전략)에서 더 갖고 있어도
  그 몫은 두고, 팔 수 있는 수량(``sellable``)보다 많이 내지 않는다.
* 지킬 수 없는 것: 밤사이 · 장 시작 갭(장이 열린 뒤 첫 확인 가격에 판다),
  프리 · 애프터마켓(감시 안 함), PC 꺼짐, 확인 사이 최대 2 분.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.ops.liquidate import choose_price
from auto_trading.ops.position_size import STOP, is_kr, round_price, stop_price
from auto_trading.ops.size_order import Order, Ticket, TraderLike, refresh

__all__ = [
    "MAX_SELL_ORDERS",
    "check_stop",
    "register_app",
    "status_lines",
    "step_sell",
]

MAX_SELL_ORDERS = 12


def _px(v: float, kr: bool) -> str:
    return f"{v:,.0f}원" if kr else f"${v:,.2f}"


def _won(v: float) -> str:
    return f"{v / 10_000:,.1f}만 원" if abs(v) >= 10_000 else f"{v:,.0f}원"


def register_app(tid: str, symbol: str, name: str, qty: float, avg: float, now_ms: int) -> Ticket:
    """앱에서 산 종목에 손절만 건다 — 토스 평균 단가 기준."""
    t = Ticket(
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
    t.filled, t.avg = float(int(qty)), avg
    t.stop = stop_price(avg, t.kr)
    return t


def check_stop(t: Ticket, last: float | None) -> list[str]:
    """보유 중이고 지금 가격이 손절가 이하면 ``selling`` 으로."""
    if t.status != "holding" or last is None or last <= 0 or last > t.stop:
        return []
    t.status = "selling"
    t.note = f"손절 — {_px(last, t.kr)} ≤ {_px(t.stop, t.kr)}"
    return []


def step_sell(
    t: Ticket,
    trader: TraderLike,
    now_ms: int,
    *,
    live: bool,
    sellable: float,
    fx: float = 1.0,
) -> list[str]:
    """한 번 확인 (``selling`` 만). ``live`` = 팔 수 있는 시간, ``sellable`` = 판매 가능 수량."""
    if t.status != "selling":
        return []
    refresh(t.sells, trader, now_ms, live=live)
    rem = int(t.filled - t.sold)
    if rem <= 0:
        return _done(t, now_ms, fx)
    if any(not o.closed for o in t.sells) or not live:
        return []
    if len(t.sells) >= MAX_SELL_ORDERS:
        t.status = "failed"
        t.note = f"{MAX_SELL_ORDERS} 번 내도 다 못 팖 — 토스 앱에서 확인"
        return [f"⚠️ {t.name} 손절 매도 {t.note} (남은 {rem}주)"]
    qty = min(rem, int(sellable))
    if qty <= 0:
        t.note = "팔 수 있는 수량 0 — 앱에서 판 것 아닌지 확인"
        return []
    price = choose_price(
        [(Decimal(str(p)), Decimal(str(v))) for p, v in trader.bids(t.symbol)], Decimal(qty)
    )
    if price is None:
        t.note = "매수 호가 없음 — 다음 확인 때 다시"
        return []
    limit = round_price(float(price), t.kr, up=False)
    cid = f"sz-{t.id}-s{len(t.sells)}"[:36]
    first = not t.sells
    try:
        res = trader.order(
            t.symbol, "SELL", qty, Decimal(str(limit)), client_order_id=cid, time_in_force="DAY"
        )
    except OrderStateUnknownError as exc:
        t.sells.append(Order("", cid, "SELL", qty, limit, now_ms, closed=True, status="UNKNOWN"))
        t.status, t.note = "failed", f"손절 매도 결과 모름 — 토스 앱에서 확인 ({exc})"
        return [f"⚠️ {t.name} {t.note}"]
    except AutoTradingError as exc:
        t.status, t.note = "failed", f"손절 매도 막힘: {exc}"
        return [f"⚠️ {t.name} {t.note}"]
    if not res.accepted:
        t.sells.append(Order("", cid, "SELL", qty, limit, now_ms, closed=True, status=res.code))
        t.note = f"매도 거절 {res.code} {res.message[:60]} — 다음 확인 때 다시"
        return [f"⚠️ {t.name} {t.note}"] if first else []
    t.sells.append(Order(res.code, cid, "SELL", qty, limit, now_ms))
    if first:
        return [f"🛑 {t.name} 손절 시작 — {t.note}\n{qty}주 지정가 {_px(limit, t.kr)} 매도 주문"]
    return []


def _done(t: Ticket, now_ms: int, fx: float) -> list[str]:
    t.status, t.closed_ms = "closed", now_ms
    sold = t.sold
    avg = t.sold_amount / sold if sold > 0 else 0.0
    pnl = (t.sold_amount - t.avg * sold) * (1.0 if t.kr else fx)
    return [
        f"🛑 {t.name} 손절 매도 끝 · {sold:g}주 · 평균 {_px(avg, t.kr)} "
        f"({avg / t.avg - 1:+.1%})\n손익: {_won(pnl)}"
    ]


def status_lines(tickets: Sequence[Ticket], last: dict[str, float]) -> list[str]:
    """'📋 지금 포지션' 에 붙일 손절 현황 한 줄씩."""
    word = {"reserved": "⏳ 장 시작에 매수", "buying": "🔄 매수 중", "selling": "🛑 손절 매도 중"}
    out = []
    for t in tickets:
        if not t.active:
            continue
        if t.status == "holding":
            px = last.get(t.symbol)
            if px:
                out.append(
                    f"🛡 {t.name} {t.filled:g}주 · 평균 {_px(t.avg, t.kr)}"
                    f" · 지금 {px / t.avg - 1:+.1%}"
                    f" · 손절 {_px(t.stop, t.kr)}까지 {t.stop / px - 1:+.1%}"
                )
            else:
                out.append(f"🛡 {t.name} {t.filled:g}주 · 손절 {_px(t.stop, t.kr)}")
        else:
            out.append(f"{word.get(t.status, t.status)} {t.name} {t.qty}주")
    if out:
        out.append(f"(손절 = 산 값 −{STOP:.0%}, 정규장에서 2분마다 확인)")
    return out
