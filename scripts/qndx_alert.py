"""적립 매수 (docs/research/89) — 매일 밤 9 시, 종목별 계획(:data:`PLANS`: QNDX·BTQ).

사용 예::

    python -m scripts.qndx_alert run            # 오늘 판단 (같은 날 두 번째부터는 텔레그램 안 보냄)
    python -m scripts.qndx_alert run --no-telegram
    python -m scripts.qndx_alert status
    python -m scripts.qndx_alert qs-preview     # QS(문서 118) 오늘 판단만 보기 — 주문·저장 없음
    python -m scripts.qndx_alert qs-start       # QS 켜기 — 이때부터 QNDX 적립이 QS 규칙을 따른다

종목마다 현재가상세(원화 환산·당일 환율)·해외 잔고를 읽고, "살 차례"이면서 ``.env`` 의
``KIS_TRADING_ENABLED=true`` 이면 **예약 매수 1 주**를 낸다(지정가 = 현재가 +3%, 당일 유효).
켜져 있지 않으면 알림만 한다. 매도·정정·취소 코드는 없다. ``data/HALT`` 가 있으면 주문하지 않는다.
상태 ``data/accumulate/<종목>.json``, 보고 ``reports/accumulate/<종목>/<KST 날짜>.json``,
하루 요약 ``reports/accumulate/<KST 날짜>.json`` (텔레그램 보냈는지).
예약 작업 이름·스크립트 이름은 첫 판(QNDX 만) 그대로 둔다.

QS (문서 118): ``data/accumulate/QS.json`` 이 있을 때만 켜진다(``qs-start``). QQQ 추세가 꺼지면
QNDX 매수를 멈추고, 다시 켜지면 쌓인 적립금으로 TQQQ 예약 매수, 꺼지면 QS 가 산 TQQQ 만 예약 매도.
TQQQ 매수가 거절되면 다시 보내지 않고 알린다. 보고 ``reports/accumulate/QS/<KST 날짜>.json``.
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
from auto_trading.ops.qs_live import (
    MAX_TQQQ_LIMIT_USD,
    MAX_TQQQ_QTY,
    TQQQ,
    TQQQ_ORDER_EXCHANGE,
    TQQQ_QUOTE_EXCHANGE,
    QsLiveState,
    plan_tqqq_order,
    qndx_paused,
    reconcile_tqqq,
    resolve_tqqq_orders,
    trend_parts,
    update_signal,
)
from auto_trading.ops.qs_paper import trend_on

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "accumulate"
REPORTS = REPO / "reports" / "accumulate"
HALT = REPO / "data" / "HALT"
QS_STATE = DATA / "QS.json"
"""있으면 QS 가 켜진 것이다 (``qs-start`` 가 만든다)."""
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
    broker: KisReservationBuyer,
    plan: Plan,
    state: State,
    advice: Advice,
    enabled: bool,
    now: datetime,
    *,
    retry_confirmed_etp_rejection: bool = False,
) -> str | None:
    """살 차례면 예약 매수 1 주. 텔레그램 줄을 돌려준다(자동 매수가 꺼져 있으면 None → 앱 안내)."""
    if advice.status != "BUY":
        return None
    ok, reason = should_order(
        state,
        advice,
        trading_enabled=enabled,
        halted=halted_reason(HALT),
        retry_confirmed_etp_rejection=retry_confirmed_etp_rejection,
        kst_hhmm=now.strftime("%H:%M"),
    )
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
    *,
    retry_confirmed_etp_rejection: bool = False,
    extra_spent: float = 0.0,
    paused: bool = False,
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
    advice = advise(
        state,
        plan,
        today,
        last,
        fx,
        weekday=now.weekday(),
        extra_spent=extra_spent,
        paused=paused,
    )
    order_line = _maybe_order(
        broker,
        plan,
        state,
        advice,
        enabled,
        now,
        retry_confirmed_etp_rejection=retry_confirmed_etp_rejection,
    )
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
        "order": next((asdict(o) for o in reversed(state.orders) if o.date == today), None),
        "holding_qty": qty,
        "start_date": state.start_date,
        "message": text,
    }
    return text, report


# ---------------------------------------------------------------- QS (문서 118)


def _qs_load() -> QsLiveState | None:
    if not QS_STATE.exists():
        return None
    return QsLiveState.from_dict(json.loads(QS_STATE.read_text(encoding="utf-8")))


def _qs_save(qs: QsLiveState) -> None:
    QS_STATE.parent.mkdir(parents=True, exist_ok=True)
    QS_STATE.write_text(json.dumps(qs.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8")


def _qs_signal(broker: KisReservationBuyer, now: datetime) -> tuple[bool, str, dict[str, float]]:
    """끝난 미국 거래일 종가까지의 QQQ T 규칙 (켜짐, 기준일, 20·60·120 일 수익)."""
    since = (now - timedelta(days=260)).strftime("%Y-%m-%d")
    bars = broker.daily_history("QQQ", since=since, exchange="NAS", adjusted=True)
    now_ms = int(time.time() * 1000)
    closed = [b for b in bars if b.close_ms <= now_ms]
    if len(closed) < 121:
        raise AutoTradingError(f"QQQ 일봉이 모자란다 ({len(closed)} < 121)")
    as_of = datetime.fromtimestamp(closed[-1].open_ms / 1000, UTC).strftime("%Y-%m-%d")
    if (now.date() - datetime.fromisoformat(as_of).date()).days > 6:
        raise AutoTradingError(f"QQQ 마지막 종가가 오래됐다 ({as_of}) — 판단하지 않는다")
    closes = [float(b.close) for b in closed]
    return trend_on(closes, len(closes) - 1), as_of, trend_parts(closes)


def _qs_prepare(
    broker: KisReservationBuyer, qs: QsLiveState, balance: OverseasBalance, now: datetime
) -> tuple[list[str], dict[str, Any]]:
    """TQQQ 체결 확인 → 오늘 신호. QNDX 판단 전에 부른다."""
    today = now.strftime("%Y-%m-%d")
    detail = broker.quote_detail(TQQQ, TQQQ_QUOTE_EXCHANGE)
    last = float(detail.last)
    fx = float(detail.fx_rate) if detail.fx_rate else 0.0
    if fx <= 0 and detail.krw_price:
        fx = float(detail.krw_price) / last
    if fx <= 0 or last <= 0:
        raise AutoTradingError("TQQQ 시세·환율을 읽지 못했다")
    pos = next((p for p in balance.positions if p.symbol == TQQQ), None)
    qty = float(pos.qty) if pos else 0.0
    avg = float(pos.avg_price) if pos else 0.0
    before = qs.last_qty
    notes = reconcile_tqqq(qs, today, qty, avg, last, fx)
    notes += resolve_tqqq_orders(qs, today, changed=abs(qty - before) > 1e-9)
    on, as_of, parts = _qs_signal(broker, now)
    notes += update_signal(qs, today, as_of, on)
    ctx = {"on": on, "as_of": as_of, "parts": parts, "tqqq_last": last, "fx": fx}
    return notes, ctx


def _qs_order(
    broker: KisReservationBuyer,
    qs: QsLiveState,
    ctx: dict[str, Any],
    accrued_krw: float,
    enabled: bool,
    now: datetime,
) -> str | None:
    today = now.strftime("%Y-%m-%d")
    plan = plan_tqqq_order(
        qs,
        today,
        accrued_krw=accrued_krw,
        fx_rate=ctx["fx"],
        last_usd=ctx["tqqq_last"],
        weekday=now.weekday(),
        kst_hhmm=now.strftime("%H:%M"),
        trading_enabled=enabled,
        halted=halted_reason(HALT),
    )
    if plan.side == "NONE":
        return None if plan.reason == "할 일 없음" else f"⏸ QS TQQQ 주문 안 함 — {plan.reason}"
    limit = Decimal(f"{plan.limit_usd:.2f}")
    qty = plan.qty
    if plan.side == "BUY":
        power = broker.buying_power(TQQQ, TQQQ_ORDER_EXCHANGE, limit)
        if power.max_qty is not None and power.max_qty < qty:
            qty = int(power.max_qty)
            if qty < 1:
                return (
                    "⏸ QS TQQQ 매수 안 함 — 매수 가능 수량 0"
                    f" (가능 금액 {power.usd_available} USD)."
                    " 달러 예수금 또는 통합증거금 필요 — 내일 다시"
                )
    order = Order(today, float(limit), "UNKNOWN")
    qs.orders.append(order)
    _qs_save(qs)  # 보내기 전에 먼저 적는다 — 같은 날 다시 보내지 않는다
    word = "매수" if plan.side == "BUY" else "매도"
    send = broker.reserve_buy if plan.side == "BUY" else broker.reserve_sell
    try:
        result = send(TQQQ, TQQQ_ORDER_EXCHANGE, qty, limit)
    except OrderStateUnknownError as exc:
        order.message = str(exc)
        return f"⚠ QS TQQQ 예약 {word} 전송 결과 모름 — 다시 보내지 않는다. 내일 밤 잔고로 확인"
    except TradingDisabledError as exc:
        order.status, order.message = "REJECTED", str(exc)
        return f"❌ QS TQQQ 예약 {word} 안 보냄: {exc}"
    order.code, order.message = result.code, result.message
    if result.accepted:
        order.status = "SENT"
        bound = "이하" if plan.side == "BUY" else "이상"
        return (
            f"🛒 QS TQQQ {qty}주 예약 {word} 접수 — 지정가 {limit} USD {bound},"
            " 오늘 밤 미국 장 시작 때 체결. 체결은 내일 밤 잔고로 확인"
        )
    order.status = "REJECTED"
    if plan.side == "BUY":
        qs.want = "NONE"
        return (
            f"❌ QS TQQQ 예약 매수 거절 {result.code}: {result.message} — 다시 보내지 않는다."
            " 기본예탁금(1,000만 원)·교육 이수를 확인해 주세요. 적립금은 QNDX 로 이어간다"
        )
    return f"❌ QS TQQQ 예약 매도 거절 {result.code}: {result.message} — 내일 다시 판단"


def _qs_text(qs: QsLiveState, ctx: dict[str, Any], notes: list[str], line: str | None) -> str:
    p = ctx["parts"]
    head = (
        f"📊 QS: QQQ 추세 {'켜짐' if ctx['on'] else '꺼짐'} ({ctx['as_of']} 종가 · 20일 "
        f"{p['r20']:+.1%} · 60일 {p['r60']:+.1%} · 120일 {p['r120']:+.1%})"
    )
    lines = [
        head,
        f"QS TQQQ {qs.strategy_qty:g}주 · TQQQ 순지출 {qs.net_krw():,.0f}원 · 할 일 {qs.want}",
    ]
    lines += notes
    if line:
        lines.append(line)
    return "\n".join(lines)


def _broker(creds: Any, with_qs: bool) -> KisReservationBuyer:
    symbols = {p.symbol for p in PLANS} | ({TQQQ} if with_qs else set())
    limit = max([p.max_limit_usd for p in PLANS] + ([MAX_TQQQ_LIMIT_USD] if with_qs else []))
    return KisReservationBuyer(
        creds,
        KisTokenCache(REPO / "data" / "kis_token.json"),
        allowed_symbols=frozenset(symbols),
        max_limit_usd=Decimal(str(limit)),
        max_qty={TQQQ: MAX_TQQQ_QTY} if with_qs else {},
        sell_symbols=frozenset({TQQQ}) if with_qs else frozenset(),
    )


def _one_with_qs(
    broker: KisReservationBuyer,
    qs: QsLiveState,
    plan: Plan,
    balance: OverseasBalance,
    now: datetime,
    creds: Any,
) -> tuple[str, dict[str, Any]]:
    """QNDX + QS: TQQQ 확인·신호 → QNDX 판단(멈춤·적립금 반영) → TQQQ 주문."""
    today = now.strftime("%Y-%m-%d")
    notes, ctx = _qs_prepare(broker, qs, balance, now)
    _qs_save(qs)
    text, report = _one(
        broker,
        plan,
        balance,
        now,
        creds.trading_enabled,
        extra_spent=qs.net_krw(),
        paused=qndx_paused(qs),
    )
    accrued = float(report["advice"]["accrued"])
    line = _qs_order(broker, qs, ctx, accrued, creds.trading_enabled, now)
    _qs_save(qs)
    qs_text = _qs_text(qs, ctx, notes, line)
    folder = REPORTS / "QS"
    folder.mkdir(parents=True, exist_ok=True)
    qs_report = {
        "at_ms": int(time.time() * 1000),
        "date": today,
        "trend_on": ctx["on"],
        "as_of": ctx["as_of"],
        "parts": ctx["parts"],
        "tqqq_last": ctx["tqqq_last"],
        "fx": ctx["fx"],
        "strategy_tqqq_qty": qs.strategy_qty,
        "net_krw": qs.net_krw(),
        "want": qs.want,
        "qndx_paused": qndx_paused(qs),
        "notes": notes,
        "order_line": line,
        "order": next((asdict(o) for o in reversed(qs.orders) if o.date == today), None),
        "message": qs_text,
    }
    (folder / f"{today}.json").write_text(
        json.dumps(qs_report, ensure_ascii=False, indent=1) + "\n", "utf-8"
    )
    report["qs"] = qs_report
    return qs_text + "\n" + text, report


ATTEMPTS = 5
RETRY_WAIT_S = 60.0
"""증권사 일시 오류(2026-09-26 21:00 EGW00300)에 대비해 실패한 종목만 1 분 간격으로 다시 판단한다.
같은 날 주문은 :func:`should_order` 가 1 건으로 막으므로 다시 판단해도 중복 주문이 없다."""


def _attempt(pending: list[Plan], now: datetime, results: dict[str, str]) -> dict[str, str]:
    """``pending`` 종목을 한 번 판단한다. 성공은 ``results`` 에, 실패 {종목: 이유} 는 돌려준다."""
    today = now.strftime("%Y-%m-%d")
    errors: dict[str, str] = {}
    try:
        creds = load_kis_credentials(REPO / ".env")
        qs = _qs_load()
        broker = _broker(creds, qs is not None)
        try:
            balance = broker.overseas_balance()
            for plan in pending:
                try:
                    if qs is not None and plan.symbol == "QNDX":
                        text, report = _one_with_qs(broker, qs, plan, balance, now, creds)
                    else:
                        text, report = _one(broker, plan, balance, now, creds.trading_enabled)
                except AutoTradingError as exc:
                    errors[plan.symbol] = str(exc)
                    continue
                folder = REPORTS / plan.symbol
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{today}.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8"
                )
                results[plan.symbol] = text
        finally:
            broker.close()
    except AutoTradingError as exc:
        errors |= {p.symbol: str(exc) for p in pending if p.symbol not in results}
    return errors


def run(telegram: bool, *, attempts: int = ATTEMPTS, wait_s: float = RETRY_WAIT_S) -> int:
    _migrate()
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    day_path = REPORTS / f"{today}.json"
    notified = last_failed = False  # 텔레그램 없는 시험 실행은 보낸 것으로 치지 않는다
    if day_path.exists():
        try:
            prev = json.loads(day_path.read_text(encoding="utf-8"))
            notified = bool(prev.get("notified"))
            last_failed = bool(prev.get("last_sent_failed", prev.get("failed")))  # 옛 형식
        except ValueError:
            pass
    results: dict[str, str] = {}
    errors: dict[str, str] = {}
    for k in range(attempts):
        pending = [p for p in PLANS if p.symbol not in results]
        errors = _attempt(pending, now, results)
        if not errors:
            break
        if k < attempts - 1:
            print(f"실패 {sorted(errors)} — {wait_s:.0f}초 뒤 다시 ({k + 2}/{attempts})")
            time.sleep(wait_s)
    for symbol, why in errors.items():
        text = f"⚠ {symbol} 적립 판단 실패 ({attempts}번 시도): {why}"
        report = {"date": today, "symbol": symbol, "status": "FAILED", "error": why}
        folder = REPORTS / symbol
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{today}.json").write_text(
            json.dumps(report | {"message": text}, ensure_ascii=False, indent=1) + "\n", "utf-8"
        )
        results[symbol] = text
    failed = bool(errors)
    text = "\n\n".join(results[p.symbol] for p in PLANS)
    send = telegram and (not notified or failed or last_failed)  # 실패 뒤 성공이면 다시 알린다
    _notify(text, send)
    REPORTS.mkdir(parents=True, exist_ok=True)
    day = {
        "date": today,
        "notified": notified or send,
        "last_sent_failed": failed if send else last_failed,
        "failed": failed,
        "message": text,
    }
    day_path.write_text(json.dumps(day, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(text)
    return 1 if failed else 0


def retry_qndx_etp(today: str, *, telegram: bool) -> int:
    """명시적으로 지정한 오늘에만 확정된 해외 ETP 거절을 QNDX 1주로 한 번 재시도한다."""
    now = _kst_now()
    if today != now.strftime("%Y-%m-%d"):
        print("날짜가 현재 KST 날짜와 다르다 — 주문하지 않는다")
        return 2
    path = _state_path("QNDX")
    if not path.exists():
        print("QNDX 상태가 없다 — 주문하지 않는다")
        return 2
    state = State.from_dict(json.loads(path.read_text(encoding="utf-8")))
    prior = [o for o in state.orders if o.date == today]
    if not (len(prior) == 1 and prior[0].status == "REJECTED" and prior[0].code == "APBK1672"):
        print("오늘의 해외 ETP 미신청 확정 거절 1건이 아니다 — 주문하지 않는다")
        return 2
    if any(o.status in {"SENT", "UNKNOWN"} for o in state.orders):
        print("미확인 주문이 있다 — 주문하지 않는다")
        return 2
    if halted_reason(HALT) is not None:
        print("긴급 정지 상태 — 주문하지 않는다")
        return 2
    qs = _qs_load()
    if qs is not None and qndx_paused(qs):
        print("QS 가 QNDX 매수를 멈춘 상태다 — 주문하지 않는다")
        return 2
    try:
        creds = load_kis_credentials(REPO / ".env")
        if not creds.trading_enabled:
            print("KIS 자동 매수 스위치가 꺼져 있다 — 주문하지 않는다")
            return 2
        plan = next(p for p in PLANS if p.symbol == "QNDX")
        broker = KisReservationBuyer(
            creds,
            KisTokenCache(REPO / "data" / "kis_token.json"),
            allowed_symbols=frozenset({"QNDX"}),
            max_limit_usd=Decimal(str(plan.max_limit_usd)),
        )
        try:
            balance = broker.overseas_balance()
            text, report = _one(
                broker,
                plan,
                balance,
                now,
                True,
                retry_confirmed_etp_rejection=True,
            )
        finally:
            broker.close()
    except AutoTradingError as exc:
        print(f"조회 실패 — 주문 재시도 중단: {exc}")
        return 1
    folder = REPORTS / "QNDX"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{today}.json").write_text(
        json.dumps(report | {"manual_etp_retry": True}, ensure_ascii=False, indent=1) + "\n",
        "utf-8",
    )
    _notify(text, telegram)
    print(text)
    return 0 if report["order"] and report["order"]["status"] == "SENT" else 1


def qs_start() -> int:
    """QS 를 켠다: 지금 가진 TQQQ 를 기준 수량(건드리지 않음)으로 적고 오늘 신호를 보여 준다."""
    if QS_STATE.exists():
        print(f"이미 켜져 있다: {QS_STATE}")
        return 2
    now = _kst_now()
    creds = load_kis_credentials(REPO / ".env")
    broker = _broker(creds, True)
    try:
        balance = broker.overseas_balance()
        on, as_of, parts = _qs_signal(broker, now)
    finally:
        broker.close()
    pos = next((p for p in balance.positions if p.symbol == TQQQ), None)
    qty = float(pos.qty) if pos else 0.0
    avg = float(pos.avg_price) if pos else 0.0
    qs = QsLiveState(now.strftime("%Y-%m-%d"), qty, qty, qty * avg)
    _qs_save(qs)
    print(
        f"QS 켜짐 — 시작 {qs.start_date} · 기존 TQQQ {qty:g}주는 건드리지 않음 · 오늘 추세 "
        f"{'켜짐' if on else '꺼짐'} ({as_of} 종가, 20일 {parts['r20']:+.1%} · 60일 "
        f"{parts['r60']:+.1%} · 120일 {parts['r120']:+.1%}). 다음 21:00 실행부터 QS 규칙을 따른다"
    )
    return 0


def qs_preview() -> int:
    """오늘 밤 QS 가 무엇을 할지 — 주문·상태 저장 없음 (QS 를 켜기 전에도 볼 수 있다)."""
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    creds = load_kis_credentials(REPO / ".env")
    broker = _broker(creds, True)
    try:
        balance = broker.overseas_balance()
        qs = _qs_load()
        if qs is None:
            pos = next((p for p in balance.positions if p.symbol == TQQQ), None)
            qty = float(pos.qty) if pos else 0.0
            qs = QsLiveState(today, qty, qty, qty * (float(pos.avg_price) if pos else 0.0))
        notes, ctx = _qs_prepare(broker, qs, balance, now)
        plan = next(p for p in PLANS if p.symbol == "QNDX")
        state = State.from_dict(json.loads(_state_path("QNDX").read_text(encoding="utf-8")))
        detail = broker.quote_detail(plan.symbol, plan.quote_exchange)
        fx = float(detail.fx_rate) if detail.fx_rate else ctx["fx"]
        advice = advise(
            state,
            plan,
            today,
            float(detail.last),
            fx,
            weekday=now.weekday(),
            extra_spent=qs.net_krw(),
            paused=qndx_paused(qs),
        )
        order = plan_tqqq_order(
            qs,
            today,
            accrued_krw=advice.accrued,
            fx_rate=ctx["fx"],
            last_usd=ctx["tqqq_last"],
            weekday=now.weekday(),
            kst_hhmm=None,
            trading_enabled=creds.trading_enabled,
            halted=halted_reason(HALT),
        )
    finally:
        broker.close()
    print("[미리 보기 — 주문·저장 없음]")
    print(_qs_text(qs, ctx, notes, f"TQQQ: {order.side} {order.qty}주 · {order.reason}"))
    print(message(advice, []))
    return 0


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
    parser.add_argument(
        "command", choices=("run", "status", "retry-qndx-etp", "qs-start", "qs-preview")
    )
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--date", help="retry-qndx-etp 전용: 현재 KST 날짜 (YYYY-MM-DD)")
    args = parser.parse_args()
    if args.command == "retry-qndx-etp":
        if not args.date:
            parser.error("retry-qndx-etp 에는 --date 가 필요하다")
        return retry_qndx_etp(args.date, telegram=not args.no_telegram)
    if args.date:
        parser.error("--date 는 retry-qndx-etp 전용이다")
    if args.command == "qs-start":
        return qs_start()
    if args.command == "qs-preview":
        return qs_preview()
    return run(not args.no_telegram) if args.command == "run" else status()


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _r = getattr(_s, "reconfigure", None)
        if _r is not None:
            _r(encoding="utf-8", errors="replace")
    sys.exit(main())
