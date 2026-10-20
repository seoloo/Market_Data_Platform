"""개별주 비중 추천 · 장 시작 예약 매수 · −3% 손절 감시 (토스 실계좌).

사용 예::

    python -m scripts.size_guard advise NVDA       # 추천만 (주문 없음)
    python -m scripts.size_guard reserve NVDA      # 추천대로 예약 → 정규장 시작 직후 지정가 매수
    python -m scripts.size_guard tick              # 2 분마다: 예약 매수 · 손절 감시 (텔레그램 알림)
    python -m scripts.size_guard status
    python -m scripts.size_guard review [YYYY-MM]  # 💹 월간 복기 (기본 이번 달)
    python -m scripts.size_guard attach 삼성전자    # 앱에서 산 종목에 손절만 (토스 평균 단가 −3%)
    python -m scripts.size_guard release <id>       # 손절 해제 (장부에서 빼고 주식은 그대로)
    python -m scripts.size_guard cancel <id>        # 아직 안 산 예약 취소
    python -m scripts.size_guard pead-offers        # PEAD 알림 기록 (주문 없음)

규칙: :mod:`auto_trading.ops.position_size` · :mod:`~auto_trading.ops.size_order` ·
:mod:`~auto_trading.ops.stop_guard`. 주문은 ``TOSS_TRADING_ENABLED=true`` 일 때만, 예약 매수는
``data/HALT`` (전체 정지) 면 하지 않는다 — 손절 매도는 정지 중에도 한다(보호 매도).
텔레그램 💹 개별주(scripts/telegram_bot.handle_stock)가 이 함수들을 부르고, 봇이 2 분마다 tick 한다.

PEAD (2026-10-17 사용자 — 연구 240 · 241): PEAD Paper(21:40)가 새 실적 갭 +10% 이상 신호를 찾으면
:func:`offer_pead` 가 [🛒 매수] 버튼 카드를 보낸다. 누르면 :func:`reserve_pead` 가
반응일 다음 정규장 시작 직후 약 $100 어치를 예약한다(손절 없음, 20 거래일째 장 마감 무렵 자동 매도).
그 장이 지나면 버튼은 무효.

언제 실행: 텔레그램 봇이 2 분마다 ``tick`` · 버튼(추천 · 예약 · 손절 등록 · PEAD) 누를 때.
읽고 쓰는 것: 토스 조회 · 주문 → 장부 ``data/size_guard/state.json`` · 추천 기록 ``journal.jsonl`` ·
              PEAD 알림 ``pead_offers.json`` · 발표 판정 기록 ``data/event_calendar/checks/`` ·
              알림 기록 ``reports/size_guard/outbox.log``.
              정지 파일 ``data/HALT`` · ``HALT_stock`` 은 읽기만.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from auto_trading.config.secrets import (
    load_telegram_credentials,
    load_toss_credentials,
    read_env_file,
)
from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.toss import TossReader, TossTokenCache, TossTrader
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops import kr_earnings, user_settings
from auto_trading.ops.deneb_live import SYMBOLS as DENEB_SYMBOLS
from auto_trading.ops.entry_events import EntryEvents, company_events, macro_events
from auto_trading.ops.event_store import earnings_day
from auto_trading.ops.kill_switch import halted_for
from auto_trading.ops.kr_accumulate import find, read_universe
from auto_trading.ops.position_size import (
    CAP,
    Advice,
    Inputs,
    advice_text,
    is_kr,
    recommend,
)
from auto_trading.ops.size_order import PEAD, Ticket, merge_into, new_ticket, refresh, step_buy
from auto_trading.ops.stop_guard import (
    check_hold_exit,
    check_stop,
    register_app,
    status_lines,
    step_sell,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "size_guard"
STATE = DATA / "state.json"
JOURNAL = DATA / "journal.jsonl"
"""추천 기록 (월간 복기용, ops/stock_journal)."""
REVIEW = DATA / "review.json"
LOCK = DATA / "lock"
OUTBOX = REPO / "reports" / "size_guard" / "outbox.log"
TOKEN = REPO / "data" / "toss_token.json"
HALT = REPO / "data" / "HALT"
TOSS_DATA = REPO / "data" / "toss"
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
INDEX = {"US": ("SPY", "S&P500(SPY)"), "KR": ("069500", "KOSPI200(KODEX 200)")}
LOCK_STALE_MS = 10 * 60_000
TAG = "💹 [개별주 · 손절]"
PEAD_OFFERS = DATA / "pead_offers.json"
PEAD_USD = 100.0
"""PEAD 한 번 매수 금액(사용자 2026-10-17 '$100')."""
PEAD_MAX_PX = 150.0
"""1 주가 $100 넘으면 $150 까지만 1 주 — 더 비싸면 알림만."""
PEAD_MIN_GAP = 0.10
"""240: 갭 10% 이상만 효과가 뚜렷했다(+1.48%, 5 ~ 10% 는 +0.16%)."""


# ------------------------------------------------------------------ 장부


def load_tickets() -> list[Ticket]:
    """[쓰임] 💹 장부 읽기 (없거나 깨졌으면 빈 목록)."""
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Ticket.from_dict(item) for item in data.get("tickets") or []]


def save_tickets(tickets: list[Ticket]) -> None:
    """[쓰임] 💹 장부 쓰기 — 임시 파일에 쓰고 바꿔 끼움(쓰다 끊겨도 안 깨지게)."""
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    payload = {"tickets": [ticket.to_dict() for ticket in tickets]}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _now_ms() -> int:
    """[쓰임] 지금 시각(ms) — 시험이 바꿔 끼운다."""
    return int(time.time() * 1000)


def _tid(now_ms: int) -> str:
    """[쓰임] 장부 번호 = 한국 시각 'MMDDHHMMSS'."""
    return datetime.fromtimestamp(now_ms / 1000, KST).strftime("%m%d%H%M%S")


class _Lock:
    """bot · 손으로 실행이 겹치지 않게 (10 분 넘은 잠금은 버린다)."""

    def __enter__(self) -> _Lock:
        DATA.mkdir(parents=True, exist_ok=True)
        now = _now_ms()
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = now - int(LOCK.read_text(encoding="utf-8") or 0)
            except (OSError, ValueError):
                age = LOCK_STALE_MS
            if age < LOCK_STALE_MS:
                raise SystemExit("다른 실행이 진행 중 — 잠시 뒤 다시") from None
            LOCK.unlink(missing_ok=True)
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(now).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc: object) -> None:
        LOCK.unlink(missing_ok=True)


# ------------------------------------------------------------------ 토스


def _reader() -> TossReader:
    """[쓰임] 토스 조회 창구 (주문 없음)."""
    return TossReader(load_toss_credentials(REPO / ".env"), TossTokenCache(TOKEN))


def _trader(tickets: list[Ticket], last: dict[str, float]) -> TossTrader:
    """장부 종목만, 수량은 장부 수량까지, 지정가는 추천 · 평균 · 지금 가격 중 큰 값의 1.1 배까지."""
    act = [ticket for ticket in tickets if ticket.active]
    qty: dict[str, int] = {}
    for ticket in act:
        qty[ticket.symbol] = max(qty.get(ticket.symbol, 1), ticket.qty, int(ticket.filled))

    def cap(kr: bool) -> float:
        prices = [
            max(ticket.ref_price, ticket.avg, last.get(ticket.symbol, 0.0))
            for ticket in act
            if ticket.kr == kr
        ]
        return max(prices, default=1.0) * 1.1

    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(qty),
        max_qty=qty,
        sell_symbols=frozenset(
            ticket.symbol for ticket in act if ticket.status in ("holding", "selling")
        ),
        max_limit_usd=Decimal(str(round(cap(False), 2))),
        max_limit_krw=Decimal(str(int(cap(True)))),
    )


def session(reader: Any, kr: bool, now_ms: int) -> tuple[str, bool, bool]:
    """(정규장 이름, 지금 살 수 있음, 지금 팔 수 있음). 시작 1 분 뒤부터, 끝 5 분 · 2 분 전까지."""
    day = datetime.fromtimestamp(now_ms / 1000, KST if kr else NY).strftime("%Y-%m-%d")
    key = f"{'KR' if kr else 'US'}:{day}"
    sess = reader.checked_regular_session(day, kr=kr)
    if sess is None:
        return key, False, False
    opening, closing = (datetime.fromisoformat(stamp) for stamp in sess)
    if opening.tzinfo is None or closing.tzinfo is None or closing <= opening:
        raise ValueError("정규장 시각 미확인")
    start = opening.timestamp() * 1000 + 60_000
    end = closing.timestamp() * 1000
    return (
        key,
        start <= now_ms < end - (15 if kr else 5) * 60_000,
        start <= now_ms < end - (12 if kr else 2) * 60_000,
    )


# ------------------------------------------------------------------ 추천 재료


def _blocked() -> dict[str, str]:
    """자동 전략이 쓰는 토스 종목 → 이름."""
    out = dict.fromkeys(DENEB_SYMBOLS, "Deneb")
    out["TQQQ"] = "Deneb · QS"
    for path in TOSS_DATA.glob("*.json"):
        name = path.stem
        if name in ("DENEB", "QS_SWITCH", "universe"):
            continue
        out[name.removeprefix("KR_")] = "자동 적립"
    return out


def _deneb_usd() -> float:
    """[쓰임] Deneb 장부의 달러 현금 — 기준 돈에서 뺀다(T 브레이크 이월 등)."""
    try:
        data = json.loads((TOSS_DATA / "DENEB.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return float(data.get("cash_usd") or 0.0)


def _session_days(reader: TossReader, kr: bool, now_ms: int) -> list[date]:
    """다음 진입 세션 + 그 뒤 3세션. 주말/공휴일을 요일로 추측하지 않는다."""
    today = datetime.fromtimestamp(now_ms / 1000, KST if kr else NY).date()
    days: list[date] = []
    for offset in range(16):
        day = today + timedelta(days=offset)
        hours = reader.checked_regular_session(day.isoformat(), kr=kr)
        if hours is None:
            continue
        start, end = (datetime.fromisoformat(stamp) for stamp in hours)
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("정규장 시각 오류")
        if start.astimezone(KST if kr else NY).date() != day:
            raise ValueError("정규장 날짜 불일치")
        if now_ms < end.timestamp() * 1000 - 5 * 60_000:
            days.append(day)
        if len(days) == 4:
            return days
    raise ValueError("다음 3거래일 범위 미확인")


def event_risk(reader: TossReader, symbol: str, now_ms: int, *, force: bool = False) -> EntryEvents:
    """알림과 같은 실적 캐시·거시 원천을 사용한다. 실패는 신규 진입만 차단."""
    kr = is_kr(symbol)
    try:
        days = _session_days(reader, kr, now_ms)
        if kr:  # 국내 — DART 공시로 실적 일정 (2026-10-16, ops/kr_earnings)
            key = read_env_file(REPO / ".env").get("OPENDART_API_KEY", "")
            if not key:
                raise ValueError("OPENDART_API_KEY 없음")
            info = kr_earnings.lookup(REPO, symbol, days[0], key)
            evidence_kr = {
                "kr_earnings": {
                    "kind": info.kind,
                    "day": str(info.day) if info.day else "",
                    "last_release": str(info.last_release) if info.last_release else "",
                },
                "sessions": [str(session_day) for session_day in days],
            }
            return _record_event_check(
                symbol, now_ms, kr_earnings.kr_company_events(info, days), evidence_kr
            )
        raw = yaml.safe_load((REPO / "config" / "event_calendar.yaml").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("거시 일정 형식 오류")
        macro = macro_events(raw, days[0], now_ms)
        snapshots = {}
        cursor = days[0]
        while cursor <= days[-1]:
            snapshots[cursor] = earnings_day(REPO, cursor, now_ms, force=force)
            cursor += timedelta(days=1)
        company = company_events(symbol, days, snapshots)
        result = EntryEvents(
            macro.blocked + company.blocked,
            macro.lines + company.lines,
            days[0],
            company.earnings_date,
            company.earnings_in_days,
        )
        evidence = {
            "macro_digest": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
            "earnings": {
                str(session_day): {
                    "digest": snapshot.digest,
                    "fetched_at_ms": snapshot.fetched_at_ms,
                    "source": snapshot.source,
                    "error": snapshot.error,
                }
                for session_day, snapshot in snapshots.items()
            },
            "sessions": [str(session_day) for session_day in days],
        }
        return _record_event_check(symbol, now_ms, result, evidence)
    except (AutoTradingError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        return _record_event_check(
            symbol,
            now_ms,
            EntryEvents((f"발표·거래일 확인 실패 ({type(exc).__name__}) — 신규 매수 보류",)),
            {},
        )


def _record_event_check(
    symbol: str, now_ms: int, risk: EntryEvents, evidence: dict[str, Any]
) -> EntryEvents:
    """주문 여부와 무관하게 당시 일정 입력·판정·소스 식별자를 보존한다."""
    from dataclasses import asdict
    from uuid import uuid4

    try:
        code = hashlib.sha256()
        for source in (
            Path(__file__),
            REPO / "src/auto_trading/ops/entry_events.py",
            REPO / "src/auto_trading/ops/event_store.py",
        ):
            if source.is_file():
                code.update(source.read_bytes())
        payload = {
            "rule_version": "entry-events-v1",
            "code_digest": code.hexdigest(),
            "as_of_ms": now_ms,
            "symbol": symbol,
            "decision": asdict(risk),
            "evidence": evidence,
        }
        path = REPO / "data/event_calendar/checks" / f"{now_ms}-{uuid4().hex}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        return risk
    except OSError:
        return EntryEvents(
            (*risk.blocked, "발표 판정 기록 실패 — 신규 매수 보류"), risk.lines, risk.entry_day
        )


def resolve(query: str) -> tuple[str, str]:
    """기호 · 6 자리 코드 · 한글 이름 → (기호, 이름). 토스 종목 목록(universe.json) 기준."""
    uni = read_universe(TOSS_DATA / "universe.json")
    text = query.strip()
    for country in ("KR", "US") if (text.isdigit() or not text.isascii()) else ("US", "KR"):
        hit = find(text, uni, country)
        if len(hit) == 1:
            return hit[0].symbol, hit[0].name
        if hit:
            raise SystemExit(
                "여러 종목이 맞아요: "
                + ", ".join(f"{listed.name}({listed.symbol})" for listed in hit)
            )
    return text.upper(), text.upper()


def _closes(reader: TossReader, symbol: str, before_day: date) -> list[float]:
    """[쓰임] ``before_day`` 전날까지의 일봉 종가 200 개.
    [이유] 못 받아도 추천은 낸다 — 빈 목록이면 추세 '확인 못 함' 으로 막힌다."""
    try:
        return [
            float(close)
            for day, close in reader.daily_closes(symbol, 200, before_day=before_day.isoformat())
            if day < before_day.isoformat()
        ]
    except Exception as exc:  # 일봉 하나 못 받아도 추천은 낸다(확인 못 함으로 표시)
        print(f"{symbol} 일봉 건너뜀: {type(exc).__name__}")
        return []


def advise(query: str) -> Advice:
    """[쓰임] 종목 이름 · 코드 → 비중 추천 (조회만) + 추천 기록(월간 복기용)."""
    symbol, name = resolve(query)
    reader = _reader()
    try:
        advice = _advise(reader, symbol, name, _now_ms())
    finally:
        reader.close()
    _journal(advice)
    return advice


def _journal(advice: Advice) -> None:
    """추천 한 번을 기록 (실패해도 추천은 그대로)."""
    row = {
        "at": _now_ms(),
        "symbol": advice.symbol,
        "name": advice.name,
        "ok": advice.ok,
        "can_override": advice.can_override,
        "qty": advice.qty,
        "price": advice.price,
        "blocked": list(advice.blocked),
    }
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"추천 기록 실패: {exc}")


def load_journal() -> list[dict[str, Any]]:
    """[쓰임] 추천 기록 줄들 (깨진 줄은 건너뜀)."""
    try:
        lines = JOURNAL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def review(month: str | None = None) -> str:
    """그 달(기본 이번 달) 💹 복기 글."""
    from auto_trading.ops.stock_journal import month_of, review_text

    month = month or month_of(_now_ms())
    tickets = load_tickets()
    last: dict[str, float] = {}
    held = sorted({ticket.symbol for ticket in tickets if ticket.status in ("holding", "selling")})
    if held:
        try:
            reader = _reader()
            try:
                last = {
                    held_symbol: float(price) for held_symbol, price in reader.prices(held).items()
                }
            finally:
                reader.close()
        except Exception as exc:  # 시세 없이도 복기는 보낸다
            print(f"복기 시세 건너뜀: {type(exc).__name__}")
    return review_text(month, load_journal(), tickets, last)


def monthly_review(now_ms: int | None = None, *, send: bool = True) -> str | None:
    """매달 1 일 09:30 KST 뒤 한 번 — 지난달 복기 (추천 · 매수가 없던 달은 안 보냄)."""
    from datetime import timedelta as _td

    now_ms = now_ms or _now_ms()
    now_kst = datetime.fromtimestamp(now_ms / 1000, KST)
    if now_kst.day != 1 or (now_kst.hour, now_kst.minute) < (9, 30):
        return None
    prev = (now_kst.replace(day=1) - _td(days=1)).strftime("%Y-%m")
    try:
        done = json.loads(REVIEW.read_text(encoding="utf-8")).get("sent")
    except (OSError, ValueError):
        done = None
    if done == prev:
        return None
    from auto_trading.ops.stock_journal import month_of

    active = any(month_of(int(row["at"])) == prev for row in load_journal()) or any(
        month_of(int(ticket.created_ms)) == prev for ticket in load_tickets()
    )
    text = review(prev) if active else None
    if text and send:
        notify([text])
    DATA.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(json.dumps({"sent": prev}), encoding="utf-8")
    return text


def _advise(
    reader: TossReader, symbol: str, name: str, now_ms: int, *, force: bool = False
) -> Advice:
    """[쓰임] 토스 조회(시세 · 환율 · 현금 · 보유 · 일봉) + 발표 일정 → :func:`recommend`.
    [이유] 장 마감 전이면 오늘 미완성 일봉은 쓰지 않는다 (다음 장 추천도 어제 종가까지)."""
    kr = is_kr(symbol)
    risk = event_risk(reader, symbol, now_ms, force=force)
    day = risk.entry_day or datetime.fromtimestamp(now_ms / 1000, KST if kr else NY).date()
    # 다음 장을 추천하는 장 마감 직전에도 현재 미완성 일봉은 사용하지 않는다.
    today = datetime.fromtimestamp(now_ms / 1000, KST if kr else NY).date()
    hours = reader.checked_regular_session(today.isoformat(), kr=kr)
    cutoff = day
    if hours is not None and now_ms < datetime.fromisoformat(hours[1]).timestamp() * 1000:
        cutoff = min(day, today)
    quote = reader.prices([symbol]).get(symbol)
    if quote is None:
        raise ValueError(f"{symbol} 가격 미확인")
    fx = float(reader.usd_krw())
    holdings = reader.holdings()
    idx_sym, idx_name = INDEX["KR" if kr else "US"]
    us_stock = (
        sum(
            float(position.qty * position.last)
            for held_symbol, position in holdings.items()
            if not is_kr(held_symbol)
        )
        * fx
    )
    return recommend(
        Inputs(
            symbol=symbol,
            name=name,
            price=float(quote),
            krw_cash=float(reader.buying_power("KRW")),
            usd_cash=float(reader.buying_power("USD")),
            fx=fx,
            deneb_usd=_deneb_usd(),
            closes=_closes(reader, symbol, cutoff),
            index_name=idx_name,
            index_closes=_closes(reader, idx_sym, cutoff),
            earnings_date=risk.earnings_date,
            earnings_in_days=risk.earnings_in_days,
            held_qty=float(holdings[symbol].qty) if symbol in holdings else 0.0,
            us_stock_krw=us_stock,
            blocked_symbol=_blocked().get(symbol, ""),
            event_risk=risk,
        )
    )


# ------------------------------------------------------------------ 알림


def notify(lines: list[str]) -> None:
    """[쓰임] 알림 — 기록 파일에 남기고 텔레그램으로도 (실패해도 실행은 계속)."""
    if not lines:
        return
    text = "\n\n".join(f"{TAG}\n{line}" for line in lines)
    OutboxNotifier(OUTBOX).send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


# ------------------------------------------------------------------ 단계


def _recheck_before_buy(
    reader: TossReader, trader: TossTrader, ticket: Ticket, now: int
) -> str | None:
    """[쓰임] 매수 직전 다시 보기 → 막는 이유 (없으면 None).
    [이유] 예약한 뒤 발표 일정 · 추세 · 돈이 바뀌었을 수 있다. 낸 주문이 남아 있으면 발표 일정만,
           아니면 추천을 처음부터 다시 낸다. '그래도 매수' · PEAD 는 넘을 수 없는 이유(자동 전략
           종목 · 돈 없음 · 최대 수량)만 본다."""
    if ticket.status == "buying":
        refresh(ticket.buys, trader, now, live=True)
    pending = any(not order.closed for order in ticket.buys)
    if pending:
        risk = event_risk(reader, ticket.symbol, now, force=True)
        if not risk.ok and not (ticket.override or ticket.kind == PEAD):
            return " · ".join(risk.blocked)
        return None
    fresh = _advise(reader, ticket.symbol, ticket.name, now, force=True)
    remaining = ticket.qty - int(sum(order.filled for order in ticket.buys))
    if ticket.override or ticket.kind == PEAD:  # 넘을 수 없는 이유만
        if fresh.hard:
            return " · ".join(fresh.hard)
        if remaining > fresh.max_qty:
            return "다시 확인한 최대 수량보다 많음 — 새로 정해 주세요"
        return None
    if not fresh.ok:
        return " · ".join(fresh.blocked)
    if remaining > fresh.qty:
        return "다시 확인한 수량 상한 감소 — 새 추천 필요"
    return None


def _opening_price(reader: TossReader, ticket: Ticket, session_key: str) -> float | None:
    """[쓰임] 첫 매수의 기준 가격 — 장 밖에서 누른 예약은 그날 정규장 첫 1 분봉 시가.
    [이유] 장중에 누른 예약은 그날 시가가 아니라 추천 때 가격이 기준
           (2026-10-16 사용자 '장시간이면 그때 바로 매수가에?').
           시가를 못 받으면 None — 사지 않는다."""
    hours = reader.checked_regular_session(session_key.split(":", 1)[1], kr=ticket.kr)
    if hours is None:
        raise ValueError("정규장 시각 미확인")
    start_ms = int(datetime.fromisoformat(hours[0]).timestamp() * 1000)
    if ticket.created_ms >= start_ms:
        return ticket.ref_price
    open_quote = reader.regular_open(ticket.symbol, start_ms)
    return float(open_quote) if open_quote is not None else None


def tick(now_ms: int | None = None, *, send: bool = True) -> list[str]:
    """[쓰임] 예약 매수 · 손절 감시 한 번 (텔레그램 봇이 2 분마다).
    [이유] 할 일이 없으면 토스를 부르지 않는다. 손절(보호 매도)은 정지 중에도 하고,
           발표 일정 조회보다 먼저 한다 — 일정 조회가 실패해도 매도는 막히지 않게."""
    tickets = load_tickets()
    if not any(ticket.active for ticket in tickets):
        return []
    now = now_ms or _now_ms()
    out: list[str] = []
    reader = _reader()
    try:
        # 1 단계: 들고 있는 종목 시세 · 주문 창구 · 환율 · 정규장 시간 · 정지
        held = [ticket for ticket in tickets if ticket.status in ("holding", "selling")]
        last: dict[str, float] = {}
        if held:
            quotes = reader.prices(sorted({ticket.symbol for ticket in held}))
            last = {held_symbol: float(price) for held_symbol, price in quotes.items()}
        trader = _trader(tickets, last)
        fx = float(reader.usd_krw()) if any(not ticket.kr for ticket in held) else 1.0
        markets = {ticket.kr for ticket in tickets if ticket.active}
        sess = {kr: session(reader, kr, now) for kr in markets}
        halt = halted_for(HALT, "stock")  # 전체 정지 또는 개별주 정지(전부 정리)
        try:
            # 2 단계: 보호 매도 — 손절 · PEAD 보유 끝 → 지정가 매도
            for ticket in held:
                key, buy_live, sell_live = sess[ticket.kr]
                if ticket.status == "holding" and sell_live:
                    check_stop(ticket, last.get(ticket.symbol))
                # PEAD: 지난 정규장을 세고 20 번째 장 마감 무렵(매수 창 닫힌 뒤 · 매도 창 안) 판다
                closing = sell_live and not buy_live
                check_hold_exit(ticket, key, sell_live=sell_live, closing=closing)
                if ticket.status == "selling":
                    sellable = float(reader.sellable(ticket.symbol)) if sell_live else 0.0
                    out += step_sell(ticket, trader, now, live=sell_live, sellable=sellable, fx=fx)
            # 3 단계: 예약 매수 — 장 시간이면 다시 보고 → 지정가 매수 → (추가 매수면) 합치기
            for ticket in tickets:
                if not ticket.active or ticket.status not in ("reserved", "buying"):
                    continue
                key, buy_live, _sell_live = sess[ticket.kr]
                block = None
                opening = ticket.open_px or None
                parent = None
                if ticket.add_to:
                    parent = next((other for other in tickets if other.id == ticket.add_to), None)
                if buy_live:
                    if halt:
                        block = f"전체 정지 중 ({halt})"
                    elif ticket.add_to and (parent is None or parent.status != "holding"):
                        block = "기존 보유분이 손절 · 정리 중 — 추가 매수 취소"
                    else:
                        try:
                            block = _recheck_before_buy(reader, trader, ticket, now)
                            if ticket.status == "reserved" and not block:
                                opening = _opening_price(reader, ticket, key)
                        except (AutoTradingError, ValueError, OSError) as exc:
                            block = f"주문 직전 재확인 실패 ({type(exc).__name__})"
                msgs = step_buy(
                    ticket,
                    trader,
                    now,
                    session=key,
                    live=buy_live,
                    block=block,
                    regular_open=opening,
                )
                if ticket.status == "holding" and parent is not None and parent.status == "holding":
                    msgs = merge_into(parent, ticket, now)
                out += msgs
        finally:
            trader.close()
    finally:
        reader.close()
        save_tickets(tickets)
    if send:
        notify(out)
    return out


def reserve(query: str, qty: int | None = None) -> str:
    """추천대로(``qty`` 없음) 또는 사람이 정한 수량으로 예약.

    ``qty`` 를 주면 규칙에 걸려도 예약한다('그래도 매수', 2026-10-16 사용자 승인) — 단 자동 전략
    종목 · 돈 없음은 넘을 수 없고, 수량은 한 종목 상한 · 현금 안(``max_qty``). 손절 −3% 그대로."""
    halt = halted_for(HALT, "stock")
    if halt:
        return f"⛔ 개별주 새 매수 정지 중 ({halt}) — 텔레그램 '정지풀기' 로 풀 수 있어요"
    advice = advise(query)
    text = advice_text(advice)
    override = False
    if qty is not None:
        if not advice.can_override:
            return text
        if not 1 <= qty <= advice.max_qty:
            return (
                f"⚠️ {advice.name} 은 1 ~ {advice.max_qty}주만 살 수 있어요"
                f" (한 종목 {CAP:.0%} · 현금)"
            )
        override = not advice.ok
        advice.qty = qty
    elif not advice.ok:
        return text
    now = _now_ms()
    with _Lock():
        tickets = load_tickets()
        same = [held for held in tickets if held.active and held.symbol == advice.symbol]
        parent = same[0] if len(same) == 1 and same[0].status == "holding" else None
        if same and parent is None:
            busy = (
                "매수 중"
                if any(held.status in ("reserved", "buying") for held in same)
                else "매도 중"
            )
            return text + f"\n\n⚠️ {advice.symbol} 는 이미 {busy}이라 새로 예약하지 않았어요"
        ticket = new_ticket(advice, now, _tid(now))
        if parent is not None:  # 이미 보유 — 추가 매수, 체결되면 합쳐 평균 −3% 손절
            ticket.add_to = parent.id
        ticket.override = override
        if override:
            ticket.note = "규칙 밖 매수: " + " · ".join(advice.blocked)
        tickets.append(ticket)
        save_tickets(tickets)
        msgs = tick(now)
    head = "⏳ 예약했어요 — 정규장 시작 직후(장중이면 바로) 지정가로 삽니다"
    if parent is not None:
        head += (
            f"\n📦 추가 매수 — 체결되면 기존 {parent.filled - parent.sold:g}주와 합쳐 "
            "평균 단가 −3% 로 손절선을 다시 잡아요"
        )
    if qty is not None:
        tag = " (규칙 밖 매수 — 기록에 따로 표시)" if override else ""
        return "\n\n".join([f"⏳ {advice.name} {qty}주 예약했어요{tag}", head, *msgs])
    return "\n\n".join([text, head, *msgs])


# ------------------------------------------------------------------ PEAD


def _load_offers() -> dict[str, dict[str, Any]]:
    """[쓰임] PEAD 알림 기록 읽기 (버튼 번호 → 알림)."""
    try:
        data = json.loads(PEAD_OFFERS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_offers(offers: dict[str, dict[str, Any]]) -> None:
    """[쓰임] PEAD 알림 기록 쓰기 (임시 파일에 쓰고 바꿔 끼움)."""
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = PEAD_OFFERS.with_suffix(".tmp")
    tmp.write_text(json.dumps(offers, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PEAD_OFFERS)


def pead_qty(price: float) -> int:
    """약 $100 어치. 1 주가 $100 넘으면 $150 까지만 1 주, 더 비싸면 0 (알림만)."""
    if price <= 0:
        return 0
    qty = int(PEAD_USD // price)
    if qty >= 1:
        return qty
    return 1 if price <= PEAD_MAX_PX else 0


def pead_text(offer: dict[str, Any]) -> str:
    """[쓰임] PEAD 알림 카드 글 (갭 · 반응일 · 살 수량 · 버튼 유효 기간)."""
    qty, ref = int(offer["qty"]), float(offer["ref"])
    lines = [
        f"📈 PEAD 후보 — {offer['sym']} 실적 발표 뒤 갭 +{float(offer['gap']):.1%}"
        f" (반응일 {offer['r_day']}, 종가 ${ref:,.2f})",
        "연구 240: 갭 10% 이상은 20 거래일 동안 SPY 보다 평균 +1.5%"
        " (생존 편향 있음 · Paper 확인 중)",
    ]
    if qty:
        lines.append(
            f"누르면 다음 정규장 시작 직후 {qty}주(약 ${qty * ref:,.0f}) 지정가 매수"
            " · 손절 없이 20 거래일째 장 마감 무렵 자동 매도"
        )
    else:
        lines.append(f"1 주가 ${PEAD_MAX_PX:,.0f} 넘어 $100 로는 못 사요 — 알림만")
    lines.append("버튼은 다음 정규장이 끝나기 전까지만 유효")
    return "\n".join(lines)


def offer_pead(
    sigs: list[dict[str, Any]], now_ms: int | None = None, *, send: bool = True
) -> list[str]:
    """새 갭 10%+ 신호마다 카드 + [🛒 N주 매수] [건너뛰기]. 같은 (종목, 반응일) 은 한 번만."""
    if not user_settings.get(REPO, "stock.pead") or not user_settings.get(REPO, "stock.orders"):
        return []
    now = now_ms or _now_ms()
    offers = _load_offers()
    seen = {str(offer.get("key")) for offer in offers.values()}
    out: list[str] = []
    creds = load_telegram_credentials(REPO / ".env") if send else None
    for signal in sorted(sigs, key=lambda signal: -float(signal["gap"])):
        key = f"{signal['sym']}:{signal['r_day']}"
        if float(signal["gap"]) < PEAD_MIN_GAP or key in seen or signal.get("r_close") is None:
            continue
        ref = float(signal["r_close"])
        nonce = hashlib.sha256(f"{key}:{now}".encode()).hexdigest()[:10]
        offer: dict[str, Any] = {
            "key": key,
            "sym": str(signal["sym"]),
            "r_day": str(signal["r_day"]),
            "gap": float(signal["gap"]),
            "ref": ref,
            "qty": pead_qty(ref),
            "at": now,
            "used": False,
        }
        offers[nonce] = offer
        seen.add(key)
        text = pead_text(offer)
        out.append(text)
        rows = (
            [
                [
                    {
                        "text": f"🛒 {offer['qty']}주 매수 (약 ${offer['qty'] * ref:,.0f})",
                        "callback_data": f"stp:{nonce}:{offer['sym']}",
                    }
                ],
                [{"text": "건너뛰기", "callback_data": f"stp:{nonce}:cancel"}],
            ]
            if offer["qty"]
            else None
        )
        if send:
            OutboxNotifier(OUTBOX).send(text)
        if creds is not None:
            try:
                TelegramNotifier(creds).send_message(text, buttons=rows)
            except NotifyError as exc:
                print(f"텔레그램 실패: {exc}")
    _save_offers(offers)
    return out


def _pead_session(reader: Any, r_day: date, now_ms: int) -> str | None:
    """반응일 다음 미국 정규장 이름. 그 장이 끝나기 5 분 전이 지났으면 None."""
    day = r_day + timedelta(days=1)
    for _ in range(10):
        hours = reader.checked_regular_session(day.isoformat(), kr=False)
        if hours is not None:
            end = datetime.fromisoformat(hours[1])
            if end.tzinfo is None:
                raise ValueError("정규장 시각 오류")
            if now_ms >= end.timestamp() * 1000 - 5 * 60_000:
                return None
            return f"US:{day.isoformat()}"
        day += timedelta(days=1)
    raise ValueError("다음 정규장 미확인")


def skip_pead(nonce: str) -> str:
    """[쓰임] PEAD 알림 [건너뛰기] — 그 버튼을 다 쓴 것으로."""
    offers = _load_offers()
    offer = offers.get(nonce)
    if offer is None:
        return "지난 알림이에요"
    offer["used"] = True
    _save_offers(offers)
    return f"{offer['sym']} PEAD 건너뛰었어요"


def reserve_pead(nonce: str, now_ms: int | None = None) -> str:
    """PEAD 알림 버튼 → 반응일 다음 정규장 시작 직후 매수 예약 (손절 없음, 20 거래일 보유)."""
    halt = halted_for(HALT, "stock")
    if halt:
        return f"⛔ 개별주 새 매수 정지 중 ({halt}) — 텔레그램 '정지풀기' 로 풀 수 있어요"
    now = now_ms or _now_ms()
    offers = _load_offers()
    offer = offers.get(nonce)
    if offer is None or offer.get("used"):
        return "⌛ 지난 버튼이에요 (이미 누름 · 건너뜀)"
    sym, qty = str(offer["sym"]), int(offer["qty"])
    if qty < 1:
        return f"{sym} 는 1 주가 비싸 $100 로 못 사요"
    blocked = _blocked()
    if sym in blocked:
        return f"{sym} 는 {blocked[sym]} 종목이라 PEAD 로 사지 않아요"
    reader = _reader()
    try:
        key = _pead_session(reader, date.fromisoformat(str(offer["r_day"])), now)
    finally:
        reader.close()
    if key is None:
        offer["used"] = True
        _save_offers(offers)
        return f"⌛ {sym} 반응일 다음 정규장이 이미 끝나 사지 않았어요 (PEAD 는 그 장 시가 매수)"
    try:
        _sym, name = resolve(sym)
    except SystemExit:
        name = sym
    with _Lock():
        tickets = load_tickets()
        if any(ticket.active and ticket.symbol == sym for ticket in tickets):
            return f"⚠️ {sym} 는 이미 💹 장부에 있어 PEAD 로 따로 사지 않았어요"
        ticket = Ticket(
            _tid(now),
            sym,
            name,
            "USD",
            qty,
            float(offer["ref"]),
            now,
            session=key,
            kind=PEAD,
            note=f"PEAD 갭 +{float(offer['gap']):.1%} (반응일 {offer['r_day']})",
        )
        tickets.append(ticket)
        save_tickets(tickets)
        offer["used"] = True
        _save_offers(offers)
        msgs = tick(now)
    head = (
        f"⏳ {name} {qty}주 PEAD 예약 — {key.split(':')[1]} 정규장 시작 직후 지정가 매수,"
        " 손절 없이 20 거래일째 장 마감 무렵 매도"
    )
    return "\n\n".join([head, *msgs])


def attach(query: str, qty: float | None = None) -> str:
    """[쓰임] 앱에서 산 종목에 손절만 건다 — 토스 평균 단가 −3%.
    [이유] 자동 전략 종목(Deneb · 적립)은 같은 계좌라 섞여서 걸지 않는다."""
    symbol, name = resolve(query)
    reader = _reader()
    try:
        pos = reader.holdings().get(symbol)
    finally:
        reader.close()
    if pos is None:
        return f"{name} 를 갖고 있지 않아요"
    watch_qty = min(float(pos.qty), qty) if qty else float(pos.qty)
    if int(watch_qty) < 1:
        return "1주 이상만 손절을 걸 수 있어요"
    if symbol in _blocked():
        return f"{name} 는 {_blocked()[symbol]} 종목이라 손절을 걸지 않아요"
    now = _now_ms()
    with _Lock():
        tickets = load_tickets()
        if any(ticket.active and ticket.symbol == symbol for ticket in tickets):
            return f"{name} 는 이미 감시 중이에요"
        ticket = register_app(_tid(now), symbol, name, watch_qty, float(pos.avg_price), now)
        tickets.append(ticket)
        save_tickets(tickets)
    stop_text = f"{ticket.stop:,.0f}원" if ticket.kr else f"${ticket.stop:,.2f}"
    return (
        f"🛡 {name} {int(watch_qty)}주 손절 등록 — 토스 평균 단가 기준 {stop_text}(−3%)"
        " 이하면 자동 매도"
    )


def _set_status(tid: str, allowed: tuple[str, ...], status: str) -> str:
    """[쓰임] 장부 상태 바꾸기 (손절 해제 · 예약 취소) — 허락된 상태에서만."""
    with _Lock():
        tickets = load_tickets()
        for ticket in tickets:
            if ticket.id == tid:
                if ticket.status not in allowed:
                    return f"{ticket.name} 는 지금 {ticket.status} 라 바꿀 수 없어요"
                ticket.status, ticket.closed_ms = status, _now_ms()
                save_tickets(tickets)
                return f"{ticket.name} → {status}"
    return f"없는 번호: {tid}"


def summary_lines() -> list[str]:
    """'📊 현황' 에 붙일 💹 개별주 칸 (예약 · 보유 · 손절까지 거리). 없으면 빈 목록.

    시세는 토스 시세 조회(계좌 조회 아님). 못 받으면 가격 없이 보여 준다."""
    act = [ticket for ticket in load_tickets() if ticket.active]
    if not act:
        return []
    last: dict[str, float] = {}
    try:
        reader = _reader()
        try:
            last = {
                held_symbol: float(price)
                for held_symbol, price in reader.prices(
                    sorted({ticket.symbol for ticket in act})
                ).items()
            }
        finally:
            reader.close()
    except Exception as exc:  # 시세 못 받아도 현황은 보낸다
        print(f"개별주 시세 건너뜀: {type(exc).__name__}")
    return ["💹 개별주", *status_lines(act, last)]


def status() -> str:
    """[쓰임] 감시 중인 장부 현황 + 장부 번호 (토스 시세 조회)."""
    tickets = load_tickets()
    act = [ticket for ticket in tickets if ticket.active]
    if not act:
        return "비중 · 손절 장부: 감시 중인 종목 없음"
    reader = _reader()
    try:
        last = {
            held_symbol: float(price)
            for held_symbol, price in reader.prices(
                sorted({ticket.symbol for ticket in act})
            ).items()
        }
    finally:
        reader.close()
    ids = [f"  번호 {ticket.id} · {ticket.symbol} · {ticket.status}" for ticket in act]
    return "\n".join([*status_lines(act, last), *ids])


def main(argv: list[str]) -> int:
    """[쓰임] 명령 이름 → 함수 (사용 예는 파일 맨 위)."""
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "advise" and rest:
        print(advice_text(advise(" ".join(rest))))
    elif cmd == "reserve" and rest:
        print(reserve(" ".join(rest)))
    elif cmd == "tick":
        with _Lock():
            for line in tick():
                print(line)
    elif cmd == "status":
        print(status())
    elif cmd == "review":
        print(review(rest[0] if rest else None))
    elif cmd == "attach" and rest:
        qty = float(rest[1]) if len(rest) > 1 else None
        print(attach(rest[0], qty))
    elif cmd == "release" and rest:
        print(_set_status(rest[0], ("holding",), "released"))
    elif cmd == "cancel" and rest:
        print(_set_status(rest[0], ("reserved",), "canceled"))
    elif cmd == "pead-offers":
        print(json.dumps(_load_offers(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
