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

import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from auto_trading.config.secrets import load_telegram_credentials, load_toss_credentials
from auto_trading.exchange.toss import TossReader, TossTokenCache, TossTrader
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier
from auto_trading.ops.deneb_live import SYMBOLS as DENEB_SYMBOLS
from auto_trading.ops.kill_switch import halted_reason
from auto_trading.ops.kr_accumulate import find, read_universe
from auto_trading.ops.position_size import (
    EARN_BLOCK_DAYS,
    Advice,
    Inputs,
    advice_text,
    is_kr,
    recommend,
)
from auto_trading.ops.size_order import Ticket, new_ticket, step_buy
from auto_trading.ops.stop_guard import check_stop, register_app, status_lines, step_sell

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "size_guard"
STATE = DATA / "state.json"
LOCK = DATA / "lock"
EARN_CACHE = DATA / "earnings"
OUTBOX = REPO / "reports" / "size_guard" / "outbox.log"
TOKEN = REPO / "data" / "toss_token.json"
HALT = REPO / "data" / "HALT"
TOSS_DATA = REPO / "data" / "toss"
NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
NASDAQ_EARNINGS = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
BROWSER = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
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
    if kr:
        k = datetime.fromtimestamp(now_ms / 1000, KST)
        day = k.strftime("%Y-%m-%d")
        hm = k.strftime("%H:%M")
        if not "09:01" <= hm < "15:18":
            return f"KR:{day}", False, False
        if not reader.kr_open_today(day):
            return f"KR:{day}", False, False
        return f"KR:{day}", hm < "15:15", True
    ny_day = datetime.fromtimestamp(now_ms / 1000, NY).strftime("%Y-%m-%d")
    sess = reader.us_regular_session(ny_day)
    if sess is None:
        return f"US:{ny_day}", False, False
    start = datetime.fromisoformat(sess[0]).timestamp() * 1000 + 60_000
    end = datetime.fromisoformat(sess[1]).timestamp() * 1000
    return (
        f"US:{ny_day}",
        start <= now_ms < end - 5 * 60_000,
        start <= now_ms < end - 2 * 60_000,
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


def _trading_days(a: date, b: date) -> int:
    """a 다음 날부터 b 까지 평일 수 (a == b 면 0). 공휴일은 무시."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def _earnings_rows(d: date) -> list[dict[str, Any]]:
    """그 미국 날짜 Nasdaq 실적 캘린더 (날짜마다 하루 한 번 받는다)."""
    EARN_CACHE.mkdir(parents=True, exist_ok=True)
    path = EARN_CACHE / f"{d.isoformat()}.json"
    today = datetime.now(NY).date().isoformat()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("fetched") == today:
            return list(cached.get("rows") or [])
    except (OSError, ValueError):
        pass
    r = httpx.get(NASDAQ_EARNINGS.format(d=d.isoformat()), headers=BROWSER, timeout=30)
    r.raise_for_status()
    rows = list(((r.json().get("data") or {}).get("rows")) or [])
    path.write_text(
        json.dumps({"fetched": today, "rows": rows}, ensure_ascii=False), encoding="utf-8"
    )
    return rows


def earnings(symbol: str, today: date | None = None) -> tuple[str | None, int | None]:
    """(가장 가까운 실적 발표일, 거래일 수) — 다음 8 일 안. 국내 · 못 받으면 (None, None)."""
    if is_kr(symbol):
        return None, None
    today = today or datetime.now(NY).date()
    try:
        for k in range(9):
            d = today + timedelta(days=k)
            if d.weekday() >= 5:
                continue
            if any(str(r.get("symbol") or "").upper() == symbol for r in _earnings_rows(d)):
                return d.isoformat(), _trading_days(today, d)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"실적 캘린더 건너뜀: {type(exc).__name__}")
        return None, None
    return "", EARN_BLOCK_DAYS + 5


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


def _closes(reader: TossReader, symbol: str) -> list[float]:
    try:
        return [float(c) for _, c in reader.daily_closes(symbol, 200)]
    except Exception as exc:  # 일봉 하나 못 받아도 추천은 낸다(확인 못 함으로 표시)
        print(f"{symbol} 일봉 건너뜀: {type(exc).__name__}")
        return []


def advise(query: str) -> Advice:
    symbol, name = resolve(query)
    kr = is_kr(symbol)
    reader = _reader()
    try:
        px = reader.prices([symbol]).get(symbol)
        if px is None:
            raise SystemExit(f"{symbol} 가격을 못 읽었어요 — 기호를 확인해 주세요")
        fx = float(reader.usd_krw())
        holdings = reader.holdings()
        idx_sym, idx_name = INDEX["KR" if kr else "US"]
        us_stock = sum(float(p.qty * p.last) for s, p in holdings.items() if not is_kr(s)) * fx
        e_day, e_n = earnings(symbol)
        inp = Inputs(
            symbol=symbol,
            name=name,
            price=float(px),
            krw_cash=float(reader.buying_power("KRW")),
            usd_cash=float(reader.buying_power("USD")),
            fx=fx,
            deneb_usd=_deneb_usd(),
            closes=_closes(reader, symbol),
            index_name=idx_name,
            index_closes=_closes(reader, idx_sym),
            earnings_date=e_day,
            earnings_in_days=e_n,
            held_qty=float(holdings[symbol].qty) if symbol in holdings else 0.0,
            us_stock_krw=us_stock,
            blocked_symbol=_blocked().get(symbol, ""),
        )
    finally:
        reader.close()
    return recommend(inp)


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
            for t in tickets:
                if not t.active:
                    continue
                key, buy_live, sell_live = sess[t.kr]
                if t.status in ("reserved", "buying"):
                    block = None
                    if t.status == "reserved" and buy_live:
                        if halt:
                            block = f"전체 정지 중 ({halt})"
                        else:
                            e_day, e_n = earnings(t.symbol)
                            if e_n is not None and e_n <= EARN_BLOCK_DAYS:
                                block = f"실적 발표 {e_day} ({e_n} 거래일 뒤)"
                    out += step_buy(t, trader, now, session=key, live=buy_live, block=block)
                if t.status == "holding" and sell_live:
                    check_stop(t, last.get(t.symbol))
                if t.status == "selling":
                    sellable = float(reader.sellable(t.symbol)) if sell_live else 0.0
                    out += step_sell(t, trader, now, live=sell_live, sellable=sellable, fx=fx)
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
