"""토스증권 적립 + QS (docs/research/118 §6~7) — 한국투자 적립(문서 89)·QS(118)를 토스로 옮긴 것.

사용 예::

    python -m scripts.toss_accumulate start    # 처음 한 번 (지금 보유 = 기준 수량)
    python -m scripts.toss_accumulate run      # 21:00 KST 월~금: 확인 → 판단 → 장 마감 지정가
    python -m scripts.toss_accumulate fx       # 09·12·15시 KST 월~금: 달러가 모자라면 알림
    python -m scripts.toss_accumulate preview  # 오늘 밤 할 일 미리 보기 (주문·저장 없음)
    python -m scripts.toss_accumulate status

규칙은 한국투자 판과 같다: QNDX 매일 1 만 원·BTQ 매일 1 달러 적립, 1 주 값이 모이면 1 주.
QS — QQQ 추세 꺼짐이면 QNDX 멈춤, 다시 켜지면 쌓인 적립금으로 TQQQ, 꺾이면 QS 의 TQQQ 매도.
주문은 **장 마감 지정가(LOC)** — 110 백테스트처럼 그날 미국 종가에 체결된다
(매수 지정가 +3%, 매도 −3%).
``TOSS_TRADING_ENABLED=true`` 일 때만 주문, ``data/HALT`` 면 멈춘다. 달러가 모자라 거절되면 알린다
(QS 의 TQQQ 매수는 다음 날 다시, 그 밖의 거절은 다시 보내지 않음).
상태 ``data/toss/``, 보고 ``reports/toss/<KST 날짜>.json``.

M3 (문서 182) 는 매달 첫 거래일 TQQQ + SCHD, **T 브레이크**(문서 183 ~ 188)는 매일 21:00:
TQQQ 고점 대비 −15% 절반 · −30% 전부 매도 → 달러 보관 → QQQ 추세가 꺼졌다 켜지면 되산다.
브레이크 매도 · 재매수는 알리지 않고 ``reports/toss/M3_BRAKE/`` 와 매달 M3 알림 맨 아래에 적는다.
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from auto_trading.config.secrets import (
    load_telegram_credentials,
    load_toss_credentials,
    read_env_file,
)
from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.exchange.overseas import TradingDisabledError
from auto_trading.exchange.toss import (
    TossApiError,
    TossPosition,
    TossReader,
    TossTokenCache,
    TossTrader,
)
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.notify.style import RULE, SUB, sub
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import halted_for
from auto_trading.ops.m3_guard import (
    DIV_STEPS_KRW,
    STAGE_SHARE,
    STEP_CUT,
    Brake,
    Proposal,
    added_monthly_krw,
    brake_lines,
    brake_sell,
    div_step_message,
    drawdown,
    expire,
    harvest_deadline,
    harvest_message,
    harvest_pick,
    monthly_dividend_krw,
    parse_callback,
    parse_reply,
    press,
    proposal_buttons,
    rebought,
    rebuy_due,
    remind_due,
    shift_plan,
    sold,
    status_line,
    step_due,
    typed,
)
from auto_trading.ops.m3_live import (
    DIV,
    LEV,
    M3Order,
    M3State,
    amount_target,
    apply_fill,
    ask_message,
    choose,
    cost_basis,
    credit_dividends,
    deposit,
    deposited,
    dividend_lines,
    due,
    holdings_lines,
    lev_share,
    limit_price,
    plan_message,
)
from auto_trading.ops.m3_live import SYMBOLS as M3_SYMBOLS
from auto_trading.ops.mf_paper import LEV_MAX
from auto_trading.ops.qndx_alert import (
    LIMIT_BUFFER,
    OPEN_ORDER,
    Advice,
    Order,
    Plan,
    State,
    advise,
    message,
    money,
    reconcile,
    should_order,
)
from auto_trading.ops.qndx_alert import PLANS as KIS_PLANS
from auto_trading.ops.qs_live import (
    MAX_TQQQ_LIMIT_USD,
    MAX_TQQQ_QTY,
    TQQQ,
    QsLiveState,
    plan_tqqq_order,
    qndx_paused,
    reconcile_tqqq,
    trend_parts,
    update_signal,
)
from auto_trading.ops.qs_paper import trend_on
from auto_trading.ops.us_capital_gains import tax_lines
from auto_trading.ops.us_daily_bars import last_settled_day

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "toss"
REPORTS = REPO / "reports" / "toss"
HALT = REPO / "data" / "HALT"
TOKEN = REPO / "data" / "toss_token.json"
QS_KEY = "QS_SWITCH"
"""QS 전략(QQQ 추세 → TQQQ) 상태·보고 이름. 종목 QS(퀀텀스케이프) 적립과 겹치지 않게 따로 둔다."""
QS_FILE = f"{QS_KEY}.json"
PLANS: tuple[Plan, ...] = (
    *KIS_PLANS,
    Plan("WQTM", "AMS", "AMEX", "USD", 4.0, 124.0, 60.0, changes=(("2026-10-10", 1.0),)),
    Plan("QS", "NAS", "NASD", "USD", 1.0, 31.0, 10.0),
)
"""토스 적립 종목: QNDX·BTQ(문서 89 그대로) + WQTM 매일 4 달러·월 124 달러(2026-10-04 사용자 추가,
2026-10-02 종가 32.5 USD) + QS(퀀텀스케이프, 나스닥) 매일 1 달러·월 31 달러(2026-10-04 사용자 추가,
2026-10-02 종가 4.57 USD, BTQ 와 같은 방식). 상태 파일이 없는 종목은 ``run`` 이 그날 시작한다.
2026-10-09 사용자 "지금 모인 적립금으로 계속 이어가고 wqtm 1달러로 변경해줘"
→ WQTM 은 10-10 부터 매일 1 달러(그 전 4 달러로 쌓인 적립금은 그대로)."""
STOPPED: dict[str, str] = {"QNDX": '2026-10-09 사용자 "qndx 적립 매수 중단 해줘"'}
"""적립을 멈춘 종목 → 이유. 주문을 내지 않는다.
QNDX 면 QS 의 TQQQ 재원(멈춘 동안 쌓인 적립금)도 0 으로 본다.
보유 · 상태 파일은 그대로 둔다(다시 켜면 이어서). 다시 켜려면 여기서 지운다."""
QS_LIVE = False
"""2026-10-09 사용자 "전략 qs는 그냥 페이퍼에만 등록 해줘" → 토스 실거래 QS(TQQQ 전환) 끔.
QS 는 Paper(`scripts/qs_paper.py`, 예약 AutoTrading_QS_Paper)로만 기록한다.
상태 파일은 그대로 둔다."""
QUIET_ROUTINE = True
"""2026-10-09 사용자 "매달 1달러씩 적립 매수하는건 알림 보내지 않아도 돼,
단 달러 부족하면 환전 알림".
밤 적립 결과는 거절 · 결과 모름 · 수수료 변경(❌ · ⚠) 이 있을 때만 텔레그램으로 보낸다.
환전 알림(``fx``) · 실패 알림은 그대로다."""
ALERT_MARKS = ("❌", "⚠", "📈")
M3_ENABLED = True
M3_START = "2026-10-09"
M3_MONTHLY_KRW = 300_000.0
"""2026-10-09 사용자 "첫 거래일로 하고 당분간은 월 30만원으로만 매수해줘" (문서 182 M3, 토스)."""
M3_FILE = "M3.json"
M3_MAX_QTY = {DIV: 40}
"""한 번에 SCHD 최대 40 주(TQQQ 는 QS 와 같은 상한 MAX_TQQQ_QTY)."""
M3_RETRY = frozenset({"insufficient-buying-power", "order-hours-closed"})
M3_BRAKE = True
"""T 브레이크 (문서 183 ~ 188, 사용자 2026-10-09 '처음부터'): TQQQ 고점 −15% 절반 · −30% 전부,
판 돈은 달러 보관, QQQ 추세가 꺼졌다 켜지면 되산다. 매일 21:00 실행에서만 판단한다."""
BRAKE_KINDS = frozenset({"brake", "rebuy"})
KIND_WORDS = {
    "brake": "브레이크 매도",
    "rebuy": "재매수",
    "harvest_sell": "공제 실현 매도",
    "harvest_buy": "공제 실현 되사기",
    "div_step_sell": "배당 단계 매도",
    "div_step_buy": "배당 단계 SCHD 매수",
}
DIV_STEP_DAYS = 30
"""배당 단계 질문은 30 일 동안 열어 두고, 답이 없으면 '이번엔 안 함' 으로 넘어간다."""
M3_MAX_KRW = 5_000_000.0
"""텔레그램 금액 답의 상한 (오타 방지). 넘으면 다시 묻는다."""


def _pref(key: str) -> Any:
    """오르빗 설정 화면 값 (사용자 2026-10-10). 저장 전이면 기본값 = 위 상수들과 같은 지금 운용값.

    REPO 를 부를 때마다 본다 — 시험이 REPO 를 임시 폴더로 바꾼다."""
    return user_settings.get(REPO, key)


def _m3_monthly() -> float:
    return float(_pref("glide.monthly_krw"))


def _m3_max() -> float:
    return float(_pref("glide.max_krw"))


EXTRA_MAX_LIMIT_USD = 1_000.0
"""설정 화면에서 추가한 미국 종목의 지정가 상한 (1 주 $1,000 넘는 종목은 적립에 안 맞는다)."""
_EXCHANGES = {"NASDAQ": ("NAS", "NASD"), "NYSE": ("NYS", "NYSE"), "AMEX": ("AMS", "AMEX")}


def _plans() -> tuple[Plan, ...]:
    """적립 종목 + 설정 화면에서 바꾼 하루 금액 · 켜기 기록(내일부터, 끈 기간은 0).

    설정에서 뺀 기본 종목은 빼고, 추가한 **미국** 종목은 더한다(국내는 scripts/toss_kr.py).
    뺀 종목의 장부 파일은 남는다 — 보유 · 원가 기록을 잇기 위해."""
    prefs = user_settings.load(REPO)
    out = []
    for plan in PLANS:
        if plan.symbol in user_settings.ACC_SYMBOLS and prefs.get(f"acc.{plan.symbol}.removed"):
            continue
        hist = user_settings.acc_history(REPO, plan.symbol)
        if hist:
            top = max([plan.daily, *(r for _, r in plan.changes), *(r for _, r in hist)])
            plan = replace(
                plan,
                changes=(*plan.changes, *hist),
                monthly_cap=max(plan.monthly_cap, top * 31),
            )
        out.append(plan)
    for x in prefs.get("_extras") or []:
        if x.get("mkt") != "US" or x.get("removed"):
            continue
        hist = user_settings.acc_history(REPO, str(x["sym"]))
        if not hist:
            continue
        quote, order = _EXCHANGES.get(str(x.get("market")), ("NAS", "NASD"))
        top = max(r for _, r in hist)
        out.append(
            Plan(
                str(x["sym"]),
                quote,
                order,
                "USD",
                0.0,  # 추가한 다음 날부터 하루 금액 (기록이 정한다)
                top * 31,
                EXTRA_MAX_LIMIT_USD,
                changes=tuple(hist),
            )
        )
    return tuple(out)


def universe(_: bool = True) -> int:
    """토스 거래 가능 종목 목록을 ``data/toss/universe.json`` 으로 (설정 화면이 키 없이 읽는다)."""
    reader = _trader()
    try:
        markets = {m: reader.stocks_all(m) for m in ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "AMEX")}
    finally:
        reader.close()
    keep = ("symbol", "name", "securityType")
    body = {
        "updated": _kst_now().strftime("%Y-%m-%d %H:%M"),
        "markets": {m: [{k: r.get(k) for k in keep} for r in rows] for m, rows in markets.items()},
    }
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / "universe.tmp"
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA / "universe.json")
    print("종목 목록: " + " · ".join(f"{m} {len(r)}" for m, r in markets.items()))
    return 0


TOSS_FEE = 0.001
"""토스 미국 수수료 0.1% (2026-10-04 API). 실행 때 ``/commissions`` 값이 있으면 그것을 쓴다."""
FX_BUFFER = 0.02
"""환전 안내 금액 여유 — 밤사이 가격·환율 변동."""
NY = ZoneInfo("America/New_York")
FILLED = frozenset({"FILLED", "PARTIAL_FILLED"})
DEAD = frozenset({"CANCELED", "REJECTED", "REPLACED"})
RETRYABLE_QS_BUY = frozenset({"insufficient-buying-power", "order-hours-closed"})


def _kst_now() -> datetime:
    return datetime.now(tz=UTC) + timedelta(hours=9)


def _utc_now() -> datetime:
    """지금 시각(UTC). ``_kst_now`` 에서 만들어 시계를 하나로 쓴다(시험이 시계를 고정하게)."""
    return _kst_now() - timedelta(hours=9)


def _path(name: str) -> Path:
    return DATA / name


def _load_plan_state(plan: Plan) -> State | None:
    _migrate_qs_switch()
    p = _path(f"{plan.symbol}.json")
    return State.from_dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None


def _save_plan_state(state: State) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _path(f"{state.symbol}.json").write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _migrate_qs_switch() -> None:
    """옛 QS 전략 상태 ``QS.json`` → ``QS_SWITCH.json`` (2026-10-04, 종목 QS 적립 추가 전 1회).

    ``QS.json`` 이 QS 전략 형식(``prev_on``)일 때만 옮긴다. 종목 QS 적립 상태는 건드리지 않는다.
    """
    old, new = _path("QS.json"), _path(QS_FILE)
    if not old.exists() or new.exists():
        return
    try:
        body = json.loads(old.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(body, dict) and "prev_on" in body:
        old.replace(new)


def _load_qs() -> QsLiveState | None:
    _migrate_qs_switch()
    p = _path(QS_FILE)
    return QsLiveState.from_dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None


def _live_qs() -> QsLiveState | None:
    """실거래 QS 상태 — ``QS_LIVE`` 가 꺼져 있으면 None (주문 · 신호 갱신 없음)."""
    return _load_qs() if QS_LIVE else None


def _save_qs(qs: QsLiveState) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _path(QS_FILE).write_text(
        json.dumps(qs.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _notify(text: str, telegram: bool) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
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


def _trader() -> TossTrader:
    symbols = {p.symbol for p in _plans()} | {TQQQ} | set(M3_SYMBOLS)
    limit = max([p.max_limit_usd for p in _plans()] + [MAX_TQQQ_LIMIT_USD])
    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(symbols),
        max_qty={TQQQ: MAX_TQQQ_QTY, **M3_MAX_QTY},
        sell_symbols=frozenset({TQQQ, DIV}),
        max_limit_usd=Decimal(str(limit)),
    )


def _write_report(name: str, today: str, body: dict[str, Any]) -> None:
    folder = REPORTS / name
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"at_ms": int(time.time() * 1000), "date": today, **body}
    (folder / f"{today}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _client_id(symbol: str, today: str, n: int) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", f"{symbol}-{today}-{n}")[:36]


# ---------------------------------------------------------------- M3 (문서 182)


def _load_m3() -> M3State:
    p = _path(M3_FILE)
    if p.exists():
        return M3State.from_dict(json.loads(p.read_text(encoding="utf-8")))
    return M3State(M3_START)


def _save_m3(m3: M3State) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _path(M3_FILE).write_text(
        json.dumps(m3.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _dividend_events() -> list[tuple[str, str, float]]:
    """Yahoo 배당 기록 (배당락일, 종목, 주당). 실패하면 빈 목록 — 다음 날 다시."""
    import httpx

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    out: list[tuple[str, str, float]] = []
    for sym in M3_SYMBOLS:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            res = httpx.get(
                url,
                params={"range": "1y", "interval": "1d", "events": "div"},
                headers={"User-Agent": ua},
                timeout=30,
            ).json()["chart"]["result"][0]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            continue
        for ev in ((res.get("events") or {}).get("dividends") or {}).values():
            ex = datetime.fromtimestamp(int(ev["date"]), UTC).astimezone(NY).strftime("%Y-%m-%d")
            out.append((ex, sym, float(ev["amount"])))
    return out


def _since(a: str, b: str) -> int:
    from datetime import date as _date

    return (_date.fromisoformat(b) - _date.fromisoformat(a)).days


def _m3_resolve(day: Day, m3: M3State) -> tuple[list[str], list[str]]:
    """지난 M3 주문을 주문 상세로 정리해 장부에 넣는다. (알림 줄, 브레이크 주문 줄 — 알림 안 함)."""
    notes: list[str] = []
    quiet: list[str] = []
    for o in m3.orders:
        if o.date >= day.today:
            continue
        if o.status == "UNKNOWN":
            o.status = "EXPIRED"
            notes.append(
                f"⚠ Glide {o.symbol} {o.date} 주문 전송 결과 모름 — 잔고를 직접 확인해 주세요"
            )
            continue
        out = quiet if o.kind else notes
        if o.status != "SENT":
            continue
        st = day.reader.order_status(o.order_id)
        if st.status in ("PENDING", "PENDING_CANCEL"):
            continue
        o.filled_qty = float(st.filled_qty)
        amount = st.filled_amount
        if amount is None and st.avg_price is not None:
            amount = st.filled_qty * st.avg_price
        o.filled_usd = float(amount or 0)
        o.commission = float(st.commission or 0)
        o.status = "FILLED" if o.filled_qty > 0 else "EXPIRED"
        apply_fill(m3, o)
        word = "매수" if o.side == "BUY" else "매도"
        if o.filled_qty > 0:
            out.append(f"Glide {o.symbol} {o.filled_qty:g}주 {word} 체결 — ${o.filled_usd:,.2f}")
        else:
            out.append(f"Glide {o.symbol} {word} 미체결 ({st.status}) — 돈은 다음 달로 이월")
    return notes, quiet


def _brake_of(m3: M3State) -> Brake:
    return Brake.from_dict(m3.brake) if m3.brake else Brake(ref="T")


def _m3_book(m3: M3State, b: Brake) -> list[str]:
    """정리된 브레이크 · 재매수 주문을 보관금에 반영한다 (한 번만)."""
    out: list[str] = []
    for o in m3.orders:
        if o.kind not in BRAKE_KINDS or o.booked or o.status not in ("FILLED", "EXPIRED"):
            continue
        info = b.pending.pop(o.client_id, {})
        o.booked = True
        if o.kind == "brake" and o.filled_qty > 0:
            net = o.filled_usd - o.commission
            m3.cash_usd -= net  # apply_fill 이 전략 현금에 넣은 판 돈을 보관금으로 옮긴다
            sold(b, o.date, float(info.get("dd", 0.0)), o.filled_qty, net)
            out.append(
                f"브레이크 매도 체결 {o.date} TQQQ {o.filled_qty:g}주 ${net:,.2f}"
                f" → 보관금 ${b.reserve_usd:,.2f}"
            )
        elif o.kind == "brake":
            b.stage = int(info.get("prev_stage", b.stage))
            out.append(f"브레이크 매도 {o.date} 미체결 — 단계 {b.stage} 로 되돌리고 다음 밤 다시")
        elif o.filled_qty > 0:
            m3.cash_usd += b.reserve_usd  # 남은 보관금은 전략 현금으로 (산 값은 apply_fill 이 뺐다)
            px = o.filled_usd / o.filled_qty
            rebought(b, o.date, 0.0, px)
            b.last_day = o.date
            out.append(
                f"재매수 체결 {o.date} TQQQ {o.filled_qty:g}주 ${o.filled_usd:,.2f}"
                f" — 브레이크 끝, 고점 ${px:.2f} 부터 다시"
            )
        else:
            out.append(f"재매수 {o.date} 미체결 — 다음 밤 다시")
    return out


def _m3_send(
    day: Day,
    trader: TossTrader,
    m3: M3State,
    side: str,
    qty: int,
    limit: float,
    kind: str,
    symbol: str = LEV,
) -> tuple[M3Order, str]:
    """브레이크 · 재매수 · 공제 실현 · 배당 단계 주문 (장 마감 지정가). 보내기 전에 저장한다."""
    cid = _client_id(f"M3{symbol}", day.today, len(m3.orders) + 1)
    order = M3Order(day.today, symbol, side, qty, limit, cid, kind=kind)
    m3.orders.append(order)
    _save_m3(m3)
    word = KIND_WORDS.get(kind, kind)
    try:
        res = trader.order(symbol, side, qty, Decimal(f"{limit:.2f}"), client_order_id=cid)
    except OrderStateUnknownError as exc:
        return order, f"⚠ Glide {symbol} {word} 전송 결과 모름 — 다시 보내지 않는다: {exc}"
    except TradingDisabledError as exc:
        order.status = "REJECTED"
        return order, f"❌ Glide {symbol} {word} 안 보냄: {exc}"
    if res.accepted:
        order.status, order.order_id = "SENT", res.code
        return order, f"🛑 Glide {symbol} {qty}주 {word} 접수 — 장 마감 지정가 {limit:.2f} USD"
    order.status, order.code = "REJECTED", res.code
    return order, f"❌ Glide {symbol} {word} 거절 {res.code}: {res.message}"


def _m3_brake(
    day: Day, trader: TossTrader, m3: M3State, *, enabled: bool, halted: str | None
) -> tuple[list[str], list[str]]:
    """T 브레이크 하루치 → (알림 줄 — 실패만, 보고 줄). 매도 · 재매수 성공은 알리지 않는다."""
    alerts: list[str] = []
    b = _brake_of(m3)
    report = _m3_book(m3, b)
    first = min(
        (o.date for o in m3.orders if o.symbol == LEV and o.side == "BUY" and o.filled_qty > 0),
        default=None,
    )
    if first is None:
        m3.brake = b.to_dict()
        return alerts, [*report, "TQQQ 산 적 없음 — 브레이크 대기"]
    # 끝난 미국 거래일만 — 21:00 이 자정 넘어 늦게 돌아도
    # 장중 봉을 어제 종가로 쓰지 않는다 (2026-10-12)
    settled = last_settled_day(day.at)
    closes = [
        (d, float(px))
        for d, px in day.reader.daily_closes(LEV, 40)
        if d < day.today and d <= settled
    ]
    for d, px in closes:
        if d >= max(first, b.last_day) and (d > b.last_day or b.lev_peak == 0):
            b.lev_peak = max(b.lev_peak, px)
            b.last_day = d
    if not closes:
        m3.brake = b.to_dict()
        return [*alerts, "⚠ Glide 브레이크: TQQQ 일봉 없음 — 오늘 판단 못 함"], report
    last_d, last = closes[-1]
    dd = drawdown(b, last)
    report.append(
        f"TQQQ {last_d} 종가 ${last:.2f} · 고점 ${b.lev_peak:.2f} · 낙폭 {dd or 0:.1%}"
        f" · 단계 {b.stage} · 보관금 ${b.reserve_usd:,.2f}"
    )
    if b.pending:
        m3.brake = b.to_dict()
        return alerts, [*report, "접수한 브레이크 주문 체결 확인 전 — 오늘은 판단만"]
    if not enabled or halted:
        m3.brake = b.to_dict()
        why = "TOSS_TRADING_ENABLED 꺼짐" if not enabled else f"정지: {halted}"
        return alerts, [*report, f"브레이크 주문 안 함 — {why}"]
    prev = b.stage
    n = brake_sell(b, dd, m3.qty.get(LEV, 0.0))
    if n > 0:
        o, line = _m3_send(
            day, trader, m3, "SELL", n, limit_price(day.prices[LEV], "SELL"), "brake"
        )
        if o.status == "REJECTED":
            b.stage, o.booked = prev, True
            alerts.append(line)
        else:
            b.pending[o.client_id] = {"kind": "brake", "dd": dd, "prev_stage": prev}
            (alerts if o.status == "UNKNOWN" else report).append(line)
    elif b.stage > 0 and b.sales:
        on: bool | None = None
        try:
            on, as_of, _ = qs_signal(day.reader, _utc_now())
            report.append(f"QQQ 추세 {'켜짐' if on else '꺼짐'} ({as_of} 종가)")
        except AutoTradingError as exc:
            alerts.append(f"⚠ Glide 브레이크 추세 확인 실패: {exc}")
        if rebuy_due(b, on):
            limit = limit_price(day.prices[LEV], "BUY")
            qty = math.floor(b.reserve_usd / (limit * (1 + day.fee)))
            if qty >= 1:
                o, line = _m3_send(day, trader, m3, "BUY", qty, limit, "rebuy")
                if o.status == "REJECTED":
                    o.booked = True
                    alerts.append(line)
                else:
                    b.pending[o.client_id] = {"kind": "rebuy"}
                    (alerts if o.status == "UNKNOWN" else report).append(line)
            else:
                m3.cash_usd += b.reserve_usd
                rebought(b, last_d, 0.0, last)
                b.last_day = last_d
                report.append("보관금이 1 주 값보다 적어 전략 현금으로 합침 — 브레이크 끝")
    m3.brake = b.to_dict()
    return alerts, report


def _m3_plan(
    day: Day, m3: M3State, events: list[tuple[str, str, float]] | None = None
) -> tuple[dict[str, int], float | None, float, list[str]]:
    """이번 달 납입 · 배당 반영 뒤 (사고팔 주수, σ20, TQQQ 몫, 메모). ``m3`` 를 고친다."""
    closes = [(d, float(px)) for d, px in day.reader.daily_closes("QQQ", 60)]
    vol, share = lev_share(closes, day.today)
    share *= min(m3.lev_cap, LEV_MAX) / LEV_MAX  # 배당 단계로 낮아진 TQQQ 상한
    if M3_BRAKE and m3.brake:
        rebuying = any(
            o.kind == "rebuy" and o.date == day.today and o.status == "SENT" for o in m3.orders
        )
        share *= 1.0 if rebuying else STAGE_SHARE[int(m3.brake.get("stage", 0))]
    events = _dividend_events() if events is None else events
    notes = credit_dividends(m3, events, day.today, _since)
    krw = m3.amounts.get(day.today[:7], _m3_monthly())
    usd = deposit(m3, day.today, krw, day.fx)
    if usd:
        src = "텔레그램 답" if day.today[:7] in m3.amounts else "기본"
        notes.append(f"이번 달 납입 {krw:,.0f}원({src}) = ${usd:,.2f} (환율 {day.fx:,.1f})")
    elif krw <= 0:
        notes.append("이번 달 납입 0원(텔레그램 답) — 이월 현금 · 배당만으로")
    last = {s: day.prices[s] for s in M3_SYMBOLS}
    # 오늘 이미 접수된 주문(아직 체결 전)은 산 것 · 판 것으로 보고 다시 사지 않는다
    qty, cash = dict(m3.qty), m3.cash_usd
    for o in m3.orders:
        if o.status == "SENT" and o.date == day.today:
            sign = 1 if o.side == "BUY" else -1
            qty[o.symbol] = qty.get(o.symbol, 0.0) + sign * o.qty
            if o.kind not in BRAKE_KINDS:  # 브레이크 · 재매수 돈은 보관금이다
                cash -= sign * o.qty * o.limit * (1 + day.fee)
    return choose(qty, cash, last, share, day.fee), vol, share, notes


def _m3_poll(today: str, telegram: bool, *, reply: bool = False) -> None:
    """텔레그램 답으로 이번(또는 물어본 다음) 달 M3 금액을 정한다. 등록 채팅의 글만 읽는다.

    ``reply`` 면 이번 달(매수 당일) 금액이 정해질 때 오늘 밤 계획을 바로 답장한다 (21:00 전)."""
    from auto_trading.notify.channels import NotifyError
    from auto_trading.notify.inbox import InboxQueue, TelegramInbox, is_command, parse_krw

    creds = load_telegram_credentials(REPO / ".env")
    if not M3_ENABLED or not telegram or creds is None:
        return
    m3 = _load_m3()
    try:
        # '현황' 같은 명령은 봇(scripts/telegram_bot.py) 몫으로 남기고 나머지만 가져온다
        msgs = InboxQueue(REPO / "data" / "telegram" / "inbox.json", m3.tg_offset).take(
            TelegramInbox(creds), lambda m: not is_command(m)
        )
    except NotifyError as exc:
        print(f"텔레그램 받기 실패: {exc}")
        return
    planned = False
    bot = _bot()
    hhmm = _kst_now().strftime("%H:%M")
    props = _props(m3)
    for msg in msgs:
        if msg.data:  # 버튼
            cb = parse_callback(msg.data)
            prop = next((x for x in props if cb and x.pid == cb[0]), None)
            if bot is not None and msg.callback_id:
                with contextlib.suppress(NotifyError):  # 늦으면 텔레그램이 거부 — 무시
                    bot.answer_callback(msg.callback_id, "접수했어요")
            if prop is not None and cb is not None:
                text, buttons = press(prop, cb[1], today, hhmm)
                _tg_send(
                    bot,
                    text,
                    buttons=buttons,
                    force_reply=prop.awaiting_qty,
                    reply_to=prop.message_id,
                )
                _after_answer(m3, prop)
                _refresh(bot, prop)
            continue
        target = amount_target(m3, today)
        live = [x for x in props if x.status in ("open", "accepted")]
        prop = next((x for x in live if x.awaiting_qty), None) or next(
            (x for x in live if msg.reply_to and x.message_id == msg.reply_to), None
        )
        if prop is None and target is None and parse_reply(msg.text) is not None:
            prop = live[-1] if live else None
        if prop is not None:
            text, buttons = typed(prop, msg.text, today, hhmm)
            _tg_send(
                bot, text, buttons=buttons, force_reply=prop.awaiting_qty, reply_to=prop.message_id
            )
            _after_answer(m3, prop)
            _refresh(bot, prop)
            continue
        if target is None:
            continue
        if target == m3.ask_month and msg.date < m3.ask_ts:
            continue
        krw = parse_krw(msg.text)
        if krw is None:
            _notify("❓ 금액으로 읽지 못했어요. 예: 30만 · 500000 · 0(이번 달 쉬기)", telegram)
        elif krw > _m3_max():
            _notify(
                f"❓ {krw:,.0f}원은 상한({_m3_max():,.0f}원)보다 커요. 다시 보내 주세요", telegram
            )
        else:
            m3.amounts[target] = krw
            planned = planned or target == today[:7]
            if not (reply and target == today[:7]):
                _notify(f"📈 Glide {target} 매수 금액 {krw:,.0f}원으로 정했어요", telegram)
    _put(m3, props)
    _save_m3(m3)
    if planned and reply and due(m3, today):
        try:
            _notify(_m3_preview(_kst_now()), telegram)
        except (AutoTradingError, TossApiError) as exc:
            _notify(f"📈 Glide 금액은 정했지만 계획 계산 실패: {exc}", telegram)


def _m3_ask_today(today: str, telegram: bool) -> bool:
    """매수 당일(이번 달 아직 안 산 평일) 아침에 금액을 묻는다. 달마다 한 번. 물었으면 True."""
    if not M3_ENABLED or not _pref("glide.ask_on"):
        return False  # 묻기 꺼짐(설정) — 기본 월 납입으로 산다
    m3 = _load_m3()
    month = today[:7]
    if not due(m3, today) or deposited(m3, month) or month in (m3.ask_month, *m3.amounts):
        return False  # 이미 물었거나 이번 달 금액을 미리 정해 두었다
    m3.ask_month, m3.ask_ts = today[:7], int(time.time())
    _save_m3(m3)
    _notify(ask_message(today, _m3_monthly()), telegram)
    return True


def _m3_preview(now: datetime, *, title: str = "") -> str:
    """오늘 밤 M3 계획 · 보유 · 배당 (상태는 복사본으로만 계산, 주문 없음)."""
    states = {p.symbol: s for p in _plans() if (s := _load_plan_state(p)) is not None}
    qs = _live_qs()
    trader = _trader()
    try:
        day = Day(trader, now, dry=True)
        before = _load_m3()
        m3 = copy.deepcopy(before)
        events = _dividend_events()
        plan, vol, share, _ = _m3_plan(day, m3, events)
        tax = _tax_summary(trader, before)
        states, qs = copy.deepcopy(states), copy.deepcopy(qs)
        if qs is not None:
            day.qs_prepare(qs, _utc_now())
        needs = _needs(day, states, qs)
    finally:
        trader.close()
    krw = before.amounts.get(day.today[:7], _m3_monthly())
    source = "텔레그램 답" if day.today[:7] in before.amounts else "기본"
    if deposited(before, day.today[:7]):
        krw, source = 0.0, "이미 넣음"
    credited = [d for d in m3.dividends if d not in before.dividends]
    after = {s: m3.qty.get(s, 0.0) + max(plan.get(s, 0), 0) for s in M3_SYMBOLS}
    text = plan_message(
        today=day.today,
        krw=krw,
        source=source,
        fx_rate=day.fx,
        vol=vol,
        share=share,
        plan=plan,
        prices={s: day.prices[s] for s in M3_SYMBOLS},
        fee=day.fee,
        usd_now=day.usd,
        need_all=sum(n["qty"] * n["limit"] * (1 + day.fee) for n in needs),
        sections=[
            holdings_lines(before, {s: day.prices[s] for s in M3_SYMBOLS}),
            dividend_lines(before, events, credited, after),
            *([b[2:]] if (b := brake_lines(_brake_of(before))) else []),
            *([_tax_section(tax)] if tax else []),
        ],
    )
    return f"{title}\n{text}" if title else text


def _m3_needs(day: Day) -> list[dict[str, Any]]:
    """오늘 밤 M3 매수 (환전 알림용). 상태는 복사본으로만 계산한다."""
    if not M3_ENABLED:
        return []
    m3 = copy.deepcopy(_load_m3())
    if not due(m3, day.today):
        return []
    plan, _, _, _ = _m3_plan(day, m3)
    return [
        {"symbol": s, "qty": n, "limit": limit_price(day.prices[s], "BUY")}
        for s, n in plan.items()
        if n > 0
    ]


def _m3_run(
    day: Day,
    trader: TossTrader,
    *,
    enabled: bool,
    halted: str | None,
    brake: bool = True,
    telegram: bool = False,
) -> list[str]:
    """M3 하루치. 이번 달 주문을 다 접수하면 그 달은 끝, 돈 · 시간 문제로 거절되면 다음 날 다시.

    ``brake`` (21:00 실행만): 먼저 T 브레이크를 판단한다. 매도 · 재매수는 알리지 않고 보고에만.
    """
    m3 = _load_m3()
    notes, quiet = _m3_resolve(day, m3)
    alerts: list[str] = []
    if brake and M3_BRAKE:
        alerts, report = _m3_brake(day, trader, m3, enabled=enabled, halted=halted)
        more_alerts, more = _glide_actions(
            day, trader, m3, enabled=enabled, halted=halted, telegram=telegram
        )
        alerts += more_alerts
        _save_m3(m3)
        _write_report(
            "M3_BRAKE",
            day.today,
            {"lines": [*quiet, *report, *more], "brake": m3.brake, "proposals": m3.proposals},
        )
    if not due(m3, day.today):
        _save_m3(m3)
        return [*(f"📈 Glide · {x}" for x in notes), *alerts]
    plan, vol, share, more = _m3_plan(day, m3)
    notes += more
    if not any(str(x[0]) == day.today[:7] for x in m3.months):
        m3.months.append([day.today[:7], vol, share])
    vol_txt = f"{vol:.1%}" if vol is not None else "모름"
    head = (
        f"📈 Glide · {day.today[:7]} TQQQ {share:.0%} / SCHD {1 - share:.0%}"
        f" (QQQ 20일 변동성 {vol_txt})"
    )
    if not enabled or halted:
        _save_m3(m3)
        why = "TOSS_TRADING_ENABLED 꺼짐" if not enabled else f"정지: {halted}"
        return [head, *notes, f"⏸ Glide 주문 안 함 — {why} (다음 날 다시)", *alerts]
    retry = False
    lines = []
    for sym, n in sorted(plan.items(), key=lambda kv: kv[1]):  # 파는 것 먼저
        side = "BUY" if n > 0 else "SELL"
        qty = abs(n)
        limit = limit_price(day.prices[sym], side)
        cid = _client_id(f"M3{sym}", day.today, len(m3.orders) + 1)
        order = M3Order(day.today, sym, side, qty, limit, cid)
        m3.orders.append(order)
        _save_m3(m3)
        word = "매수" if side == "BUY" else "매도"
        try:
            res = trader.order(sym, side, qty, Decimal(f"{limit:.2f}"), client_order_id=cid)
        except OrderStateUnknownError as exc:
            lines.append(f"⚠ Glide {sym} {word} 전송 결과 모름 — 다시 보내지 않는다: {exc}")
            continue
        except TradingDisabledError as exc:
            order.status = "REJECTED"
            lines.append(f"❌ Glide {sym} {word} 안 보냄: {exc}")
            continue
        if res.accepted:
            order.status, order.order_id = "SENT", res.code
            lines.append(f"🛒 Glide {sym} {qty}주 장 마감 지정가 {word} 접수 — {limit:.2f} USD")
        else:
            order.status, order.code = "REJECTED", res.code
            if res.code in M3_RETRY:
                retry = True
            lines.append(f"❌ Glide {sym} {word} 거절 {res.code}: {res.message}")
        _save_m3(m3)
    if not plan:
        lines.append("살 것 없음 (돈이 1 주 값보다 적음) — 다음 달로 이월")
    if retry:
        lines.append(
            "↻ 달러 부족 · 장 시간 문제 — 오늘 23:40 · 03:30 에 다시, 안 되면 다음 거래일 밤"
        )
    else:
        m3.done_month = day.today[:7]
    lines.append(
        f"전략 현금 ${m3.cash_usd:,.2f} (체결 뒤 정산) · 보유 TQQQ {m3.qty.get(LEV, 0):g}"
        f" · SCHD {m3.qty.get(DIV, 0):g}"
    )
    _save_m3(m3)
    return [head, *notes, *lines, *alerts]


# ------------------------------------------------------------ Glide 제안 (공제 실현 · 배당 단계)


def _bot() -> TelegramNotifier | None:
    creds = load_telegram_credentials(REPO / ".env")
    return TelegramNotifier(creds) if creds is not None else None


def _props(m3: M3State) -> list[Proposal]:
    return [Proposal.from_dict(d) for d in m3.proposals]


def _put(m3: M3State, props: list[Proposal]) -> None:
    m3.proposals = [x.to_dict() for x in props]


def _tg_send(
    bot: TelegramNotifier | None,
    text: str,
    *,
    buttons: list[list[dict[str, str]]] | None = None,
    force_reply: bool = False,
    reply_to: int = 0,
) -> int:
    """버튼 · 입력칸 · 답장 알림. 텔레그램이 없거나 실패하면 출력만 하고 0."""
    print(text)
    if bot is None:
        return 0
    try:
        return bot.send_message(text, buttons=buttons, force_reply=force_reply, reply_to=reply_to)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")
        return 0


def _refresh(bot: TelegramNotifier | None, p: Proposal) -> None:
    """제안 메시지 맨 아래 상태를 바꾼다(알림 없음).

    끝난 제안은 (예전에 고정했던 것이면) 고정을 푼다.

    2026-10-11 부터 새 제안은 고정하지 않는다 — 공지 자리는 사용법 안내(pin-help)가 쓴다."""
    if bot is None or not p.message_id:
        return
    try:
        bot.edit(
            p.message_id,
            f"{p.plan.get('text', '')}\n\n{status_line(p)}",
            buttons=proposal_buttons(p) if p.status == "open" else None,
        )
        if p.status in ("declined", "expired", "done", "canceled"):
            bot.unpin(p.message_id)
    except NotifyError as exc:
        print(f"고정 공지 고치기 실패: {exc}")


def _after_answer(m3: M3State, p: Proposal) -> None:
    """배당 단계 답 → TQQQ 상한 · 단계 수.

    '2주만' 이면 상한을 그만큼만 낮춘다. 취소하면 되돌린다.
    """
    if p.kind != "div_step":
        return
    pl = p.plan
    step = int(pl.get("step", m3.div_steps))
    if "prev_cap" not in pl:
        pl["prev_cap"], pl["prev_steps"] = m3.lev_cap, m3.div_steps
    prev_cap, prev_steps = float(pl["prev_cap"]), int(pl["prev_steps"])
    if p.status == "accepted":
        cap, new = float(pl["cap"]), float(pl["new_cap"])
        m3.lev_cap = cap - (cap - new) * (p.qty / max(p.max_qty, 1))
        m3.div_steps = max(prev_steps, step + 1)
    elif p.status == "open":  # 취소 → 원래대로
        m3.lev_cap, m3.div_steps = prev_cap, prev_steps
    elif p.status in ("declined", "expired"):
        m3.lev_cap, m3.div_steps = prev_cap, max(prev_steps, step + 1)


def _tax_summary(reader: TossReader, m3: M3State) -> dict[str, Any] | None:
    """올해 양도소득 추정 (토스 체결 · ECOS 환율).

    키가 없거나 실패하면 None — 알림은 양도세 줄 없이 그대로 간다.
    """
    key = read_env_file(REPO / ".env").get("ECOS_API_KEY", "")
    if not key:
        return None
    from scripts.us_tax import compute

    try:
        return compute(
            reader, key, {o.order_id for o in m3.orders if o.order_id}, _kst_now().strftime("%Y")
        )
    except (AutoTradingError, TossApiError, RuntimeError, ValueError, KeyError) as exc:
        print(f"양도세 계산 실패: {exc}")
        return None


def _tax_section(res: dict[str, Any]) -> list[str]:
    s = res["summary"]
    return tax_lines(
        gain_krw=float(s["gain_krw"]),
        glide_krw=float(res["by_tag"].get("M3", 0.0)),
        tax_krw=float(s["tax_krw"]),
        room_krw=float(s["tax_free_room_krw"]),
        unreal_krw=float(res["unrealized_total"]),
    )


def _new_harvest(
    m3: M3State, today: str, prices: dict[str, float], fx_rate: float, tax: dict[str, Any]
) -> tuple[Proposal, str]:
    """12 월 공제 실현 제안 (보낼 글과 함께). 실현할 이익이 없으면 끝난 기록만 남긴다."""
    year = int(today[:4])
    room = float(tax["summary"]["tax_free_room_krw"])
    basis = cost_basis(m3)
    pick = harvest_pick({s: m3.qty.get(s, 0.0) for s in M3_SYMBOLS}, basis, prices, fx_rate, room)
    deadline = harvest_deadline(year)
    if pick is None:
        text = (
            f"🧾 {year}년 해외주식 공제 — 올해는 실현할 이익이 없어요\n{RULE}\n"
            f"세금 없이 더 실현할 수 있는 이익 {room:,.0f}원 · Glide 보유에 이익 난 종목이 없음"
        )
        return Proposal(
            f"h{year}", "harvest", today, deadline, LEV, 0, {"text": text}, "expired"
        ), text
    sym, n, gain = pick
    text = harvest_message(year=year, room_krw=room, picks=[pick], deadline=deadline)
    return Proposal(
        f"h{year}", "harvest", today, deadline, sym, n, {"text": text, "gain_krw": gain}
    ), text


def _new_div_step(
    m3: M3State,
    today: str,
    prices: dict[str, float],
    fx_rate: float,
    events: list[tuple[str, str, float]],
) -> tuple[Proposal | None, str | None]:
    """배당 단계에 닿았으면 (제안, 글). 팔 TQQQ 가 없으면 상한만 낮추고 알림 글만."""
    monthly = monthly_dividend_krw(m3.dividends, today, fx_rate)
    k = step_due(m3.div_steps, monthly)
    if k is None:
        return None, None
    cap = m3.lev_cap
    new_cap = max(cap - STEP_CUT, 0.0)
    total = m3.cash_usd + sum(m3.qty.get(s, 0.0) * prices[s] for s in M3_SYMBOLS)
    sell, buy = shift_plan(m3.qty.get(LEV, 0.0), prices[LEV], prices[DIV], total, new_cap)
    step_krw = DIV_STEPS_KRW[k]
    if sell < 1:
        m3.div_steps, m3.lev_cap = k + 1, new_cap
        return None, (
            f"💰 Glide · 배당 단계 도달 (월 {step_krw / 10_000:,.0f}만 원)\n{RULE}\n"
            f"TQQQ 비중이 이미 낮아 팔 것이 없어요. 앞으로 TQQQ 목표를 {new_cap:.0%}로 낮춰 둘게요."
        )
    since = (datetime.fromisoformat(today) - timedelta(days=365)).date().isoformat()
    dps = sum(a for ex, sym, a in events if sym == DIV and ex >= since)
    text = div_step_message(
        monthly_krw=monthly,
        step_krw=step_krw,
        cap=cap,
        new_cap=new_cap,
        sell=sell,
        lev_px=prices[LEV],
        buy=buy,
        fx=fx_rate,
        added_krw=added_monthly_krw(buy, dps, fx_rate),
        gain_krw=None,
        room_krw=None,
    )
    deadline = (datetime.fromisoformat(today) + timedelta(days=DIV_STEP_DAYS)).date().isoformat()
    nxt = DIV_STEPS_KRW[k + 1] if k + 1 < len(DIV_STEPS_KRW) else None
    plan = {
        "text": text,
        "cap": cap,
        "new_cap": new_cap,
        "step": k,
        "next_step_krw": nxt,
        "lev_px": prices[LEV],
        "div_px": prices[DIV],
    }
    return Proposal(f"d{k}-{today}", "div_step", today, deadline, LEV, sell, plan), text


def _glide_morning(now: datetime, telegram: bool) -> None:
    """아침 확인 (환전 알림 실행에서): 마감 · 다시 묻기 · 12 월 공제 공지 · 배당 단계 질문."""
    if not M3_ENABLED:
        return
    today = now.strftime("%Y-%m-%d")
    m3 = _load_m3()
    props = _props(m3)
    bot = _bot() if telegram else None
    changed = False
    for x in props:
        if expire(x, today):
            _after_answer(m3, x)
            _refresh(bot, x)
            changed = True
        elif remind_due(x, today):
            x.reminded = today
            head = (x.plan.get("text", "") or "Glide 제안").splitlines()[0]
            _tg_send(
                bot,
                f"⏰ 아직 답을 기다리고 있어요 — {x.deadline} 밤까지\n{head}",
                buttons=proposal_buttons(x),
                reply_to=x.message_id,
            )
            changed = True
    year = now.year
    want_harvest = (
        bool(_pref("glide.harvest_on"))
        and now.month == 12
        and not any(x.pid == f"h{year}" for x in props)
        and today <= harvest_deadline(year)
    )
    want_div = m3.div_steps < len(DIV_STEPS_KRW) and not any(
        x.kind == "div_step" and x.status in ("open", "accepted") and x.phase != "done"
        for x in props
    )
    if want_harvest or want_div:
        trader = _trader()
        try:
            prices = {s: float(v) for s, v in trader.prices(list(M3_SYMBOLS)).items()}
            fx_rate = float(trader.usd_krw())
            if want_harvest and (tax := _tax_summary(trader, m3)) is not None:
                prop, text = _new_harvest(m3, today, prices, fx_rate, tax)
                if prop.status == "open":
                    # 공지로 고정하지 않는다 — 메시지 아래 버튼으로만 답한다 (사용자 2026-10-11)
                    prop.message_id = _tg_send(bot, text, buttons=proposal_buttons(prop))
                else:
                    _tg_send(bot, text)
                props.append(prop)
                changed = True
            if want_div:
                dprop, dtext = _new_div_step(m3, today, prices, fx_rate, _dividend_events())
                if dprop is not None and dtext is not None:
                    dprop.message_id = _tg_send(bot, dtext, buttons=proposal_buttons(dprop))
                    props.append(dprop)
                    changed = True
                elif dtext is not None:
                    _tg_send(bot, dtext)
                    changed = True
        finally:
            trader.close()
    if changed:
        _put(m3, props)
        _save_m3(m3)


def _glide_actions(
    day: Day,
    trader: TossTrader,
    m3: M3State,
    *,
    enabled: bool,
    halted: str | None,
    telegram: bool,
) -> tuple[list[str], list[str]]:
    """승인된 제안 실행 (21:00): 팔기 → 체결 다음 밤 되사기(공제) · SCHD 사기(배당 단계)."""
    alerts: list[str] = []
    report: list[str] = []
    props = _props(m3)
    if not any(x.status == "accepted" for x in props):
        return alerts, report
    if not enabled or halted:
        return alerts, ["Glide 제안 주문 안 함 — 거래 꺼짐 · 정지"]
    bot = _bot() if telegram else None
    by_cid = {o.client_id: o for o in m3.orders}
    for x in props:
        if x.status != "accepted":
            continue
        if x.phase == "":
            if due(m3, day.today):
                report.append(f"{x.pid}: 오늘은 매달 매수일 — 내일 밤 진행")
                continue
            if x.kind == "harvest" and day.today > x.deadline:
                x.status = "expired"
                _refresh(bot, x)
                alerts.append(f"⚠ Glide 공제 실현 {x.deadline} 마감을 넘겨 진행하지 않았어요")
                continue
            q = min(x.qty, math.floor(m3.qty.get(x.symbol, 0.0)))
            if q < 1:
                x.status = "expired"
                _refresh(bot, x)
                continue
            o, line = _m3_send(
                day,
                trader,
                m3,
                "SELL",
                q,
                limit_price(day.prices[x.symbol], "SELL"),
                f"{x.kind}_sell",
                x.symbol,
            )
            if o.status == "REJECTED":
                alerts.append(line)
                continue
            x.phase, x.plan["sell_cid"] = "selling", o.client_id
            report.append(line)
            _refresh(bot, x)
        elif x.phase == "selling":
            sold_o = by_cid.get(str(x.plan.get("sell_cid", "")))
            if sold_o is None or sold_o.status in ("SENT", "UNKNOWN"):
                continue
            if sold_o.filled_qty <= 0:
                x.phase = ""
                report.append(f"{x.pid}: 매도 미체결 — 오늘 밤 다시")
                continue
            if x.kind == "harvest":
                sym, n = x.symbol, math.floor(sold_o.filled_qty)
            else:
                sym = DIV
                net = min(sold_o.filled_usd - sold_o.commission, m3.cash_usd)
                n = math.floor(net / (limit_price(day.prices[DIV], "BUY") * (1 + day.fee)))
            if n < 1:
                x.status, x.phase = "done", "done"
                _refresh(bot, x)
                continue
            o2, line = _m3_send(
                day, trader, m3, "BUY", n, limit_price(day.prices[sym], "BUY"), f"{x.kind}_buy", sym
            )
            if o2.status == "REJECTED":
                alerts.append(line)
                continue
            x.phase, x.plan["buy_cid"] = "buying", o2.client_id
            report.append(line)
            _refresh(bot, x)
        elif x.phase == "buying":
            got_o = by_cid.get(str(x.plan.get("buy_cid", "")))
            if got_o is None or got_o.status in ("SENT", "UNKNOWN"):
                continue
            if got_o.filled_qty > 0:
                x.status, x.phase = "done", "done"
                report.append(f"{x.pid}: 끝 — {got_o.symbol} {got_o.filled_qty:g}주 되삼")
                _refresh(bot, x)
            else:
                x.phase = "selling"  # 판 것은 체결됐으니 다음 실행에서 다시 산다
    _put(m3, props)
    return alerts, report


# ---------------------------------------------------------------- 신호


def qs_signal(reader: TossReader, now_utc: datetime) -> tuple[bool, str, dict[str, float]]:
    """끝난 미국 거래일 종가까지의 QQQ T 규칙. 진행 중인 날의 봉은 뺀다(뉴욕 16:15 전)."""
    bars = reader.daily_closes("QQQ", 200)
    ny = now_utc.astimezone(NY)
    done = ny.strftime("%Y-%m-%d") if (ny.hour, ny.minute) >= (16, 15) else None
    closed = [(d, px) for d, px in bars if d < ny.strftime("%Y-%m-%d") or d == done]
    if len(closed) < 121:
        raise AutoTradingError(f"QQQ 일봉이 모자란다 ({len(closed)} < 121)")
    as_of = closed[-1][0]
    if (ny.date() - datetime.fromisoformat(as_of).date()).days > 6:
        raise AutoTradingError(f"QQQ 마지막 종가가 오래됐다 ({as_of}) — 판단하지 않는다")
    closes = [float(px) for _, px in closed]
    return trend_on(closes, len(closes) - 1), as_of, trend_parts(closes)


# ---------------------------------------------------------------- 하루 판단 (공통)


class Day:
    """하루치 시세·잔고·상태를 모아 판단한다. ``dry`` 면 상태를 저장하지 않는다."""

    def __init__(
        self, reader: TossReader, now: datetime, *, dry: bool, at: datetime | None = None
    ) -> None:
        self.reader, self.now, self.dry = reader, now, dry
        self.today = now.strftime("%Y-%m-%d")
        self.at = at if at is not None else now - timedelta(hours=9)
        """실제 지금 시각(UTC). ``now`` 는 업무 날짜용(자정 넘은 재시도는 전날)이라 따로 둔다."""
        self.notes: list[str] = []
        self.holdings: dict[str, TossPosition] = reader.holdings()
        self.prices = {
            k: float(v)
            for k, v in reader.prices([p.symbol for p in _plans()] + [TQQQ, DIV]).items()
        }
        self.fx = float(reader.usd_krw())
        fee = reader.us_commission()
        self.fee = float(fee) if fee is not None else TOSS_FEE
        self.usd = float(reader.buying_power("USD"))

    def qty_avg(self, symbol: str) -> tuple[float, float]:
        pos = self.holdings.get(symbol)
        return (float(pos.qty), float(pos.avg_price)) if pos else (0.0, 0.0)

    def resolve(self, orders: list[Order]) -> dict[str, Any]:
        """지난날 주문을 토스 주문 상세로 정리. 체결된 매도 금액(수수료 뺌)을 돌려준다."""
        out: dict[str, Any] = {"sold_usd": None}
        for order in orders:
            if order.status not in OPEN_ORDER or order.date >= self.today:
                continue
            if order.status == "UNKNOWN" or not order.code:
                order.status = "EXPIRED"  # 접수 여부 모름 — 잔고 변화로만 확인한다
                continue
            st = self.reader.order_status(order.code)
            if st.status in FILLED:
                order.status = "FILLED"
                if order.message.startswith("SELL") and st.filled_amount is not None:
                    out["sold_usd"] = float(st.filled_amount - (st.commission or Decimal(0)))
            elif st.status in DEAD or st.status not in ("PENDING", "PENDING_CANCEL"):
                order.status = "EXPIRED"
                self.notes.append(
                    f"{order.date} 주문이 체결되지 않고 끝남 ({st.status}) — 다시 판단"
                )
        return out

    def plan_advice(self, plan: Plan, state: State, qs: QsLiveState | None) -> Advice:
        qty, avg = self.qty_avg(plan.symbol)
        self.notes += reconcile(state, plan, self.today, qty, avg, self.fx, fee=self.fee)
        paused = qs is not None and plan.symbol == "QNDX" and qndx_paused(qs)
        extra = qs.net_krw() if qs is not None and plan.symbol == "QNDX" else 0.0
        return advise(
            state,
            plan,
            self.today,
            self.prices[plan.symbol],
            self.fx,
            weekday=self.now.weekday(),
            extra_spent=extra,
            paused=paused,
            fee=self.fee,
        )

    def qs_prepare(self, qs: QsLiveState, now_utc: datetime) -> dict[str, Any]:
        sold = self.resolve(qs.orders)["sold_usd"]
        qty, avg = self.qty_avg(TQQQ)
        self.notes += reconcile_tqqq(
            qs, self.today, qty, avg, self.prices[TQQQ], self.fx, fee=self.fee, sold_usd=sold
        )
        on, as_of, parts = qs_signal(self.reader, now_utc)
        self.notes += update_signal(qs, self.today, as_of, on)
        return {"on": on, "as_of": as_of, "parts": parts}


def _needs(day: Day, states: dict[str, State], qs: QsLiveState | None) -> list[dict[str, Any]]:
    """오늘 밤 낼 매수 주문 (종목·수량·지정가·달러). 상태는 복사본으로 계산한다."""
    out: list[dict[str, Any]] = []
    accrued_qndx = 0.0
    for plan in _plans():
        state = states.get(plan.symbol)
        if state is None:
            continue
        if plan.symbol in STOPPED:
            continue
        a = day.plan_advice(plan, state, qs)
        if plan.symbol == "QNDX":
            accrued_qndx = a.accrued
        if a.status == "BUY":
            out.append({"symbol": plan.symbol, "qty": 1, "limit": a.limit_usd})
    if qs is not None:
        order = plan_tqqq_order(
            qs,
            day.today,
            accrued_krw=accrued_qndx,
            fx_rate=day.fx,
            last_usd=day.prices[TQQQ],
            weekday=day.now.weekday(),
            kst_hhmm=None,
            trading_enabled=True,
            halted=None,
            fee=day.fee,
        )
        if order.side == "BUY":
            out.append({"symbol": TQQQ, "qty": order.qty, "limit": order.limit_usd})
    out += _m3_needs(day)
    for o in out:
        o["usd"] = round(o["qty"] * o["limit"] * (1 + day.fee), 2)
    return out


# ---------------------------------------------------------------- 명령


def start() -> int:
    if any(_path(f"{p.symbol}.json").exists() for p in _plans()) or _path(QS_FILE).exists():
        print(f"이미 시작했다: {DATA}")
        return 2
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    reader = _trader()
    try:
        holdings = reader.holdings()
        on, as_of, parts = qs_signal(reader, _utc_now())
    finally:
        reader.close()
    lines = [f"토스 적립 + QS 시작 {today}"]
    for plan in _plans():
        pos = holdings.get(plan.symbol)
        qty = float(pos.qty) if pos else 0.0
        _save_plan_state(
            State(plan.symbol, today, qty, qty, qty * float(pos.avg_price if pos else 0))
        )
        lines.append(f"  {plan.symbol}: 기존 {qty:g}주는 적립 밖(기준 수량)")
    pos = holdings.get(TQQQ)
    qty = float(pos.qty) if pos else 0.0
    _save_qs(QsLiveState(today, qty, qty, qty * float(pos.avg_price if pos else 0)))
    lines.append(
        f"  QS: 기존 TQQQ {qty:g}주는 건드리지 않음 · 추세 {'켜짐' if on else '꺼짐'}"
        f" ({as_of} 종가,"
        f" 20일 {parts['r20']:+.1%} · 60일 {parts['r60']:+.1%} · 120일 {parts['r120']:+.1%})"
    )
    print("\n".join(lines))
    return 0


def fx(telegram: bool = True) -> int:
    """오늘 밤 매수에 쓸 달러가 모자라면 환전 금액을 알린다 (주말·휴장 밤 없음은 조용히)."""
    now = _kst_now()
    if now.weekday() >= 5:
        print("주말 — 오늘 밤 미국 장 없음")
        return 0
    states = {p.symbol: s for p in _plans() if (s := _load_plan_state(p)) is not None}
    qs = _live_qs()
    if not states:
        print("아직 시작 전 (start)")
        return 2
    _m3_ask_today(now.strftime("%Y-%m-%d"), telegram)
    _m3_poll(now.strftime("%Y-%m-%d"), telegram, reply=True)
    try:
        _glide_morning(now, telegram)
    except (AutoTradingError, TossApiError) as exc:
        print(f"Glide 제안 확인 실패: {exc}")
    reader = _trader()
    try:
        day = Day(reader, now, dry=True)
        states, qs = copy.deepcopy(states), copy.deepcopy(qs)
        if qs is not None:
            day.qs_prepare(qs, _utc_now())
        needs = _needs(day, states, qs)
    finally:
        reader.close()
    total = sum(n["usd"] for n in needs)
    short = total * (1 + FX_BUFFER) - day.usd
    report = {
        "at": now.isoformat(timespec="seconds"),
        "needs": needs,
        "usd": day.usd,
        "total_usd": round(total, 2),
        "short_usd": round(max(short, 0.0), 2),
        "fx": day.fx,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"fx-{now.strftime('%Y-%m-%d-%H%M')}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8"
    )
    if not needs or short <= 0:
        print(f"환전 필요 없음 — 오늘 밤 매수 {len(needs)}건, 달러 {day.usd:,.2f}")
        return 0
    if not _pref("alerts.fx"):
        print(f"환전 알림 꺼짐 (설정) — 모자란 달러 약 ${short:,.2f}")
        return 0
    usd_to_buy = math.ceil(short)
    items = [f"• {n['symbol']} {n['qty']}주  약 ${n['usd']:,.2f}" for n in needs]
    text = "\n".join(
        [
            "💱 환전 알림 · 오늘 밤 매수 예정",
            RULE,
            *items,
            f"필요 약 ${total:,.2f} · 지금 달러 ${day.usd:,.2f}",
            "",
            f"👉 ${usd_to_buy:,} (약 {usd_to_buy * day.fx:,.0f}원) 환전해 주세요",
            sub("15:30 까지 95% 우대"),
            sub("못 해도 밤에 그대로 주문 (원화 자동 환전이 안 되면 알림)"),
        ]
    )
    _notify(text, telegram)
    print(text)
    return 0


def _send(
    trader: TossTrader,
    orders: list[Order],
    symbol: str,
    side: str,
    qty: int,
    limit: float,
    today: str,
    save: Any,
) -> tuple[str, str]:
    """(상태, 텔레그램 줄). 보내기 전에 주문을 먼저 저장한다."""
    order = Order(today, limit, "UNKNOWN", "", side)
    orders.append(order)
    save()
    word = "매수" if side == "BUY" else "매도"
    try:
        result = trader.order(
            symbol,
            side,
            qty,
            Decimal(f"{limit:.2f}"),
            client_order_id=_client_id(symbol, today, len(orders)),
        )
    except OrderStateUnknownError as exc:
        order.message = f"{side} {exc}"
        return "UNKNOWN", f"⚠ {symbol} {word} 전송 결과 모름 — 다시 보내지 않는다. 내일 잔고로 확인"
    except TradingDisabledError as exc:
        order.status, order.message = "REJECTED", f"{side} {exc}"
        return "REJECTED", f"⏸ {symbol} {word} 안 보냄: {exc}"
    if result.accepted:
        order.status, order.code = "SENT", result.code
        return "SENT", (
            f"🛒 {symbol} {qty}주 장 마감 지정가 {word} 접수 — {limit:.2f} USD"
            f" {'이하' if side == 'BUY' else '이상'}이면 오늘 미국 종가에 체결"
        )
    order.status, order.code, order.message = "REJECTED", result.code, f"{side} {result.message}"
    hint = (
        " — 달러가 모자란다. 환전 뒤 내일 다시"
        if result.code == "insufficient-buying-power"
        else ""
    )
    return "REJECTED", f"❌ {symbol} {word} 거절 {result.code}: {result.message}{hint}"


def _refresh_universe() -> None:
    """종목 목록이 하루 넘게 묵었으면 새로 받는다 (실패해도 적립은 그대로)."""
    path = DATA / "universe.json"
    try:
        if path.exists() and time.time() - path.stat().st_mtime < 20 * 3600:
            return
        universe()
    except Exception as exc:  # 부가 기능 — 어떤 실패도 적립을 막지 않는다
        print(f"종목 목록 새로 받기 실패: {exc}")


def run(telegram: bool = True) -> int:
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    _refresh_universe()
    states = {p.symbol: s for p in _plans() if (s := _load_plan_state(p)) is not None}
    qs = _live_qs()
    if not states:
        print("아직 시작 전 (start)")
        return 2
    lines: list[str] = []
    trader = _trader()
    try:
        day = Day(trader, now, dry=False)
        ctx: dict[str, Any] | None = None
        if qs is not None:
            ctx = day.qs_prepare(qs, _utc_now())
            _save_qs(qs)
        enabled = trader.credentials.trading_enabled
        halted = halted_for(HALT, "acc")  # 적립 · QS: 전체 정지 또는 토스 적립 정지
        halted_glide = halted_for(HALT, "glide")  # Glide: 전체 정지 또는 Glide 정지
        accrued_qndx = 0.0
        for plan in _plans():
            state = states.get(plan.symbol)
            if state is None:  # 새로 추가된 종목 — 오늘 시작, 지금 보유는 적립 밖
                q, avg = day.qty_avg(plan.symbol)
                state = states[plan.symbol] = State(plan.symbol, today, q, q, q * avg)
                day.notes.append(
                    f"{plan.symbol} 적립 시작 (매일 {money(plan.daily, plan.currency)})"
                )
            day.resolve(state.orders)
            if plan.symbol in STOPPED:
                _save_plan_state(state)
                stop_line = f"⏹ {plan.symbol} 적립 중단 — {STOPPED[plan.symbol]}"
                lines += ([""] if lines else []) + [stop_line]
                _write_report(plan.symbol, today, {"symbol": plan.symbol, "status": "STOPPED"})
                continue
            a = day.plan_advice(plan, state, qs)
            if plan.symbol == "QNDX":
                accrued_qndx = a.accrued
            line = None
            ok, reason = should_order(
                state, a, trading_enabled=enabled, halted=halted, kst_hhmm=now.strftime("%H:%M")
            )
            if a.status == "BUY" and ok:
                _, line = _send(
                    trader,
                    state.orders,
                    plan.symbol,
                    "BUY",
                    1,
                    a.limit_usd,
                    today,
                    lambda s=state: _save_plan_state(s),
                )
            elif a.status == "BUY":
                line = f"⏸ 자동 매수 안 함 — {reason}"
            _save_plan_state(state)
            text_one = message(a, [], order_line=line)
            lines += ([""] if lines else []) + [text_one]
            _write_report(
                plan.symbol,
                today,
                {
                    "symbol": plan.symbol,
                    "status": a.status,
                    "advice": a.to_dict(),
                    "auto_buy": enabled,
                    "order": next(
                        (asdict(o) for o in reversed(state.orders) if o.date == today), None
                    ),
                    "holding_qty": day.qty_avg(plan.symbol)[0],
                    "message": text_one,
                },
            )
        if qs is not None and ctx is not None:
            plan_q = plan_tqqq_order(
                qs,
                today,
                accrued_krw=accrued_qndx,
                fx_rate=day.fx,
                last_usd=day.prices[TQQQ],
                weekday=now.weekday(),
                kst_hhmm=now.strftime("%H:%M"),
                trading_enabled=enabled,
                halted=halted,
                fee=day.fee,
            )
            qline = None
            if plan_q.side != "NONE":
                status, qline = _send(
                    trader,
                    qs.orders,
                    TQQQ,
                    plan_q.side,
                    plan_q.qty,
                    plan_q.limit_usd,
                    today,
                    lambda: _save_qs(qs),
                )
                if status == "REJECTED" and plan_q.side == "BUY":
                    code = qs.orders[-1].code
                    if code not in RETRYABLE_QS_BUY:
                        qs.want = "NONE"
                        qline += " — 다시 보내지 않는다. 적립금은 QNDX 로 이어간다"
            elif plan_q.reason != "할 일 없음":
                qline = f"⏸ QS TQQQ 주문 안 함 — {plan_q.reason}"
            _save_qs(qs)
            _write_report(
                QS_KEY,
                today,
                {
                    "trend_on": ctx["on"],
                    "as_of": ctx["as_of"],
                    "parts": ctx["parts"],
                    "strategy_tqqq_qty": qs.strategy_qty,
                    "net_krw": qs.net_krw(),
                    "want": qs.want,
                    "qndx_paused": qndx_paused(qs),
                    "order_line": qline,
                    "order": next(
                        (asdict(o) for o in reversed(qs.orders) if o.date == today), None
                    ),
                },
            )
            p = ctx["parts"]
            head = [
                f"📊 QS  QQQ 추세 {'🟢 켜짐' if ctx['on'] else '🔴 꺼짐'}",
                sub(
                    f"{ctx['as_of']} 종가 · 20일 {p['r20']:+.1%} · 60일 {p['r60']:+.1%}"
                    f" · 120일 {p['r120']:+.1%}"
                ),
                f"{SUB}TQQQ {qs.strategy_qty:g}주 · 순지출 {qs.net_krw():,.0f}원 · 할 일 {qs.want}",
            ]
            lines = head + ([SUB + qline] if qline else []) + ["", RULE] + lines
        if M3_ENABLED:
            _m3_poll(today, telegram)
            m3_lines = _m3_run(day, trader, enabled=enabled, halted=halted_glide, telegram=telegram)
            if m3_lines:
                lines = [*m3_lines, *(["", RULE] if lines else []), *lines]
    except (AutoTradingError, TossApiError) as exc:
        text = f"⚠ 토스 적립 실패\n{RULE}\n{exc}"
        if isinstance(exc, TossApiError) and exc.status == 403:
            text += "\n허용 IP 가 바뀌었을 수 있다 — WTS 설정 > Open API > 허용 IP 관리"
        _notify(text, telegram)
        print(text)
        return 1
    finally:
        trader.close()
    if abs(day.fee - TOSS_FEE) > 1e-9:  # 0.1% 의 '적용 종료일'이 하루씩 밀리는 값이라 바뀌면 알린다
        day.notes.insert(0, f"⚠ 토스 미국 수수료가 {day.fee:.3%} 로 바뀌었다 (기존 {TOSS_FEE:.3%})")
    notes = [f"📌 {n}" for n in day.notes]
    text = "\n".join([f"💰 토스 적립 · {today}", RULE, *notes, *([""] if notes else []), *lines])
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{today}.json").write_text(
        json.dumps(
            {
                "at_ms": int(time.time() * 1000),
                "message": text,
                "usd": day.usd,
                "fee": day.fee,
                "fx": day.fx,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        "utf-8",
    )
    if not QUIET_ROUTINE or any(mark in text for mark in ALERT_MARKS):
        _notify(text, telegram)
    print(text)
    return 0


def m3_only(telegram: bool = True) -> int:
    """M3 만 (재시도 예약 23:40 · 03:30 KST). 이번 달이 끝났으면 아무것도 하지 않는다."""
    now = real = _kst_now()
    if now.hour < 12:  # 자정 넘은 재시도(03:30)는 전날 밤(미국 같은 거래일)의 일이다
        now -= timedelta(days=1)
    today = now.strftime("%Y-%m-%d")
    if not M3_ENABLED or now.weekday() >= 5 or not due(_load_m3(), today):
        print("Glide: 할 일 없음")
        return 0
    if not any(o.date == today for o in _load_m3().orders):
        print("Glide: 오늘 21:00 실행 전 — 재시도만 한다")
        return 0
    _m3_poll(today, telegram)
    trader = _trader()
    try:
        day = Day(trader, now, dry=False, at=real - timedelta(hours=9))
        lines = _m3_run(
            day,
            trader,
            enabled=trader.credentials.trading_enabled,
            halted=halted_for(HALT, "glide"),
            brake=False,
        )
    except (AutoTradingError, TossApiError) as exc:
        text = f"⚠ Glide 재시도 실패\n{RULE}\n{exc}"
        _notify(text, telegram)
        print(text)
        return 1
    finally:
        trader.close()
    text = "\n".join([f"📈 Glide 재시도 · {today} {now:%H:%M}", RULE, *lines])
    _notify(text, telegram)
    print(text)
    return 0


def preview() -> int:
    now = _kst_now()
    states = {p.symbol: s for p in _plans() if (s := _load_plan_state(p)) is not None}
    qs = _live_qs()
    reader = _trader()
    try:
        day = Day(reader, now, dry=True)
        if not states:  # 시작 전이면 지금 보유를 기준으로 가정
            today = now.strftime("%Y-%m-%d")
            for plan in _plans():
                q, a = day.qty_avg(plan.symbol)
                states[plan.symbol] = State(plan.symbol, today, q, q, q * a)
            q, a = day.qty_avg(TQQQ)
            qs = QsLiveState(today, q, q, q * a)
        states, qs = copy.deepcopy(states), copy.deepcopy(qs)
        ctx = day.qs_prepare(qs, _utc_now()) if qs is not None else None
        needs = _needs(day, states, qs)
        advices = [
            day.plan_advice(p, states[p.symbol], qs)
            for p in _plans()
            if p.symbol in states and p.symbol not in STOPPED
        ]
    finally:
        reader.close()
    print("[미리 보기 — 주문·저장 없음]")
    print(f"수수료 {day.fee:.3%} · 환율 {day.fx:,.1f} · 달러 {day.usd:,.2f}")
    if ctx is not None and qs is not None:
        print(
            f"QS: 추세 {'켜짐' if ctx['on'] else '꺼짐'} ({ctx['as_of']} 종가) · 할 일 {qs.want}"
            f" · QNDX 멈춤 {qndx_paused(qs)}"
        )
    print("\n".join(day.notes))
    for adv in advices:
        print(message(adv, []))
    print("오늘 밤 매수:", needs or "없음")
    return 0


def status() -> int:
    for plan in _plans():
        s = _load_plan_state(plan)
        if s is None:
            print(f"{plan.symbol}: 시작 전")
            continue
        spent = sum(b.cost for b in s.buys)
        stop = f" · 적립 중단 ({STOPPED[plan.symbol]})" if plan.symbol in STOPPED else ""
        print(
            f"{plan.symbol}{stop}: 시작 {s.start_date}"
            f" · 적립 매수 {sum(b.qty for b in s.buys):g}주 "
            f"· 쓴 돈 {money(spent, plan.currency)}"
        )
    qs = _load_qs()
    if qs is not None:
        print(
            ("QS (실거래 꺼짐 — Paper 만): " if not QS_LIVE else "")
            + f"QS: 추세 {qs.prev_on} · 할 일 {qs.want} · QS TQQQ {qs.strategy_qty:g}주 "
            f"· 순지출 {qs.net_krw():,.0f}원"
        )
    if M3_ENABLED:
        m3 = _load_m3()
        print(
            f"Glide(M3): 시작 {m3.start} · 이번 달 끝 {m3.done_month or '아직'}"
            f" · 전략 현금 ${m3.cash_usd:,.2f} · TQQQ {m3.qty.get(LEV, 0):g}"
            f" · SCHD {m3.qty.get(DIV, 0):g} · 월 {_m3_monthly():,.0f}원"
        )
        b = _brake_of(m3)
        print(
            f"Glide 브레이크({'켜짐' if M3_BRAKE else '꺼짐'}): 단계 {b.stage}"
            f" · TQQQ 고점 ${b.lev_peak:.2f}"
            f" ({b.last_day or '기록 전'}) · 보관금 ${b.reserve_usd:,.2f}"
            f" · 누적 매도 ${b.sold_total_usd:,.2f}"
        )
    return 0


def harvest_plan(other_gains_krw: float) -> int:
    """연말 양도세 공제(250 만 원) 수확 계획 — 조회만, 주문 없음 (문서 123)."""
    from dataclasses import asdict as _asdict

    from auto_trading.ops.tax_harvest import Holding, plan, qs_realized_krw

    now = _kst_now()
    reader = _trader()
    try:
        holdings = reader.holdings()
        fx_rate = float(reader.usd_krw())
        fee = reader.us_commission()
    finally:
        reader.close()
    strategy = {p.symbol for p in _plans()}
    hs = [
        Holding(sym, float(pos.qty), float(pos.avg_price), float(pos.last), sym in strategy)
        for sym, pos in holdings.items()
        if pos.currency == "USD"
    ]
    qs = _load_qs()
    qs_gain = qs_realized_krw([_asdict(t) for t in qs.trades], now.strftime("%Y")) if qs else 0.0
    result = plan(
        hs,
        fx_rate,
        other_gains_krw=other_gains_krw,
        qs_gains_krw=qs_gain,
        fee=float(fee) if fee is not None else TOSS_FEE,
    )
    print("[양도세 공제 수확 계획 — 조회만, 주문 없음]")
    print(
        f"공제 250만 원 − 다른 실현 이익 {other_gains_krw:,.0f}원 − QS 실현 {qs_gain:,.0f}원"
        f" − 여유 30만 원 = 쓸 수 있는 공제 {max(result.budget_krw, 0):,.0f}원"
        f" (환율 {fx_rate:,.1f})"
    )
    for h in hs:
        gain = (h.last_usd - h.avg_usd) * h.qty * fx_rate
        mark = "자동 적립" if h.strategy else "직접 보유"
        print(f"  {h.symbol} {h.qty:g}주 · 평가 이익 약 {gain:,.0f}원 · {mark}")
    if not result.lines:
        print("팔 것 없음 — 이익 난 종목이 없거나 공제가 남지 않았다")
        return 0
    for x in result.lines:
        who = "" if x.strategy else " (직접 보유 — 사용자 결정)"
        print(f"→ {x.symbol} {x.qty}주 팔고 다음 날 다시 사기 · 실현 약 {x.gain_krw:,.0f}원{who}")
    print(
        f"수확 약 {result.harvested_krw:,.0f}원 → 미래 세금 약 {result.future_tax_saved_krw:,.0f}원"
        f" 줄어듦 · 수수료 약 ${result.cost_usd:,.2f} · 하루는 시장 밖"
    )
    return 0


def m3_chat(telegram: bool = True) -> int:
    """매수 당일 09:00 ~ 20:59 에만 텔레그램 답을 읽어 계획을 답장한다 (5 분마다 예약용)."""
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    m3 = _load_m3()
    asking = due(m3, today) and m3.ask_month == today[:7]
    live = any(
        x.status == "open" or x.awaiting_qty or (x.status == "accepted" and x.answered == today)
        for x in _props(m3)
    )
    if not M3_ENABLED or now.weekday() >= 5 or not (9 <= now.hour < 21) or not (asking or live):
        print("Glide 대화: 할 일 없음")
        return 0
    _m3_poll(today, telegram, reply=asking)
    return 0


def m3_test(telegram: bool = True) -> int:
    """테스트: 묻기 · 계획 답장 두 통을 지금 상태로 보낸다 (상태 · 주문 바꾸지 않음)."""
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    mark = "🧪 테스트 — 실제 묻기 · 주문 아님"
    ask = f"{mark}\n{ask_message(today, _m3_monthly())}"
    _notify(ask, telegram)
    print(ask)
    plan = _m3_preview(now, title=f"{mark} (답 30만원이라고 가정)")
    _notify(plan, telegram)
    print(plan)
    return 0


def main(argv: list[str]) -> int:
    telegram = "--no-telegram" not in argv
    args = [a for a in argv if a != "--no-telegram"]
    if args[:1] == ["harvest-plan"]:
        other = float(args[2]) if len(args) == 3 and args[1] == "--other-gains" else 0.0
        return harvest_plan(other)
    commands: dict[str, Callable[[], int]] = {
        "start": start,
        "run": lambda: run(telegram),
        "fx": lambda: fx(telegram),
        "m3": lambda: m3_only(telegram),
        "m3-chat": lambda: m3_chat(telegram),
        "m3-test": lambda: m3_test(telegram),
        "preview": preview,
        "status": status,
        "universe": lambda: universe(telegram),
    }
    if len(args) != 1 or args[0] not in commands:
        raise SystemExit(
            "usage: python -m scripts.toss_accumulate"
            " start|run|fx|m3|m3-chat|m3-test|preview|status|universe"
            " [--no-telegram]"
            " | harvest-plan [--other-gains 원]"
        )
    return commands[args[0]]()


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _r = getattr(_s, "reconfigure", None)
        if _r is not None:
            _r(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["LIMIT_BUFFER", "Day", "qs_signal"]
