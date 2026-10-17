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


def _px(v: float, kr: bool) -> str:
    return f"{v:,.0f}원" if kr else f"${v:,.2f}"


def _won(v: float) -> str:
    return f"{v / 10_000:,.1f}만 원" if abs(v) >= 10_000 else f"{v:,.0f}원"


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
    """보유 중이고 지금 가격이 손절가 이하면 ``selling`` 으로. PEAD 장부는 손절 없음."""
    if t.kind == PEAD or t.status != "holding" or last is None or last <= 0 or last > t.stop:
        return []
    t.status = "selling"
    t.note = f"손절 — {_px(last, t.kr)} ≤ {_px(t.stop, t.kr)}"
    return []


def check_hold_exit(t: Ticket, session: str, *, sell_live: bool, closing: bool) -> list[str]:
    """PEAD 보유 중 정규장(``sell_live``)이면 그 장을 세고, ``PEAD_HOLD`` 번째 장의 마감 무렵
    (``closing``)이면 ``selling`` 으로."""
    if t.kind != PEAD or t.status != "holding" or not sell_live:
        return []
    if session not in t.hold_days:
        t.hold_days.append(session)
    if len(t.hold_days) >= PEAD_HOLD and closing:
        t.status = "selling"
        t.note = f"PEAD {PEAD_HOLD} 거래일 보유 끝"
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
    if first and t.note.startswith("매도 신호"):  # 월간 추천의 매도 신호 버튼 (stock_reco)
        est = (limit - t.avg) * rem
        return [
            f"🧹 {t.name} {t.note} — {qty}주 지정가 {_px(limit, t.kr)} 매도 주문\n"
            + _loss_line(est, limit / t.avg - 1, t.kr, fx, done=False).replace(
                "예상 손해 약", "예상 손익"
            )
            + f" ({rem}주, 평균 {_px(t.avg, t.kr)} 기준, 수수료 전)"
        ]
    if first and t.kind == PEAD:
        est = (limit - t.avg) * rem
        return [
            f"📅 {t.name} {t.note} — {qty}주 지정가 {_px(limit, t.kr)} 매도 주문\n"
            + _loss_line(est, limit / t.avg - 1, t.kr, fx, done=False).replace(
                "예상 손해 약", "예상 손익"
            )
            + f" ({rem}주, 평균 {_px(t.avg, t.kr)} 기준, 수수료 전)"
        ]
    if first:
        est = (limit - t.avg) * rem
        return [
            f"🛑 {t.name} 손절 시작 — {t.note}\n{qty}주 지정가 {_px(limit, t.kr)} 매도 주문\n"
            + _loss_line(est, limit / t.avg - 1, t.kr, fx, done=False)
            + f" ({rem}주, 평균 {_px(t.avg, t.kr)} 기준, 수수료 전)"
        ]
    return []


def _done(t: Ticket, now_ms: int, fx: float) -> list[str]:
    t.status, t.closed_ms = "closed", now_ms
    sold = t.sold
    avg = t.sold_amount / sold if sold > 0 else 0.0
    pnl = t.sold_amount - t.avg * sold
    pct = pnl / (t.avg * sold) if sold > 0 and t.avg > 0 else 0.0
    head = (
        f"📅 {t.name} PEAD 매도 끝"
        if t.kind == PEAD
        else f"🧹 {t.name} 매도 신호 매도 끝"
        if t.note.startswith("매도 신호")
        else f"🛑 {t.name} 손절 매도 끝"
    )
    return [
        f"{head} · {sold:g}주 · 평균 {_px(avg, t.kr)} "
        f"({avg / t.avg - 1:+.1%})\n" + _loss_line(pnl, pct, t.kr, fx, done=True)
    ]


def status_lines(tickets: Sequence[Ticket], last: dict[str, float]) -> list[str]:
    """'📋 지금 포지션' 에 붙일 손절 현황 한 줄씩."""
    word = {"reserved": "⏳ 장 시작에 매수", "buying": "🔄 매수 중", "selling": "🛑 손절 매도 중"}
    out = []
    for t in tickets:
        if not t.active:
            continue
        if t.status == "holding" and t.kind == PEAD:
            px = last.get(t.symbol)
            now = f" · 지금 {px / t.avg - 1:+.1%}" if px and t.avg else ""
            out.append(
                f"📅 {t.name} {t.filled:g}주 · 평균 {_px(t.avg, t.kr)}{now}"
                f" · PEAD {len(t.hold_days)}/{PEAD_HOLD} 거래일 (손절 없음)"
            )
        elif t.kind == PEAD and t.status == "selling":
            out.append(f"📅 PEAD {PEAD_HOLD}일 매도 중 {t.name} {t.qty}주")
        elif t.status == "holding":
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
