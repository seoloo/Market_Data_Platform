"""개별주 비중 추천 · 장 시작 예약 매수 · −3% 손절 감시 (토스 실계좌).

사용 예::

    python -m scripts.size_guard advise NVDA       # 추천만 (주문 없음)
    python -m scripts.size_guard reserve NVDA      # 추천대로 예약 → 정규장 시작 직후 지정가 매수
    python -m scripts.size_guard tick              # 2 분마다: 예약 매수 · 손절 감시 (텔레그램 알림)
    python -m scripts.size_guard status
    python -m scripts.size_guard attach 삼성전자    # 앱에서 산 종목에 손절만 (토스 평균 단가 −3%)
    python -m scripts.size_guard release <id>       # 손절 해제 (장부에서 빼고 주식은 그대로)
    python -m scripts.size_guard cancel <id>        # 아직 안 산 예약 취소

규칙: :mod:`auto_trading.ops.position_size` · :mod:`~auto_trading.ops.size_order` ·
:mod:`~auto_trading.ops.stop_guard`. 주문은 ``TOSS_TRADING_ENABLED=true`` 일 때만, 예약 매수는
``data/HALT`` (전체 정지) 면 하지 않는다 — 손절 매도는 정지 중에도 한다(보호 매도).
텔레그램 메뉴(📐 비중)는 나중에 이 함수들을 부른다.
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

from auto_trading.config.secrets import load_telegram_credentials, load_toss_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.toss import TossReader, TossTokenCache, TossTrader
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.deneb_live import SYMBOLS as DENEB_SYMBOLS
from auto_trading.ops.entry_events import EntryEvents, company_events, macro_events
from auto_trading.ops.event_store import earnings_day
from auto_trading.ops.kill_switch import halted_reason
from auto_trading.ops.kr_accumulate import find, read_universe
from auto_trading.ops.position_size import (
    Advice,
    Inputs,
    advice_text,
    is_kr,
    recommend,
)
from auto_trading.ops.size_order import Ticket, new_ticket, refresh, step_buy
from auto_trading.ops.stop_guard import check_stop, register_app, status_lines, step_sell

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "size_guard"
STATE = DATA / "state.json"
LOCK = DATA / "lock"
OUTBOX = REPO / "reports" / "size_guard" / "outbox.log"
TOKEN = REPO / "data" / "toss_token.json"
HALT = REPO / "data" / "HALT"
TOSS_DATA = REPO / "data" / "toss"
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
INDEX = {"US": ("SPY", "S&P500(SPY)"), "KR": ("069500", "KOSPI200(KODEX 200)")}
LOCK_STALE_MS = 10 * 60_000
TAG = "📐 [비중 · 손절]"


# ------------------------------------------------------------------ 장부


def load_tickets() -> list[Ticket]:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Ticket.from_dict(x) for x in d.get("tickets") or []]


def save_tickets(tickets: list[Ticket]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    payload = {"tickets": [t.to_dict() for t in tickets]}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _tid(now_ms: int) -> str:
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
    return TossReader(load_toss_credentials(REPO / ".env"), TossTokenCache(TOKEN))


def _trader(tickets: list[Ticket], last: dict[str, float]) -> TossTrader:
    """장부 종목만, 수량은 장부 수량까지, 지정가는 추천 · 평균 · 지금 가격 중 큰 값의 1.1 배까지."""
    act = [t for t in tickets if t.active]
    qty: dict[str, int] = {}
    for t in act:
        qty[t.symbol] = max(qty.get(t.symbol, 1), t.qty, int(t.filled))

    def cap(kr: bool) -> float:
        xs = [max(t.ref_price, t.avg, last.get(t.symbol, 0.0)) for t in act if t.kr == kr]
        return max(xs, default=1.0) * 1.1

    return TossTrader(
        load_toss_credentials(REPO / ".env"),
        TossTokenCache(TOKEN),
        allowed_symbols=frozenset(qty),
        max_qty=qty,
        sell_symbols=frozenset(t.symbol for t in act if t.status in ("holding", "selling")),
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
    opening, closing = (datetime.fromisoformat(s) for s in sess)
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
    for p in TOSS_DATA.glob("*.json"):
        name = p.stem
        if name in ("DENEB", "QS_SWITCH", "universe"):
            continue
        out[name.removeprefix("KR_")] = "자동 적립"
    return out


def _deneb_usd() -> float:
    try:
        d = json.loads((TOSS_DATA / "DENEB.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return float(d.get("cash_usd") or 0.0)


def _session_days(reader: TossReader, kr: bool, now_ms: int) -> list[date]:
    """다음 진입 세션 + 그 뒤 3세션. 주말/공휴일을 요일로 추측하지 않는다."""
    today = datetime.fromtimestamp(now_ms / 1000, KST if kr else NY).date()
    days: list[date] = []
    for k in range(16):
        day = today + timedelta(days=k)
        hours = reader.checked_regular_session(day.isoformat(), kr=kr)
        if hours is None:
            continue
        start, end = (datetime.fromisoformat(s) for s in hours)
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
        if kr:
            return _record_event_check(
                symbol,
                now_ms,
                EntryEvents(("국내 실적 일정 원천 미확보 — 신규 매수 보류",), entry_day=days[0]),
                {},
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
                str(d): {
                    "digest": s.digest,
                    "fetched_at_ms": s.fetched_at_ms,
                    "source": s.source,
                    "error": s.error,
                }
                for d, s in snapshots.items()
            },
            "sessions": [str(d) for d in days],
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
    q = query.strip()
    for country in ("KR", "US") if (q.isdigit() or not q.isascii()) else ("US", "KR"):
        hit = find(q, uni, country)
        if len(hit) == 1:
            return hit[0].symbol, hit[0].name
        if hit:
            raise SystemExit(
                "여러 종목이 맞아요: " + ", ".join(f"{x.name}({x.symbol})" for x in hit)
            )
    return q.upper(), q.upper()


def _closes(reader: TossReader, symbol: str, before_day: date) -> list[float]:
    try:
        return [
            float(c)
            for d, c in reader.daily_closes(symbol, 200, before_day=before_day.isoformat())
            if d < before_day.isoformat()
        ]
    except Exception as exc:  # 일봉 하나 못 받아도 추천은 낸다(확인 못 함으로 표시)
        print(f"{symbol} 일봉 건너뜀: {type(exc).__name__}")
        return []


def advise(query: str) -> Advice:
    symbol, name = resolve(query)
    reader = _reader()
    try:
        return _advise(reader, symbol, name, _now_ms())
    finally:
        reader.close()


def _advise(
    reader: TossReader, symbol: str, name: str, now_ms: int, *, force: bool = False
) -> Advice:
    kr = is_kr(symbol)
    risk = event_risk(reader, symbol, now_ms, force=force)
    day = risk.entry_day or datetime.fromtimestamp(now_ms / 1000, KST if kr else NY).date()
    # 다음 장을 추천하는 장 마감 직전에도 현재 미완성 일봉은 사용하지 않는다.
    today = datetime.fromtimestamp(now_ms / 1000, KST if kr else NY).date()
    hours = reader.checked_regular_session(today.isoformat(), kr=kr)
    cutoff = day
    if hours is not None and now_ms < datetime.fromisoformat(hours[1]).timestamp() * 1000:
        cutoff = min(day, today)
    px = reader.prices([symbol]).get(symbol)
    if px is None:
        raise ValueError(f"{symbol} 가격 미확인")
    fx = float(reader.usd_krw())
    holdings = reader.holdings()
    idx_sym, idx_name = INDEX["KR" if kr else "US"]
    us_stock = sum(float(p.qty * p.last) for s, p in holdings.items() if not is_kr(s)) * fx
    return recommend(
        Inputs(
            symbol=symbol,
            name=name,
            price=float(px),
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
    if not lines:
        return
    text = "\n\n".join(f"{TAG}\n{x}" for x in lines)
    OutboxNotifier(OUTBOX).send(text)
    creds = load_telegram_credentials(REPO / ".env")
    if creds is None:
        return
    try:
        TelegramNotifier(creds).send(text)
    except NotifyError as exc:
        print(f"텔레그램 실패: {exc}")


# ------------------------------------------------------------------ 단계


def tick(now_ms: int | None = None, *, send: bool = True) -> list[str]:
    """예약 매수 · 손절 감시 한 번. 할 일이 없으면 토스를 부르지 않는다."""
    tickets = load_tickets()
    if not any(t.active for t in tickets):
        return []
    now = now_ms or _now_ms()
    out: list[str] = []
    reader = _reader()
    try:
        held = [t for t in tickets if t.status in ("holding", "selling")]
        last = (
            {s: float(v) for s, v in reader.prices(sorted({t.symbol for t in held})).items()}
            if held
            else {}
        )
        trader = _trader(tickets, last)
        fx = float(reader.usd_krw()) if any(not t.kr for t in held) else 1.0
        sess = {kr: session(reader, kr, now) for kr in {t.kr for t in tickets if t.active}}
        halt = halted_reason(HALT)
        try:
            # 보호 매도는 일정 조회보다 먼저 실행한다. 발표 수집 실패가 매도를 막지 않는다.
            for t in held:
                _key, _buy_live, sell_live = sess[t.kr]
                if t.status == "holding" and sell_live:
                    check_stop(t, last.get(t.symbol))
                if t.status == "selling":
                    sellable = float(reader.sellable(t.symbol)) if sell_live else 0.0
                    out += step_sell(t, trader, now, live=sell_live, sellable=sellable, fx=fx)
            for t in tickets:
                if not t.active:
                    continue
                key, buy_live, _sell_live = sess[t.kr]
                if t.status in ("reserved", "buying"):
                    block = None
                    opening = t.open_px or None
                    if buy_live:
                        if halt:
                            block = f"전체 정지 중 ({halt})"
                        else:
                            try:
                                if t.status == "buying":
                                    refresh(t.buys, trader, now, live=True)
                                pending = any(not o.closed for o in t.buys)
                                if pending:
                                    risk = event_risk(reader, t.symbol, now, force=True)
                                    if not risk.ok:
                                        block = " · ".join(risk.blocked)
                                else:
                                    fresh = _advise(reader, t.symbol, t.name, now, force=True)
                                    rem = t.qty - int(sum(o.filled for o in t.buys))
                                    if not fresh.ok:
                                        block = " · ".join(fresh.blocked)
                                    elif rem > fresh.qty:
                                        block = "다시 확인한 수량 상한 감소 — 새 추천 필요"
                                if t.status == "reserved" and not block:
                                    hours = reader.checked_regular_session(
                                        key.split(":", 1)[1], kr=t.kr
                                    )
                                    if hours is None:
                                        raise ValueError("정규장 시각 미확인")
                                    start_ms = int(
                                        datetime.fromisoformat(hours[0]).timestamp() * 1000
                                    )
                                    px = reader.regular_open(t.symbol, start_ms)
                                    opening = float(px) if px is not None else None
                            except (AutoTradingError, ValueError, OSError) as exc:
                                block = f"주문 직전 재확인 실패 ({type(exc).__name__})"
                    out += step_buy(
                        t,
                        trader,
                        now,
                        session=key,
                        live=buy_live,
                        block=block,
                        regular_open=opening,
                    )
        finally:
            trader.close()
    finally:
        reader.close()
        save_tickets(tickets)
    if send:
        notify(out)
    return out


def reserve(query: str) -> str:
    a = advise(query)
    text = advice_text(a)
    if not a.ok:
        return text
    now = _now_ms()
    with _Lock():
        tickets = load_tickets()
        if any(t.active and t.symbol == a.symbol for t in tickets):
            return text + f"\n\n⚠️ {a.symbol} 는 이미 예약 · 감시 중이라 새로 예약하지 않았어요"
        tickets.append(new_ticket(a, now, _tid(now)))
        save_tickets(tickets)
        msgs = tick(now)
    head = "⏳ 예약했어요 — 정규장 시작 직후(장중이면 바로) 지정가로 삽니다"
    return "\n\n".join([text, head, *msgs])


def attach(query: str, qty: float | None = None) -> str:
    symbol, name = resolve(query)
    reader = _reader()
    try:
        pos = reader.holdings().get(symbol)
    finally:
        reader.close()
    if pos is None:
        return f"{name} 를 갖고 있지 않아요"
    q = min(float(pos.qty), qty) if qty else float(pos.qty)
    if int(q) < 1:
        return "1주 이상만 손절을 걸 수 있어요"
    if symbol in _blocked():
        return f"{name} 는 {_blocked()[symbol]} 종목이라 손절을 걸지 않아요"
    now = _now_ms()
    with _Lock():
        tickets = load_tickets()
        if any(t.active and t.symbol == symbol for t in tickets):
            return f"{name} 는 이미 감시 중이에요"
        t = register_app(_tid(now), symbol, name, q, float(pos.avg_price), now)
        tickets.append(t)
        save_tickets(tickets)
    px = f"{t.stop:,.0f}원" if t.kr else f"${t.stop:,.2f}"
    return f"🛡 {name} {int(q)}주 손절 등록 — 토스 평균 단가 기준 {px}(−3%) 이하면 자동 매도"


def _set_status(tid: str, allowed: tuple[str, ...], status: str) -> str:
    with _Lock():
        tickets = load_tickets()
        for t in tickets:
            if t.id == tid:
                if t.status not in allowed:
                    return f"{t.name} 는 지금 {t.status} 라 바꿀 수 없어요"
                t.status, t.closed_ms = status, _now_ms()
                save_tickets(tickets)
                return f"{t.name} → {status}"
    return f"없는 번호: {tid}"


def status() -> str:
    tickets = load_tickets()
    act = [t for t in tickets if t.active]
    if not act:
        return "비중 · 손절 장부: 감시 중인 종목 없음"
    reader = _reader()
    try:
        last = {s: float(v) for s, v in reader.prices(sorted({t.symbol for t in act})).items()}
    finally:
        reader.close()
    ids = [f"  번호 {t.id} · {t.symbol} · {t.status}" for t in act]
    return "\n".join([*status_lines(act, last), *ids])


def main(argv: list[str]) -> int:
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
    elif cmd == "attach" and rest:
        qty = float(rest[1]) if len(rest) > 1 else None
        print(attach(rest[0], qty))
    elif cmd == "release" and rest:
        print(_set_status(rest[0], ("holding",), "released"))
    elif cmd == "cancel" and rest:
        print(_set_status(rest[0], ("reserved",), "canceled"))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
