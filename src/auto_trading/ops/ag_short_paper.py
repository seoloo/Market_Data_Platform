"""AGS Paper — AG-CR(AG3) Paper 롱 + 문서 232 숏을 가상으로 붙인 관찰 장부 (주문 없음, I/O 없음).

* 롱: 기존 AG-CR Paper 원장(``ag_cr3``)의 날마다 평가액 · 코인별 목표 비중을 **그대로** 쓴다.
* 숏: 232 와 같은 규칙. 4 시간봉 RSI7 이 70 위 → 아래로 꺾이고,
  끝난 마지막 일봉 ADX14 > 25 · −DI > +DI.
  **그 코인의 AG 롱 목표가 0 일 때만** 다음 4 시간봉 시가에 숏. 3% 손절 · 48 시간 · 롱 목표가 다시
  0 보다 커지면 먼저 정리. 크기는 증거금 equity 10% × 배율 · 거래 위험 1% · AG 장부의 남은 증거금 중
  작은 값. 4 연패 → 240 분 쉼, 청산 뒤 60 분 쉼, 하루 손실 2% 면 새 숏 금지,
  닫힌 숏 누적 손실이 자기 고점에서 계좌 고점 × 2.5% 이상이면 숏 영구 차단(232 OWN).
* 합친 평가액 = AG-CR 평가액 + 숏 누적 손익. 숏 손익이 AG 롱 크기에 다시 들어가지는 않는다
  (232 연구 엔진과 다른 점 — 장부를 읽기 쉽게 더하기로만 합친다).

연구(232)는 1 분봉 지정가 · 부분 체결 엔진이고, 이 장부는 4 시간봉 시가 체결 근사다.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from auto_trading.research import indicators_229 as ind

__all__ = [
    "AgDay",
    "Bar",
    "Book",
    "replay",
    "short_signals",
]

H4 = 4 * 3_600_000
DAY = 86_400_000
MINUTE = 60_000
FEE = 0.0005
STOP_SLIP = 0.00245
STOP_PCT = 0.03
HOLD_MS = 48 * 3_600_000
OWN_LIMIT = 0.025
SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class Bar:
    t: int
    o: float
    h: float
    low: float
    c: float


@dataclass(frozen=True)
class AgDay:
    """AG-CR Paper 하루 (``decision_ms`` = 그날 마감). 다음 날 0 시(UTC)부터 효력."""

    decision_ms: int
    equity: float
    target: Mapping[str, float]
    wallet: float
    spot_qty: Mapping[str, float]
    fut_qty: Mapping[str, float]
    exposure: float = 3.0


def short_signals(bars: Sequence[Bar], daily: Sequence[Bar]) -> set[int]:
    """신호가 난 4 시간봉 인덱스(그 봉 마감에 판단 → 다음 봉 시가 진입)."""
    closes = [b.c for b in bars]
    r7 = ind.rsi(closes, 7)
    adx, pdi, mdi = ind.adx([d.h for d in daily], [d.low for d in daily], [d.c for d in daily])
    day_index = {d.t // DAY: j for j, d in enumerate(daily)}
    out = set()
    for i in range(1, len(bars)):
        a, b = r7[i - 1], r7[i]
        if math.isnan(a) or math.isnan(b) or not a >= 70 > b:
            continue
        j = day_index.get((bars[i].t + H4) // DAY - 1)  # 이 봉 마감 때 끝나 있는 마지막 일봉
        if j is None or any(math.isnan(x[j]) for x in (adx, pdi, mdi)):
            continue
        if adx[j] > 25 and mdi[j] > pdi[j]:
            out.add(i)
    return out


@dataclass
class Short:
    symbol: str
    qty: float
    entry: float
    entry_ms: int
    stop: float
    funding: float = 0.0
    fee: float = 0.0


@dataclass
class Book:
    name: str
    leverage: float
    realized: float = 0.0
    open: dict[str, Short] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    cum: float = 0.0
    top: float = 0.0
    halted_ms: int | None = None
    streak: int = 0
    cooldown_ms: int = -1
    peak: float = 0.0
    day_start: float = 0.0
    day: int = -1
    equity_path: list[tuple[int, float]] = field(default_factory=list)


def _ag_at(days: Sequence[AgDay], ms: int) -> AgDay | None:
    k = bisect.bisect_left([d.decision_ms for d in days], ms) - 1
    return days[k] if k >= 0 else None


def _unreal(book: Book, px: Mapping[str, float]) -> float:
    return sum(s.qty * (s.entry - px[s.symbol]) for s in book.open.values())


def _close(book: Book, s: Short, at_ms: int, px: float, reason: str) -> None:
    fee = s.qty * px * FEE
    gross = s.qty * (s.entry - px)
    net = gross + s.funding - s.fee - fee
    book.realized += net
    del book.open[s.symbol]
    book.trades.append(
        {
            "symbol": s.symbol,
            "entry_ms": s.entry_ms,
            "exit_ms": at_ms,
            "entry": s.entry,
            "exit": px,
            "qty": s.qty,
            "net": net,
            "funding": s.funding,
            "reason": reason,
        }
    )
    book.cooldown_ms = max(book.cooldown_ms, at_ms + 61 * MINUTE)
    book.streak = book.streak + 1 if net < 0 else 0
    if book.streak >= 4:
        book.cooldown_ms = max(book.cooldown_ms, at_ms + 241 * MINUTE)
        book.streak = 0
    book.cum += net
    book.top = max(book.top, book.cum)
    if book.halted_ms is None and book.top - book.cum >= OWN_LIMIT * book.peak:
        book.halted_ms = at_ms


def _size(
    book: Book, ag: AgDay, equity: float, px: Mapping[str, float], sym: str, rate: float
) -> float:
    price = px[sym]
    lev = book.leverage
    short_margin = sum(s.qty * px[s.symbol] / lev for s in book.open.values())
    risk_pct = STOP_PCT + 0.0039 + max(0.0, -rate) * 6
    spot_value = sum(q * px[k] for k, q in ag.spot_qty.items())
    long_margin = sum(abs(q) * px[k] for k, q in ag.fut_qty.items()) / ag.exposure
    ag_unreal = ag.equity - ag.wallet - spot_value
    free = max(
        0.0, ag.wallet + min(0.0, ag_unreal) + book.realized - long_margin - 1.5 * short_margin
    )
    notional = min(
        equity * 0.10 * lev,
        max(0.0, 0.10 * equity - short_margin) * lev,
        0.01 * equity / risk_pct,
        free * lev / 1.5,
    )
    return max(0.0, notional) / price


def replay(
    book: Book,
    *,
    start_ms: int,
    bars: Mapping[str, Sequence[Bar]],
    signals: Mapping[str, set[int]],
    ag_days: Sequence[AgDay],
    funding: Mapping[str, Sequence[tuple[int, float]]],
) -> Book:
    """``start_ms`` 이후 4 시간봉을 처음부터 다시 돈다(같은 입력이면 같은 결과)."""
    times = sorted(set.intersection(*({b.t for b in bars[s]} for s in SYMBOLS)))
    index = {s: {b.t: i for i, b in enumerate(bars[s])} for s in SYMBOLS}
    first_equity: float | None = None
    for t in times:
        if t < start_ms:
            continue
        ag = _ag_at(ag_days, t)
        if ag is None:
            continue
        bar = {s: bars[s][index[s][t]] for s in SYMBOLS}
        px_open = {s: bar[s].o for s in SYMBOLS}
        equity = ag.equity + book.realized + _unreal(book, px_open)
        if first_equity is None:
            first_equity = equity
            book.peak = equity
        if t // DAY != book.day:
            book.day, book.day_start = t // DAY, equity
        # 1) 펀딩 (이 봉 안의 정산, 봉 시가 근사)
        for s in list(book.open.values()):
            for at, rate in funding.get(s.symbol, ()):
                if t <= at < t + H4:
                    s.funding += s.qty * bar[s.symbol].o * rate
        # 2) 시가 청산: 롱 복귀 · 48 시간
        for s in list(book.open.values()):
            if ag.target.get(s.symbol, 0.0) > 0:
                _close(book, s, t, bar[s.symbol].o, "LONG_PRIORITY")
            elif t >= s.entry_ms + HOLD_MS:
                _close(book, s, t, bar[s.symbol].o, "48H")
        # 3) 시가 진입: 직전 봉 신호
        for sym in SYMBOLS:
            i = index[sym][t]
            if sym in book.open or (i - 1) not in signals.get(sym, set()):
                continue
            why = None
            if ag.target.get(sym, 0.0) > 0:
                why = "LONG_TARGET_POSITIVE"
            elif book.halted_ms is not None:
                why = "OWN_HALT"
            elif t < book.cooldown_ms:
                why = "COOLDOWN"
            elif equity <= book.day_start * 0.98:
                why = "DAILY_LOSS"
            rates = [r for at, r in funding.get(sym, ()) if at <= t]
            qty = 0.0 if why else _size(book, ag, equity, px_open, sym, rates[-1] if rates else 0.0)
            if not why and qty <= 0:
                why = "NO_ROOM"
            book.events.append({"at_ms": t, "symbol": sym, "signal": True, "skipped": why})
            if why:
                continue
            price = bar[sym].o
            book.open[sym] = Short(
                sym, qty, price, t, price * (1 + STOP_PCT), fee=qty * price * FEE
            )
        # 4) 봉 안 손절
        for s in list(book.open.values()):
            if bar[s.symbol].h >= s.stop:
                px = max(bar[s.symbol].o, s.stop) * (1 + STOP_SLIP)
                _close(book, s, t + H4 - 1, px, "STOP")
        px_close = {s: bar[s].c for s in SYMBOLS}
        equity_close = ag.equity + book.realized + _unreal(book, px_close)
        book.peak = max(book.peak, equity_close)
        book.equity_path.append((t + H4, equity_close))
    return book
