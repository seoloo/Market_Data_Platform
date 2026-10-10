"""실거래 홈 — Glide(토스) · Cruise(업비트) · 토스 적립을 한 화면에 (사용자 2026-10-09).

* 장부(``data/toss/*.json`` · ``data/upbit_t2/state.json``)는 읽기만 한다. 주문 · 계좌 조회 없음.
* 평가는 **공개 시세만** 쓴다 — 업비트 ticker · 야후 차트(키 없음), 60 초 캐시.
  시세를 못 받으면 원가로 두고 그렇다고 적는다.
* 실거래 장부만 더한다. Paper · 데모 · 테스트넷은 넣지 않는다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_trading.ui.data import DashboardData

__all__ = ["Quotes", "build_books", "fetch_quotes", "live_page"]

CRUISE_COINS = ("BTC", "ETH", "SOL")
GLIDE_TICKERS = ("TQQQ", "SCHD")
_SKIP = frozenset({"M3", "QS_SWITCH"})
"""data/toss 안에서 적립 장부가 아닌 파일 (Glide 는 따로, QS_SWITCH 는 Paper)."""
_TTL_S = 60.0
_KST = timedelta(hours=9)


# ---------------------------------------------------------------- 시세 (I/O)


@dataclass(frozen=True, slots=True)
class Quotes:
    krw: Mapping[str, float] = field(default_factory=dict)
    """코인 → 업비트 원화 현재가."""
    usd: Mapping[str, float] = field(default_factory=dict)
    """미국 종목 → 달러 현재가."""
    fx: float | None = None
    """원/달러."""
    at_ms: int = 0
    errors: tuple[str, ...] = ()


_cache: dict[tuple[str, ...], tuple[float, Quotes]] = {}


def _get_json(url: str) -> Any:
    import httpx

    resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4.0)
    resp.raise_for_status()
    return resp.json()


def fetch_quotes(
    coins: Iterable[str], tickers: Iterable[str], get: Callable[[str], Any] = _get_json
) -> Quotes:
    """공개 시세만 (키 없음). 같은 종목 묶음은 60 초 동안 다시 부르지 않는다."""
    coins, tickers = tuple(sorted(set(coins))), tuple(sorted(set(tickers)))
    key = (*coins, "|", *tickers)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    errors: list[str] = []
    krw: dict[str, float] = {}
    if coins:
        try:
            rows = get(
                "https://api.upbit.com/v1/ticker?markets=" + ",".join(f"KRW-{c}" for c in coins)
            )
            krw = {str(r["market"])[4:]: float(r["trade_price"]) for r in rows}
        except Exception as exc:
            errors.append(f"업비트 시세 실패: {type(exc).__name__}")
    usd: dict[str, float] = {}
    fx: float | None = None
    for sym in (*tickers, "KRW=X"):
        try:
            meta = get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d"
            )["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
        except Exception as exc:
            errors.append(f"{sym} 시세 실패: {type(exc).__name__}")
            continue
        if sym == "KRW=X":
            fx = price
        else:
            usd[sym] = price
    q = Quotes(krw, usd, fx, int(time.time() * 1000), tuple(errors))
    _cache[key] = (time.monotonic(), q)
    return q


# ---------------------------------------------------------------- 장부 (순수)


@dataclass(frozen=True, slots=True)
class Holding:
    name: str
    qty: float
    cost_krw: float
    value_krw: float | None
    """None 이면 시세 없음 (평가는 원가로 셈)."""
    note: str = ""


@dataclass(frozen=True, slots=True)
class Book:
    key: str
    title: str
    venue: str
    deposits_krw: float
    cash_krw: float
    holdings: tuple[Holding, ...]
    notes: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    activity: tuple[tuple[str, str], ...] = ()
    """(날짜, 한 줄)."""

    @property
    def value_krw(self) -> float:
        return self.cash_krw + sum(
            h.value_krw if h.value_krw is not None else h.cost_krw for h in self.holdings
        )

    @property
    def priced(self) -> bool:
        return all(h.value_krw is not None for h in self.holdings)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _read(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def glide_book(state: Mapping[str, Any], q: Quotes) -> Book:
    deposits = [d for d in state.get("deposits") or [] if isinstance(d, list) and len(d) >= 4]
    dep_krw = sum(_f(d[1]) for d in deposits)
    dep_usd = sum(_f(d[2]) for d in deposits)
    avg_fx = dep_krw / dep_usd if dep_usd > 0 else None
    fx = q.fx or avg_fx
    cash = _f(state.get("cash_usd")) * (fx or 0.0)
    qty = state.get("qty") or {}
    spent: dict[str, float] = {}
    pending: list[str] = []
    activity: list[tuple[str, str]] = [(str(d[0]), f"💵 입금 {_f(d[1]):,.0f} 원") for d in deposits]
    for o in state.get("orders") or []:
        sym = str(o.get("symbol"))
        filled = _f(o.get("filled_qty"))
        if filled > 0:
            spent[sym] = spent.get(sym, 0.0) + _f(o.get("filled_usd")) + _f(o.get("commission"))
            activity.append((str(o.get("date")), f"🛒 {sym} {filled:g}주 체결"))
        if o.get("status") == "SENT" and filled <= 0:
            pending.append(f"{sym} {_f(o.get('qty')):g}주 · 지정가 ${_f(o.get('limit')):,.2f}")
    holdings = []
    for sym in GLIDE_TICKERS:
        n = _f(qty.get(sym))
        if n <= 0:
            continue
        price = q.usd.get(sym)
        holdings.append(
            Holding(
                sym,
                n,
                spent.get(sym, 0.0) * (avg_fx or fx or 0.0),
                None if price is None or fx is None else n * price * fx,
            )
        )
    notes = []
    months = [m for m in state.get("months") or [] if isinstance(m, list) and len(m) >= 3]
    if months:
        m = months[-1]
        notes.append(f"{m[0]} TQQQ 목표 {_f(m[2]):.0%} (QQQ 20일 변동성 {_f(m[1]):.1%})")
    amounts = state.get("amounts") or {}
    if amounts:
        last = max(amounts)
        notes.append(f"{last} 납입 {_f(amounts[last]):,.0f} 원")
    notes.append(f"남은 달러 ${_f(state.get('cash_usd')):,.2f}")
    return Book(
        "glide",
        "Glide",
        "토스 · TQQQ + SCHD",
        dep_krw,
        cash,
        tuple(holdings),
        tuple(notes),
        tuple(pending),
        tuple(activity),
    )


def _stored_coin_costs(orders: Iterable[Mapping[str, object]]) -> dict[str, Decimal]:
    """저장 체결의 표시 원가만 읽는다. ops/전략을 import하거나 주문을 계산하지 않는다.

    기존 t2_upbit.books의 수수료 포함 이동평균 원가와 동일한 장부 읽기 규칙.
    실현 손익·목표 비중·실행 판단은 화면에서 계산하지 않는다.
    """
    qty = dict.fromkeys(CRUISE_COINS, Decimal(0))
    cost = dict.fromkeys(CRUISE_COINS, Decimal(0))
    for order in orders:
        parts = str(order.get("identifier") or order.get("id") or "").split("-")
        if len(parts) < 3 or parts[-2] not in CRUISE_COINS:
            continue
        coin = parts[-2]
        volume = Decimal(str(order.get("executed_volume") or "0"))
        if volume <= 0:
            continue
        if order.get("side") == "bid":
            qty[coin] += volume
            cost[coin] += Decimal(str(order.get("funds") or "0")) + Decimal(
                str(order.get("paid_fee") or "0")
            )
        else:
            average = cost[coin] / qty[coin] if qty[coin] > 0 else Decimal(0)
            sold = min(volume, qty[coin])
            cost[coin] -= average * sold
            qty[coin] -= sold
    return cost


def cruise_book(state: Mapping[str, Any], q: Quotes) -> Book:
    exp = state.get("expected") or {}
    costs = _stored_coin_costs(state.get("orders") or [])
    sig = state.get("last_trade_signals") or {}
    holdings = []
    for coin in CRUISE_COINS:
        n = _f(exp.get(coin))
        if n <= 0:
            continue
        price = q.krw.get(coin)
        s = sig.get(coin) or {}
        w = _f(s.get("weight"))
        trend = "🟢 추세 켜짐" if w > 0 else "🔴 추세 꺼짐"
        holdings.append(
            Holding(
                coin,
                n,
                float(costs[coin]),
                None if price is None else n * price,
                f"{trend} · 목표 {w:.0%}",
            )
        )
    deposits = sum(_f(d[1]) for d in state.get("deposits") or [] if isinstance(d, list))
    cash = _f(exp.get("KRW"))
    book = Book("cruise", "Cruise", "업비트 · BTC · ETH · SOL", deposits, cash, tuple(holdings))
    units, peak = _f(state.get("units")), _f(state.get("peak"))
    notes = []
    if units > 0 and peak > 0:
        dd = max(0.0, 1 - book.value_krw / units / peak)
        drop = f"−{dd:.1%}" if dd >= 0.0005 else "0%"
        notes.append(f"고점 대비 {drop} (30% 면 새 매수 멈춤)")
    if state.get("stopped"):
        notes.append("⛔ 낙폭 정지 중 — 해제는 사람이")
    if state.get("last_decision"):
        notes.append(f"마지막 결정 {state['last_decision']} · 매일 09:05")
    activity = [
        (str(o.get("id", ""))[4:14], str(o.get("line", "")))
        for o in state.get("orders") or []
        if o.get("line")
    ]
    activity += [
        (str(d[0]), f"💵 입금 {_f(d[1]):,.0f} 원")
        for d in state.get("deposits") or []
        if isinstance(d, list)
    ]
    return Book(
        book.key,
        book.title,
        book.venue,
        deposits,
        cash,
        book.holdings,
        tuple(notes),
        (),
        tuple(activity),
    )


def accumulate_book(states: Iterable[Mapping[str, Any]], q: Quotes) -> Book:
    holdings = []
    activity: list[tuple[str, str]] = []
    total_cost = 0.0
    for st in states:
        sym = str(st.get("symbol"))
        buys = [b for b in st.get("buys") or [] if isinstance(b, dict)]
        # 'cost' 칸은 장부마다 단위가 달라(원 · 달러) 쓰지 않는다 — 달러 원가 × 그날 환율.
        cost = sum(_f(b.get("cost_usd")) * _f(b.get("fx_rate")) for b in buys)
        total_cost += cost
        activity += [(str(b.get("date")), f"🛒 {sym} {_f(b.get('qty')):g}주 적립") for b in buys]
        n = _f(st.get("last_qty"))
        if n <= 0:
            continue
        price = q.usd.get(sym)
        holdings.append(
            Holding(sym, n, cost, None if price is None or q.fx is None else n * price * q.fx)
        )
    return Book(
        "accumulate",
        "토스 적립",
        "토스 · 매일 $1 · 기존 보유",
        total_cost,
        0.0,
        tuple(holdings),
        ("입금 = 산 원가 (적립은 따로 입금 기록이 없다)",),
        (),
        tuple(activity),
    )


def _toss_states(root: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted((root / "data" / "toss").glob("*.json")):
        if path.stem in _SKIP:
            continue
        d = _read(path)
        if d and d.get("symbol"):
            out.append(d)
    return out


def needed(root: Path) -> tuple[set[str], set[str]]:
    """시세가 필요한 (코인, 미국 종목)."""
    tickers = set(GLIDE_TICKERS) | {str(d["symbol"]) for d in _toss_states(root)}
    return set(CRUISE_COINS), tickers


def build_books(root: Path, q: Quotes) -> list[Book]:
    out = []
    glide = _read(root / "data" / "toss" / "M3.json")
    if glide:
        out.append(glide_book(glide, q))
    cruise = _read(root / "data" / "upbit_t2" / "state.json")
    if cruise:
        out.append(cruise_book(cruise, q))
    acc = _toss_states(root)
    if acc:
        out.append(accumulate_book(acc, q))
    return out


# ---------------------------------------------------------------- 화면


def _won(x: float) -> str:
    return f"{x:,.0f}"


def _pl(value: float, base: float) -> str:
    diff = value - base
    pct = f" ({diff / base:+.2%})" if base > 0 else ""
    cls = "pos" if diff > 0 else "neg" if diff < 0 else "mute"
    return f'<span class="{cls}">{diff:+,.0f} 원{pct}</span>'


def _priced(b: Book, q: Quotes) -> bool:
    """표시 품질만 판단한다. 달러 현금의 환율 대체도 확정 평가로 표시하지 않는다."""
    return b.priced and (b.key not in {"glide", "accumulate"} or q.fx is not None)


def render_live(books: list[Book], q: Quotes, now_ms: int) -> str:
    """실거래 계산은 보존하고 사용자 승인 오르빗 화면만 렌더링한다."""
    from auto_trading.ui.orbit_home import render_live as render_orbit

    return render_orbit(books, q, now_ms)


QuoteSource = Callable[[set[str], set[str]], Quotes]


def _default_quotes(coins: set[str], tickers: set[str]) -> Quotes:
    return fetch_quotes(coins, tickers)


quote_source: QuoteSource = _default_quotes
"""시험에서 바꿔 끼운다."""


def live_page(data: DashboardData) -> str:
    if data.root is None:
        return '<section class="panel"><h2>저장소 위치 없음</h2></section>'
    if not build_books(data.root, Quotes()):
        return render_live([], Quotes(), data.now_ms)  # 장부가 없으면 시세도 부르지 않는다
    coins, tickers = needed(data.root)
    q = quote_source(coins, tickers)
    return render_live(build_books(data.root, q), q, data.now_ms)


# ---------------------------------------------------------------- 텔레그램 '현황' 답


_ICON = {"glide": "📈", "cruise": "🛫", "accumulate": "💰"}


def _pl_text(value: float, base: float) -> str:
    diff = value - base
    mark = "🔺" if diff > 0 else "🔻" if diff < 0 else "▫️"
    pct = f" ({diff / base:+.2%})" if base > 0 else ""
    return f"{mark} {diff:+,.0f}원{pct}"


def summary_text(books: list[Book], q: Quotes, now_ms: int) -> str:
    """실거래 홈을 휴대폰 한 통으로 (순수). 텔레그램 '현황' 명령의 답."""
    from auto_trading.notify.style import RULE, sub

    now = (datetime.fromtimestamp(now_ms / 1000, UTC) + _KST).strftime("%m/%d %H:%M")
    if not books:
        return f"📊 [실거래 현황] {now}\n실거래 장부가 없어요"
    dep = sum(b.deposits_krw for b in books)
    val = sum(b.value_krw for b in books)
    lines = [
        f"📊 [실거래 현황] {now}",
        f"💼 총 입금 {dep:,.0f}원 → 평가 {val:,.0f}원",
        sub(f"손익 {_pl_text(val, dep)}"),
    ]
    for b in books:
        lines += [
            "",
            RULE,
            f"{_ICON.get(b.key, '■')} {b.title} · {b.venue.split(' · ')[0]}",
            f"{b.deposits_krw:,.0f}원 → {b.value_krw:,.0f}원"
            f" · {_pl_text(b.value_krw, b.deposits_krw)}",
        ]
        for h in b.holdings:
            trend = h.note[:1] + " " if h.note[:1] in ("🟢", "🔴") else ""
            qty = f"{h.qty:g}주 " if b.key != "cruise" else ""
            if h.value_krw is None:
                lines.append(sub(f"{trend}{h.name} {qty}원가 {h.cost_krw:,.0f}원 (시세 없음)"))
            else:
                pct = f"{h.value_krw / h.cost_krw - 1:+.1%}" if h.cost_krw > 0 else "—"
                lines.append(sub(f"{trend}{h.name} {qty}{h.value_krw:,.0f}원 · {pct}"))
        if b.cash_krw:
            lines.append(sub(f"현금 {b.cash_krw:,.0f}원"))
        lines += [sub(f"⏳ {p}") for p in b.pending]
    fx = f"원/달러 {q.fx:,.1f}" if q.fx else "환율 없음"
    lines += ["", RULE, f"공개 시세 · {fx} · 세금 전"]
    lines += [f"⚠ {e}" for e in q.errors]
    return "\n".join(lines)
