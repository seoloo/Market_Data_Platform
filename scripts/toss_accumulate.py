"""토스 밤 실행기 — 🌟 Deneb 매달 적립 · 하락 방어 · 💰 미국 자동 적립 (문서 118 · 182 · 183).

언제 실행: 평일 21:00 ``AutoTrading_Toss_Accumulate`` (``run``) · 재시도 23:40 · 03:30
           ``AutoTrading_Deneb_Retry`` (``deneb``) · 금액 묻기 · 답 09:00 부터
           ``AutoTrading_Deneb_Chat`` (``deneb-chat``) · 환전 알림 09 · 12 · 15 시
           ``AutoTrading_Toss_FX`` (``fx``).
읽고 쓰는 것: 토스 API · 장부 ``data/toss/``
              (``DENEB.json`` · ``<기호>.json`` · ``QS_SWITCH.json``) ·
              보고 ``reports/toss/<KST 날짜>.json`` · ``reports/toss/DENEB_BRAKE/`` ·
              알림 기록 ``outbox.log``.

사용 예::

    python -m scripts.toss_accumulate start    # 처음 한 번 (지금 보유 = 기준 수량)
    python -m scripts.toss_accumulate run      # 21:00 KST 월~금: 확인 → 판단 → 장 마감 지정가
    python -m scripts.toss_accumulate fx       # 09·12·15시 KST 월~금: 달러가 모자라면 알림
    python -m scripts.toss_accumulate preview  # 오늘 밤 할 일 미리 보기 (주문·저장 없음)
    python -m scripts.toss_accumulate status

* **자동 적립** (한국투자 판 문서 89 를 토스로 옮김): 종목마다 매일 정한 금액이 쌓이고,
  1 주 값이 모이면 1 주 산다 (BTQ · WQTM · QS, QNDX 는 2026-10-09 멈춤).
* **QS 전략** (QQQ 추세 꺼짐이면 QNDX 멈춤 · 켜지면 쌓인 돈으로 TQQQ) 실거래는 꺼짐(``QS_LIVE``).
* **Deneb** (문서 182): 매달 첫 거래일 TQQQ + SCHD (+ 금), 다음 거래일부터 남은 현금 채우기.
* **T 브레이크** (문서 183 ~ 188, 매일 21:00): TQQQ 고점 대비 −15% 절반 · −30% 전부 매도 → 달러 보관
  → QQQ 추세가 꺼졌다 켜지면 되산다. 매도 · 재매수는 알리지 않고 ``reports/toss/DENEB_BRAKE/`` 와
  매달 Deneb 알림 맨 아래에 적는다.
* 주문은 **장 마감 지정가(LOC)** — 그날 미국 종가에 체결된다 (매수 지정가 +3%, 매도 −3%).
  ``TOSS_TRADING_ENABLED=true`` 일 때만 주문, 정지 파일(``data/HALT`` · 그룹)이면 멈춘다.
  달러가 모자라 거절되면 알린다
  (QS 의 TQQQ 매수 · Deneb 은 다음에 다시, 그 밖의 거절은 다시 안 보냄).

계산은 ``ops/deneb_live.py`` (Deneb) · ``ops/deneb_guard.py`` (브레이크 · 제안) ·
``ops/qndx_alert.py`` (적립) · ``ops/qs_live.py`` (QS).
이 파일은 조회 · 주문 · 장부 · 알림을 맡는다.
함수 이름은 시험이 바꿔 끼우는 것이 많아 리팩토링 3 단계(2026-10-20)에서도 그대로 두었다.
옛 이름 'M3' = 지금의 Deneb (주문 번호 앞머리 ``M3`` 도 그대로).
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

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
from auto_trading.ops import deposit_split, user_settings
from auto_trading.ops.deneb_guard import (
    ACCOUNT_STEPS_KRW,
    DIV_STEPS_KRW,
    STAGE_SHARE,
    STEP_CUT,
    Brake,
    Proposal,
    added_monthly_krw,
    brake_ask_line,
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
    step_due_account,
    typed,
)
from auto_trading.ops.deneb_live import (
    DIVIDEND_ETF,
    GOLD_ETF,
    GOLD_MIX,
    LEVERAGED_ETF,
    DenebOrder,
    DenebState,
    amount_target,
    apply_fill,
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
    plan_monthly_rebalance,
    plan_next_day_fill,
    ratio_text,
    targets,
)
from auto_trading.ops.deneb_live import SYMBOLS as DENEB_SYMBOLS
from auto_trading.ops.kill_switch import halted_for
from auto_trading.ops.mf_paper import LEV_MAX
from auto_trading.ops.qndx_alert import (
    LIMIT_BUFFER,
    MAX_ORDER_QTY,
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
"""조용한 밤에도 이 표시가 든 줄이 있으면 텔레그램으로 보낸다."""
DENEB_ENABLED = True
DENEB_START = "2026-10-09"
DENEB_MONTHLY_KRW = 300_000.0
"""2026-10-09 사용자 "첫 거래일로 하고 당분간은 월 30만원으로만 매수해줘" (문서 182 M3, 토스)."""
DENEB_FILE = "DENEB.json"
DENEB_MAX_QTY = {DIVIDEND_ETF: 40, GOLD_ETF: 40}
"""한 번에 SCHD · IAUM 최대 40 주(TQQQ 는 QS 와 같은 상한 MAX_TQQQ_QTY)."""
DENEB_RETRY = frozenset({"insufficient-buying-power", "order-hours-closed"})
"""이 거절(달러 부족 · 장 시간 밖)은 다시 해 볼 만하다 — 재시도 예약 · 다음 날 채우기."""
DENEB_BRAKE = True
"""T 브레이크 (문서 183 ~ 188, 사용자 2026-10-09 '처음부터'): TQQQ 고점 −15% 절반 · −30% 전부,
판 돈은 달러 보관, QQQ 추세가 꺼졌다 켜지면 되산다. 매일 21:00 실행에서만 판단한다."""
DENEB_FILL = True
"""다음 날 채우기 (사용자 2026-10-18 "b로 변경 부탁해", reports/deneb_fraction_20261018):
그달 계획 다음 거래일부터 매일 21:00, 남은 전략 현금으로 목표보다 가장 모자란 종목을
1 주씩 장 마감 지정가로 산다(사기만)."""
BRAKE_KINDS = frozenset({"brake", "rebuy"})
"""보관금으로 돈이 오가는 주문 종류 (전략 현금 계산에서 뺀다)."""
KIND_WORDS = {
    "fill": "채우기 매수",
    "brake": "브레이크 매도",
    "rebuy": "재매수",
    "harvest_sell": "공제 실현 매도",
    "harvest_buy": "공제 실현 되사기",
    "div_step_sell": "배당 단계 매도",
    "div_step_buy": "배당 단계 SCHD 매수",
}
"""장부의 주문 종류 값 → 알림에 쓸 말 (값은 파일에 저장돼 바꾸지 않는다)."""
DIV_STEP_DAYS = 30
"""배당 단계 질문은 30 일 동안 열어 두고, 답이 없으면 '이번엔 안 함' 으로 넘어간다."""
DENEB_MAX_KRW = 5_000_000.0
"""텔레그램 금액 답의 상한 (오타 방지). 넘으면 다시 묻는다."""


def _setting(key: str) -> Any:
    """[쓰임] 오르빗 설정 화면 값 하나 (사용자 2026-10-10). 저장 전이면 기본값 = 지금 운용값.
    [이유] REPO 를 부를 때마다 본다 — 시험이 REPO 를 임시 폴더로 바꾼다."""
    return user_settings.get(REPO, key)


def _deneb_monthly() -> float:
    """[쓰임] Deneb 기본 월 납입(원) — 텔레그램 답이 없을 때 쓴다 (설정 ``deneb.monthly_krw``)."""
    return float(_setting("deneb.monthly_krw"))


def _deneb_gold() -> float:
    """[쓰임] 금 섞기 설정 (``deneb.gold_on``, 사용자 2026-10-19) → 나머지 중 금 몫 (꺼지면 0)."""
    return GOLD_MIX if _setting("deneb.gold_on") else 0.0


def _deneb_max() -> float:
    """[쓰임] 텔레그램 금액 답의 상한(원) (설정 ``deneb.max_krw``)."""
    return float(_setting("deneb.max_krw"))


EXTRA_MAX_LIMIT_USD = 1_000.0
"""설정 화면에서 추가한 미국 종목의 지정가 상한 (1 주 $1,000 넘는 종목은 적립에 안 맞는다)."""
_EXCHANGES = {"NASDAQ": ("NAS", "NASD"), "NYSE": ("NYS", "NYSE"), "AMEX": ("AMS", "AMEX")}
"""토스 거래소 이름 → (시세 코드, 주문 코드) — 한국투자 판 ``Plan`` 모양을 그대로 쓰려고."""


def _plans() -> tuple[Plan, ...]:
    """[쓰임] 지금 적립 종목 목록 — 기본 종목 + 설정 화면에서 바꾼 하루 금액 · 켜기 기록(내일부터,
    끈 기간은 0).
    [이유] 설정에서 뺀 기본 종목은 빼고, 추가한 **미국** 종목은 더한다(국내는 scripts/toss_kr.py).
           뺀 종목의 장부 파일은 남는다 — 보유 · 원가 기록을 잇기 위해."""
    prefs = user_settings.load(REPO)
    plans = []
    for plan in PLANS:
        if plan.symbol in user_settings.ACC_SYMBOLS and prefs.get(f"acc.{plan.symbol}.removed"):
            continue
        history = user_settings.acc_history(REPO, plan.symbol)
        if history:
            rates = [
                plan.daily,
                *(rate for _, rate in plan.changes),
                *(rate for _, rate in history),
            ]
            max_daily = max(rates)
            plan = replace(
                plan,
                changes=(*plan.changes, *history),
                monthly_cap=max(plan.monthly_cap, max_daily * 31),
            )
        plans.append(plan)
    for item in prefs.get("_extras") or []:
        if item.get("mkt") != "US" or item.get("removed"):
            continue
        history = user_settings.acc_history(REPO, str(item["sym"]))
        if not history:
            continue
        quote_code, order_code = _EXCHANGES.get(str(item.get("market")), ("NAS", "NASD"))
        max_daily = max(rate for _, rate in history)
        plans.append(
            Plan(
                str(item["sym"]),
                quote_code,
                order_code,
                "USD",
                0.0,  # 추가한 다음 날부터 하루 금액 (기록이 정한다)
                max_daily * 31,
                EXTRA_MAX_LIMIT_USD,
                changes=tuple(history),
            )
        )
    return tuple(plans)


def universe(_: bool = True) -> int:
    """[쓰임] 토스 거래 가능 종목 목록을 ``data/toss/universe.json`` 으로.

    설정 화면 '종목 추가' 가 키 없이 이 파일을 읽는다."""
    reader = _trader()
    try:
        markets = {}
        for market in ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ", "AMEX"):
            markets[market] = reader.stocks_all(market)
    finally:
        reader.close()
    keep = ("symbol", "name", "securityType")
    slim: dict[str, list[dict[str, Any]]] = {}
    for market, rows in markets.items():
        slim[market] = [{key: row.get(key) for key in keep} for row in rows]
    body = {"updated": _kst_now().strftime("%Y-%m-%d %H:%M"), "markets": slim}
    DATA.mkdir(parents=True, exist_ok=True)
    temporary = DATA / "universe.tmp"
    temporary.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    temporary.replace(DATA / "universe.json")
    print("종목 목록: " + " · ".join(f"{market} {len(rows)}" for market, rows in markets.items()))
    return 0


TOSS_FEE = 0.001
"""토스 미국 수수료 0.1% (2026-10-04 API). 실행 때 ``/commissions`` 값이 있으면 그것을 쓴다."""
FX_BUFFER = 0.02
"""환전 안내 금액 여유 — 밤사이 가격·환율 변동."""
NY = ZoneInfo("America/New_York")
FILLED = frozenset({"FILLED", "PARTIAL_FILLED"})
"""토스 주문 상태 중 '체결이 있음'."""
DEAD = frozenset({"CANCELED", "REJECTED", "REPLACED"})
"""토스 주문 상태 중 '끝났는데 체결 없음'."""
RETRYABLE_QS_BUY = frozenset({"insufficient-buying-power", "order-hours-closed"})


def _kst_now() -> datetime:
    """[쓰임] 지금 한국 시각 (UTC + 9 시간 — 시간대 표시는 UTC 그대로).

    시험이 이것을 바꿔 끼워 날짜를 고정한다."""
    return datetime.now(tz=UTC) + timedelta(hours=9)


def _utc_now() -> datetime:
    """[쓰임] 지금 시각(UTC). ``_kst_now`` 에서 만들어 시계를 하나로 쓴다.

    시험이 ``_kst_now`` 하나만 고정해도 두 시계가 함께 고정된다."""
    return _kst_now() - timedelta(hours=9)


def _path(name: str) -> Path:
    return DATA / name


def _load_plan_state(plan: Plan) -> State | None:
    """[쓰임] 적립 종목 장부 ``data/toss/<기호>.json`` (없으면 None — 그날 새로 시작)."""
    _migrate_qs_switch()
    path = _path(f"{plan.symbol}.json")
    if not path.exists():
        return None
    return State.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _save_plan_state(state: State) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _path(f"{state.symbol}.json").write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _migrate_qs_switch() -> None:
    """[쓰임] 옛 QS 전략 상태 ``QS.json`` → ``QS_SWITCH.json`` (2026-10-04, 한 번).
    [이유] 종목 QS(퀀텀스케이프) 적립을 더하면서 이름이 겹쳤다. ``QS.json`` 이 QS 전략
           형식(``prev_on``)일 때만 옮긴다 — 종목 QS 적립 상태는 건드리지 않는다."""
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
    """[쓰임] QS 전략 장부 ``QS_SWITCH.json`` (없으면 None)."""
    _migrate_qs_switch()
    path = _path(QS_FILE)
    if not path.exists():
        return None
    return QsLiveState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _live_qs() -> QsLiveState | None:
    """[쓰임] 실거래 QS 상태 — ``QS_LIVE`` 가 꺼져 있으면 None (주문 · 신호 갱신 없음)."""
    return _load_qs() if QS_LIVE else None


def _save_qs(qs: QsLiveState) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _path(QS_FILE).write_text(
        json.dumps(qs.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _notify(text: str, telegram: bool) -> None:
    """[쓰임] 알림 — 늘 ``reports/toss/outbox.log`` 에 남기고, ``telegram`` 이면 텔레그램으로도.
    [이유] 텔레그램이 실패해도 실행은 멈추지 않는다 (기록 파일에는 남아 있다)."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(REPORTS / "outbox.log").send(text)
    if not telegram:
        return
    credentials = load_telegram_credentials(REPO / ".env")
    if credentials is None:
        return
    try:
        TelegramNotifier(credentials).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


def _trader() -> TossTrader:
    """[쓰임] 토스 주문 창구 — 허용 종목 · 수량 · 지정가 상한을 이 실행기가 쓰는 만큼만 연다.
    [이유] 버그로 다른 종목 · 큰 수량이 나가지 않게 하는 마지막 문 (exchange/toss.py 가 검사)."""
    plans = _plans()
    symbols = {plan.symbol for plan in plans} | {TQQQ} | set(DENEB_SYMBOLS)
    limit = max([plan.max_limit_usd for plan in plans] + [MAX_TQQQ_LIMIT_USD])
    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(symbols),
        max_qty={
            **dict.fromkeys((plan.symbol for plan in plans), MAX_ORDER_QTY),
            TQQQ: MAX_TQQQ_QTY,
            **DENEB_MAX_QTY,
        },
        sell_symbols=frozenset({TQQQ, DIVIDEND_ETF, GOLD_ETF}),
        max_limit_usd=Decimal(str(limit)),
    )


def _write_report(name: str, today: str, body: dict[str, Any]) -> None:
    """[쓰임] 그날 보고 파일 ``reports/toss/<name>/<날짜>.json`` (같은 날은 덮어씀)."""
    folder = REPORTS / name
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"at_ms": int(time.time() * 1000), "date": today, **body}
    (folder / f"{today}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _client_id(symbol: str, today: str, number: int) -> str:
    """[쓰임] 토스 주문 번호(우리 쪽) ``<기호>-<날짜>-<순번>`` — 영문 · 숫자 · - _ 만, 36 자까지."""
    return re.sub(r"[^A-Za-z0-9_-]", "", f"{symbol}-{today}-{number}")[:36]


# ---------------------------------------------------------------- Deneb (옛 M3, 문서 182)


def _load_deneb() -> DenebState:
    """[쓰임] Deneb 장부 ``data/toss/DENEB.json`` (없으면 시작일만 든 새 장부)."""
    path = _path(DENEB_FILE)
    if path.exists():
        return DenebState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return DenebState(DENEB_START)


def _save_deneb(deneb: DenebState) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    _path(DENEB_FILE).write_text(
        json.dumps(deneb.to_dict(), ensure_ascii=False, indent=1) + "\n", "utf-8"
    )


def _dividend_events() -> list[tuple[str, str, float]]:
    """[쓰임] Yahoo 배당 기록 [(배당락일, 종목, 주당)] (최근 1 년). 실패하면 빈 목록 — 다음 날 다시.
    [이유] 토스 API 에 배당 기록이 없어서 공개 시세 사이트에서 읽는다 (키 필요 없음)."""
    import httpx

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    )
    events: list[tuple[str, str, float]] = []
    for symbol in DENEB_SYMBOLS:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            chart = httpx.get(
                url,
                params={"range": "1y", "interval": "1d", "events": "div"},
                headers={"User-Agent": user_agent},
                timeout=30,
            ).json()["chart"]["result"][0]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            continue
        dividends = (chart.get("events") or {}).get("dividends") or {}
        for event in dividends.values():
            ex_moment = datetime.fromtimestamp(int(event["date"]), UTC).astimezone(NY)
            events.append((ex_moment.strftime("%Y-%m-%d"), symbol, float(event["amount"])))
    return events


def _days_between(start: str, end: str) -> int:
    """[쓰임] 두 날짜(YYYY-MM-DD) 사이 날수 (end − start)."""
    from datetime import date as _date

    return (_date.fromisoformat(end) - _date.fromisoformat(start)).days


def _deneb_resolve(day: Day, deneb: DenebState) -> tuple[list[str], list[str]]:
    """[쓰임] 지난 Deneb 주문을 토스 주문 상세로 정리해 장부에 넣는다.
    → (알림 줄, 조용한 줄 — 브레이크 · 채우기 등 종류가 있는 주문은 알리지 않음).
    [이유] 장부는 '실제로 체결된 수량 · 금액 · 수수료' 로만 고친다 (낸 주문 수량이 아니라).
           보내기 전 저장만 되고 결과를 모르는 주문(UNKNOWN)은 사람이 확인하게 알린다."""
    notes: list[str] = []
    quiet: list[str] = []
    for order in deneb.orders:
        if order.date >= day.today:
            continue
        if order.status == "UNKNOWN":
            order.status = "EXPIRED"
            notes.append(
                f"⚠ Deneb {order.symbol} {order.date} 주문 전송 결과 모름"
                " — 잔고를 직접 확인해 주세요"
            )
            continue
        lines = quiet if order.kind else notes
        if order.status != "SENT":
            continue
        status = day.reader.order_status(order.order_id)
        if status.status in ("PENDING", "PENDING_CANCEL"):
            continue
        order.filled_qty = float(status.filled_qty)
        amount = status.filled_amount
        if amount is None and status.avg_price is not None:
            amount = status.filled_qty * status.avg_price
        order.filled_usd = float(amount or 0)
        order.commission = float(status.commission or 0)
        order.status = "FILLED" if order.filled_qty > 0 else "EXPIRED"
        apply_fill(deneb, order)
        word = "매수" if order.side == "BUY" else "매도"
        if order.filled_qty > 0:
            lines.append(
                f"Deneb {order.symbol} {order.filled_qty:g}주 {word} 체결"
                f" — ${order.filled_usd:,.2f}"
            )
        else:
            lines.append(
                f"Deneb {order.symbol} {word} 미체결 ({status.status}) — 돈은 다음 달로 이월"
            )
    return notes, quiet


def _load_brake(deneb: DenebState) -> Brake:
    """[쓰임] 장부의 브레이크 상태 (처음이면 T 기준 새 상태)."""
    return Brake.from_dict(deneb.brake) if deneb.brake else Brake(ref="T")


def _deneb_book(deneb: DenebState, brake: Brake) -> list[str]:
    """[쓰임] 정리된 브레이크 · 재매수 주문을 보관금에 반영한다 (주문마다 한 번만). 보고 줄 목록.
    [이유] 브레이크로 판 돈은 매달 비율 계산에 안 쓰는 '보관금' 이라 전략 현금에서 옮겨 둔다."""
    report: list[str] = []
    for order in deneb.orders:
        if order.kind not in BRAKE_KINDS or order.booked:
            continue
        if order.status not in ("FILLED", "EXPIRED"):
            continue
        info = brake.pending.pop(order.client_id, {})
        order.booked = True
        if order.kind == "brake" and order.filled_qty > 0:
            net_usd = order.filled_usd - order.commission
            deneb.cash_usd -= net_usd  # apply_fill 이 전략 현금에 넣은 판 돈을 보관금으로 옮긴다
            sold(brake, order.date, float(info.get("dd", 0.0)), order.filled_qty, net_usd)
            report.append(
                f"브레이크 매도 체결 {order.date} TQQQ {order.filled_qty:g}주 ${net_usd:,.2f}"
                f" → 보관금 ${brake.reserve_usd:,.2f}"
            )
        elif order.kind == "brake":
            brake.stage = int(info.get("prev_stage", brake.stage))
            report.append(
                f"브레이크 매도 {order.date} 미체결 — 단계 {brake.stage} 로 되돌리고 다음 밤 다시"
            )
        elif order.filled_qty > 0:
            # 남은 보관금은 전략 현금으로 (산 값은 apply_fill 이 뺐다)
            deneb.cash_usd += brake.reserve_usd
            price = order.filled_usd / order.filled_qty
            rebought(brake, order.date, 0.0, price)
            brake.last_day = order.date
            report.append(
                f"재매수 체결 {order.date} TQQQ {order.filled_qty:g}주 ${order.filled_usd:,.2f}"
                f" — 브레이크 끝, 고점 ${price:.2f} 부터 다시"
            )
        else:
            report.append(f"재매수 {order.date} 미체결 — 다음 밤 다시")
    return report


def _deneb_send(
    day: Day,
    trader: TossTrader,
    deneb: DenebState,
    side: str,
    qty: int,
    limit: float,
    kind: str,
    symbol: str = LEVERAGED_ETF,
) -> tuple[DenebOrder, str]:
    """[쓰임] 브레이크 · 재매수 · 채우기 · 공제 실현 · 배당 단계 주문 하나 (장 마감 지정가).
    → (주문, 알림 줄).
    [이유] **보내기 전에 장부에 저장한다** — 보내다 PC 가 꺼져도 '낸 줄 모르는 주문' 이 없게."""
    client_id = _client_id(f"M3{symbol}", day.today, len(deneb.orders) + 1)
    order = DenebOrder(day.today, symbol, side, qty, limit, client_id, kind=kind)
    deneb.orders.append(order)
    _save_deneb(deneb)
    word = KIND_WORDS.get(kind, kind)
    try:
        result = trader.order(symbol, side, qty, Decimal(f"{limit:.2f}"), client_order_id=client_id)
    except OrderStateUnknownError as exc:
        return order, f"⚠ Deneb {symbol} {word} 전송 결과 모름 — 다시 보내지 않는다: {exc}"
    except TradingDisabledError as exc:
        order.status = "REJECTED"
        return order, f"❌ Deneb {symbol} {word} 안 보냄: {exc}"
    if result.accepted:
        order.status = "SENT"
        order.order_id = result.code
        return order, f"🛑 Deneb {symbol} {qty}주 {word} 접수 — 장 마감 지정가 {limit:.2f} USD"
    order.status = "REJECTED"
    order.code = result.code
    return order, f"❌ Deneb {symbol} {word} 거절 {result.code}: {result.message}"


def _first_tqqq_buy_day(deneb: DenebState) -> str | None:
    """[쓰임] Deneb 이 TQQQ 를 처음 산(체결된) 날 — 브레이크 고점은 이날부터 센다."""
    days = []
    for order in deneb.orders:
        if order.symbol == LEVERAGED_ETF and order.side == "BUY" and order.filled_qty > 0:
            days.append(order.date)
    return min(days, default=None)


def _deneb_brake(
    day: Day, trader: TossTrader, deneb: DenebState, *, enabled: bool, halted: str | None
) -> tuple[list[str], list[str]]:
    """[쓰임] T 브레이크 하루치 → (알림 줄 — 실패만, 보고 줄). 매도 · 재매수 성공은 알리지 않는다.
    [이유] 사용자가 매일 신경 쓰지 않게 — 내역은 보고 파일과 매달 Deneb 알림 맨 아래에 남긴다."""
    alerts: list[str] = []
    brake = _load_brake(deneb)
    report = _deneb_book(deneb, brake)
    first_buy = _first_tqqq_buy_day(deneb)
    if first_buy is None:
        deneb.brake = brake.to_dict()
        return alerts, [*report, "TQQQ 산 적 없음 — 브레이크 대기"]
    # 1 단계: 끝난 미국 거래일 종가로 TQQQ 고점 갱신
    # (21:00 이 자정 넘어 늦게 돌아도 장중 봉을 어제 종가로 쓰지 않는다, 2026-10-12)
    settled = last_settled_day(day.at)
    closes = []
    for close_day, price in day.reader.daily_closes(LEVERAGED_ETF, 40):
        if close_day < day.today and close_day <= settled:
            closes.append((close_day, float(price)))
    for close_day, price in closes:
        new_day = close_day > brake.last_day or brake.lev_peak == 0
        if close_day >= max(first_buy, brake.last_day) and new_day:
            brake.lev_peak = max(brake.lev_peak, price)
            brake.last_day = close_day
    if not closes:
        deneb.brake = brake.to_dict()
        return [*alerts, "⚠ Deneb 브레이크: TQQQ 일봉 없음 — 오늘 판단 못 함"], report
    last_day, last_close = closes[-1]
    dd = drawdown(brake, last_close)
    report.append(
        f"TQQQ {last_day} 종가 ${last_close:.2f} · 고점 ${brake.lev_peak:.2f} · 낙폭 {dd or 0:.1%}"
        f" · 단계 {brake.stage} · 보관금 ${brake.reserve_usd:,.2f}"
    )
    # 2 단계: 주문을 낼 수 있나
    if brake.pending:
        deneb.brake = brake.to_dict()
        return alerts, [*report, "접수한 브레이크 주문 체결 확인 전 — 오늘은 판단만"]
    if not enabled or halted:
        deneb.brake = brake.to_dict()
        why = "TOSS_TRADING_ENABLED 꺼짐" if not enabled else f"정지: {halted}"
        return alerts, [*report, f"브레이크 주문 안 함 — {why}"]
    # 3 단계: 팔 때인가 (−15% · −30%)
    previous_stage = brake.stage
    sell_qty = brake_sell(brake, dd, deneb.qty.get(LEVERAGED_ETF, 0.0))
    if sell_qty > 0:
        sell_limit = limit_price(day.prices[LEVERAGED_ETF], "SELL")
        order, line = _deneb_send(day, trader, deneb, "SELL", sell_qty, sell_limit, "brake")
        if order.status == "REJECTED":
            brake.stage = previous_stage
            order.booked = True
            alerts.append(line)
        else:
            brake.pending[order.client_id] = {
                "kind": "brake",
                "dd": dd,
                "prev_stage": previous_stage,
            }
            (alerts if order.status == "UNKNOWN" else report).append(line)
    # 4 단계: 보관 중이면 되살 때인가 (QQQ 추세가 꺼졌다 켜짐)
    elif brake.stage > 0 and brake.sales:
        trend_now: bool | None = None
        try:
            trend_now, as_of, _ = qs_signal(day.reader, _utc_now())
            report.append(f"QQQ 추세 {'켜짐' if trend_now else '꺼짐'} ({as_of} 종가)")
        except AutoTradingError as exc:
            alerts.append(f"⚠ Deneb 브레이크 추세 확인 실패: {exc}")
        if rebuy_due(brake, trend_now):
            buy_limit = limit_price(day.prices[LEVERAGED_ETF], "BUY")
            buy_qty = math.floor(brake.reserve_usd / (buy_limit * (1 + day.fee)))
            if buy_qty >= 1:
                order, line = _deneb_send(day, trader, deneb, "BUY", buy_qty, buy_limit, "rebuy")
                if order.status == "REJECTED":
                    order.booked = True
                    alerts.append(line)
                else:
                    brake.pending[order.client_id] = {"kind": "rebuy"}
                    (alerts if order.status == "UNKNOWN" else report).append(line)
            else:
                deneb.cash_usd += brake.reserve_usd
                rebought(brake, last_day, 0.0, last_close)
                brake.last_day = last_day
                report.append("보관금이 1 주 값보다 적어 전략 현금으로 합침 — 브레이크 끝")
    deneb.brake = brake.to_dict()
    return alerts, report


def _deneb_share(day: Day, deneb: DenebState) -> tuple[float | None, float]:
    """[쓰임] (σ20, 이번 달 TQQQ 몫) — 지난달 말까지 변동성 × 배당 단계 상한 × 브레이크 단계.
    [이유] 브레이크 중이면 TQQQ 목표를 줄인다(1 단계 절반 · 2 단계 0) —
           단, 오늘 되사기를 냈으면 그대로."""
    closes = [(close_day, float(price)) for close_day, price in day.reader.daily_closes("QQQ", 60)]
    vol, share = lev_share(closes, day.today)
    share *= min(deneb.lev_cap, LEV_MAX) / LEV_MAX  # 배당 단계로 낮아진 TQQQ 상한
    if DENEB_BRAKE and deneb.brake:
        rebuying = any(
            order.kind == "rebuy" and order.date == day.today and order.status == "SENT"
            for order in deneb.orders
        )
        share *= 1.0 if rebuying else STAGE_SHARE[int(deneb.brake.get("stage", 0))]
    return vol, share


def _deneb_today(day: Day, deneb: DenebState) -> tuple[dict[str, float], float]:
    """[쓰임] (보유, 전략 현금) — 오늘 이미 접수된 주문(아직 체결 전)은 산 것 · 판 것으로 본다.
    [이유] 같은 날 두 번 계산해도(재시도 · 채우기) 이미 낸 주문만큼 또 사지 않게."""
    qty = dict(deneb.qty)
    cash = deneb.cash_usd
    for order in deneb.orders:
        if order.status == "SENT" and order.date == day.today:
            sign = 1 if order.side == "BUY" else -1
            qty[order.symbol] = qty.get(order.symbol, 0.0) + sign * order.qty
            if order.kind not in BRAKE_KINDS:  # 브레이크 · 재매수 돈은 보관금이다
                cash -= sign * order.qty * order.limit * (1 + day.fee)
    return qty, cash


def _deneb_plan(
    day: Day, deneb: DenebState, events: list[tuple[str, str, float]] | None = None
) -> tuple[dict[str, int], float | None, float, list[str]]:
    """[쓰임] 이번 달 납입 · 배당을 장부에 넣고 → (사고팔 주수, σ20, TQQQ 몫, 메모).
    ``deneb`` 을 고친다.
    [이유] 납입 금액은 텔레그램 답이 있으면 그것, 없으면 설정의 기본 월 납입."""
    vol, share = _deneb_share(day, deneb)
    events = _dividend_events() if events is None else events
    notes = credit_dividends(deneb, events, day.today, _days_between)
    krw = deneb.amounts.get(day.today[:7], _deneb_monthly())
    usd = deposit(deneb, day.today, krw, day.fx)
    if usd:
        source = "텔레그램 답" if day.today[:7] in deneb.amounts else "기본"
        notes.append(f"이번 달 납입 {krw:,.0f}원({source}) = ${usd:,.2f} (환율 {day.fx:,.1f})")
    elif krw <= 0:
        notes.append("이번 달 납입 0원(텔레그램 답) — 이월 현금 · 배당만으로")
    last = {symbol: day.prices[symbol] for symbol in DENEB_SYMBOLS}
    qty, cash = _deneb_today(day, deneb)
    plan = plan_monthly_rebalance(qty, cash, last, share, day.fee, _deneb_gold())
    return plan, vol, share, notes


def _deneb_fill(
    day: Day, trader: TossTrader, deneb: DenebState, *, enabled: bool, halted: str | None
) -> list[str]:
    """[쓰임] 다음 날 채우기 (21:00 실행만) — 남은 전략 현금으로 1 주씩 장 마감 지정가 매수.
    [이유] 이번 달 계획이 끝난 뒤 · 계획 날이 아닐 때 · 지난 주문이 다 정리됐을 때 · 하루 한 번만
           (현금이 확정되지 않았으면 사지 않는다).
           알림은 ❌ · ⚠ 만, 달러 부족 거절은 ↻ (내일 다시)."""
    if not DENEB_FILL or not enabled or halted or due(deneb, day.today):
        return []
    if any(o.date == day.today and (not o.kind or o.kind == "fill") for o in deneb.orders):
        return []  # 계획 날(그달 첫 주문) · 오늘 이미 채움
    if any(o.status == "SENT" and o.date < day.today for o in deneb.orders):
        return []  # 지난 주문이 아직 정리 전 — 현금이 확정되지 않음
    _, share = _deneb_share(day, deneb)
    qty, cash = _deneb_today(day, deneb)
    last = {symbol: day.prices[symbol] for symbol in DENEB_SYMBOLS}
    plan = plan_next_day_fill(
        qty,
        cash,
        last,
        share,
        day.fee,
        {LEVERAGED_ETF: MAX_TQQQ_QTY, **DENEB_MAX_QTY},
        _deneb_gold(),
    )
    lines: list[str] = []
    for symbol, count in sorted(plan.items()):
        limit = limit_price(day.prices[symbol], "BUY")
        order, line = _deneb_send(day, trader, deneb, "BUY", count, limit, "fill", symbol)
        if order.status == "REJECTED" and order.code in DENEB_RETRY:
            order.booked = True
            line = f"↻ Deneb {symbol} 채우기 매수 거절 {order.code} — 내일 다시"
        lines.append(line.replace("🛑", "🛒"))
        _save_deneb(deneb)
    return lines


def _usdt_krw() -> float | None:
    """[쓰임] 업비트 USDT 원화 값(공개 시세, 키 없음). 실패하면 None — 원화만 알린다."""
    try:
        response = httpx.get("https://api.upbit.com/v1/ticker?markets=KRW-USDT", timeout=5.0)
        if response.status_code != 200:
            return None
        return float(response.json()[0]["trade_price"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None


def _find_typed_proposal(
    proposals: list[Proposal], incoming: Any, target: str | None
) -> Proposal | None:
    """[쓰임] 글 답이 어느 제안에 대한 것인가 — 주수 입력을 기다리는 제안 · 그 제안 글에 단 답장 ·
    (금액 답을 받을 달이 없고 '네 · 아니요' 같은 답이면) 가장 최근 열린 제안. 없으면 None.
    [이유] 금액 답('30만')과 제안 답('2주만')이 같은 대화방으로 오니, 금액을 받을 달이 있으면
           애매한 글은 금액으로 본다."""
    open_proposals = [p for p in proposals if p.status in ("open", "accepted")]
    for proposal in open_proposals:
        if proposal.awaiting_qty:
            return proposal
    for proposal in open_proposals:
        if incoming.reply_to and proposal.message_id == incoming.reply_to:
            return proposal
    if target is None and parse_reply(incoming.text) is not None and open_proposals:
        return open_proposals[-1]
    return None


def _deneb_poll(today: str, telegram: bool, *, reply: bool = False) -> None:
    """[쓰임] 텔레그램 답 읽기 — 이번(또는 물어본 다음) 달 Deneb 금액 · 제안 버튼 · 제안 글 답.
    등록된 대화방의 글만 읽는다.
    [이유] '현황' 같은 명령은 텔레그램 봇(scripts/telegram_bot.py) 몫이라 남겨 둔다.
           답은 세 곳 합계(시안 A, 사용자 2026-10-15) — Deneb 몫만 그달 Deneb 매수 금액이 된다.

    ``reply`` 면 이번 달(매수 당일) 금액이 정해질 때 오늘 밤 계획을 바로 답장한다 (21:00 전)."""
    from auto_trading.notify.channels import NotifyError
    from auto_trading.notify.inbox import InboxQueue, TelegramInbox, is_command, parse_krw

    credentials = load_telegram_credentials(REPO / ".env")
    if not DENEB_ENABLED or not telegram or credentials is None:
        return
    deneb = _load_deneb()
    try:
        # '현황' 같은 명령은 봇(scripts/telegram_bot.py) 몫으로 남기고 나머지만 가져온다
        incoming_messages = InboxQueue(
            REPO / "data" / "telegram" / "inbox.json", deneb.tg_offset
        ).take(TelegramInbox(credentials), lambda item: not is_command(item))
    except NotifyError as exc:
        print(f"텔레그램 받기 실패: {exc}")
        return
    planned = False
    bot = _bot()
    hhmm = _kst_now().strftime("%H:%M")
    proposals = _load_proposals(deneb)
    for incoming in incoming_messages:
        # 1 단계: 버튼 (제안의 [전부 진행] [직접입력] [이번엔 안 함] [취소])
        if incoming.data:
            callback = parse_callback(incoming.data)
            proposal = None
            if callback is not None:
                proposal = next((p for p in proposals if p.pid == callback[0]), None)
            if bot is not None and incoming.callback_id:
                with contextlib.suppress(NotifyError):  # 늦으면 텔레그램이 거부 — 무시
                    bot.answer_callback(incoming.callback_id, "접수했어요")
            if proposal is not None and callback is not None:
                text, buttons = press(proposal, callback[1], today, hhmm)
                _tg_send(
                    bot,
                    text,
                    buttons=buttons,
                    force_reply=proposal.awaiting_qty,
                    reply_to=proposal.message_id,
                )
                _after_answer(deneb, proposal)
                _refresh(bot, proposal)
            continue
        # 2 단계: 제안에 대한 글 답 ('2주만' · '네')
        target = amount_target(deneb, today)
        proposal = _find_typed_proposal(proposals, incoming, target)
        if proposal is not None:
            text, buttons = typed(proposal, incoming.text, today, hhmm)
            _tg_send(
                bot,
                text,
                buttons=buttons,
                force_reply=proposal.awaiting_qty,
                reply_to=proposal.message_id,
            )
            _after_answer(deneb, proposal)
            _refresh(bot, proposal)
            continue
        # 3 단계: 금액 답 ('30만') — 받을 달이 있고, 물어본 뒤에 온 글만
        if target is None:
            continue
        if target == deneb.ask_month and incoming.date < deneb.ask_ts:
            continue
        total = parse_krw(incoming.text)
        krw = None if total is None else deposit_split.split(total)["Deneb"]
        if total is None or krw is None:
            _notify("❓ 금액으로 읽지 못했어요. 예: 100만 · 1000000 · 0(이번 달 쉬기)", telegram)
        elif krw > _deneb_max():
            _notify(
                f"❓ Deneb 몫 {krw:,.0f}원이 상한({_deneb_max():,.0f}원)보다 커요."
                " 다시 보내 주세요",
                telegram,
            )
        else:
            deneb.amounts[target] = krw
            planned = planned or target == today[:7]
            _notify(deposit_split.split_text(target, total, _usdt_krw()), telegram)
    _store_proposals(deneb, proposals)
    _save_deneb(deneb)
    # 4 단계: 오늘 금액이 정해졌으면 오늘 밤 계획을 바로 답장
    if planned and reply and due(deneb, today):
        try:
            _notify(_deneb_preview(_kst_now()), telegram)
        except (AutoTradingError, TossApiError) as exc:
            _notify(f"🌟 Deneb 금액은 정했지만 계획 계산 실패: {exc}", telegram)


def _deneb_ask_today(today: str, telegram: bool) -> bool:
    """[쓰임] 매수 당일(이번 달 아직 안 산 평일) 아침에 금액을 묻는다. 달마다 한 번. 물었으면 True.
    [이유] 설정 '매수 전날 금액 묻기' 가 꺼졌거나 이번 달 금액을 미리 정해 두었으면 묻지 않는다."""
    if not DENEB_ENABLED or not _setting("deneb.ask_on"):
        return False  # 묻기 꺼짐(설정) — 기본 월 납입으로 산다
    deneb = _load_deneb()
    month = today[:7]
    already = month in (deneb.ask_month, *deneb.amounts)
    if not due(deneb, today) or deposited(deneb, month) or already:
        return False  # 이미 물었거나 이번 달 금액을 미리 정해 두었다
    deneb.ask_month = today[:7]
    deneb.ask_ts = int(time.time())
    _save_deneb(deneb)
    stage = int(_load_brake(deneb).stage) if DENEB_BRAKE else 0
    extra = brake_ask_line(stage, bool(_deneb_gold()))
    _notify(deposit_split.ask_text(today, _deneb_monthly(), extra), telegram)
    return True


def _deneb_preview(now: datetime, *, title: str = "") -> str:
    """[쓰임] 오늘 밤 Deneb 계획 · 보유 · 배당 · 브레이크 · 양도세 글
    (장부는 복사본으로만, 주문 없음).
    [이유] 밤사이 체결된 주문을 복사본에 먼저 반영해, 보유 · 현금이 그 순간 계좌와 맞게
           (사용자 2026-10-20 '항시 모든 현황 알림은 동기화'). 장부 파일은 그대로."""
    states = _load_plan_states()
    qs = _live_qs()
    trader = _trader()
    try:
        # 1 단계: 복사본 장부에 밤사이 체결 반영 · 이번 달 계획 계산
        day = Day(trader, now, dry=True)
        before = copy.deepcopy(_load_deneb())
        try:
            _deneb_resolve(day, before)
        except (AutoTradingError, TossApiError) as exc:
            print(f"Deneb 미리 보기: 주문 상태 조회 실패 — 장부 기준 ({exc})")
            before = _load_deneb()
        deneb = copy.deepcopy(before)
        events = _dividend_events()
        plan, vol, share, _ = _deneb_plan(day, deneb, events)
        tax = _tax_summary(trader, before)
        states, qs = copy.deepcopy(states), copy.deepcopy(qs)
        if qs is not None:
            day.qs_prepare(qs, _utc_now())
        needs = _needs(day, states, qs)
    finally:
        trader.close()
    # 2 단계: 글 만들기
    krw = before.amounts.get(day.today[:7], _deneb_monthly())
    source = "텔레그램 답" if day.today[:7] in before.amounts else "기본"
    if deposited(before, day.today[:7]):
        krw, source = 0.0, "이미 넣음"
    credited = [row for row in deneb.dividends if row not in before.dividends]
    after_qty = {}
    for symbol in DENEB_SYMBOLS:
        after_qty[symbol] = deneb.qty.get(symbol, 0.0) + max(plan.get(symbol, 0), 0)
    prices = {symbol: day.prices[symbol] for symbol in DENEB_SYMBOLS}
    sections = [
        holdings_lines(before, prices),
        dividend_lines(before, events, credited, after_qty),
    ]
    brake_section = brake_lines(_load_brake(before))
    if brake_section:
        sections.append(brake_section[2:])
    if tax:
        sections.append(_tax_section(tax))
    text = plan_message(
        today=day.today,
        krw=krw,
        source=source,
        fx_rate=day.fx,
        vol=vol,
        share=share,
        plan=plan,
        prices=prices,
        fee=day.fee,
        usd_now=day.usd,
        need_all=sum(need["qty"] * need["limit"] * (1 + day.fee) for need in needs),
        sections=sections,
        gold=_deneb_gold(),
        binance=deposit_split.vega_line(krw) if krw > 0 else "",
    )
    return f"{title}\n{text}" if title else text


def _deneb_needs(day: Day) -> list[dict[str, Any]]:
    """[쓰임] 오늘 밤 Deneb 이 살 것 [{symbol, qty, limit}] (환전 알림용). 장부는 복사본으로만."""
    if not DENEB_ENABLED:
        return []
    deneb = copy.deepcopy(_load_deneb())
    if not due(deneb, day.today):
        return []
    plan, _, _, _ = _deneb_plan(day, deneb)
    needs = []
    for symbol, count in plan.items():
        if count > 0:
            needs.append(
                {"symbol": symbol, "qty": count, "limit": limit_price(day.prices[symbol], "BUY")}
            )
    return needs


def _deneb_run(
    day: Day,
    trader: TossTrader,
    *,
    enabled: bool,
    halted: str | None,
    brake: bool = True,
    telegram: bool = False,
) -> list[str]:
    """[쓰임] Deneb 하루치 → 알림 줄. 이번 달 주문을 다 접수하면 그 달은 끝,
    돈 · 시간 문제로 거절되면 다음(23:40 · 03:30 · 다음 날) 다시.
    [이유] ``brake`` (21:00 실행만): 먼저 T 브레이크 · 제안 실행을 본다 —
           매도 · 재매수는 알리지 않고 보고 파일에만. 그달 계획이 끝났으면 다음 날 채우기만 한다."""
    deneb = _load_deneb()
    # 1 단계: 지난 주문 정리
    notes, quiet = _deneb_resolve(day, deneb)
    alerts: list[str] = []
    # 2 단계: 브레이크 · 제안 실행 (21:00 만)
    if brake and DENEB_BRAKE:
        alerts, report = _deneb_brake(day, trader, deneb, enabled=enabled, halted=halted)
        more_alerts, more = _deneb_actions(
            day, trader, deneb, enabled=enabled, halted=halted, telegram=telegram
        )
        alerts += more_alerts
        _save_deneb(deneb)
        _write_report(
            "DENEB_BRAKE",
            day.today,
            {"lines": [*quiet, *report, *more], "brake": deneb.brake, "proposals": deneb.proposals},
        )
    # 3 단계: 그달 계획이 끝났으면 다음 날 채우기만
    if not due(deneb, day.today):
        filled = []
        if brake:
            filled = _deneb_fill(day, trader, deneb, enabled=enabled, halted=halted)
        _save_deneb(deneb)
        return [*(f"🌟 Deneb · {line}" for line in [*notes, *filled]), *alerts]
    # 4 단계: 이번 달 계획 (납입 · 배당 반영 → 사고팔 주수)
    plan, vol, share, more = _deneb_plan(day, deneb)
    notes += more
    if not any(str(row[0]) == day.today[:7] for row in deneb.months):
        deneb.months.append([day.today[:7], vol, share, _deneb_gold()])
    vol_text = f"{vol:.1%}" if vol is not None else "모름"
    ratio = ratio_text(share, _deneb_gold())
    head = f"🌟 Deneb · {day.today[:7]} {ratio} (QQQ 20일 변동성 {vol_text})"
    if not enabled or halted:
        _save_deneb(deneb)
        why = "TOSS_TRADING_ENABLED 꺼짐" if not enabled else f"정지: {halted}"
        return [head, *notes, f"⏸ Deneb 주문 안 함 — {why} (다음 날 다시)", *alerts]
    # 5 단계: 주문 (파는 것 먼저 — 판 돈으로 살 수 있게). 보내기 전에 장부에 저장한다
    retry = False
    lines = []
    for symbol, change in sorted(plan.items(), key=lambda item: item[1]):
        side = "BUY" if change > 0 else "SELL"
        qty = abs(change)
        limit = limit_price(day.prices[symbol], side)
        client_id = _client_id(f"M3{symbol}", day.today, len(deneb.orders) + 1)
        order = DenebOrder(day.today, symbol, side, qty, limit, client_id)
        deneb.orders.append(order)
        _save_deneb(deneb)
        word = "매수" if side == "BUY" else "매도"
        try:
            result = trader.order(
                symbol, side, qty, Decimal(f"{limit:.2f}"), client_order_id=client_id
            )
        except OrderStateUnknownError as exc:
            lines.append(f"⚠ Deneb {symbol} {word} 전송 결과 모름 — 다시 보내지 않는다: {exc}")
            continue
        except TradingDisabledError as exc:
            order.status = "REJECTED"
            lines.append(f"❌ Deneb {symbol} {word} 안 보냄: {exc}")
            continue
        if result.accepted:
            order.status = "SENT"
            order.order_id = result.code
            lines.append(f"🛒 Deneb {symbol} {qty}주 장 마감 지정가 {word} 접수 — {limit:.2f} USD")
        else:
            order.status = "REJECTED"
            order.code = result.code
            if result.code in DENEB_RETRY:
                retry = True
            lines.append(f"❌ Deneb {symbol} {word} 거절 {result.code}: {result.message}")
        _save_deneb(deneb)
    # 6 단계: 이번 달을 끝낼지 (다시 해 볼 거절이 있으면 열어 둔다)
    if not plan:
        lines.append("살 것 없음 (돈이 1 주 값보다 적음) — 다음 달로 이월")
    if retry:
        lines.append(
            "↻ 달러 부족 · 장 시간 문제 — 오늘 23:40 · 03:30 에 다시, 안 되면 다음 거래일 밤"
        )
    else:
        deneb.done_month = day.today[:7]
    lines.append(
        f"전략 현금 ${deneb.cash_usd:,.2f} (체결 뒤 정산)"
        f" · 보유 TQQQ {deneb.qty.get(LEVERAGED_ETF, 0):g}"
        f" · SCHD {deneb.qty.get(DIVIDEND_ETF, 0):g} · IAUM {deneb.qty.get(GOLD_ETF, 0):g}"
    )
    _save_deneb(deneb)
    return [head, *notes, *lines, *alerts]


# ------------------------------------------------------------ Deneb 제안 (공제 실현 · 배당 단계)


def _bot() -> TelegramNotifier | None:
    """[쓰임] 버튼 · 고정 · 수정이 되는 텔레그램 창구 (키가 없으면 None)."""
    credentials = load_telegram_credentials(REPO / ".env")
    return TelegramNotifier(credentials) if credentials is not None else None


def _load_proposals(deneb: DenebState) -> list[Proposal]:
    """[쓰임] 장부의 제안 목록 → :class:`Proposal` 목록."""
    return [Proposal.from_dict(row) for row in deneb.proposals]


def _store_proposals(deneb: DenebState, proposals: list[Proposal]) -> None:
    """[쓰임] 제안 목록을 장부 칸으로 되돌려 쓴다 (파일 저장은 부르는 쪽이 ``_save_deneb``)."""
    deneb.proposals = [proposal.to_dict() for proposal in proposals]


def _tg_send(
    bot: TelegramNotifier | None,
    text: str,
    *,
    buttons: list[list[dict[str, str]]] | None = None,
    force_reply: bool = False,
    reply_to: int = 0,
) -> int:
    """[쓰임] 버튼 · 입력칸 · 답장 알림 → 보낸 메시지 번호.

    텔레그램이 없거나 실패하면 출력만 하고 0."""
    print(text)
    if bot is None:
        return 0
    try:
        return bot.send_message(text, buttons=buttons, force_reply=force_reply, reply_to=reply_to)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")
        return 0


def _refresh(bot: TelegramNotifier | None, proposal: Proposal) -> None:
    """[쓰임] 제안 메시지 맨 아래 상태 줄을 바꾼다 (새 알림 없이 고쳐 쓰기).
    끝난 제안은 (예전에 고정했던 것이면) 고정을 푼다.
    [이유] 2026-10-11 부터 새 제안은 고정하지 않는다 — 공지 자리는 사용법 안내(pin-help)가 쓴다."""
    if bot is None or not proposal.message_id:
        return
    try:
        bot.edit(
            proposal.message_id,
            f"{proposal.plan.get('text', '')}\n\n{status_line(proposal)}",
            buttons=proposal_buttons(proposal) if proposal.status == "open" else None,
        )
        if proposal.status in ("declined", "expired", "done", "canceled"):
            bot.unpin(proposal.message_id)
    except NotifyError as exc:
        print(f"고정 공지 고치기 실패: {exc}")


def _after_answer(deneb: DenebState, proposal: Proposal) -> None:
    """[쓰임] 배당 단계 답 → 장부의 TQQQ 상한(``lev_cap``) · 지난 단계 수(``div_steps``).
    [이유] '2주만' 이면 상한을 그만큼만 낮춘다. 취소하면 답 전으로 되돌린다.
           '안 함' · 마감이면 상한은 그대로, 단계만 넘긴다 (같은 단계를 또 묻지 않게)."""
    if proposal.kind != "div_step":
        return
    plan = proposal.plan
    step = int(plan.get("step", deneb.div_steps))
    if "prev_cap" not in plan:
        plan["prev_cap"], plan["prev_steps"] = deneb.lev_cap, deneb.div_steps
    previous_cap = float(plan["prev_cap"])
    previous_steps = int(plan["prev_steps"])
    if proposal.status == "accepted":
        cap = float(plan["cap"])
        new_cap = float(plan["new_cap"])
        deneb.lev_cap = cap - (cap - new_cap) * (proposal.qty / max(proposal.max_qty, 1))
        deneb.div_steps = max(previous_steps, step + 1)
    elif proposal.status == "open":  # 취소 → 원래대로
        deneb.lev_cap = previous_cap
        deneb.div_steps = previous_steps
    elif proposal.status in ("declined", "expired"):
        deneb.lev_cap = previous_cap
        deneb.div_steps = max(previous_steps, step + 1)


def _tax_summary(reader: TossReader, deneb: DenebState) -> dict[str, Any] | None:
    """[쓰임] 올해 양도소득 추정 (토스 체결 · 한국은행 ECOS 환율).
    [이유] 키가 없거나 실패하면 None — 알림은 양도세 줄 없이 그대로 간다
           (계산 실패로 매수를 막지 않음)."""
    key = read_env_file(REPO / ".env").get("ECOS_API_KEY", "")
    if not key:
        return None
    from scripts.us_tax import compute

    deneb_order_ids = {order.order_id for order in deneb.orders if order.order_id}
    try:
        return compute(reader, key, deneb_order_ids, _kst_now().strftime("%Y"))
    except (AutoTradingError, TossApiError, RuntimeError, ValueError, KeyError) as exc:
        print(f"양도세 계산 실패: {exc}")
        return None


def _tax_section(result: dict[str, Any]) -> list[str]:
    """[쓰임] 양도세 계산 결과 → Deneb 알림에 붙일 양도세 구역."""
    summary = result["summary"]
    return tax_lines(
        gain_krw=float(summary["gain_krw"]),
        deneb_krw=float(result["by_tag"].get("M3", 0.0)),
        tax_krw=float(summary["tax_krw"]),
        room_krw=float(summary["tax_free_room_krw"]),
        unreal_krw=float(result["unrealized_total"]),
    )


def _new_harvest(
    deneb: DenebState, today: str, prices: dict[str, float], fx_rate: float, tax: dict[str, Any]
) -> tuple[Proposal, str]:
    """[쓰임] 12 월 공제 실현 제안 (보낼 글과 함께). 실현할 이익이 없으면 끝난(마감) 기록만 남긴다.
    [이유] 끝난 기록이라도 남겨야 그해 12 월에 같은 확인을 매일 다시 하지 않는다."""
    year = int(today[:4])
    room = float(tax["summary"]["tax_free_room_krw"])
    basis = cost_basis(deneb)
    held = {symbol: deneb.qty.get(symbol, 0.0) for symbol in DENEB_SYMBOLS}
    pick = harvest_pick(held, basis, prices, fx_rate, room)
    deadline = harvest_deadline(year)
    if pick is None:
        text = (
            f"🧾 {year}년 해외주식 공제 — 올해는 실현할 이익이 없어요\n{RULE}\n"
            f"세금 없이 더 실현할 수 있는 이익 {room:,.0f}원 · Deneb 보유에 이익 난 종목이 없음"
        )
        proposal = Proposal(
            f"h{year}", "harvest", today, deadline, LEVERAGED_ETF, 0, {"text": text}, "expired"
        )
        return proposal, text
    symbol, qty, gain = pick
    text = harvest_message(year=year, room_krw=room, picks=[pick], deadline=deadline)
    proposal = Proposal(
        f"h{year}", "harvest", today, deadline, symbol, qty, {"text": text, "gain_krw": gain}
    )
    return proposal, text


def _new_div_step(
    deneb: DenebState,
    today: str,
    prices: dict[str, float],
    fx_rate: float,
    events: list[tuple[str, str, float]],
) -> tuple[Proposal | None, str | None]:
    """[쓰임] 배당 단계에 닿았으면 (제안, 글). 팔 TQQQ 가 없으면 상한만 낮추고 알림 글만.
    [이유] 금 섞기 켜짐이면 SCHD 배당이 절반이라, 배당 대신 Deneb 계좌 크기
           (:data:`ACCOUNT_STEPS_KRW`)로 단계를 잰다 (사용자 2026-10-19 '1번 진행')."""
    gold = _deneb_gold()
    monthly = monthly_dividend_krw(deneb.dividends, today, fx_rate)
    held_usd = sum(deneb.qty.get(symbol, 0.0) * prices[symbol] for symbol in DENEB_SYMBOLS)
    total = deneb.cash_usd + held_usd
    account = total * fx_rate if gold else None
    # 1 단계: 다음 단계에 닿았나
    if account is not None:
        step_index = step_due_account(deneb.div_steps, account)
    else:
        step_index = step_due(deneb.div_steps, monthly)
    if step_index is None:
        return None, None
    # 2 단계: 무엇을 얼마나 팔고 살지
    cap = deneb.lev_cap
    new_cap = max(cap - STEP_CUT, 0.0)
    buy_symbol = _step_buy_symbol(deneb, prices, new_cap) if gold else DIVIDEND_ETF
    sell, buy = shift_plan(
        deneb.qty.get(LEVERAGED_ETF, 0.0),
        prices[LEVERAGED_ETF],
        prices[buy_symbol],
        total,
        new_cap,
    )
    steps = ACCOUNT_STEPS_KRW if account is not None else DIV_STEPS_KRW
    step_krw = steps[step_index]
    if sell < 1:
        deneb.div_steps = step_index + 1
        deneb.lev_cap = new_cap
        if account is not None:
            what = f"계좌 단계 도달 ({step_krw / 100_000_000:,.1f}억 원)"
        else:
            what = f"배당 단계 도달 (월 {step_krw / 10_000:,.0f}만 원)"
        return None, (
            f"💰 Deneb · {what}\n{RULE}\n"
            f"TQQQ 비중이 이미 낮아 팔 것이 없어요. 앞으로 TQQQ 목표를 {new_cap:.0%}로 낮춰 둘게요."
        )
    # 3 단계: 제안 글 · 기록
    since = (datetime.fromisoformat(today) - timedelta(days=365)).date().isoformat()
    dividend_per_share_ttm = sum(
        amount for ex_date, symbol, amount in events if symbol == DIVIDEND_ETF and ex_date >= since
    )
    text = div_step_message(
        monthly_krw=monthly,
        step_krw=step_krw,
        cap=cap,
        new_cap=new_cap,
        sell=sell,
        lev_px=prices[LEVERAGED_ETF],
        buy=buy,
        fx=fx_rate,
        added_krw=added_monthly_krw(buy, dividend_per_share_ttm, fx_rate),
        gain_krw=None,
        room_krw=None,
        account_krw=account,
        buy_symbol=buy_symbol,
    )
    deadline = (datetime.fromisoformat(today) + timedelta(days=DIV_STEP_DAYS)).date().isoformat()
    next_step_krw = steps[step_index + 1] if step_index + 1 < len(steps) else None
    plan = {
        "text": text,
        "cap": cap,
        "new_cap": new_cap,
        "step": step_index,
        "next_step_krw": next_step_krw,
        "lev_px": prices[LEVERAGED_ETF],
        "div_px": prices[DIVIDEND_ETF],
    }
    proposal = Proposal(
        f"d{step_index}-{today}", "div_step", today, deadline, LEVERAGED_ETF, sell, plan
    )
    return proposal, text


def _step_buy_symbol(deneb: DenebState, prices: Mapping[str, float], cap: float) -> str:
    """[쓰임] 금 섞기 켜짐: 단계 매도 돈으로 살 종목 — SCHD · IAUM 중 새 목표보다 더 모자란 쪽.
    [이유] 한 종목만 산다 — 나머지 차이는 매달 맞추기 · 다음 날 채우기가
           새 돈으로 메운다(팔지 않고)."""
    symbols = (LEVERAGED_ETF, DIVIDEND_ETF, GOLD_ETF)
    total = deneb.cash_usd + sum(deneb.qty.get(symbol, 0.0) * prices[symbol] for symbol in symbols)
    target = targets(total, cap, GOLD_MIX)
    shortfall = {}
    for symbol in (DIVIDEND_ETF, GOLD_ETF):
        shortfall[symbol] = target[symbol] - deneb.qty.get(symbol, 0.0) * prices[symbol]
    return GOLD_ETF if shortfall[GOLD_ETF] > shortfall[DIVIDEND_ETF] else DIVIDEND_ETF


def _deneb_morning(now: datetime, telegram: bool) -> None:
    """[쓰임] 아침 확인 (환전 알림 실행에서) — 제안 마감 · 다시 묻기 · 12 월 공제 공지 ·
    배당 단계 질문.
    [이유] 질문은 사람이 답할 수 있는 아침에 보내고, 주문은 21:00 실행이 낸다."""
    if not DENEB_ENABLED:
        return
    today = now.strftime("%Y-%m-%d")
    deneb = _load_deneb()
    proposals = _load_proposals(deneb)
    bot = _bot() if telegram else None
    changed = False
    # 1 단계: 열린 제안 — 마감 · 다시 묻기
    for proposal in proposals:
        if expire(proposal, today):
            _after_answer(deneb, proposal)
            _refresh(bot, proposal)
            changed = True
        elif remind_due(proposal, today):
            proposal.reminded = today
            head = (proposal.plan.get("text", "") or "Deneb 제안").splitlines()[0]
            _tg_send(
                bot,
                f"⏰ 아직 답을 기다리고 있어요 — {proposal.deadline} 밤까지\n{head}",
                buttons=proposal_buttons(proposal),
                reply_to=proposal.message_id,
            )
            changed = True
    # 2 단계: 새 제안이 필요한가 (12 월 공제 · 배당 단계)
    year = now.year
    want_harvest = (
        bool(_setting("deneb.harvest_on"))
        and now.month == 12
        and not any(proposal.pid == f"h{year}" for proposal in proposals)
        and today <= harvest_deadline(year)
    )
    div_step_open = any(
        proposal.kind == "div_step"
        and proposal.status in ("open", "accepted")
        and proposal.phase != "done"
        for proposal in proposals
    )
    want_div = deneb.div_steps < len(DIV_STEPS_KRW) and not div_step_open
    if want_harvest or want_div:
        trader = _trader()
        try:
            prices = {
                symbol: float(price) for symbol, price in trader.prices(list(DENEB_SYMBOLS)).items()
            }
            fx_rate = float(trader.usd_krw())
            tax = _tax_summary(trader, deneb) if want_harvest else None
            if want_harvest and tax is not None:
                harvest, text = _new_harvest(deneb, today, prices, fx_rate, tax)
                if harvest.status == "open":
                    # 공지로 고정하지 않는다 — 메시지 아래 버튼으로만 답한다 (사용자 2026-10-11)
                    harvest.message_id = _tg_send(bot, text, buttons=proposal_buttons(harvest))
                else:
                    _tg_send(bot, text)
                proposals.append(harvest)
                changed = True
            if want_div:
                step_proposal, step_text = _new_div_step(
                    deneb, today, prices, fx_rate, _dividend_events()
                )
                if step_proposal is not None and step_text is not None:
                    step_proposal.message_id = _tg_send(
                        bot, step_text, buttons=proposal_buttons(step_proposal)
                    )
                    proposals.append(step_proposal)
                    changed = True
                elif step_text is not None:
                    _tg_send(bot, step_text)
                    changed = True
        finally:
            trader.close()
    if changed:
        _store_proposals(deneb, proposals)
        _save_deneb(deneb)


def _deneb_actions(
    day: Day,
    trader: TossTrader,
    deneb: DenebState,
    *,
    enabled: bool,
    halted: str | None,
    telegram: bool,
) -> tuple[list[str], list[str]]:
    """[쓰임] 승인된 제안 실행 (21:00) → (알림 줄, 보고 줄).
    단계: 팔기(selling) → 판 것 체결 뒤 다음 밤 사기(buying) → 산 것 체결 확인(done).
    공제 실현은 같은 종목 · 같은 수량을 되사고, 배당 단계는 판 돈으로 SCHD(금 섞기면 더 모자란 쪽).
    [이유] 매달 매수일에는 하지 않는다 (같은 밤 돈 계산이 겹치지 않게) — 다음 밤으로 미룬다."""
    alerts: list[str] = []
    report: list[str] = []
    proposals = _load_proposals(deneb)
    if not any(proposal.status == "accepted" for proposal in proposals):
        return alerts, report
    if not enabled or halted:
        return alerts, ["Deneb 제안 주문 안 함 — 거래 꺼짐 · 정지"]
    bot = _bot() if telegram else None
    orders_by_client_id = {order.client_id: order for order in deneb.orders}
    for proposal in proposals:
        if proposal.status != "accepted":
            continue
        if proposal.phase == "":
            # 1 단계: 팔기
            if due(deneb, day.today):
                report.append(f"{proposal.pid}: 오늘은 매달 매수일 — 내일 밤 진행")
                continue
            if proposal.kind == "harvest" and day.today > proposal.deadline:
                proposal.status = "expired"
                _refresh(bot, proposal)
                alerts.append(
                    f"⚠ Deneb 공제 실현 {proposal.deadline} 마감을 넘겨 진행하지 않았어요"
                )
                continue
            sell_qty = min(proposal.qty, math.floor(deneb.qty.get(proposal.symbol, 0.0)))
            if sell_qty < 1:
                proposal.status = "expired"
                _refresh(bot, proposal)
                continue
            sell_order, line = _deneb_send(
                day,
                trader,
                deneb,
                "SELL",
                sell_qty,
                limit_price(day.prices[proposal.symbol], "SELL"),
                f"{proposal.kind}_sell",
                proposal.symbol,
            )
            if sell_order.status == "REJECTED":
                alerts.append(line)
                continue
            proposal.phase = "selling"
            proposal.plan["sell_cid"] = sell_order.client_id
            report.append(line)
            _refresh(bot, proposal)
        elif proposal.phase == "selling":
            # 2 단계: 판 것이 체결됐으면 사기
            sell_order = orders_by_client_id.get(str(proposal.plan.get("sell_cid", "")))
            if sell_order is None or sell_order.status in ("SENT", "UNKNOWN"):
                continue
            if sell_order.filled_qty <= 0:
                proposal.phase = ""
                report.append(f"{proposal.pid}: 매도 미체결 — 오늘 밤 다시")
                continue
            if proposal.kind == "harvest":
                buy_symbol = proposal.symbol
                buy_qty = math.floor(sell_order.filled_qty)
            else:
                if _deneb_gold():
                    buy_symbol = _step_buy_symbol(deneb, day.prices, deneb.lev_cap)
                else:
                    buy_symbol = DIVIDEND_ETF
                net_usd = min(sell_order.filled_usd - sell_order.commission, deneb.cash_usd)
                buy_cost = limit_price(day.prices[buy_symbol], "BUY") * (1 + day.fee)
                buy_qty = math.floor(net_usd / buy_cost)
            if buy_qty < 1:
                proposal.status, proposal.phase = "done", "done"
                _refresh(bot, proposal)
                continue
            buy_order, line = _deneb_send(
                day,
                trader,
                deneb,
                "BUY",
                buy_qty,
                limit_price(day.prices[buy_symbol], "BUY"),
                f"{proposal.kind}_buy",
                buy_symbol,
            )
            if buy_order.status == "REJECTED":
                alerts.append(line)
                continue
            proposal.phase = "buying"
            proposal.plan["buy_cid"] = buy_order.client_id
            report.append(line)
            _refresh(bot, proposal)
        elif proposal.phase == "buying":
            # 3 단계: 산 것이 체결됐으면 끝
            buy_order = orders_by_client_id.get(str(proposal.plan.get("buy_cid", "")))
            if buy_order is None or buy_order.status in ("SENT", "UNKNOWN"):
                continue
            if buy_order.filled_qty > 0:
                proposal.status, proposal.phase = "done", "done"
                report.append(
                    f"{proposal.pid}: 끝 — {buy_order.symbol} {buy_order.filled_qty:g}주 되삼"
                )
                _refresh(bot, proposal)
            else:
                proposal.phase = "selling"  # 판 것은 체결됐으니 다음 실행에서 다시 산다
    _store_proposals(deneb, proposals)
    return alerts, report


# ---------------------------------------------------------------- 신호


def qs_signal(reader: TossReader, now_utc: datetime) -> tuple[bool, str, dict[str, float]]:
    """[쓰임] QQQ 추세(T 규칙) → (켜짐?, 기준 종가 날짜, 20 · 60 · 120 일 수익률).
    [이유] 끝난 미국 거래일 종가까지만 쓴다 — 진행 중인 날의 봉(뉴욕 16:15 전)은 뺀다.
           종가가 일주일 넘게 오래됐으면 판단하지 않는다(오류)."""
    bars = reader.daily_closes("QQQ", 200)
    ny_now = now_utc.astimezone(NY)
    ny_today = ny_now.strftime("%Y-%m-%d")
    finished_today = ny_today if (ny_now.hour, ny_now.minute) >= (16, 15) else None
    closed = [(day, price) for day, price in bars if day < ny_today or day == finished_today]
    if len(closed) < 121:
        raise AutoTradingError(f"QQQ 일봉이 모자란다 ({len(closed)} < 121)")
    as_of = closed[-1][0]
    if (ny_now.date() - datetime.fromisoformat(as_of).date()).days > 6:
        raise AutoTradingError(f"QQQ 마지막 종가가 오래됐다 ({as_of}) — 판단하지 않는다")
    closes = [float(price) for _, price in closed]
    return trend_on(closes, len(closes) - 1), as_of, trend_parts(closes)


# ---------------------------------------------------------------- 하루 판단 (공통)


class Day:
    """[쓰임] 하루치 시세 · 잔고 · 환율 · 수수료를 한 번에 모아 판단에 쓴다.
    ``dry`` 면 상태를 저장하지 않는다.
    [이유] 같은 실행 안에서 종목마다 토스를 따로 부르지 않고, 모두 같은 순간의 값으로 계산하려고."""

    def __init__(
        self, reader: TossReader, now: datetime, *, dry: bool, at: datetime | None = None
    ) -> None:
        self.reader, self.now, self.dry = reader, now, dry
        self.today = now.strftime("%Y-%m-%d")
        self.at = at if at is not None else now - timedelta(hours=9)
        """실제 지금 시각(UTC). ``now`` 는 업무 날짜용(자정 넘은 재시도는 전날)이라 따로 둔다."""
        self.notes: list[str] = []
        self.holdings: dict[str, TossPosition] = reader.holdings()
        symbols = [plan.symbol for plan in _plans()] + [TQQQ, DIVIDEND_ETF, GOLD_ETF]
        self.prices = {symbol: float(price) for symbol, price in reader.prices(symbols).items()}
        self.fx = float(reader.usd_krw())
        fee = reader.us_commission()
        self.fee = float(fee) if fee is not None else TOSS_FEE
        self.usd = float(reader.buying_power("USD"))

    def qty_avg(self, symbol: str) -> tuple[float, float]:
        """[쓰임] 토스 실제 보유 (수량, 평균가). 없으면 (0, 0)."""
        position = self.holdings.get(symbol)
        if position is None:
            return 0.0, 0.0
        return float(position.qty), float(position.avg_price)

    def resolve(self, orders: list[Order]) -> dict[str, Any]:
        """[쓰임] 적립 · QS 의 지난날 주문을 토스 주문 상세로 정리.
        → {sold_usd: 체결된 매도 금액(수수료 뺌)}.
        [이유] 결과를 모르는 주문은 '끝남' 으로 두고 잔고 변화로만 확인한다 (다시 보내지 않음)."""
        result: dict[str, Any] = {"sold_usd": None}
        for order in orders:
            if order.status not in OPEN_ORDER or order.date >= self.today:
                continue
            if order.status == "UNKNOWN" or not order.code:
                order.status = "EXPIRED"  # 접수 여부 모름 — 잔고 변화로만 확인한다
                continue
            status = self.reader.order_status(order.code)
            if status.status in FILLED:
                order.status = "FILLED"
                if order.message.startswith("SELL") and status.filled_amount is not None:
                    commission = status.commission or Decimal(0)
                    result["sold_usd"] = float(status.filled_amount - commission)
            elif status.status in DEAD or status.status not in ("PENDING", "PENDING_CANCEL"):
                order.status = "EXPIRED"
                self.notes.append(
                    f"{order.date} 주문이 체결되지 않고 끝남 ({status.status}) — 다시 판단"
                )
        return result

    def plan_advice(self, plan: Plan, state: State, qs: QsLiveState | None) -> Advice:
        """[쓰임] 적립 종목 하나의 오늘 판단 (장부를 실제 보유와 맞춘 뒤, 몇 주 살 만큼 모였나)."""
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
        """[쓰임] QS 전략 장부를 지난 주문 · 실제 TQQQ 보유와 맞추고 추세를 갱신.

        → {on, as_of, parts}."""
        sold_usd = self.resolve(qs.orders)["sold_usd"]
        qty, avg = self.qty_avg(TQQQ)
        self.notes += reconcile_tqqq(
            qs, self.today, qty, avg, self.prices[TQQQ], self.fx, fee=self.fee, sold_usd=sold_usd
        )
        trend_now, as_of, parts = qs_signal(self.reader, now_utc)
        self.notes += update_signal(qs, self.today, as_of, trend_now)
        return {"on": trend_now, "as_of": as_of, "parts": parts}


def _load_plan_states() -> dict[str, State]:
    """[쓰임] 적립 종목 장부 전부 {기호: 장부} — 장부가 없는(시작 전) 종목은 뺀다."""
    states = {}
    for plan in _plans():
        state = _load_plan_state(plan)
        if state is not None:
            states[plan.symbol] = state
    return states


def _needs(day: Day, states: dict[str, State], qs: QsLiveState | None) -> list[dict[str, Any]]:
    """[쓰임] 오늘 밤 낼 매수 주문 [{symbol, qty, limit, usd}] (적립 · QS · Deneb).
    장부는 복사본으로.
    [이유] 환전 알림 · 미리 보기가 '오늘 밤 필요한 달러' 를 밤 실행과 같은 계산으로 내려고."""
    needs: list[dict[str, Any]] = []
    accrued_qndx = 0.0
    for plan in _plans():
        state = states.get(plan.symbol)
        if state is None:
            continue
        if plan.symbol in STOPPED:
            continue
        advice = day.plan_advice(plan, state, qs)
        if plan.symbol == "QNDX":
            accrued_qndx = advice.accrued
        if advice.status == "BUY":
            needs.append({"symbol": plan.symbol, "qty": advice.qty, "limit": advice.limit_usd})
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
            needs.append({"symbol": TQQQ, "qty": order.qty, "limit": order.limit_usd})
    needs += _deneb_needs(day)
    for need in needs:
        need["usd"] = round(need["qty"] * need["limit"] * (1 + day.fee), 2)
    return needs


# ---------------------------------------------------------------- 명령


def start() -> int:
    """[쓰임] 처음 한 번 — 적립 · QS 장부를 만든다. 지금 보유는 적립 밖(기준 수량)으로 둔다."""
    already = any(_path(f"{plan.symbol}.json").exists() for plan in _plans())
    if already or _path(QS_FILE).exists():
        print(f"이미 시작했다: {DATA}")
        return 2
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    reader = _trader()
    try:
        holdings = reader.holdings()
        trend_now, as_of, parts = qs_signal(reader, _utc_now())
    finally:
        reader.close()
    lines = [f"토스 적립 + QS 시작 {today}"]
    for plan in _plans():
        position = holdings.get(plan.symbol)
        qty = float(position.qty) if position else 0.0
        average = float(position.avg_price if position else 0)
        _save_plan_state(State(plan.symbol, today, qty, qty, qty * average))
        lines.append(f"  {plan.symbol}: 기존 {qty:g}주는 적립 밖(기준 수량)")
    position = holdings.get(TQQQ)
    qty = float(position.qty) if position else 0.0
    _save_qs(QsLiveState(today, qty, qty, qty * float(position.avg_price if position else 0)))
    lines.append(
        f"  QS: 기존 TQQQ {qty:g}주는 건드리지 않음 · 추세 {'켜짐' if trend_now else '꺼짐'}"
        f" ({as_of} 종가,"
        f" 20일 {parts['r20']:+.1%} · 60일 {parts['r60']:+.1%} · 120일 {parts['r120']:+.1%})"
    )
    print("\n".join(lines))
    return 0


def fx(telegram: bool = True) -> int:
    """[쓰임] 환전 알림 실행 (평일 09 · 12 · 15 시) — 오늘 밤 매수에 쓸 달러가 모자라면
    환전 금액을 알린다.
    같은 실행에서 Deneb 금액 묻기 · 텔레그램 답 읽기 · 아침 제안 확인도 한다.
    [이유] 토스에는 환전 API 가 없어 사람이 앱에서 바꿔야 해서, 우대 시간(15:30) 전에 알린다.
           주말 · 모자라지 않으면 조용히."""
    now = _kst_now()
    if now.weekday() >= 5:
        print("주말 — 오늘 밤 미국 장 없음")
        return 0
    states = _load_plan_states()
    qs = _live_qs()
    if not states:
        print("아직 시작 전 (start)")
        return 2
    # 1 단계: Deneb 아침 일 (금액 묻기 · 답 읽기 · 제안)
    _deneb_ask_today(now.strftime("%Y-%m-%d"), telegram)
    _deneb_poll(now.strftime("%Y-%m-%d"), telegram, reply=True)
    try:
        _deneb_morning(now, telegram)
    except (AutoTradingError, TossApiError) as exc:
        print(f"Deneb 제안 확인 실패: {exc}")
    # 2 단계: 오늘 밤 필요한 달러 계산 (장부는 복사본)
    reader = _trader()
    try:
        day = Day(reader, now, dry=True)
        states, qs = copy.deepcopy(states), copy.deepcopy(qs)
        if qs is not None:
            day.qs_prepare(qs, _utc_now())
        needs = _needs(day, states, qs)
    finally:
        reader.close()
    total = sum(need["usd"] for need in needs)
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
    # 3 단계: 모자라면 알림
    if not needs or short <= 0:
        print(f"환전 필요 없음 — 오늘 밤 매수 {len(needs)}건, 달러 {day.usd:,.2f}")
        return 0
    if not _setting("alerts.fx"):
        print(f"환전 알림 꺼짐 (설정) — 모자란 달러 약 ${short:,.2f}")
        return 0
    usd_to_buy = math.ceil(short)
    items = [f"• {need['symbol']} {need['qty']}주  약 ${need['usd']:,.2f}" for need in needs]
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
    """[쓰임] 적립 · QS 주문 하나 (장 마감 지정가) → (상태, 텔레그램 줄).
    [이유] **보내기 전에 주문을 먼저 저장한다** (``save``) — 결과를 모르면 다시 보내지 않는다."""
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
        order.status = "REJECTED"
        order.message = f"{side} {exc}"
        return "REJECTED", f"⏸ {symbol} {word} 안 보냄: {exc}"
    if result.accepted:
        order.status = "SENT"
        order.code = result.code
        return "SENT", (
            f"🛒 {symbol} {qty}주 장 마감 지정가 {word} 접수 — {limit:.2f} USD"
            f" {'이하' if side == 'BUY' else '이상'}이면 오늘 미국 종가에 체결"
        )
    order.status = "REJECTED"
    order.code = result.code
    order.message = f"{side} {result.message}"
    hint = ""
    if result.code == "insufficient-buying-power":
        hint = " — 달러가 모자란다. 환전 뒤 내일 다시"
    return "REJECTED", f"❌ {symbol} {word} 거절 {result.code}: {result.message}{hint}"


def _refresh_universe() -> None:
    """[쓰임] 종목 목록이 하루(20 시간) 넘게 묵었으면 새로 받는다 (실패해도 적립은 그대로)."""
    path = DATA / "universe.json"
    try:
        if path.exists() and time.time() - path.stat().st_mtime < 20 * 3600:
            return
        universe()
    except Exception as exc:  # 부가 기능 — 어떤 실패도 적립을 막지 않는다
        print(f"종목 목록 새로 받기 실패: {exc}")


def run(telegram: bool = True) -> int:
    """[쓰임] 밤 21:00 실행 — 자동 적립 · (QS) · Deneb 을 한 번에 판단하고 주문한다.
    [이유] 한 실행에서 같은 순간의 시세 · 달러로 모두 계산해, 달러를 서로 겹쳐 쓰지 않게.
           평소 적립은 조용히 — ❌ · ⚠ · 📈 가 있을 때만 텔레그램 (사용자 2026-10-09)."""
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    _refresh_universe()
    states = _load_plan_states()
    qs = _live_qs()
    if not states:
        print("아직 시작 전 (start)")
        return 2
    lines: list[str] = []
    trader = _trader()
    try:
        day = Day(trader, now, dry=False)
        qs_context: dict[str, Any] | None = None
        if qs is not None:
            qs_context = day.qs_prepare(qs, _utc_now())
            _save_qs(qs)
        enabled = trader.credentials.trading_enabled
        halted = halted_for(HALT, "acc")  # 적립 · QS: 전체 정지 또는 토스 적립 정지
        halted_deneb = halted_for(HALT, "deneb")  # Deneb: 전체 정지 또는 Deneb 정지
        accrued_qndx = 0.0
        # 1 단계: 자동 적립 — 종목마다 지난 주문 정리 → 모인 돈으로 살 수 있는 만큼(정수 주)
        for plan in _plans():
            state = states.get(plan.symbol)
            if state is None:  # 새로 추가된 종목 — 오늘 시작, 지금 보유는 적립 밖
                held_qty, average = day.qty_avg(plan.symbol)
                state = State(plan.symbol, today, held_qty, held_qty, held_qty * average)
                states[plan.symbol] = state
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
            advice = day.plan_advice(plan, state, qs)
            if plan.symbol == "QNDX":
                accrued_qndx = advice.accrued
            line = None
            ok, reason = should_order(
                state,
                advice,
                trading_enabled=enabled,
                halted=halted,
                kst_hhmm=now.strftime("%H:%M"),
            )
            if advice.status == "BUY" and ok:
                _, line = _send(
                    trader,
                    state.orders,
                    plan.symbol,
                    "BUY",
                    advice.qty,
                    advice.limit_usd,
                    today,
                    lambda saved=state: _save_plan_state(saved),
                )
            elif advice.status == "BUY":
                line = f"⏸ 자동 매수 안 함 — {reason}"
            _save_plan_state(state)
            text_one = message(advice, [], order_line=line)
            lines += ([""] if lines else []) + [text_one]
            todays_order = next(
                (asdict(order) for order in reversed(state.orders) if order.date == today), None
            )
            _write_report(
                plan.symbol,
                today,
                {
                    "symbol": plan.symbol,
                    "status": advice.status,
                    "advice": advice.to_dict(),
                    "auto_buy": enabled,
                    "order": todays_order,
                    "holding_qty": day.qty_avg(plan.symbol)[0],
                    "message": text_one,
                },
            )
        # 2 단계: QS 전략 (실거래 꺼짐이면 건너뜀)
        if qs is not None and qs_context is not None:
            qs_order = plan_tqqq_order(
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
            qs_line = None
            if qs_order.side != "NONE":
                status, qs_line = _send(
                    trader,
                    qs.orders,
                    TQQQ,
                    qs_order.side,
                    qs_order.qty,
                    qs_order.limit_usd,
                    today,
                    lambda: _save_qs(qs),
                )
                if status == "REJECTED" and qs_order.side == "BUY":
                    code = qs.orders[-1].code
                    if code not in RETRYABLE_QS_BUY:
                        qs.want = "NONE"
                        qs_line += " — 다시 보내지 않는다. 적립금은 QNDX 로 이어간다"
            elif qs_order.reason != "할 일 없음":
                qs_line = f"⏸ QS TQQQ 주문 안 함 — {qs_order.reason}"
            _save_qs(qs)
            todays_qs_order = next(
                (asdict(order) for order in reversed(qs.orders) if order.date == today), None
            )
            _write_report(
                QS_KEY,
                today,
                {
                    "trend_on": qs_context["on"],
                    "as_of": qs_context["as_of"],
                    "parts": qs_context["parts"],
                    "strategy_tqqq_qty": qs.strategy_qty,
                    "net_krw": qs.net_krw(),
                    "want": qs.want,
                    "qndx_paused": qndx_paused(qs),
                    "order_line": qs_line,
                    "order": todays_qs_order,
                },
            )
            parts = qs_context["parts"]
            head = [
                f"📊 QS  QQQ 추세 {'🟢 켜짐' if qs_context['on'] else '🔴 꺼짐'}",
                sub(
                    f"{qs_context['as_of']} 종가 · 20일 {parts['r20']:+.1%}"
                    f" · 60일 {parts['r60']:+.1%} · 120일 {parts['r120']:+.1%}"
                ),
                f"{SUB}TQQQ {qs.strategy_qty:g}주 · 순지출 {qs.net_krw():,.0f}원 · 할 일 {qs.want}",
            ]
            lines = head + ([SUB + qs_line] if qs_line else []) + ["", RULE] + lines
        # 3 단계: Deneb (텔레그램 답 먼저 읽고 → 브레이크 · 제안 · 매달 계획 · 채우기)
        if DENEB_ENABLED:
            _deneb_poll(today, telegram)
            deneb_lines = _deneb_run(
                day, trader, enabled=enabled, halted=halted_deneb, telegram=telegram
            )
            if deneb_lines:
                lines = [*deneb_lines, *(["", RULE] if lines else []), *lines]
    except (AutoTradingError, TossApiError) as exc:
        text = f"⚠ 토스 적립 실패\n{RULE}\n{exc}"
        if isinstance(exc, TossApiError) and exc.status == 403:
            text += "\n허용 IP 가 바뀌었을 수 있다 — WTS 설정 > Open API > 허용 IP 관리"
        _notify(text, telegram)
        print(text)
        return 1
    finally:
        trader.close()
    # 4 단계: 알림 글 · 보고 파일
    if abs(day.fee - TOSS_FEE) > 1e-9:  # 0.1% 의 '적용 종료일'이 하루씩 밀리는 값이라 바뀌면 알린다
        day.notes.insert(0, f"⚠ 토스 미국 수수료가 {day.fee:.3%} 로 바뀌었다 (기존 {TOSS_FEE:.3%})")
    notes = [f"📌 {note}" for note in day.notes]
    text = "\n".join([f"💰 토스 적립 · {today}", RULE, *notes, *([""] if notes else []), *lines])
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = {
        "at_ms": int(time.time() * 1000),
        "message": text,
        "usd": day.usd,
        "fee": day.fee,
        "fx": day.fx,
    }
    (REPORTS / f"{today}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", "utf-8"
    )
    if not QUIET_ROUTINE or any(mark in text for mark in ALERT_MARKS):
        _notify(text, telegram)
    print(text)
    return 0


def deneb_only(telegram: bool = True) -> int:
    """[쓰임] Deneb 재시도만 (예약 23:40 · 03:30 KST). 이번 달이 끝났으면 아무것도 하지 않는다.
    [이유] 낮에 환전을 못 해 21:00 매수가 달러 부족으로 거절됐을 때, 그날 밤 안에 다시 해 본다.
           21:00 실행 전(그날 주문이 하나도 없음)이면 하지 않는다 — 계획은 21:00 실행 몫."""
    now = real_now = _kst_now()
    if now.hour < 12:  # 자정 넘은 재시도(03:30)는 전날 밤(미국 같은 거래일)의 일이다
        now -= timedelta(days=1)
    today = now.strftime("%Y-%m-%d")
    if not DENEB_ENABLED or now.weekday() >= 5 or not due(_load_deneb(), today):
        print("Deneb: 할 일 없음")
        return 0
    if not any(order.date == today for order in _load_deneb().orders):
        print("Deneb: 오늘 21:00 실행 전 — 재시도만 한다")
        return 0
    _deneb_poll(today, telegram)
    trader = _trader()
    try:
        day = Day(trader, now, dry=False, at=real_now - timedelta(hours=9))
        lines = _deneb_run(
            day,
            trader,
            enabled=trader.credentials.trading_enabled,
            halted=halted_for(HALT, "deneb"),
            brake=False,
        )
    except (AutoTradingError, TossApiError) as exc:
        text = f"⚠ Deneb 재시도 실패\n{RULE}\n{exc}"
        _notify(text, telegram)
        print(text)
        return 1
    finally:
        trader.close()
    text = "\n".join([f"🌟 Deneb 재시도 · {today} {now:%H:%M}", RULE, *lines])
    _notify(text, telegram)
    print(text)
    return 0


def preview() -> int:
    """[쓰임] 오늘 밤 할 일 미리 보기 (적립 · QS) — 주문 · 저장 없음.

    시작 전이면 지금 보유를 기준으로 가정한다."""
    now = _kst_now()
    states = _load_plan_states()
    qs = _live_qs()
    reader = _trader()
    try:
        day = Day(reader, now, dry=True)
        if not states:  # 시작 전이면 지금 보유를 기준으로 가정
            today = now.strftime("%Y-%m-%d")
            for plan in _plans():
                held_qty, average = day.qty_avg(plan.symbol)
                states[plan.symbol] = State(
                    plan.symbol, today, held_qty, held_qty, held_qty * average
                )
            held_qty, average = day.qty_avg(TQQQ)
            qs = QsLiveState(today, held_qty, held_qty, held_qty * average)
        states, qs = copy.deepcopy(states), copy.deepcopy(qs)
        qs_context = day.qs_prepare(qs, _utc_now()) if qs is not None else None
        needs = _needs(day, states, qs)
        advices = []
        for plan in _plans():
            if plan.symbol in states and plan.symbol not in STOPPED:
                advices.append(day.plan_advice(plan, states[plan.symbol], qs))
    finally:
        reader.close()
    print("[미리 보기 — 주문·저장 없음]")
    print(f"수수료 {day.fee:.3%} · 환율 {day.fx:,.1f} · 달러 {day.usd:,.2f}")
    if qs_context is not None and qs is not None:
        print(
            f"QS: 추세 {'켜짐' if qs_context['on'] else '꺼짐'} ({qs_context['as_of']} 종가)"
            f" · 할 일 {qs.want} · QNDX 멈춤 {qndx_paused(qs)}"
        )
    print("\n".join(day.notes))
    for advice in advices:
        print(message(advice, []))
    print("오늘 밤 매수:", needs or "없음")
    return 0


def status() -> int:
    """[쓰임] 장부 요약 출력 (적립 종목 · QS · Deneb · 브레이크) — 조회 없이 파일만 읽는다."""
    for plan in _plans():
        state = _load_plan_state(plan)
        if state is None:
            print(f"{plan.symbol}: 시작 전")
            continue
        spent = sum(buy.cost for buy in state.buys)
        stop = f" · 적립 중단 ({STOPPED[plan.symbol]})" if plan.symbol in STOPPED else ""
        print(
            f"{plan.symbol}{stop}: 시작 {state.start_date}"
            f" · 적립 매수 {sum(buy.qty for buy in state.buys):g}주 "
            f"· 쓴 돈 {money(spent, plan.currency)}"
        )
    qs = _load_qs()
    if qs is not None:
        print(
            ("QS (실거래 꺼짐 — Paper 만): " if not QS_LIVE else "")
            + f"QS: 추세 {qs.prev_on} · 할 일 {qs.want} · QS TQQQ {qs.strategy_qty:g}주 "
            f"· 순지출 {qs.net_krw():,.0f}원"
        )
    if DENEB_ENABLED:
        deneb = _load_deneb()
        print(
            f"Deneb(M3): 시작 {deneb.start} · 이번 달 끝 {deneb.done_month or '아직'}"
            f" · 전략 현금 ${deneb.cash_usd:,.2f} · TQQQ {deneb.qty.get(LEVERAGED_ETF, 0):g}"
            f" · SCHD {deneb.qty.get(DIVIDEND_ETF, 0):g} · IAUM {deneb.qty.get(GOLD_ETF, 0):g}"
            f" · 금 섞기 {'켜짐' if _deneb_gold() else '꺼짐'} · 월 {_deneb_monthly():,.0f}원"
        )
        brake = _load_brake(deneb)
        print(
            f"Deneb 브레이크({'켜짐' if DENEB_BRAKE else '꺼짐'}): 단계 {brake.stage}"
            f" · TQQQ 고점 ${brake.lev_peak:.2f}"
            f" ({brake.last_day or '기록 전'}) · 보관금 ${brake.reserve_usd:,.2f}"
            f" · 누적 매도 ${brake.sold_total_usd:,.2f}"
        )
    return 0


def harvest_plan(other_gains_krw: float) -> int:
    """[쓰임] 연말 양도세 공제(250 만 원) 수확 계획 — 조회만, 주문 없음 (문서 123)."""
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
    strategy_symbols = {accumulate_plan.symbol for accumulate_plan in _plans()}
    usd_holdings = []
    for symbol, position in holdings.items():
        if position.currency != "USD":
            continue
        usd_holdings.append(
            Holding(
                symbol,
                float(position.qty),
                float(position.avg_price),
                float(position.last),
                symbol in strategy_symbols,
            )
        )
    qs = _load_qs()
    qs_gain = 0.0
    if qs:
        qs_gain = qs_realized_krw([_asdict(trade) for trade in qs.trades], now.strftime("%Y"))
    result = plan(
        usd_holdings,
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
    for holding in usd_holdings:
        gain = (holding.last_usd - holding.avg_usd) * holding.qty * fx_rate
        mark = "자동 적립" if holding.strategy else "직접 보유"
        print(f"  {holding.symbol} {holding.qty:g}주 · 평가 이익 약 {gain:,.0f}원 · {mark}")
    if not result.lines:
        print("팔 것 없음 — 이익 난 종목이 없거나 공제가 남지 않았다")
        return 0
    for line in result.lines:
        who = "" if line.strategy else " (직접 보유 — 사용자 결정)"
        print(
            f"→ {line.symbol} {line.qty}주 팔고 다음 날 다시 사기"
            f" · 실현 약 {line.gain_krw:,.0f}원{who}"
        )
    print(
        f"수확 약 {result.harvested_krw:,.0f}원 → 미래 세금 약 {result.future_tax_saved_krw:,.0f}원"
        f" 줄어듦 · 수수료 약 ${result.cost_usd:,.2f} · 하루는 시장 밖"
    )
    return 0


def deneb_chat(telegram: bool = True) -> int:
    """[쓰임] 텔레그램 대화 예약 (평일 09:00 ~ 20:59, 5 분마다) — 답을 읽어 계획을 답장한다.
    [이유] 금액을 물었거나 열린 제안이 있을 때만 읽는다 —
           할 일이 없으면 텔레그램을 부르지 않는다."""
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    deneb = _load_deneb()
    asking = due(deneb, today) and deneb.ask_month == today[:7]
    live = any(
        proposal.status == "open"
        or proposal.awaiting_qty
        or (proposal.status == "accepted" and proposal.answered == today)
        for proposal in _load_proposals(deneb)
    )
    in_hours = 9 <= now.hour < 21
    if not DENEB_ENABLED or now.weekday() >= 5 or not in_hours or not (asking or live):
        print("Deneb 대화: 할 일 없음")
        return 0
    _deneb_poll(today, telegram, reply=asking)
    return 0


def deneb_test(telegram: bool = True) -> int:
    """[쓰임] 시험: 묻기 · 입금 나누기 · 계획 답장을 지금 상태로 보낸다.

    장부 · 주문은 바꾸지 않는다."""
    now = _kst_now()
    today = now.strftime("%Y-%m-%d")
    mark = "🧪 테스트 — 실제 묻기 · 주문 아님"
    ask = f"{mark}\n{deposit_split.ask_text(today, _deneb_monthly())}"
    _notify(ask, telegram)
    print(ask)
    total = deposit_split.default_total(_deneb_monthly())
    split = f"{mark} (기본 합계라고 가정)\n" + deposit_split.split_text(
        today[:7], total, _usdt_krw()
    )
    _notify(split, telegram)
    print(split)
    plan = _deneb_preview(now, title=f"{mark} (Deneb 몫 기본 금액이라고 가정)")
    _notify(plan, telegram)
    print(plan)
    return 0


def main(argv: list[str]) -> int:
    """[쓰임] 명령 이름 → 함수 (표 하나, 디자인 패턴 Command + 목록표).

    예약 작업이 이 이름으로 부르니 이름은 바꾸지 않는다."""
    telegram = "--no-telegram" not in argv
    args = [arg for arg in argv if arg != "--no-telegram"]
    if args[:1] == ["harvest-plan"]:
        other = float(args[2]) if len(args) == 3 and args[1] == "--other-gains" else 0.0
        return harvest_plan(other)
    commands: dict[str, Callable[[], int]] = {
        "start": start,
        "run": lambda: run(telegram),
        "fx": lambda: fx(telegram),
        "deneb": lambda: deneb_only(telegram),
        "deneb-chat": lambda: deneb_chat(telegram),
        "deneb-test": lambda: deneb_test(telegram),
        # 2026-10-15 이름 변경 전 명령 — 이미 등록된 예약(AutoTrading_Toss_M3_*)이 부른다
        "m3": lambda: deneb_only(telegram),
        "m3-chat": lambda: deneb_chat(telegram),
        "m3-test": lambda: deneb_test(telegram),
        "preview": preview,
        "status": status,
        "universe": lambda: universe(telegram),
    }
    if len(args) != 1 or args[0] not in commands:
        raise SystemExit(
            "usage: python -m scripts.toss_accumulate"
            " start|run|fx|deneb|deneb-chat|deneb-test|preview|status|universe"
            " [--no-telegram]"
            " | harvest-plan [--other-gains 원]"
        )
    return commands[args[0]]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["LIMIT_BUFFER", "Day", "qs_signal"]
