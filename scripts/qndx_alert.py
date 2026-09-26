"""QNDX 적립 알림 (docs/research/89) — 매일 밤 9 시, **조회만 하고 주문하지 않는다.**

사용 예::

    python -m scripts.qndx_alert run            # 오늘 알림 (같은 날 두 번째부터는 텔레그램 안 보냄)
    python -m scripts.qndx_alert run --no-telegram
    python -m scripts.qndx_alert status

한국투자증권 **읽기 전용** 어댑터로 현재가상세(원화 환산·당일 환율)와 해외 잔고만 읽는다.
상태 ``data/qndx_alert/state.json``, 보고 ``reports/qndx_alert/<KST 날짜>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_trading.config.secrets import load_kis_credentials, load_telegram_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.qndx_alert import State, advise, message, reconcile

REPO = Path(__file__).resolve().parent.parent
SYMBOL, EXCHANGE = "QNDX", "NAS"
STATE = REPO / "data" / "qndx_alert" / "state.json"
REPORTS = REPO / "reports" / "qndx_alert"


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
        broker = KisOverseasReadOnly(
            load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
        )
        try:
            detail = broker.quote_detail(SYMBOL, EXCHANGE)
            balance = broker.overseas_balance()
        finally:
            broker.close()
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
            state = State(SYMBOL, today, qty, qty, qty * avg)  # 이미 가진 수량은 적립에 넣지 않는다
        notes = reconcile(state, today, qty, avg, fx)
        advice = advise(state, today, last, fx, weekday=now.weekday())
        text = message(advice, SYMBOL, notes)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8")
        report |= {
            "status": advice.status,
            "advice": advice.to_dict(),
            "notes": notes,
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
