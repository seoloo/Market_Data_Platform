"""실거래 홈 — Deneb(토스) · Vega(업비트) · 토스 적립을 한 화면에 (사용자 2026-10-09).

* 장부(``data/toss/*.json`` · ``data/upbit_t2/state.json``)는 읽기만 한다. 주문 · 계좌 조회 없음.
* 평가는 **공개 시세만** 쓴다 — 업비트 ticker · 야후 차트(키 없음), 60 초 캐시.
  시세를 못 받으면 원가로 두고 그렇다고 적는다.
* 실거래 장부만 더한다. Paper · 데모 · 테스트넷은 넣지 않는다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_trading.ui.data import DashboardData

__all__ = ["Quotes", "Trade", "build_books", "fetch_quotes", "live_page", "load_history"]

VEGA_COINS = ("BTC", "ETH", "SOL")
BINANCE_VEGA = Path("data") / "vega" / "state.json"
SHOW_UPBIT = False
"""업비트 Vega 장부를 보일지. 2026-10-15 사용자 "vega를 이제 바이낸스로 변경해줘 업비트는
지워줘" — 실거래 화면 · '현황' 에서 뺀다(장부 파일은 기록으로 남긴다)."""
"""바이낸스 Vega 장부 (2026-10-15 업비트에서 옮김). 있으면 업비트 장부 대신 보인다."""
DENEB_TICKERS = ("TQQQ", "SCHD")
DUST_KRW = 1_000.0
"""이보다 작은 코인 잔고는 보유로 보이지 않는다 (매도 뒤 남는 먼지)."""
_SKIP = frozenset({"M3", "QS_SWITCH"})
"""data/toss 안에서 적립 장부가 아닌 파일 (Deneb 은 따로, QS_SWITCH 는 Paper)."""
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

    def one(sym: str) -> float:
        meta = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d")[
            "chart"
        ]["result"][0]["meta"]
        return float(meta["regularMarketPrice"])

    # 종목마다 따로 기다리면 화면 열기가 몇 초씩 걸렸다 (2026-10-10) — 한꺼번에 묻는다
    from concurrent.futures import ThreadPoolExecutor

    names = (*tickers, "KRW=X")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {sym: pool.submit(one, sym) for sym in names}
    for sym in names:
        try:
            price = futures[sym].result()
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
    target: float | None = None
    """장부가 정한 목표 비중 (0 ~ 1). 없으면 None (보유 상세 막대에 목표선 없음)."""
    avg_usd: float | None = None
    """달러 평균 매입단가 (미국 종목, 장부에서 셀 수 있을 때만)."""


@dataclass(frozen=True, slots=True)
class Trade:
    """거래 내역 한 줄 (장부 그대로 읽은 값, 2026-10-10 화면 개선)."""

    date: str
    kind: str
    """buy · sell · deposit."""
    symbol: str
    qty: float = 0.0
    price: float | None = None
    """1 단위 체결가 (``currency`` 단위)."""
    amount: float = 0.0
    """체결 금액 또는 입금액 (``currency`` 단위)."""
    fee: float = 0.0
    currency: str = "KRW"
    status: str = "체결"
    """체결 · 접수(체결 확인 대기) · 체결 없음 · 입금 기록 · 그 밖의 장부 상태."""
    note: str = ""
    ref: str = ""
    """기록 DB 고유 번호 (장부 · 주문 번호 · 입금 순서로 고정, 2026-10-10 기록 DB)."""
    fx: float | None = None
    """원/달러 (장부에 있을 때만 — Deneb 입금 · 미국 적립 매수)."""


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
    trades: tuple[Trade, ...] = ()
    updated_ms: int = 0
    """장부 파일을 마지막으로 저장한 시각 (0 이면 모름)."""
    info: Mapping[str, Any] = field(default_factory=dict)
    """보유 상세 상태 칸에 쓰는 장부 값 (2026-10-10 화면 개선)."""

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


def deneb_book(state: Mapping[str, Any], q: Quotes) -> Book:
    deposits = [d for d in state.get("deposits") or [] if isinstance(d, list) and len(d) >= 4]
    dep_krw = sum(_f(d[1]) for d in deposits)
    dep_usd = sum(_f(d[2]) for d in deposits)
    avg_fx = dep_krw / dep_usd if dep_usd > 0 else None
    fx = q.fx or avg_fx
    # T 브레이크로 판 돈은 cash_usd 와 따로 brake.reserve_usd 에 달러로 들고 있다 — 평가액에 넣는다
    reserve_usd = _f((state.get("brake") or {}).get("reserve_usd"))
    cash = (_f(state.get("cash_usd")) + reserve_usd) * (fx or 0.0)
    qty = state.get("qty") or {}
    spent: dict[str, float] = {}
    spent_usd: dict[str, float] = {}
    pending: list[str] = []
    activity: list[tuple[str, str]] = [(str(d[0]), f"💵 입금 {_f(d[1]):,.0f} 원") for d in deposits]
    trades: list[Trade] = [
        Trade(
            str(d[0]),
            "deposit",
            "KRW",
            amount=_f(d[1]),
            status="입금 기록",
            note=f"${_f(d[2]):,.2f} 환전 · {_f(d[3]):,.1f}원/$",
            ref=f"deneb:deposit:{d[0]}:{i}",
            fx=_f(d[3]) or None,
        )
        for i, d in enumerate(deposits)
    ]
    for oi, o in enumerate(state.get("orders") or []):
        sym = str(o.get("symbol"))
        filled = _f(o.get("filled_qty"))
        status = str(o.get("status") or "")
        if filled > 0:
            state_text = "체결"
        elif status == "SENT":
            state_text = "접수 · 체결 확인 대기"
        else:
            state_text = status or "기록"
        trades.append(
            Trade(
                str(o.get("date")),
                "sell" if str(o.get("side")).upper() == "SELL" else "buy",
                sym,
                filled if filled > 0 else _f(o.get("qty")),
                _f(o.get("filled_usd")) / filled if filled > 0 else (_f(o.get("limit")) or None),
                _f(o.get("filled_usd")),
                _f(o.get("commission")),
                "USD",
                state_text,
                "" if filled > 0 else f"지정가 ${_f(o.get('limit')):,.2f}",
                ref="deneb:order:"
                + str(o.get("client_id") or o.get("order_id") or f"{o.get('date')}-{sym}-{oi}"),
            )
        )
        if filled > 0:
            spent[sym] = spent.get(sym, 0.0) + _f(o.get("filled_usd")) + _f(o.get("commission"))
            spent_usd[sym] = spent_usd.get(sym, 0.0) + _f(o.get("filled_usd"))
            activity.append((str(o.get("date")), f"🛒 {sym} {filled:g}주 체결"))
        if o.get("status") == "SENT" and filled <= 0:
            pending.append(f"{sym} {_f(o.get('qty')):g}주 · 지정가 ${_f(o.get('limit')):,.2f}")
    months = [m for m in state.get("months") or [] if isinstance(m, list) and len(m) >= 3]
    lev_target = _f(months[-1][2]) if months else None
    targets = {"TQQQ": lev_target, "SCHD": None if lev_target is None else 1 - lev_target}
    holdings = []
    for sym in DENEB_TICKERS:
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
                target=targets.get(sym),
                avg_usd=spent_usd[sym] / n if spent_usd.get(sym) else None,
            )
        )
    notes = []
    if months:
        m = months[-1]
        notes.append(f"{m[0]} TQQQ 목표 {_f(m[2]):.0%} (QQQ 20일 변동성 {_f(m[1]):.1%})")
    amounts = state.get("amounts") or {}
    if amounts:
        last = max(amounts)
        notes.append(f"{last} 납입 {_f(amounts[last]):,.0f} 원")
    notes.append(f"남은 달러 ${_f(state.get('cash_usd')):,.2f}")
    if not any(_f(qty.get(s)) > 0 for s in DENEB_TICKERS) and _f(state.get("cash_usd")) > 0:
        notes.append("보유 종목 없음 — 평가액은 남은 달러뿐이라 환율에 따라 움직여요")
    if reserve_usd > 0:
        notes.append(f"브레이크 보관 달러 ${reserve_usd:,.2f} (재매수 때 씀)")
    last_month = max(amounts) if amounts else ""
    info = {
        "cash_usd": _f(state.get("cash_usd")),
        "month": last_month,
        "month_amount": _f(amounts[last_month]) if last_month else None,
        "lev_target": lev_target,
        "brake_stage": int(_f((state.get("brake") or {}).get("stage"))),
        "reserve_usd": reserve_usd,
    }
    return Book(
        "deneb",
        "Deneb",
        "토스 · TQQQ + SCHD",
        dep_krw,
        cash,
        tuple(holdings),
        tuple(notes),
        tuple(pending),
        tuple(activity),
        tuple(trades),
        info=info,
    )


def _stored_coin_costs(orders: Iterable[Mapping[str, object]]) -> dict[str, Decimal]:
    """저장 체결의 표시 원가만 읽는다. ops/전략을 import하거나 주문을 계산하지 않는다.

    기존 t2_upbit.books의 수수료 포함 이동평균 원가와 동일한 장부 읽기 규칙.
    실현 손익·목표 비중·실행 판단은 화면에서 계산하지 않는다.
    """
    qty = dict.fromkeys(VEGA_COINS, Decimal(0))
    cost = dict.fromkeys(VEGA_COINS, Decimal(0))
    for order in orders:
        parts = str(order.get("identifier") or order.get("id") or "").split("-")
        if len(parts) < 3 or parts[-2] not in VEGA_COINS:
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


def vega_book(state: Mapping[str, Any], q: Quotes) -> Book:
    exp = state.get("expected") or {}
    costs = _stored_coin_costs(state.get("orders") or [])
    sig = state.get("last_trade_signals") or {}
    holdings = []
    for coin in VEGA_COINS:
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
                target=w,
            )
        )
    deposits = sum(_f(d[1]) for d in state.get("deposits") or [] if isinstance(d, list))
    cash = _f(exp.get("KRW"))
    book = Book("vega", "Vega", "업비트 · BTC · ETH · SOL", deposits, cash, tuple(holdings))
    units, peak = _f(state.get("units")), _f(state.get("peak"))
    notes = []
    dd: float | None = None
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
    trades = [
        Trade(
            str(d[0]),
            "deposit",
            "KRW",
            amount=_f(d[1]),
            status="입금 기록",
            ref=f"vega:deposit:{d[0]}:{i}",
        )
        for i, d in enumerate(state.get("deposits") or [])
        if isinstance(d, list)
    ]
    for o in state.get("orders") or []:
        parts = str(o.get("identifier") or o.get("id") or "").split("-")
        coin = parts[-2] if len(parts) >= 3 else "?"
        vol = _f(o.get("executed_volume"))
        funds = _f(o.get("funds"))
        if vol > 0:
            state_text = "체결"
        elif o.get("state"):
            state_text = "체결 없음"
        else:
            state_text = "결과 확인 안 됨"
        trades.append(
            Trade(
                str(o.get("id", ""))[4:14],
                "buy" if o.get("side") == "bid" or parts[-1:] == ["B"] else "sell",
                coin,
                vol if vol > 0 else _f(o.get("volume")),
                funds / vol if vol > 0 else None,
                funds,
                _f(o.get("paid_fee")),
                "KRW",
                state_text,
                ref=f"vega:order:{o.get('identifier') or o.get('id')}",
            )
        )
    info = {
        "buying": sum(1 for c in VEGA_COINS if _f((sig.get(c) or {}).get("weight")) > 0),
        "coins": len(VEGA_COINS),
        "dd": dd,
        "stopped": bool(state.get("stopped")),
        "last_decision": str(state.get("last_decision") or ""),
    }
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
        tuple(trades),
        info=info,
    )


VEGA_USD = {c: f"{c}-USD" for c in VEGA_COINS}
"""바이낸스 Vega 코인 시세 — 야후 달러 시세(USDT ≈ USD)로 재고 원/달러로 원화 환산."""


def vega_binance_book(state: Mapping[str, Any], q: Quotes) -> Book:
    """바이낸스 Vega (2026-10-15 업비트에서 옮김, ``data/vega/state.json``) — 원화로 환산.

    USDT 는 1 달러로 보고 지금 원/달러로 바꾼다. 입금 · 원가도 지금 환율로 바꾼 값이다(근사)."""
    fx = q.fx
    exp = state.get("expected") or {}
    orders = [o for o in state.get("orders") or [] if isinstance(o, Mapping)]
    qty = dict.fromkeys(VEGA_COINS, 0.0)
    cost = dict.fromkeys(VEGA_COINS, 0.0)
    trades = []
    for o in orders:
        parts = str(o.get("id") or "").split("-")
        coin = parts[-2] if len(parts) >= 3 and parts[-2] in VEGA_COINS else ""
        vol, funds, fee = _f(o.get("executed_qty")), _f(o.get("quote_qty")), _f(o.get("fee_usdt"))
        buy = parts[-1:] == ["B"]
        if coin and vol > 0:
            if buy:
                qty[coin] += vol
                cost[coin] += funds + fee
            else:
                avg = cost[coin] / qty[coin] if qty[coin] > 0 else 0.0
                sold = min(vol, qty[coin])
                cost[coin] -= avg * sold
                qty[coin] -= sold
        trades.append(
            Trade(
                str(o.get("id", ""))[4:14],
                "buy" if buy else "sell",
                coin or "?",
                vol,
                funds / vol if vol > 0 else None,
                funds,
                fee,
                "USDT",
                "체결" if vol > 0 else "체결 없음" if o.get("status") else "결과 확인 안 됨",
                ref=f"vega:order:{o.get('id')}",
            )
        )
    sig = state.get("last_trade_signals") or {}
    holdings = []
    for coin in VEGA_COINS:
        n = _f(exp.get(coin))
        if n <= 0:
            continue
        usd = q.usd.get(VEGA_USD[coin])
        if usd is not None and fx is not None and n * usd * fx < DUST_KRW:
            continue  # 팔고 남은 먼지(2026-10-16 사용자: 보유 코인이 없는데 있다고 나옴)
        w = _f((sig.get(coin) or {}).get("weight"))
        holdings.append(
            Holding(
                coin,
                n,
                cost[coin] * (fx or 0.0),
                None if usd is None or fx is None else n * usd * fx,
                f"바이낸스 · {n:g} {coin}",
                target=w or None,
            )
        )
    rows = [d for d in state.get("deposits") or [] if isinstance(d, list) and len(d) >= 2]
    deposits = sum(_f(d[1]) for d in rows) * (fx or 0.0)
    cash = _f(exp.get("USDT")) * (fx or 0.0)
    notes = []
    units, peak = _f(state.get("units")), _f(state.get("peak"))
    dd: float | None = None
    if units > 0 and peak > 0 and fx:
        nav_usdt = (
            _f(exp.get("USDT"))
            + sum(_f(exp.get(c)) * (q.usd.get(VEGA_USD[c]) or 0.0) for c in VEGA_COINS)
        ) / units
        dd = max(0.0, 1 - nav_usdt / peak)
        notes.append(
            f"고점 대비 −{dd:.1%} (30% 면 새 매수 멈춤)" if dd >= 0.0005 else "고점 대비 0%"
        )
    if state.get("stopped"):
        notes.append("⛔ 낙폭 정지 중 — 해제는 사람이")
    if state.get("last_decision"):
        notes.append(f"마지막 결정 {state['last_decision']} · 매일 09:05 · USDT 를 원화로 환산")
    activity = [
        (str(o.get("id", ""))[4:14], str(o.get("line", ""))) for o in orders if o.get("line")
    ]
    activity += [(str(d[0]), f"💵 입금 {_f(d[1]):,.2f} USDT") for d in rows]
    trades += [
        Trade(
            str(d[0]),
            "deposit",
            "USDT",
            amount=_f(d[1]),
            status="입금 기록",
            ref=f"vega:deposit:{d[0]}:{i}",
        )
        for i, d in enumerate(rows)
    ]
    info = {
        "buying": sum(1 for c in VEGA_COINS if _f((sig.get(c) or {}).get("weight")) > 0),
        "coins": len(VEGA_COINS),
        "dd": dd,
        "stopped": bool(state.get("stopped")),
        "last_decision": str(state.get("last_decision") or ""),
    }
    return Book(
        "vega",
        "Vega",
        "바이낸스 · BTC · ETH · SOL",
        deposits,
        cash,
        tuple(holdings),
        tuple(notes),
        (),
        tuple(activity),
        tuple(trades),
        info=info,
    )


ALTAIR_LIVE = Path("data") / "altair_live" / "state.json"
"""Altair 실거래 실행기 장부 (``scripts/altair_live.py``, 2026-10-17 시작). 화면은 읽기만."""
ALTAIR_EXPOSURE = 4.0
"""롱 목표 × 4 = 명목 비중 (``scripts.altair_demo.EXPOSURE``)."""


def altair_book(state: Mapping[str, Any], q: Quotes, live: Mapping[str, Any] | None = None) -> Book:
    """Altair (옛 AG4S2) — 바이낸스 USDⓈ-M 선물 지갑.

    사용자 2026-10-15 "오르빗에 실거래에 altair 추가". 입금 = Vega 실행기가 남긴 현물 → 선물
    이체(``altair.transfers``). 평가액은 실거래 실행기 장부(``live``)의 마지막 기록
    — 선물 지갑 전체 증거금 잔고(USDT + USDC, 미실현 포함) — 를 쓴다
    (2026-10-18 사용자 "현황에 altair 반영 안 되어 있다"). 실행기 기록이 없으면 Vega 가 확인한
    선물 지갑 USDT. 화면은 계좌를 조회하지 않는다."""
    fx = q.fx or 0.0
    fal = state.get("altair") or {}
    rows = [t for t in fal.get("transfers") or [] if isinstance(t, list) and len(t) >= 2]
    hist = [h for h in (live or {}).get("history") or [] if isinstance(h, list) and len(h) >= 3]
    summary: list[str] = []
    if hist:
        at_ms, usd, nav = int(hist[-1][0]), _f(hist[-1][1]), _f(hist[-1][2])
        at = (datetime.fromtimestamp(at_ms / 1000, UTC) + _KST).strftime("%m/%d %H:%M")
        peak = _f((live or {}).get("nav_peak"), nav) or nav
        w = (live or {}).get("long_w") or {}
        longs = " · ".join(f"{c} {ALTAIR_EXPOSURE * _f(v):.0%}" for c, v in w.items() if _f(v) > 0)
        shorts = " · ".join(str(c) for c in ((live or {}).get("shorts") or {}))
        pos = f"롱 {longs or '없음'} · 숏 {shorts or '없음'}"
        summary = [
            f"장부 {usd:,.2f} USDT (미실현 포함) · {at} 기준",
            f"{pos} (장부 대비 명목)",
        ]
        notes = [
            "실거래 중 — 4 시간마다 실행기가 기록",
            f"선물 지갑 전체 {usd:,.2f} USDT (USDT + USDC, 미실현 포함) · {at} 기준",
            pos + " — 롱은 T/v2 목표 × 4",
            f"단위 가치 {nav:.3f} · 고점 대비 {nav / peak - 1:+.1%} (30% 면 새 노출 정지)",
        ]
        if (live or {}).get("dd_halt"):
            notes.append(f"⛔ {live['dd_halt']} — 해제는 사람이")  # type: ignore[index]
    else:
        usd = _f(fal.get("futures_usdt"))
        notes = [
            "실거래 실행기 준비 중 — 선물 지갑에서 대기",
            f"선물 지갑 {usd:,.2f} USDT · 마지막 확인 {fal.get('checked', '—')}",
        ]
    notes.append("바이낸스 돈의 50% 를 선물로 (선물 몫 500 USDT 부터 자동 이체)")
    trades = tuple(
        Trade(
            str(t[0]),
            "deposit",
            "USDT",
            amount=_f(t[1]),
            status="현물 → 선물 이체",
            ref=f"altair:transfer:{t[0]}:{i}",
        )
        for i, t in enumerate(rows)
    )
    return Book(
        "altair",
        "Altair",
        "바이낸스 선물 · BTC · ETH",
        sum(_f(t[1]) for t in rows) * fx,
        usd * fx,
        (),
        tuple(notes),
        (),
        tuple((str(t[0]), f"☄️ 현물 → 선물 {_f(t[1]):,.2f} USDT") for t in rows),
        trades,
        info={"summary": summary} if summary else {},
    )


def accumulate_book(states: Iterable[Mapping[str, Any]], q: Quotes) -> Book:
    holdings = []
    activity: list[tuple[str, str]] = []
    trades: list[Trade] = []
    total_cost = 0.0
    sold: list[str] = []
    for st in states:
        sym = str(st.get("symbol"))
        buys = [b for b in st.get("buys") or [] if isinstance(b, dict)]
        if st.get("currency") == "KRW":  # 국내 적립 (scripts/toss_kr.py, 2026-10-10)
            k_cost = sum(_f(b.get("cost_krw")) for b in buys)
            total_cost += k_cost
            name = str(st.get("name") or sym)
            activity += [
                (str(b.get("date")), f"🛒 {name} {_f(b.get('qty')):g}주 적립") for b in buys
            ]
            for bi, b in enumerate(buys):
                n_b = _f(b.get("qty"))
                trades.append(
                    Trade(
                        str(b.get("date")),
                        "buy",
                        name,
                        n_b,
                        _f(b.get("cost_krw")) / n_b if n_b > 0 else None,
                        _f(b.get("cost_krw")),
                        0.0,
                        "KRW",
                        "체결",
                        "국내",
                        ref=f"accumulate:{sym}:buy:{b.get('date')}:{bi}",
                    )
                )
            n = _f(st.get("last_qty"))
            if n > 0:
                px = q.usd.get(kr_ticker(st))
                holdings.append(Holding(name, n, k_cost, None if px is None else n * px))
            continue
        # 'cost' 칸은 장부마다 단위가 달라(원 · 달러) 쓰지 않는다 — 달러 원가 × 그날 환율.
        cost = sum(_f(b.get("cost_usd")) * _f(b.get("fx_rate")) for b in buys)
        total_cost += cost
        activity += [(str(b.get("date")), f"🛒 {sym} {_f(b.get('qty')):g}주 적립") for b in buys]
        for bi, b in enumerate(buys):
            n_b = _f(b.get("qty"))
            trades.append(
                Trade(
                    str(b.get("date")),
                    "buy",
                    sym,
                    n_b,
                    _f(b.get("cost_usd")) / n_b if n_b > 0 else None,
                    _f(b.get("cost_usd")),
                    0.0,
                    "USD",
                    "체결",
                    f"{_f(b.get('fx_rate')):,.1f}원/$",
                    ref=f"accumulate:{sym}:buy:{b.get('date')}:{bi}",
                    fx=_f(b.get("fx_rate")) or None,
                )
            )
        n = _f(st.get("last_qty"))
        if n <= 0:
            # 다 팔린 장부 — 산 원가를 '넣은 돈' 에서 빼 가짜 손실이 안 보이게 (2026-10-16)
            if buys:
                total_cost -= cost
                sold.append(sym)
            continue
        price = q.usd.get(sym)
        bought = sum(_f(b.get("qty")) for b in buys)
        usd = sum(_f(b.get("cost_usd")) for b in buys)
        holdings.append(
            Holding(
                sym,
                n,
                cost,
                None if price is None or q.fx is None else n * price * q.fx,
                # 기존 보유분(장부 밖 원가)이 섞이면 평균 단가를 셀 수 없다
                avg_usd=usd / n if bought > 0 and abs(bought - n) < 1e-9 else None,
            )
        )
    return Book(
        "accumulate",
        "자동 적립",
        "토스 · 매일 $1 · 기존 보유",
        total_cost,
        0.0,
        tuple(holdings),
        (
            "입금 = 산 원가 (적립은 따로 입금 기록이 없다)",
            *([f"다 판 종목 {', '.join(sold)} — 원가에서 뺌 (판 돈은 토스 현금)"] if sold else []),
        ),
        (),
        tuple(activity),
        tuple(trades),
        info={"last_buy": max((d for d, _ in activity), default="")},
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


def kr_ticker(state: Mapping[str, Any]) -> str:
    """국내 적립 장부 → 야후 시세 기호 (코스닥 .KQ · 그 밖 .KS, 값은 원)."""
    return f"{state.get('symbol')}.{'KQ' if state.get('market') == 'KOSDAQ' else 'KS'}"


def needed(root: Path) -> tuple[set[str], set[str]]:
    """시세가 필요한 (코인, 미국 종목 · 국내는 .KS · .KQ 기호)."""
    tickers = set(DENEB_TICKERS) | {
        kr_ticker(d) if d.get("currency") == "KRW" else str(d["symbol"]) for d in _toss_states(root)
    }
    if (root / BINANCE_VEGA).exists():
        tickers |= set(VEGA_USD.values())
    return set(VEGA_COINS), tickers


def _mtime_ms(*paths: Path) -> int:
    best = 0
    for p in paths:
        try:
            best = max(best, int(p.stat().st_mtime * 1000))
        except OSError:
            continue
    return best


def build_books(root: Path, q: Quotes) -> list[Book]:
    out = []
    toss = root / "data" / "toss"
    deneb = _read(toss / "DENEB.json")
    if deneb:
        book = deneb_book(deneb, q)
        halt = root / "data" / "HALT_deneb"
        # 전부 정리 · 텔레그램 정지 — 새 매수 안 함 (2026-10-16 사용자: 정리했는데 산 것처럼 보임)
        if halt.exists():
            why = halt.read_text(encoding="utf-8", errors="replace").strip()[:60]
            book = replace(book, notes=(f"⏸ 정지 중 — 새 매수 안 함 ({why})", *book.notes))
        out.append(replace(book, updated_ms=_mtime_ms(toss / "DENEB.json")))
    binance = _read(root / BINANCE_VEGA)
    vega_path = root / "data" / "upbit_t2" / "state.json"
    vega = _read(vega_path) if SHOW_UPBIT else {}
    if vega and not binance:
        out.append(replace(vega_book(vega, q), updated_ms=_mtime_ms(vega_path)))
    if binance:
        out.append(
            replace(vega_binance_book(binance, q), updated_ms=_mtime_ms(root / BINANCE_VEGA))
        )
        if binance.get("altair"):
            live = _read(root / ALTAIR_LIVE)
            src = root / (ALTAIR_LIVE if live and live.get("history") else BINANCE_VEGA)
            out.append(replace(altair_book(binance, q, live), updated_ms=_mtime_ms(src)))
    acc = _toss_states(root)
    if acc:
        files = [p for p in toss.glob("*.json") if p.stem not in _SKIP and p.stem != "M3"]
        out.append(replace(accumulate_book(acc, q), updated_ms=_mtime_ms(*files)))
    return out


HISTORY = Path("data") / "live" / "history.jsonl"
"""하루 한 줄 실거래 평가 기록 (``scripts/live_snapshot.py`` 가 쓴다). 화면은 읽기만."""


def load_history(root: Path) -> list[dict[str, Any]]:
    """기록 파일 → 날짜 오름차순 줄들 (깨진 줄은 건너뜀, 같은 날은 마지막 줄)."""
    try:
        text = (root / HISTORY).read_text(encoding="utf-8")
    except OSError:
        return []
    by_day: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("date"), str):
            by_day[row["date"]] = row
    return [by_day[d] for d in sorted(by_day)]


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
    return b.priced and (b.key not in {"deneb", "accumulate"} or q.fx is not None)


def render_live(
    books: list[Book],
    q: Quotes,
    now_ms: int,
    *,
    hide: bool = False,
    onboarded: bool = True,
    history: list[dict[str, Any]] | None = None,
    detail: bool = False,
) -> str:
    """실거래 계산은 보존하고 사용자 승인 오르빗 화면만 렌더링한다."""
    from auto_trading.ui.orbit_home import render_live as render_orbit

    return render_orbit(
        books, q, now_ms, hide=hide, onboarded=onboarded, history=history, show_detail=detail
    )


QuoteSource = Callable[[set[str], set[str]], Quotes]


def _default_quotes(coins: set[str], tickers: set[str]) -> Quotes:
    return fetch_quotes(coins, tickers)


quote_source: QuoteSource = _default_quotes
"""시험에서 바꿔 끼운다."""


def live_page(data: DashboardData) -> str:
    if data.root is None:
        return '<section class="panel"><h2>저장소 위치 없음</h2></section>'
    flags: dict[str, Any] = {
        "hide": data.hide_amounts,
        "onboarded": data.onboarded,
        "history": load_history(data.root),
        "detail": data.show_detail,
    }
    if not build_books(data.root, Quotes()):
        # 장부가 없으면 시세도 부르지 않는다
        return render_live([], Quotes(), data.now_ms, **flags)
    coins, tickers = needed(data.root)
    q = quote_source(coins, tickers)
    return render_live(build_books(data.root, q), q, data.now_ms, **flags)


# ---------------------------------------------------------------- 텔레그램 '현황' 답


_ICON = {"deneb": "🌟", "vega": "🌌", "altair": "☄️", "accumulate": "💰"}


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
            qty = f"{h.qty:g}주 " if b.key != "vega" else ""
            if h.value_krw is None:
                lines.append(sub(f"{trend}{h.name} {qty}원가 {h.cost_krw:,.0f}원 (시세 없음)"))
            else:
                pct = f"{h.value_krw / h.cost_krw - 1:+.1%}" if h.cost_krw > 0 else "—"
                lines.append(sub(f"{trend}{h.name} {qty}{h.value_krw:,.0f}원 · {pct}"))
        if b.info.get("summary"):  # Altair — 선물 지갑 평가액은 현금이 아니다
            lines += [sub(x) for x in b.info["summary"]]
        elif b.cash_krw:
            lines.append(sub(f"현금 {b.cash_krw:,.0f}원"))
        lines += [sub(f"⏳ {p}") for p in b.pending]
    fx = f"원/달러 {q.fx:,.1f}" if q.fx else "환율 없음"
    lines += ["", RULE, f"공개 시세 · {fx} · 세금 전"]
    lines += [f"⚠ {e}" for e in q.errors]
    return "\n".join(lines)
