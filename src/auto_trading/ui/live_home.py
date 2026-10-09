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
from html import escape
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


def cruise_book(state: Mapping[str, Any], q: Quotes) -> Book:
    from auto_trading.ops.t2_upbit import books

    exp = state.get("expected") or {}
    costs = books(state.get("orders") or [])
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
                float(costs[coin].cost),
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


def _book_card(b: Book) -> str:
    rows = "".join(
        f"<tr><td><b>{escape(h.name)}</b>"
        + (f'<small class="mute"> {escape(h.note)}</small>' if h.note else "")
        + f'</td><td class="num">{h.qty:,.8g}</td>'
        f'<td class="num">{_won(h.value_krw if h.value_krw is not None else h.cost_krw)}'
        + ("" if h.value_krw is not None else '<small class="mute"> 원가</small>')
        + f'</td><td class="num">{_pl(h.value_krw, h.cost_krw) if h.value_krw is not None else "—"}'
        "</td></tr>"
        for h in b.holdings
    )
    rows += (
        f'<tr><td class="mute">현금</td><td></td><td class="num">{_won(b.cash_krw)}</td>'
        "<td></td></tr>"
    )
    notes = "".join(f"<li>{escape(n)}</li>" for n in b.notes)
    pending = "".join(f"<li>⏳ {escape(p)}</li>" for p in b.pending)
    return (
        f'<section class="panel live-book live-{b.key}"><div class="row">'
        f'<div><div class="eyebrow">{escape(b.venue)}</div><h2>{escape(b.title)}</h2></div>'
        f'<div class="live-book-total"><strong>{_won(b.value_krw)}<small> 원</small></strong>'
        f"<span>입금 {_won(b.deposits_krw)} · {_pl(b.value_krw, b.deposits_krw)}</span></div></div>"
        "<table><tr><th>종목</th><th class='num'>수량</th><th class='num'>평가(원)</th>"
        f"<th class='num'>손익</th></tr>{rows}</table>"
        + (f'<ul class="live-pending">{pending}</ul>' if pending else "")
        + (f'<ul class="live-notes">{notes}</ul>' if notes else "")
        + "</section>"
    )


def _next_glide(now_ms: int) -> str:
    d = datetime.fromtimestamp(now_ms / 1000, UTC) + _KST
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    first = datetime(y, m, 1, tzinfo=UTC)
    while first.weekday() >= 5:
        first += timedelta(days=1)
    return first.strftime("%m/%d")


def render_live(books: list[Book], q: Quotes, now_ms: int) -> str:
    if not books:
        return '<section class="panel"><h2>실거래 장부 없음</h2></section>'
    dep = sum(b.deposits_krw for b in books)
    val = sum(b.value_krw for b in books)
    shares = "".join(
        f'<span class="live-seg live-seg-{b.key}" style="width:{b.value_krw / val * 100:.2f}%"'
        f' title="{escape(b.title)}"></span>'
        for b in books
        if val > 0
    )
    legend = "".join(
        f'<span><i class="live-dot live-seg-{b.key}"></i>{escape(b.title)} '
        f"<b>{b.value_krw / val:.0%}</b></span>"
        for b in books
        if val > 0
    )
    stale = [] if all(b.priced for b in books) else ["일부 종목은 시세가 없어 원가로 셈"]
    warn = "".join(f"<div class='banner info'>{escape(e)}</div>" for e in (*q.errors, *stale))
    fx = f"원/달러 {q.fx:,.1f}" if q.fx else "환율 없음"
    at = (datetime.fromtimestamp(q.at_ms / 1000, UTC) + _KST).strftime("%H:%M") if q.at_ms else "—"
    hero = (
        '<section class="panel live-hero"><div class="live-kpis">'
        f"<div><span>총 입금</span><strong>{_won(dep)}<small> 원</small></strong></div>"
        f"<div><span>지금 평가</span><strong>{_won(val)}<small> 원</small></strong></div>"
        f"<div><span>평가 손익</span><strong>{_pl(val, dep)}</strong></div></div>"
        f'<div class="live-bar">{shares}</div><div class="live-legend">{legend}</div>'
        f'<div class="foot">공개 시세 기준 · {fx} · '
        f"{at} KST · 세금 전</div></section>"
    )
    schedule = (
        '<section class="panel live-schedule"><h2>다음 일정</h2><ul class="timeline">'
        f"<li><time>{_next_glide(now_ms)}</time>"
        "<b>Glide 월 매수 (전날 텔레그램으로 금액 묻기)</b></li>"
        "<li><time>매일 09:05</time><b>Cruise 비중 결정 · 체결</b></li>"
        "<li><time>토 09:30</time><b>Cruise 주간 요약</b></li>"
        "<li><time>평일 밤</time><b>토스 $1 적립 (부족하면 환전 알림)</b></li></ul></section>"
    )
    acts = sorted((a for b in books for a in b.activity), key=lambda a: a[0], reverse=True)[:10]
    activity = (
        '<section class="panel"><h2>최근 기록</h2><ul class="timeline">'
        + "".join(f"<li><time>{escape(d)}</time><b>{escape(t)}</b></li>" for d, t in acts)
        + "</ul></section>"
    )
    cards = "".join(_book_card(b) for b in books)
    return (
        f"{warn}{hero}<div class='live-grid'><div>{cards}</div>"
        f"<div>{schedule}{activity}</div></div>"
    )


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
