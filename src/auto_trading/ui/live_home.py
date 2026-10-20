"""실거래 홈 — Deneb(토스) · Vega(업비트) · 토스 적립을 한 화면에 (사용자 2026-10-09).

* 장부(``data/toss/*.json`` · ``data/upbit_t2/state.json``)는 읽기만 한다. 주문 · 계좌 조회 없음.
* 평가는 **공개 시세만** 쓴다 — 업비트 ticker · 야후 차트(키 없음), 60 초 캐시.
  시세를 못 받으면 원가로 두고 그렇다고 적는다.
* 실거래 장부만 더한다. Paper · 데모 · 테스트넷은 넣지 않는다.

언제 쓰나: 대시보드 '실거래' 쪽 · 텔레그램 '현황' · 매일 기록(``scripts/live_snapshot``) ·
           기록 DB(``scripts/ledger_sync``) · 파이어 보고.
읽고 쓰는 것: ``data/toss/*.json`` · ``data/upbit_t2/state.json`` · ``data/sirius/state.json`` ·
              ``data/HALT_*`` · ``data/live/history.jsonl`` 를 읽기만. 그 순간 계좌로 맞추기는
              :mod:`auto_trading.ui.live_sync` (장부 파일은 고치지 않는다).
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
SHOW_UPBIT = True
"""업비트 Vega 장부를 보일지 (2026-10-15 바이낸스로 옮길 때 껐다가 10-19 업비트로 되돌리며 다시 켬).
바이낸스 Vega · Altair 장부(``data/vega/`` · ``data/altair_live/``)는 기록으로만 남고 화면에서 뺐다
— 그 카드 코드는 리팩토링 5 단계(2026-10-21)에서 지웠다."""
DENEB_TICKERS = ("TQQQ", "SCHD", "IAUM")
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
    """[쓰임] 공개 시세 JSON 한 번 (야후는 User-Agent 가 없으면 막는다)."""
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
                "https://api.upbit.com/v1/ticker?markets="
                + ",".join(f"KRW-{coin}" for coin in coins)
            )
            krw = {str(row["market"])[4:]: float(row["trade_price"]) for row in rows}
        except Exception as exc:
            errors.append(f"업비트 시세 실패: {type(exc).__name__}")
    usd: dict[str, float] = {}
    fx: float | None = None

    def one(sym: str) -> float:
        """[쓰임] 야후에서 한 종목의 현재가."""
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
    quotes = Quotes(krw, usd, fx, int(time.time() * 1000), tuple(errors))
    _cache[key] = (time.monotonic(), quotes)
    return quotes


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
        """[쓰임] 카드 평가액(원) — 현금 + 종목(시세가 없으면 원가)."""
        return self.cash_krw + sum(
            h.value_krw if h.value_krw is not None else h.cost_krw for h in self.holdings
        )

    @property
    def priced(self) -> bool:
        """[쓰임] 모든 종목에 시세가 있나 (없으면 '추정')."""
        return all(h.value_krw is not None for h in self.holdings)


def _to_float(value: Any, default: float = 0.0) -> float:
    """[쓰임] 장부 값을 float 로 (비었거나 숫자가 아니면 ``default``)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read(path: Path) -> dict[str, Any] | None:
    """[쓰임] 장부 JSON 하나 (없거나 깨졌으면 None)."""
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def deneb_book(state: Mapping[str, Any], quotes: Quotes) -> Book:
    """[쓰임] Deneb 장부 → 카드 (넣은 돈 · 평가 · 종목 · 접수 주문 · 거래 · 브레이크 보관금).
    [이유] 원화 평가는 지금 환율, 시세가 없으면 원가로 두고 '추정' 으로 표시한다."""
    deposits = [
        row for row in state.get("deposits") or [] if isinstance(row, list) and len(row) >= 4
    ]
    deposited_krw = sum(_to_float(row[1]) for row in deposits)
    deposited_usd = sum(_to_float(row[2]) for row in deposits)
    avg_fx = deposited_krw / deposited_usd if deposited_usd > 0 else None
    fx = quotes.fx or avg_fx
    # T 브레이크로 판 돈은 cash_usd 와 따로 brake.reserve_usd 에 달러로 들고 있다 — 평가액에 넣는다
    reserve_usd = _to_float((state.get("brake") or {}).get("reserve_usd"))
    cash = (_to_float(state.get("cash_usd")) + reserve_usd) * (fx or 0.0)
    qty = state.get("qty") or {}
    spent: dict[str, float] = {}
    spent_usd: dict[str, float] = {}
    held_qty: dict[str, float] = {}
    pending: list[str] = []
    activity: list[tuple[str, str]] = [
        (str(row[0]), f"💵 입금 {_to_float(row[1]):,.0f} 원") for row in deposits
    ]
    trades: list[Trade] = [
        Trade(
            str(row[0]),
            "deposit",
            "KRW",
            amount=_to_float(row[1]),
            status="입금 기록",
            note=f"${_to_float(row[2]):,.2f} 환전 · {_to_float(row[3]):,.1f}원/$",
            ref=f"deneb:deposit:{row[0]}:{index}",
            fx=_to_float(row[3]) or None,
        )
        for index, row in enumerate(deposits)
    ]
    for order_index, order in enumerate(state.get("orders") or []):
        sym = str(order.get("symbol"))
        filled = _to_float(order.get("filled_qty"))
        status = str(order.get("status") or "")
        if filled > 0:
            state_text = "체결"
        elif status == "SENT":
            state_text = "접수 · 체결 확인 대기"
        else:
            state_text = status or "기록"
        trades.append(
            Trade(
                str(order.get("date")),
                "sell" if str(order.get("side")).upper() == "SELL" else "buy",
                sym,
                filled if filled > 0 else _to_float(order.get("qty")),
                _to_float(order.get("filled_usd")) / filled
                if filled > 0
                else (_to_float(order.get("limit")) or None),
                _to_float(order.get("filled_usd")),
                _to_float(order.get("commission")),
                "USD",
                state_text,
                "" if filled > 0 else f"지정가 ${_to_float(order.get('limit')):,.2f}",
                ref="deneb:order:"
                + str(
                    order.get("client_id")
                    or order.get("order_id")
                    or f"{order.get('date')}-{sym}-{order_index}"
                ),
            )
        )
        if filled > 0 and str(order.get("side")).upper() == "SELL":
            # 판 것은 이동평균 원가로 뺀다 (2026-10-20: 정리 매도 금액이 원가에 더해져
            # TQQQ −53% 처럼 보이던 문제 — deneb_live.cost_basis 와 같은 규칙)
            held = held_qty.get(sym, 0.0)
            sold = min(filled, held)
            if held > 0:
                spent[sym] = spent.get(sym, 0.0) * (held - sold) / held
                spent_usd[sym] = spent_usd.get(sym, 0.0) * (held - sold) / held
            held_qty[sym] = held - sold
            activity.append((str(order.get("date")), f"↩ {sym} {filled:g}주 매도 체결"))
        elif filled > 0:
            spent[sym] = (
                spent.get(sym, 0.0)
                + _to_float(order.get("filled_usd"))
                + _to_float(order.get("commission"))
            )
            spent_usd[sym] = spent_usd.get(sym, 0.0) + _to_float(order.get("filled_usd"))
            held_qty[sym] = held_qty.get(sym, 0.0) + filled
            activity.append((str(order.get("date")), f"🛒 {sym} {filled:g}주 체결"))
        if order.get("status") == "SENT" and filled <= 0:
            ordered_qty = _to_float(order.get("qty"))
            pending.append(
                f"{sym} {ordered_qty:g}주 · 지정가 ${_to_float(order.get('limit')):,.2f}"
            )
    months = [
        month for month in state.get("months") or [] if isinstance(month, list) and len(month) >= 3
    ]
    lev_target = _to_float(months[-1][2]) if months else None
    # 금 섞기 몫 (10-19 ~)
    gold = _to_float(months[-1][3]) if months and len(months[-1]) > 3 else 0.0
    rest = None if lev_target is None else 1 - lev_target
    targets = {
        "TQQQ": lev_target,
        "SCHD": None if rest is None else rest * (1 - gold),
        "IAUM": None if rest is None or gold <= 0 else rest * gold,
    }
    holdings = []
    for sym in DENEB_TICKERS:
        shares = _to_float(qty.get(sym))
        if shares <= 0:
            continue
        price = quotes.usd.get(sym)
        holdings.append(
            Holding(
                sym,
                shares,
                spent.get(sym, 0.0) * (avg_fx or fx or 0.0),
                None if price is None or fx is None else shares * price * fx,
                target=targets.get(sym),
                avg_usd=spent_usd[sym] / shares if spent_usd.get(sym) else None,
            )
        )
    notes = []
    if months:
        month = months[-1]
        target, volatility = _to_float(month[2]), _to_float(month[1])
        notes.append(f"{month[0]} TQQQ 목표 {target:.0%} (QQQ 20일 변동성 {volatility:.1%})")
    amounts = state.get("amounts") or {}
    if amounts:
        last = max(amounts)
        notes.append(f"{last} 납입 {_to_float(amounts[last]):,.0f} 원")
    notes.append(f"남은 달러 ${_to_float(state.get('cash_usd')):,.2f}")
    if (
        not any(_to_float(qty.get(s)) > 0 for s in DENEB_TICKERS)
        and _to_float(state.get("cash_usd")) > 0
    ):
        notes.append("보유 종목 없음 — 평가액은 남은 달러뿐이라 환율에 따라 움직여요")
    if reserve_usd > 0:
        notes.append(f"브레이크 보관 달러 ${reserve_usd:,.2f} (재매수 때 씀)")
    last_month = max(amounts) if amounts else ""
    info = {
        "cash_usd": _to_float(state.get("cash_usd")),
        "month": last_month,
        "month_amount": _to_float(amounts[last_month]) if last_month else None,
        "lev_target": lev_target,
        "brake_stage": int(_to_float((state.get("brake") or {}).get("stage"))),
        "reserve_usd": reserve_usd,
    }
    return Book(
        "deneb",
        "Deneb",
        "토스 · TQQQ + SCHD",
        deposited_krw,
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


def vega_book(state: Mapping[str, Any], quotes: Quotes) -> Book:
    """[쓰임] Vega(업비트) 장부 → 카드.
    원가는 동기화된 업비트 평균가(``live_costs``)가 있으면 그것."""
    expected = state.get("expected") or {}
    live = state.get("live_costs")  # live_sync: 업비트 앱 평균가 × 보유 (2026-10-20)
    costs = (
        {symbol: Decimal(str(cost)) for symbol, cost in live.items()}
        if isinstance(live, dict)
        else _stored_coin_costs(state.get("orders") or [])
    )
    signals = state.get("last_trade_signals") or {}
    holdings = []
    for coin in VEGA_COINS:
        shares = _to_float(expected.get(coin))
        if shares <= 0:
            continue
        price = quotes.krw.get(coin)
        signal = signals.get(coin) or {}
        weight = _to_float(signal.get("weight"))
        trend = "🟢 추세 켜짐" if weight > 0 else "🔴 추세 꺼짐"
        holdings.append(
            Holding(
                coin,
                shares,
                float(costs[coin]),
                None if price is None else shares * price,
                f"{trend} · 목표 {weight:.0%}",
                target=weight,
            )
        )
    deposits = sum(
        _to_float(row[1]) for row in state.get("deposits") or [] if isinstance(row, list)
    )
    cash = _to_float(expected.get("KRW"))
    book = Book("vega", "Vega", "업비트 · BTC · ETH · SOL", deposits, cash, tuple(holdings))
    units, peak = _to_float(state.get("units")), _to_float(state.get("peak"))
    notes = []
    drawdown: float | None = None
    if units > 0 and peak > 0:
        drawdown = max(0.0, 1 - book.value_krw / units / peak)
        drop = f"−{drawdown:.1%}" if drawdown >= 0.0005 else "0%"
        notes.append(f"고점 대비 {drop} (30% 면 새 매수 멈춤)")
    if state.get("stopped"):
        notes.append("⛔ 낙폭 정지 중 — 해제는 사람이")
    if state.get("last_decision"):
        notes.append(f"마지막 결정 {state['last_decision']} · 매일 09:05")
    activity = [
        (str(order.get("id", ""))[4:14], str(order.get("line", "")))
        for order in state.get("orders") or []
        if order.get("line")
    ]
    activity += [
        (str(row[0]), f"💵 입금 {_to_float(row[1]):,.0f} 원")
        for row in state.get("deposits") or []
        if isinstance(row, list)
    ]
    trades = [
        Trade(
            str(row[0]),
            "deposit",
            "KRW",
            amount=_to_float(row[1]),
            status="입금 기록",
            ref=f"vega:deposit:{row[0]}:{index}",
        )
        for index, row in enumerate(state.get("deposits") or [])
        if isinstance(row, list)
    ]
    for order in state.get("orders") or []:
        parts = str(order.get("identifier") or order.get("id") or "").split("-")
        coin = parts[-2] if len(parts) >= 3 else "?"
        volume = _to_float(order.get("executed_volume"))
        funds = _to_float(order.get("funds"))
        if volume > 0:
            state_text = "체결"
        elif order.get("state"):
            state_text = "체결 없음"
        else:
            state_text = "결과 확인 안 됨"
        trades.append(
            Trade(
                str(order.get("id", ""))[4:14],
                "buy" if order.get("side") == "bid" or parts[-1:] == ["B"] else "sell",
                coin,
                volume if volume > 0 else _to_float(order.get("volume")),
                funds / volume if volume > 0 else None,
                funds,
                _to_float(order.get("paid_fee")),
                "KRW",
                state_text,
                ref=f"vega:order:{order.get('identifier') or order.get('id')}",
            )
        )
    info = {
        "buying": sum(
            1
            for coin_name in VEGA_COINS
            if _to_float((signals.get(coin_name) or {}).get("weight")) > 0
        ),
        "coins": len(VEGA_COINS),
        "dd": drawdown,
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


SIRIUS = Path("data") / "sirius" / "state.json"


def sirius_book(state: Mapping[str, Any], quotes: Quotes) -> Book:
    """✨ Sirius — 토스 소수점 미장 주도주 (문서 279). 장부 = 이 전략 몫만(묶음별 lot)."""
    deposits = [
        row for row in state.get("deposits") or [] if isinstance(row, list) and len(row) >= 4
    ]
    deposited_krw = sum(_to_float(row[1]) for row in deposits)
    deposited_usd = sum(_to_float(row[2]) for row in deposits)
    avg_fx = deposited_krw / deposited_usd if deposited_usd > 0 else None
    fx = quotes.fx or avg_fx or 0.0
    agg: dict[str, list[float]] = {}
    for lot in state.get("lots") or []:
        totals = agg.setdefault(str(lot.get("sym")), [0.0, 0.0])
        totals[0] += _to_float(lot.get("qty"))
        totals[1] += _to_float(lot.get("cost_usd"))
    holdings = []
    for sym, (shares, cost) in sorted(agg.items()):
        price = quotes.usd.get(sym)
        holdings.append(
            Holding(
                sym,
                round(shares, 6),
                cost * (avg_fx or fx),
                None if price is None or not fx else shares * price * fx,
                avg_usd=cost / shares if shares > 0 else None,
            )
        )
    decisions = state.get("decisions") or {}
    notes = [f"남은 달러 ${_to_float(state.get('cash_usd')):,.2f}"]
    if decisions:
        month = max(decisions)
        decision = decisions[month]
        notes.append(f"{month} 결정: {', '.join(decision.get('picks') or [])}")
    else:
        notes.append("첫 결정 대기 — 매달 마지막 거래일 종가 기준, 다음 거래일 정규장에서 매수")
    pending = [
        f"{o.get('sym')} {'매수' if o.get('side') == 'BUY' else '매도'} 체결 확인 대기"
        for o in state.get("orders") or []
        if o.get("status") in ("SENT", "UNKNOWN")
    ]
    trades = tuple(
        Trade(
            str(row[0]),
            "deposit",
            "KRW",
            amount=_to_float(row[1]),
            status="입금 기록",
            note=f"${_to_float(row[2]):,.2f} · {_to_float(row[3]):,.1f}원/$",
            ref=f"sirius:deposit:{row[0]}:{index}",
            fx=_to_float(row[3]) or None,
        )
        for index, row in enumerate(deposits)
    )
    return Book(
        "sirius",
        "Sirius",
        "토스 · 미장 주도주 소수점",
        deposited_krw,
        _to_float(state.get("cash_usd")) * fx,
        tuple(holdings),
        tuple(notes),
        tuple(pending),
        tuple((str(row[0]), f"💵 입금 {_to_float(row[1]):,.0f} 원") for row in deposits),
        trades,
    )


def _kept_cost(cost: float, held: float, bought: float) -> float:
    """남은 몫의 원가 — 산 수량보다 적게 들고 있으면 이동평균으로 판 몫을 뺀다(다 팔면 0)."""
    if bought <= 0 or held >= bought - 1e-9:
        return cost
    return cost * max(held, 0.0) / bought


def accumulate_book(states: Iterable[Mapping[str, Any]], quotes: Quotes) -> Book:
    """[쓰임] 토스 자동 적립 장부들(미국 · 국내) → 카드 하나.
    [이유] 정리한(다 · 일부 판) 종목은 판 몫의 원가 · 수익을 이동평균으로 뺀다 (2026-10-20)."""
    holdings = []
    activity: list[tuple[str, str]] = []
    trades: list[Trade] = []
    total_cost = 0.0
    sold: list[str] = []
    for plan in states:
        sym = str(plan.get("symbol"))
        buys = [buy for buy in plan.get("buys") or [] if isinstance(buy, dict)]
        if plan.get("currency") == "KRW":  # 국내 적립 (scripts/toss_kr.py, 2026-10-10)
            krw_cost = sum(_to_float(buy.get("cost_krw")) for buy in buys)
            total_cost += krw_cost
            name = str(plan.get("name") or sym)
            activity += [
                (str(buy.get("date")), f"🛒 {name} {_to_float(buy.get('qty')):g}주 적립")
                for buy in buys
            ]
            for buy_index, buy in enumerate(buys):
                buy_qty = _to_float(buy.get("qty"))
                trades.append(
                    Trade(
                        str(buy.get("date")),
                        "buy",
                        name,
                        buy_qty,
                        _to_float(buy.get("cost_krw")) / buy_qty if buy_qty > 0 else None,
                        _to_float(buy.get("cost_krw")),
                        0.0,
                        "KRW",
                        "체결",
                        "국내",
                        ref=f"accumulate:{sym}:buy:{buy.get('date')}:{buy_index}",
                    )
                )
            held = _to_float(plan.get("last_qty"))
            bought_krw = sum(_to_float(buy.get("qty")) for buy in buys)
            kept = _kept_cost(krw_cost, held, bought_krw)
            if kept < krw_cost:  # 정리(다 · 일부 판) — 판 몫의 원가 · 손익을 뺀다 (2026-10-20)
                total_cost -= krw_cost - kept
                sold.append(name if held <= 0 else f"{name}(일부)")
            if held > 0:
                krw_price = quotes.usd.get(kr_ticker(plan))
                holdings.append(
                    Holding(name, held, kept, None if krw_price is None else held * krw_price)
                )
            continue
        # 'cost' 칸은 장부마다 단위가 달라(원 · 달러) 쓰지 않는다 — 달러 원가 × 그날 환율.
        cost = sum(_to_float(buy.get("cost_usd")) * _to_float(buy.get("fx_rate")) for buy in buys)
        total_cost += cost
        activity += [
            (str(buy.get("date")), f"🛒 {sym} {_to_float(buy.get('qty')):g}주 적립") for buy in buys
        ]
        for buy_index, buy in enumerate(buys):
            buy_qty = _to_float(buy.get("qty"))
            trades.append(
                Trade(
                    str(buy.get("date")),
                    "buy",
                    sym,
                    buy_qty,
                    _to_float(buy.get("cost_usd")) / buy_qty if buy_qty > 0 else None,
                    _to_float(buy.get("cost_usd")),
                    0.0,
                    "USD",
                    "체결",
                    f"{_to_float(buy.get('fx_rate')):,.1f}원/$",
                    ref=f"accumulate:{sym}:buy:{buy.get('date')}:{buy_index}",
                    fx=_to_float(buy.get("fx_rate")) or None,
                )
            )
        held = _to_float(plan.get("last_qty"))
        bought = sum(_to_float(buy.get("qty")) for buy in buys)
        mine = max(
            held - _to_float(plan.get("baseline_qty")), 0.0
        )  # 적립으로 산 몫 (기존 보유 빼고)
        kept = _kept_cost(cost, mine, bought)
        if kept < cost:
            # 정리한 종목(다 · 일부 판) — 판 몫의 원가 · 손익을 뺀다: '넣은 돈' 에서 빼 가짜 손실 ·
            # 이익이 안 보이게 (2026-10-16 다 판 장부, 2026-10-20 사용자 '정리한 종목 수익 빼줘')
            total_cost -= cost - kept
            sold.append(sym if mine <= 0 else f"{sym}(일부)")
            cost = kept
        if held <= 0 or (mine <= 0 and buys):
            continue  # 적립 몫을 다 정리 — 남은 것은 기존 보유(장부 밖)라 카드에서 뺀다
        price = quotes.usd.get(sym)
        usd = sum(_to_float(buy.get("cost_usd")) for buy in buys)
        holdings.append(
            Holding(
                sym,
                held,
                cost,
                None if price is None or quotes.fx is None else held * price * quotes.fx,
                # 기존 보유분(장부 밖 원가)이 섞이면 평균 단가를 셀 수 없다
                avg_usd=usd / held if bought > 0 and abs(bought - held) < 1e-9 else None,
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
            *(
                [f"정리한 종목 {', '.join(sold)} — 판 몫의 원가 · 수익은 뺌 (판 돈은 토스 현금)"]
                if sold
                else []
            ),
        ),
        (),
        tuple(activity),
        tuple(trades),
        info={"last_buy": max((d for d, _ in activity), default="")},
    )


def _toss_states(root: Path) -> list[dict[str, Any]]:
    """[쓰임] ``data/toss/`` 의 적립 장부들 (Deneb · QS 전략 장부 등은 뺀다)."""
    out = []
    for path in sorted((root / "data" / "toss").glob("*.json")):
        if path.stem in _SKIP:
            continue
        state = _read(path)
        if state and state.get("symbol"):
            out.append(state)
    return out


def kr_ticker(state: Mapping[str, Any]) -> str:
    """국내 적립 장부 → 야후 시세 기호 (코스닥 .KQ · 그 밖 .KS, 값은 원)."""
    return f"{state.get('symbol')}.{'KQ' if state.get('market') == 'KOSDAQ' else 'KS'}"


def needed(root: Path) -> tuple[set[str], set[str]]:
    """시세가 필요한 (코인, 미국 종목 · 국내는 .KS · .KQ 기호)."""
    tickers = set(DENEB_TICKERS) | {
        kr_ticker(state) if state.get("currency") == "KRW" else str(state["symbol"])
        for state in _toss_states(root)
    }
    sirius = _read(root / SIRIUS)
    if sirius:
        tickers |= {str(lot.get("sym")) for lot in sirius.get("lots") or []}
    return set(VEGA_COINS), tickers


def _mtime_ms(*paths: Path) -> int:
    """[쓰임] 파일들 중 가장 늦은 수정 시각(ms) — 카드의 '장부 업데이트'."""
    best = 0
    for path in paths:
        try:
            best = max(best, int(path.stat().st_mtime * 1000))
        except OSError:
            continue
    return best


def build_books(root: Path, quotes: Quotes, *, sync: bool = True) -> list[Book]:
    """장부 → 카드. ``sync`` 면 그 순간의 실제 계좌로 맞춘 사본을 쓴다(:mod:`live_sync`,
    사용자 2026-10-20 '항시 모든 현황 알림은 동기화'). 장부 파일은 고치지 않는다."""
    from auto_trading.ui import live_sync

    out = []
    toss = root / "data" / "toss"
    deneb = _read(toss / "DENEB.json")
    plans = _toss_states(root)
    vega_path = root / "data" / "upbit_t2" / "state.json"
    snapshot = live_sync.load(root, deneb, plans) if sync else None
    sync_note = tuple(f"⚠ {note}" for note in (snapshot.notes if snapshot else []))
    if snapshot is not None and deneb:
        deneb = live_sync.sync_deneb_ledger_to_toss(deneb, snapshot.toss)
    if snapshot is not None:
        plans = [
            live_sync.sync_accumulate_ledger_to_toss(
                plan, snapshot.toss, quotes.fx, snapshot.toss_qty
            )
            for plan in plans
        ]
    if deneb:
        book = deneb_book(deneb, quotes)
        halt = root / "data" / "HALT_deneb"
        # 전부 정리 · 텔레그램 정지 — 새 매수 안 함 (2026-10-16 사용자: 정리했는데 산 것처럼 보임)
        if halt.exists():
            why = halt.read_text(encoding="utf-8", errors="replace").strip()[:60]
            book = replace(book, notes=(f"⏸ 정지 중 — 새 매수 안 함 ({why})", *book.notes))
        out.append(replace(book, updated_ms=_mtime_ms(toss / "DENEB.json")))
    vega = _read(vega_path) if SHOW_UPBIT else {}
    if vega:
        synced = snapshot is not None and snapshot.upbit is not None
        if synced and snapshot is not None and snapshot.upbit is not None:
            vega = live_sync.sync_vega_ledger_to_upbit(vega, snapshot.upbit)
        book = vega_book(vega, quotes)
        if synced:
            book = replace(book, notes=(*book.notes, "업비트 실제 잔고 · 평균가 기준"))
        stamp = int(snapshot.at * 1000) if synced and snapshot is not None else _mtime_ms(vega_path)
        out.append(replace(book, updated_ms=stamp))
    sirius = _read(root / SIRIUS)
    if sirius:
        book = sirius_book(sirius, quotes)
        if (root / "data" / "HALT_sirius").exists():
            book = replace(book, notes=("⏸ 정지 중 — 새 주문 안 함", *book.notes))
        out.append(replace(book, updated_ms=_mtime_ms(root / SIRIUS)))
    acc = plans
    if acc:
        files = [
            path for path in toss.glob("*.json") if path.stem not in _SKIP and path.stem != "M3"
        ]
        out.append(replace(accumulate_book(acc, quotes), updated_ms=_mtime_ms(*files)))
    if sync_note and out:
        out[0] = replace(out[0], notes=(*sync_note, *out[0].notes))
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
    return [by_day[day] for day in sorted(by_day)]


# ---------------------------------------------------------------- 화면


def _won(amount: float) -> str:
    """[쓰임] 원화 금액 글 (쉼표)."""
    return f"{amount:,.0f}"


def _pl(value: float, base: float) -> str:
    """[쓰임] 손익 HTML (색 · 부호 · %)."""
    diff = value - base
    pct = f" ({diff / base:+.2%})" if base > 0 else ""
    cls = "pos" if diff > 0 else "neg" if diff < 0 else "mute"
    return f'<span class="{cls}">{diff:+,.0f} 원{pct}</span>'


def _priced(book: Book, quotes: Quotes) -> bool:
    """표시 품질만 판단한다. 달러 현금의 환율 대체도 확정 평가로 표시하지 않는다."""
    return book.priced and (
        book.key not in {"deneb", "accumulate", "sirius"} or quotes.fx is not None
    )


def render_live(
    books: list[Book],
    quotes: Quotes,
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
        books, quotes, now_ms, hide=hide, onboarded=onboarded, history=history, show_detail=detail
    )


QuoteSource = Callable[[set[str], set[str]], Quotes]


def _default_quotes(coins: set[str], tickers: set[str]) -> Quotes:
    """[쓰임] 기본 시세 가져오기 (시험이 바꿔 끼울 수 있게 따로 둔 함수)."""
    return fetch_quotes(coins, tickers)


quote_source: QuoteSource = _default_quotes
"""시험에서 바꿔 끼운다."""


def live_page(data: DashboardData) -> str:
    """[쓰임] 대시보드 '실거래' 쪽 HTML (장부 → 카드 → 오르빗 화면)."""
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
    quotes = quote_source(coins, tickers)
    return render_live(build_books(data.root, quotes), quotes, data.now_ms, **flags)


# ---------------------------------------------------------------- 텔레그램 '현황' 답


_ICON = {"deneb": "🌟", "vega": "🌌", "sirius": "✨", "accumulate": "💰"}


def _pl_text(value: float, base: float) -> str:
    """[쓰임] 손익 글 (텔레그램용 — 🔺 · 🔻 · %)."""
    diff = value - base
    mark = "🔺" if diff > 0 else "🔻" if diff < 0 else "▫️"
    pct = f" ({diff / base:+.2%})" if base > 0 else ""
    return f"{mark} {diff:+,.0f}원{pct}"


def summary_text(books: list[Book], quotes: Quotes, now_ms: int) -> str:
    """실거래 홈을 휴대폰 한 통으로 (순수). 텔레그램 '현황' 명령의 답."""
    from auto_trading.notify.style import RULE, sub

    now = (datetime.fromtimestamp(now_ms / 1000, UTC) + _KST).strftime("%m/%d %H:%M")
    if not books:
        return f"📊 [실거래 현황] {now}\n실거래 장부가 없어요"
    deposited = sum(book.deposits_krw for book in books)
    valued = sum(book.value_krw for book in books)
    lines = [
        f"📊 [실거래 현황] {now}",
        f"💼 총 입금 {deposited:,.0f}원 → 평가 {valued:,.0f}원",
        sub(f"손익 {_pl_text(valued, deposited)}"),
    ]
    for book in books:
        lines += [
            "",
            RULE,
            f"{_ICON.get(book.key, '■')} {book.title} · {book.venue.split(' · ')[0]}",
            f"{book.deposits_krw:,.0f}원 → {book.value_krw:,.0f}원"
            f" · {_pl_text(book.value_krw, book.deposits_krw)}",
        ]
        for holding in book.holdings:
            trend = holding.note[:1] + " " if holding.note[:1] in ("🟢", "🔴") else ""
            qty = f"{holding.qty:g}주 " if book.key != "vega" else ""
            if holding.value_krw is None:
                lines.append(
                    sub(f"{trend}{holding.name} {qty}원가 {holding.cost_krw:,.0f}원 (시세 없음)")
                )
            else:
                pct = (
                    f"{holding.value_krw / holding.cost_krw - 1:+.1%}"
                    if holding.cost_krw > 0
                    else "—"
                )
                lines.append(sub(f"{trend}{holding.name} {qty}{holding.value_krw:,.0f}원 · {pct}"))
        if book.cash_krw:
            lines.append(sub(f"현금 {book.cash_krw:,.0f}원"))
        lines += [sub(f"⏳ {pending_line}") for pending_line in book.pending]
    fx = f"원/달러 {quotes.fx:,.1f}" if quotes.fx else "환율 없음"
    lines += ["", RULE, f"공개 시세 · {fx} · 세금 전"]
    lines += [f"⚠ {error}" for error in quotes.errors]
    return "\n".join(lines)
