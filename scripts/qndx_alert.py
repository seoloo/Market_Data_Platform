"""적립 매수 (docs/research/89) — 매일 밤 9 시, 종목별 계획(:data:`PLANS`: QNDX·BTQ).

사용 예::

    python -m scripts.qndx_alert run            # 오늘 판단 (같은 날 두 번째부터는 텔레그램 안 보냄)
    python -m scripts.qndx_alert run --no-telegram
    python -m scripts.qndx_alert status

종목마다 현재가상세(원화 환산·당일 환율)·해외 잔고를 읽고, "살 차례"이면서 ``.env`` 의
``KIS_TRADING_ENABLED=true`` 이면 **예약 매수 1 주**를 낸다(지정가 = 현재가 +3%, 당일 유효).
켜져 있지 않으면 알림만 한다. 매도·정정·취소 코드는 없다. ``data/HALT`` 가 있으면 주문하지 않는다.
상태 ``data/accumulate/<종목>.json``, 보고 ``reports/accumulate/<종목>/<KST 날짜>.json``,
하루 요약 ``reports/accumulate/<KST 날짜>.json`` (텔레그램 보냈는지).
예약 작업 이름·스크립트 이름은 첫 판(QNDX 만) 그대로 둔다.
"""

from __future__ import annotations

import argparse
import json
import shutil
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
from auto_trading.exchange.overseas import OverseasBalance, TradingDisabledError
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.kill_switch import halted_reason
from auto_trading.ops.qndx_alert import (
    PLANS,
    Advice,
    Order,
    Plan,
    State,
    advise,
    message,
    money,
    reconcile,
    resolve_orders,
    should_order,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "accumulate"
REPORTS = REPO / "reports" / "accumulate"
HALT = REPO / "data" / "HALT"
_OLD_STATE = REPO / "data" / "qndx_alert" / "state.json"
_OLD_REPORTS = REPO / "reports" / "qndx_alert"


def _kst_now() -> datetime:
    return datetime.now(tz=UTC) + timedelta(hours=9)


def _state_path(symbol: str) -> Path:
    return DATA / f"{symbol}.json"


def _migrate() -> None:
    """첫 판(QNDX 만, 2026-09-26)의 상태·보고를 종목별 자리로 옮긴다. 한 번만."""
    if _OLD_STATE.exists() and not _state_path("QNDX").exists():
        DATA.mkdir(parents=True, exist_ok=True)
        state = State.from_dict(json.loads(_OLD_STATE.read_text(encoding="utf-8")))
        _save(state)
        _OLD_STATE.unlink()
    if _OLD_REPORTS.is_dir():
        target = REPORTS / "QNDX"
        target.mkdir(parents=True, exist_ok=True)
        for p in _OLD_REPORTS.glob("20*.json"):
            if not (target / p.name).exists():
                shutil.move(str(p), target / p.name)
        outbox = _OLD_REPORTS / "outbox.log"
        if outbox.exists() and not (REPORTS / "outbox.log").exists():
            shutil.move(str(outbox), REPORTS / "outbox.log")


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
    path = _state_path(state.symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8")


def _maybe_order(
    broker: KisReservationBuyer, plan: Plan, state: State, advice: Advice, enabled: bool
) -> str | None:
    """살 차례면 예약 매수 1 주. 텔레그램 줄을 돌려준다(자동 매수가 꺼져 있으면 None → 앱 안내)."""
    if advice.status != "BUY":
        return None
    ok, reason = should_order(state, advice, trading_enabled=enabled, halted=halted_reason(HALT))
    if not ok:
        return None if not enabled else f"⏸ 자동 매수 안 함 — {reason}"
    limit = Decimal(f"{advice.limit_usd:.2f}")
    if limit > Decimal(str(plan.max_limit_usd)):
        return f"⏸ 자동 매수 안 함 — 지정가 {limit} USD 가 상한 {plan.max_limit_usd} USD 초과"
    power = broker.buying_power(plan.symbol, plan.order_exchange, limit)
    if power.max_qty is not None and power.max_qty < 1:
        return (
            f"⏸ 자동 매수 안 함 — 매수 가능 수량 0 (가능 금액 {power.usd_available} USD)."
            " 달러 예수금 또는 통합증거금 필요"
        )
    today = advice.today
    order = Order(today, float(limit), "UNKNOWN")
    state.orders.append(order)
    _save(state)  # 보내기 전에 먼저 적는다 — 실행이 중간에 끊겨도 같은 날 다시 보내지 않는다
    try:
        result = broker.reserve_buy(plan.symbol, plan.order_exchange, 1, limit)
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


def _one(
    broker: KisReservationBuyer,
    plan: Plan,
    balance: OverseasBalance,
    now: datetime,
    enabled: bool,
) -> tuple[str, dict[str, Any]]:
    today = now.strftime("%Y-%m-%d")
    detail = broker.quote_detail(plan.symbol, plan.quote_exchange)
    last = float(detail.last)
    fx = float(detail.fx_rate) if detail.fx_rate else 0.0
    if fx <= 0 and detail.krw_price:
        fx = float(detail.krw_price) / last
    if fx <= 0:
        raise AutoTradingError(f"{plan.symbol} 환율을 읽지 못했다 (t_rate·t_xprc 없음)")
    pos = next((p for p in balance.positions if p.symbol == plan.symbol), None)
    qty = float(pos.qty) if pos else 0.0
    avg = float(pos.avg_price) if pos else 0.0
    path = _state_path(plan.symbol)
    if path.exists():
        state = State.from_dict(json.loads(path.read_text(encoding="utf-8")))
    else:
        state = State(plan.symbol, today, qty, qty, qty * avg)  # 이미 가진 수량은 적립 밖
    before = len(state.buys)
    notes = reconcile(state, plan, today, qty, avg, fx)
    notes += resolve_orders(state, today, bought_now=len(state.buys) > before)
    advice = advise(state, plan, today, last, fx, weekday=now.weekday())
    order_line = _maybe_order(broker, plan, state, advice, enabled)
    _save(state)
    text = message(advice, notes, order_line=order_line)
    report = {
        "at_ms": int(time.time() * 1000),
        "date": today,
        "symbol": plan.symbol,
        "status": advice.status,
        "advice": advice.to_dict(),
        "notes": notes,
        "auto_buy": enabled,
        "order": next((asdict(o) for o in state.orders if o.date == today), None),
        "holding_qty": qty,
        "start_date": state.start_date,
        "message": text,
    }
    return text, report


def run(telegram: bool) -> int:
    _migrate()
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    day_path = REPORTS / f"{today}.json"
    notified = False  # 오늘 텔레그램을 이미 보냈나 (텔레그램 없는 시험 실행은 치지 않는다)
    if day_path.exists():
        try:
            notified = bool(json.loads(day_path.read_text(encoding="utf-8")).get("notified"))
        except ValueError:
            notified = False
    blocks: list[str] = []
    failed = False
    try:
        creds = load_kis_credentials(REPO / ".env")
        broker = KisReservationBuyer(
            creds,
            KisTokenCache(REPO / "data" / "kis_token.json"),
            allowed_symbols=frozenset(p.symbol for p in PLANS),
            max_limit_usd=Decimal(str(max(p.max_limit_usd for p in PLANS))),
        )
        try:
            balance = broker.overseas_balance()
            for plan in PLANS:
                try:
                    text, report = _one(broker, plan, balance, now, creds.trading_enabled)
                except AutoTradingError as exc:
                    failed = True
                    text = f"⚠ {plan.symbol} 적립 판단 실패: {exc}"
                    report = {"date": today, "symbol": plan.symbol, "status": "FAILED"}
                    report |= {"error": str(exc), "message": text}
                folder = REPORTS / plan.symbol
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{today}.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8"
                )
                blocks.append(text)
        finally:
            broker.close()
    except AutoTradingError as exc:
        failed = True
        blocks.append(f"⚠ 적립 매수 실행 실패: {exc}")
    text = "\n\n".join(blocks)
    send = telegram and (not notified or failed)
    _notify(text, send)
    REPORTS.mkdir(parents=True, exist_ok=True)
    day = {"date": today, "notified": notified or send, "failed": failed, "message": text}
    day_path.write_text(json.dumps(day, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(text)
    return 1 if failed else 0


def status() -> int:
    _migrate()
    for plan in PLANS:
        path = _state_path(plan.symbol)
        if not path.exists():
            print(f"{plan.symbol}: 아직 기록이 없다 — run 을 한 번 실행하면 그날부터 적립")
            continue
        state = State.from_dict(json.loads(path.read_text(encoding="utf-8")))
        print(
            f"{plan.symbol} 적립 시작 {state.start_date} · 매일 {money(plan.daily, plan.currency)}"
            f" · 기준 수량 {state.baseline_qty:g}"
        )
        for b in state.buys:
            print(f"  {b.date} {b.qty:g}주 {b.cost_usd:,.2f} USD ({money(b.cost, plan.currency)})")
    days = sorted(REPORTS.glob("20*.json"))
    if days:
        print(json.loads(days[-1].read_text(encoding="utf-8")).get("message", ""))
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
