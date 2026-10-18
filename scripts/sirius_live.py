"""✨ Sirius 실거래 (문서 279) — 274 GBM 미장 주도주 10 종목 · 6 달 보유, 토스 소수점(금액 주문).

사용 예 (numpy · scikit-learn 이 있는 연구 venv)::

    build/tcn-venv/Scripts/python.exe -m scripts.sirius_live start --krw 1000000  # 장부 시작(한 번)
    build/tcn-venv/Scripts/python.exe -m scripts.sirius_live run       # 예약: 미국 정규장 안에서
    build/tcn-venv/Scripts/python.exe -m scripts.sirius_live preview   # 주문 · 저장 없이 보기
    build/tcn-venv/Scripts/python.exe -m scripts.sirius_live status

* 주문은 설정 ``live.sirius``(기본 꺼짐) · ``.env`` ``TOSS_TRADING_ENABLED`` 둘 다 켜져야 나간다.
  ``data/HALT`` · ``data/HALT_sirius`` 면 주문하지 않는다.
* 금액 주문은 정규장 시작 ~ 종료 1 시간 전만 받는다(토스) — 그 안에서만 주문한다.
* 보내기 전에 주문을 저장하고, 결과 모름이면 다시 보내지 않는다(사람이 확인).
* 장부 ``data/sirius/state.json``, 보고 ``reports/sirius/latest.json``.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
_SITE = REPO / ".venv" / "Lib" / "site-packages"
if _SITE.exists() and str(_SITE) not in sys.path:
    sys.path.append(str(_SITE))

from auto_trading.config.secrets import (  # noqa: E402
    load_telegram_credentials,
    load_toss_credentials,
)
from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError  # noqa: E402
from auto_trading.exchange.overseas import TradingDisabledError  # noqa: E402
from auto_trading.exchange.toss import TossTokenCache, TossTrader  # noqa: E402
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier  # noqa: E402
from auto_trading.notify.style import RULE, sub  # noqa: E402
from auto_trading.ops import sirius_live as core  # noqa: E402
from auto_trading.ops import user_settings  # noqa: E402
from auto_trading.ops.kill_switch import halted_for  # noqa: E402

ROOT = REPO / "data" / "sirius"
STATE = ROOT / "state.json"
REPORTS = REPO / "reports" / "sirius"
HALT = REPO / "data" / "HALT"
TOKEN = REPO / "data" / "toss_token.json"
TAG = "✨ [Sirius]"
NY = ZoneInfo("America/New_York")
KST = timedelta(hours=9)
MAX_ORDER_SHARE = 0.10
"""주문 한 건 상한 = 전략 평가액의 10% (보통 한 종목 몫은 약 1.7%)."""
MIN_MAX_ORDER_USD = 25.0
POLL = (3, 5, 10, 20)
"""시장가 주문 체결 확인 간격(초)."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _kst(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, UTC) + KST).strftime("%m/%d %H:%M")


def _us_today(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(NY).strftime("%Y-%m-%d")


def _load() -> dict[str, Any] | None:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None


def _save(state: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _notify(text: str) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def _trader(symbols: set[str], max_usd: float) -> TossTrader:
    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        fraction_symbols=frozenset(symbols),
        max_amount_usd=Decimal(str(round(max_usd, 2))),
    )


def _window(trader: TossTrader, today: str, now_ms: int) -> str | None:
    """주문해도 되는 때면 None, 아니면 이유. 정규장 시작 + 1 분 ~ 종료 − 61 분."""
    try:
        session = trader.checked_regular_session(today, kr=False)
    except AutoTradingError as exc:
        return f"거래일 달력 확인 실패 ({exc})"
    if session is None:
        return f"{today} 미국 휴장"
    start = datetime.fromisoformat(session[0]).timestamp() * 1000
    end = datetime.fromisoformat(session[1]).timestamp() * 1000
    if now_ms < start + 60_000:
        return "정규장 시작 전"
    if now_ms > end - 61 * 60_000:
        return "정규장 종료 1 시간 전이 지남 — 소수점 주문 접수 안 됨"
    return None


def _bars_fresh(trader: TossTrader, last_bar: str, today: str) -> bool:
    """일봉 마지막 날 다음 ~ 오늘 전에 정규장이 없었는지 (LT Paper 갱신 확인)."""
    d = datetime.fromisoformat(last_bar) + timedelta(days=1)
    end = datetime.fromisoformat(today)
    while d < end:
        if (end - d).days > 10:
            return False
        try:
            if trader.us_regular_session(d.strftime("%Y-%m-%d")) is not None:
                return False
        except AutoTradingError:
            return False
        d += timedelta(days=1)
    return True


def _reconcile(trader: TossTrader, state: dict[str, Any], today: str) -> list[str]:
    """보낸 주문의 체결을 장부에 넣는다. 결과 모름(UNKNOWN)은 알리고 멈춤 표시."""
    lines: list[str] = []
    for o in state["orders"]:
        if o["status"] == "UNKNOWN":
            lines.append(
                f"⚠ {o['sym']} {o['date']} 주문 결과 모름 — 토스 앱에서 확인 뒤 'resolve' 필요"
            )
            continue
        if o["status"] != "SENT":
            continue
        st = trader.order_status(o["order_id"])
        if st.status in ("PENDING", "PENDING_CANCEL"):
            continue
        amount = st.filled_amount
        if amount is None and st.avg_price is not None:
            amount = st.filled_qty * st.avg_price
        o["filled_qty"], o["filled_usd"] = float(st.filled_qty), float(amount or 0)
        o["commission"] = float(st.commission or 0)
        o["status"] = "FILLED" if o["filled_qty"] > 0 else "EXPIRED"
        core.apply(state, o, o["date"], o["filled_qty"], o["filled_usd"], o["commission"])
        word = "매수" if o["side"] == "BUY" else "매도"
        if o["filled_qty"] > 0:
            px = o["filled_usd"] / o["filled_qty"]
            got = f"{o['filled_qty']:.6g}주 @ ${px:,.2f} = ${o['filled_usd']:,.2f}"
            lines.append(f"✅ {o['sym']} {word} {got}")
        else:
            lines.append(f"⚠ {o['sym']} {word} 체결 없음 ({st.status}) — 다음 실행에서 다시")
    return lines


def _send(trader: TossTrader, state: dict[str, Any], order: dict[str, Any], today: str) -> str:
    cid = core.client_id(today, order["sym"], order["side"])
    rec = {**order, "date": today, "client_id": cid, "status": "UNKNOWN", "order_id": "",
           "filled_qty": 0.0, "filled_usd": 0.0, "commission": 0.0}  # fmt: skip
    state["orders"].append(rec)
    _save(state)
    try:
        if order["side"] == "BUY":
            res = trader.amount_buy(order["sym"], Decimal(str(order["usd"])), client_order_id=cid)
        else:
            res = trader.fractional_sell(
                order["sym"], Decimal(str(order["qty"])), client_order_id=cid
            )
    except OrderStateUnknownError as exc:
        _save(state)
        return f"⚠ {order['sym']} 주문 결과 모름 — 다시 보내지 않음 ({exc})"
    except TradingDisabledError as exc:
        rec["status"] = "BLOCKED"
        _save(state)
        return f"⛔ {order['sym']} 주문 막힘 ({exc})"
    if res.accepted:
        rec["status"], rec["order_id"] = "SENT", res.code
    else:
        rec["status"], rec["code"] = "REJECTED", res.code
    _save(state)
    what = f"${order['usd']:,.2f}" if order["side"] == "BUY" else f"{order['qty']:.6g}주"
    word = {"buy": "매수", "sell": "매도", "roll": "넘기기"}[order["kind"]]
    return f"{'📨' if res.accepted else '❌'} {order['sym']} {word} {what}" + (
        "" if res.accepted else f" 거절 ({res.code}: {res.message})"
    )


def liquidate_step(now_ms: int | None = None) -> list[str]:
    """정리 요청이 있으면 정규장(종료 1 시간 전까지)에서 Sirius 몫 전부를 소수점 시장가로 판다.

    설정 ``live.sirius`` · 정지 파일과 상관없이(사람이 누른 정리) — 토스 주문 허용은 필요.
    알림은 부르는 쪽이 보낸다. 다 팔면 요청을 지운다."""
    now = _now_ms() if now_ms is None else now_ms
    req = core.liquidation_request(REPO)
    state = _load()
    if req is None or state is None:
        return []
    trader = _trader(set(), MIN_MAX_ORDER_USD)
    if not trader.credentials.trading_enabled:
        return ["⛔ Sirius 정리 — TOSS_TRADING_ENABLED 꺼짐, 못 팖"]
    today = _us_today(now)
    lines = _reconcile(trader, state, today)
    _save(state)
    if not state["lots"]:
        core.clear_liquidation(REPO)
        return [*lines, f"✅ Sirius 정리 끝 — 보유 없음 · 현금 ${state['cash_usd']:,.2f}"]
    why = _window(trader, today, now)
    if why:
        return [*lines, f"⏳ Sirius 정리 대기 — {why}"] if lines else []
    if any(o["status"] in ("SENT", "UNKNOWN") for o in state["orders"]):
        return [*lines, "⏳ Sirius 정리 — 확인 안 된 주문이 있어 다음 확인 때"]
    syms = {lot["sym"] for lot in state["lots"]}
    prices = {k: float(v) for k, v in trader.prices(sorted(syms)).items()}
    trader = _trader(syms, MIN_MAX_ORDER_USD)
    for o in core.liquidation_orders(state, prices):
        lines.append(_send(trader, state, o, today))
    for wait in POLL:
        if not any(o["status"] == "SENT" for o in state["orders"]):
            break
        time.sleep(wait)
        lines += _reconcile(trader, state, today)
        _save(state)
    _report(state, prices)
    if not state["lots"]:
        core.clear_liquidation(REPO)
        lines.append(f"✅ Sirius 정리 끝 — 현금 ${state['cash_usd']:,.2f}")
    return lines


def run(preview: bool = False) -> int:
    now = _now_ms()
    state = _load()
    if state is None:
        print("장부가 없다 — 먼저 start --krw <금액>")
        return 2
    if core.liquidation_request(REPO) is not None and not preview:
        out = liquidate_step(now)
        if out:
            _notify("\n".join([f"{TAG} 정리 {_kst(now)}", RULE, *out]))
        return 0
    today = _us_today(now)
    on = bool(user_settings.get(REPO, "live.sirius"))
    halted = halted_for(HALT, "sirius")
    lines: list[str] = []
    alerts: list[str] = []
    trader = _trader(set(), MIN_MAX_ORDER_USD)
    if not preview and not trader.credentials.trading_enabled:
        print("TOSS_TRADING_ENABLED 꺼짐 — 주문 안 함")
        return 0
    if not preview and not on:
        print("설정 live.sirius 꺼짐 — 주문 안 함")
        return 0
    why = _window(trader, today, now)
    if why and not preview:
        print(f"주문 안 함: {why}")
        return 0
    if not preview:
        lines += _reconcile(trader, state, today)
        _save(state)
    pending = [o for o in state["orders"] if o["status"] in ("SENT", "UNKNOWN")]
    from scripts import leader_ml_paper as lp

    days, close, dvol, _spy, _opens = lp.load_panel()
    last_bar = days[-1]
    due_month = core.month_due(state["decisions"], last_bar, today)
    picks: list[str] | None = None
    tranche: str | None = None
    if due_month and not _bars_fresh(trader, last_bar, today):
        alerts.append(
            f"⚠ 일봉이 {last_bar} 에서 멈춤 — LT Paper 갱신 확인. 새 묶음은 다음 실행에서"
        )
        due_month = None
    if due_month:
        dec = lp.make_decider(days, close, dvol)(len(days) - 1)
        if not preview:
            state["decisions"][due_month] = {"picks": dec["GBM"], "prob": dec["GBM_prob"],
                                             "decided": today, "bought": False}  # fmt: skip
        picks, tranche = dec["GBM"], due_month
        lines.append(f"📋 {due_month} 결정 상위 10: {', '.join(dec['GBM'])}")
    else:  # 결정은 했지만 아직 못 산 달(달러 부족 등)
        open_dec = [(k, v) for k, v in state["decisions"].items() if not v.get("bought")]
        if open_dec:
            k, v = max(open_dec)
            picks, tranche = v["picks"], k
    if pending and not preview:
        alerts.append("⏳ 확인 안 된 주문이 있어 새 주문은 다음 실행에서")
        picks = None
    syms = sorted({lot["sym"] for lot in state["lots"]} | set(picks or []))
    prices = {k: float(v) for k, v in trader.prices(syms).items()} if syms else {}
    orders = core.plan(state, today, days, prices, picks, tranche) if not pending else []
    eq = core.equity(state, prices)
    trader = _trader(set(syms), max(MIN_MAX_ORDER_USD, eq * MAX_ORDER_SHARE))
    need = sum(float(o.get("usd") or 0) for o in orders if o["side"] == "BUY")
    if preview:
        for o in orders:
            what = f"${o['usd']:,.2f}" if o["side"] == "BUY" else f"{o.get('qty', 0):.6g}주"
            print(f"[미리 보기] {o['kind']} {o['side']} {o['sym']} {what}")
        print(f"평가 ${eq:,.2f} · 현금 ${state['cash_usd']:,.2f} · 살 돈 ${need:,.2f}")
        if why:
            print(f"(지금 주문 시간 아님: {why})")
        return 0
    if halted:
        alerts.append(f"⏸ 정지 중 — 주문 안 함 ({halted})")
        orders = []
    buys = [o for o in orders if o["side"] == "BUY"]
    if buys:
        # 토스 달러는 Deneb 장부 현금과 섞여 있다 — 그 몫은 쓰지 않는다
        power = float(trader.buying_power("USD")) - _others_usd()
        sells_first = sum(
            float(o["qty"]) * (o.get("ref_price") or 0) for o in orders if o["side"] == "SELL"
        )
        if power + core.SELL_PROCEEDS_HAIRCUT * sells_first < need:
            alerts.append(
                f"💵 토스 달러 부족 — 살 돈 ${need:,.2f}, 매수 가능 ${power:,.2f}. "
                "달러로 바꿔 주세요(다음 실행에서 다시 삼)"
            )
            orders = [o for o in orders if o["side"] != "BUY" and o["kind"] != "roll"]
            buys = []
    for o in orders:
        if o["side"] == "NONE":
            core.apply(state, o, today, 0.0, 0.0, 0.0)
            lines.append(f"↪ {o['sym']} 그대로 다음 묶음으로 넘김")
            continue
        lines.append(_send(trader, state, o, today))
    if buys and tranche and tranche in state["decisions"]:
        state["decisions"][tranche]["bought"] = True
    _save(state)
    for wait in POLL:
        if not any(o["status"] == "SENT" for o in state["orders"]):
            break
        time.sleep(wait)
        lines += _reconcile(trader, state, today)
        _save(state)
    eq = core.equity(state, prices)
    state["history"].append([today, round(eq, 2), round(float(state["cash_usd"]), 2)])
    _save(state)
    _report(state, prices)
    if lines or alerts:
        head = f"{TAG} {_kst(now)}"
        body = [
            head,
            RULE,
            *alerts,
            *lines,
            sub(f"평가 ${eq:,.2f} · 현금 ${state['cash_usd']:,.2f}"),
        ]
        _notify("\n".join(body))
    return 0


def _others_usd() -> float:
    """같은 토스 계좌의 다른 전략이 장부에 들고 있는 달러 (Deneb 현금 + 브레이크 보관금)."""
    try:
        d = json.loads((REPO / "data" / "toss" / "DENEB.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return float(d.get("cash_usd") or 0) + float((d.get("brake") or {}).get("reserve_usd") or 0)


def _report(state: dict[str, Any], prices: dict[str, float]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    rep = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "cash_usd": state["cash_usd"],
        "equity_usd": core.equity(state, prices),
        "holdings": {
            k: {"qty": q, "cost_usd": c, "price": prices.get(k)}
            for k, (q, c) in core.holdings(state).items()
        },
        "lots": state["lots"],
        "last_decision": max(state["decisions"].items()) if state["decisions"] else None,
    }
    (REPORTS / "latest.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), "utf-8")


def start(krw: float) -> int:
    if STATE.exists():
        print("이미 시작했다 — 장부를 지우려면 사람이 직접")
        return 2
    trader = _trader(set(), MIN_MAX_ORDER_USD)
    fx = float(trader.usd_krw())
    today = _us_today(_now_ms())
    state = core.new_state(today)
    core.deposit(state, today, krw, round(krw / fx, 2), fx)
    _save(state)
    print(f"Sirius 장부 시작: {krw:,.0f}원 = ${krw / fx:,.2f} (환율 {fx:,.1f})")
    return 0


def status() -> str:
    state = _load()
    if state is None:
        return "Sirius 장부 없음"
    h = core.holdings(state)
    out = [
        f"✨ Sirius — 시작 {state['start']} · 현금 ${state['cash_usd']:,.2f} · 보유 {len(h)} 종목"
    ]
    for sym, (q, c) in sorted(h.items()):
        out.append(f"  {sym} {q:.6g}주 · 원가 ${c:,.2f}")
    if state["decisions"]:
        k, v = max(state["decisions"].items())
        out.append(
            f"최근 결정 {k}: {', '.join(v['picks'])}{'' if v.get('bought') else ' (아직 안 삼)'}"
        )
    out.append(f"끝난 종목 {len(state['closed'])} 개")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    reconf = getattr(sys.stdout, "reconfigure", None)
    if reconf is not None:
        reconf(encoding="utf-8", errors="replace")
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "run":
        return run()
    if cmd == "preview":
        return run(preview=True)
    if cmd == "status":
        print(status())
        return 0
    if cmd == "start" and len(argv) > 3 and argv[2] == "--krw":
        return start(float(argv[3]))
    if cmd == "liquidate":  # 사람이 직접: 요청 + Sirius 정지, 정규장이면 바로 판다
        from auto_trading.ops.liquidate import set_group_halts

        reason = f"Sirius 정리 {_kst(_now_ms())} · 명령"
        set_group_halts(HALT, ["sirius"], reason)
        core.request_liquidation(REPO, reason, _now_ms())
        out = liquidate_step()
        print("\n".join(out) or "정리 요청 남김 — 다음 정규장에서 판다")
        if out:
            _notify("\n".join([f"{TAG} 정리", RULE, *out]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
