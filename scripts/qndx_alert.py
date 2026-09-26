"""QNDX 적립 알림·자동 예약 매수 (docs/research/89) — 매일 밤 9 시.

사용 예::

    python -m scripts.qndx_alert run            # 오늘 판단 (같은 날 두 번째부터는 텔레그램 안 보냄)
    python -m scripts.qndx_alert run --no-telegram
    python -m scripts.qndx_alert status

현재가상세(원화 환산·당일 환율)·해외 잔고를 읽고, "살 차례"이면서 ``.env`` 의
``KIS_TRADING_ENABLED=true`` 이면 **QNDX 예약 매수 1 주**를 낸다(지정가 = 현재가 +3%, 당일 유효).
켜져 있지 않으면 알림만 한다. 매도·정정·취소 코드는 없다. ``data/HALT`` 가 있으면 주문하지 않는다.
상태 ``data/qndx_alert/state.json``, 보고 ``reports/qndx_alert/<KST 날짜>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_trading.config.secrets import load_kis_credentials, load_telegram_credentials
from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.exchange.kis import KisReservationBuyer, KisTokenCache
from auto_trading.exchange.overseas import TradingDisabledError
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.kill_switch import halted_reason
from auto_trading.ops.qndx_alert import (
    Order,
    State,
    advise,
    message,
    reconcile,
    resolve_orders,
    should_order,
)

REPO = Path(__file__).resolve().parent.parent
SYMBOL = "QNDX"
EXCHANGE, ORDER_EXCHANGE = "NAS", "NASD"
"""시세용 거래소 코드 · 주문용 거래소 코드 (KIS 는 둘이 다르다)."""
MAX_LIMIT_USD = Decimal("40")
"""지정가 상한 — 설정 실수로 비싼 주문이 나가지 않게 (2026-09-26 가격 약 25 USD)."""
STATE = REPO / "data" / "qndx_alert" / "state.json"
REPORTS = REPO / "reports" / "qndx_alert"
HALT = REPO / "data" / "HALT"


def _kst_now() -> datetime:
    return datetime.now(tz=UTC) + timedelta(hours=9)


def _notify(text: str, telegram: bool) -> None:
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    if not telegram:
        return
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def _save(state: State) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8")


def _maybe_order(
    broker: KisReservationBuyer, state: State, advice: Any, enabled: bool
) -> str | None:
    """살 차례면 예약 매수 1 주. 텔레그램 줄을 돌려준다(자동 매수가 꺼져 있으면 None → 앱 안내)."""
    if advice.status != "BUY":
        return None
    ok, reason = should_order(state, advice, trading_enabled=enabled, halted=halted_reason(HALT))
    if not ok:
        return None if not enabled else f"⏸ 자동 매수 안 함 — {reason}"
    limit = Decimal(f"{advice.limit_usd:.2f}")
    power = broker.buying_power(SYMBOL, ORDER_EXCHANGE, limit)
    if power.max_qty is not None and power.max_qty < 1:
        return (
            f"⏸ 자동 매수 안 함 — 매수 가능 수량 0 (가능 금액 {power.usd_available} USD). 입금 필요"
        )
    today = advice.today
    order = Order(today, float(limit), "UNKNOWN")
    state.orders.append(order)
    _save(state)  # 보내기 전에 먼저 적는다 — 실행이 중간에 끊겨도 같은 날 다시 보내지 않는다
    try:
        result = broker.reserve_buy(SYMBOL, ORDER_EXCHANGE, 1, limit)
    except OrderStateUnknownError as exc:
        order.message = str(exc)
        return f"⚠ 예약 매수 전송 결과 모름 — 다시 보내지 않는다. 내일 밤 잔고로 확인 ({today})"
    except TradingDisabledError as exc:
        order.status, order.message = "REJECTED", str(exc)
        return f"❌ 예약 매수 안 보냄: {exc}"
    order.code, order.message = result.code, result.message
    if result.accepted:
        order.status = "SENT"
        return (
            f"🛒 예약 매수 1주 접수 — 지정가 {limit} USD 이하, 오늘 밤 미국 장 시작 때 체결."
            " 체결은 내일 밤 잔고로 확인한다"
        )
    order.status = "REJECTED"
    return f"❌ 예약 매수 거절 {result.code}: {result.message} — 내일 다시 판단"


def run(telegram: bool) -> int:
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    report_path = REPORTS / f"{today}.json"
    notified = (
        False  # 오늘 텔레그램을 이미 보냈나 (텔레그램 없는 시험 실행은 보낸 것으로 치지 않는다)
    )
    if report_path.exists():
        try:
            notified = bool(json.loads(report_path.read_text(encoding="utf-8")).get("notified"))
        except ValueError:
            notified = False
    report: dict[str, Any] = {"at_ms": int(time.time() * 1000), "date": today, "symbol": SYMBOL}
    try:
        creds = load_kis_credentials(REPO / ".env")
        broker = KisReservationBuyer(
            creds,
            KisTokenCache(REPO / "data" / "kis_token.json"),
            allowed_symbols=frozenset({SYMBOL}),
            max_limit_usd=MAX_LIMIT_USD,
        )
        try:
            detail = broker.quote_detail(SYMBOL, EXCHANGE)
            balance = broker.overseas_balance()
            last = float(detail.last)
            fx = float(detail.fx_rate) if detail.fx_rate else 0.0
            if fx <= 0 and detail.krw_price:
                fx = float(detail.krw_price) / last
            if fx <= 0:
                raise AutoTradingError("환율을 읽지 못했다 (t_rate·t_xprc 없음)")
            pos = next((p for p in balance.positions if p.symbol == SYMBOL), None)
            qty = float(pos.qty) if pos else 0.0
            avg = float(pos.avg_price) if pos else 0.0
            if STATE.exists():
                state = State.from_dict(json.loads(STATE.read_text(encoding="utf-8")))
            else:
                state = State(SYMBOL, today, qty, qty, qty * avg)  # 이미 가진 수량은 적립 밖
            before = len(state.buys)
            notes = reconcile(state, today, qty, avg, fx)
            notes += resolve_orders(state, today, bought_now=len(state.buys) > before)
            advice = advise(state, today, last, fx, weekday=now.weekday())
            order_line = _maybe_order(broker, state, advice, creds.trading_enabled)
        finally:
            broker.close()
        text = message(advice, SYMBOL, notes, order_line=order_line)
        _save(state)
        report |= {
            "status": advice.status,
            "advice": advice.to_dict(),
            "notes": notes,
            "auto_buy": creds.trading_enabled,
            "order": next((asdict(o) for o in state.orders if o.date == today), None),
            "holding_qty": qty,
            "start_date": state.start_date,
            "buys": state.to_dict()["buys"],
            "message": text,
        }
    except AutoTradingError as exc:
        text = f"⚠ {SYMBOL} 적립 알림 실패: {exc}"
        report |= {"status": "FAILED", "error": str(exc), "message": text}
    send = telegram and (not notified or report.get("status") == "FAILED")
    _notify(text, send)
    report["notified"] = notified or send
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(text)
    return 0 if report.get("status") != "FAILED" else 1


def status() -> int:
    if not STATE.exists():
        print("아직 기록이 없다 — run 을 한 번 실행하면 오늘부터 적립이 시작된다")
        return 0
    state = json.loads(STATE.read_text(encoding="utf-8"))
    print(f"{SYMBOL} 적립 시작 {state['start_date']} · 기준 수량 {state['baseline_qty']:g}")
    for b in state["buys"]:
        print(f"  {b['date']} {b['qty']:g}주 {b['cost_usd']:,.2f} USD ≈ {b['cost_krw']:,.0f}원")
    latest = sorted(REPORTS.glob("20*.json"))
    if latest:
        print(json.loads(latest[-1].read_text(encoding="utf-8")).get("message", ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()
    return run(not args.no_telegram) if args.command == "run" else status()


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _r = getattr(_s, "reconfigure", None)
        if _r is not None:
            _r(encoding="utf-8", errors="replace")
    sys.exit(main())
