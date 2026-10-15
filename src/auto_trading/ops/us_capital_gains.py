"""해외주식 양도소득 추정 (한국 거주자, 순수 계산) — 신고용이 아니라 연중 판단용.

* 양도차익 = 매도 금액(원) − 취득 금액(원) − 경비(수수료 · 거래소 세금). 원화 환산은 **결제일**
  매매기준율(한국은행 ECOS). 그 날 값이 없으면 바로 앞 영업일 값.
* 연도는 **매도 결제일**의 해. 같은 해 모든 해외 종목의 손익을 합치고(손실 상계),
  250 만 원 기본공제 뒤 22%(지방세 포함). 손실은 다음 해로 넘어가지 않는다.
* 취득가는 **이동평균**(토스 앱 평균 단가와 같은 방식)을 기본으로, **선입선출** 을 참고로 함께 낸다.
  실제 신고에 어느 방식이 쓰이는지는 토스 연간 양도세 자료로 대조한다.
* 주문 기록보다 먼저 가진 주식(다른 증권사에서 옮겨 옴 등)은 ``openings`` 로 넣는다 —
  토스 평균 단가 × 가장 이른 환율로 어림하고 결과에 '추정' 으로 표시한다.
"""

from __future__ import annotations

import bisect
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEDUCTION_KRW = 2_500_000.0
TAX_RATE = 0.22
NY = ZoneInfo("America/New_York")
# NYSE 휴장 + DTC 결제 휴일(콜럼버스 · 재향군인의 날 — 장은 열지만 결제는 쉼)
_HOLIDAYS = (
    "2024-01-01 2024-01-15 2024-02-19 2024-03-29 2024-05-27 2024-06-19 2024-07-04 2024-09-02 "
    "2024-10-14 2024-11-11 2024-11-28 2024-12-25 "
    "2025-01-01 2025-01-09 2025-01-20 2025-02-17 2025-04-18 2025-05-26 2025-06-19 2025-07-04 "
    "2025-09-01 2025-10-13 2025-11-11 2025-11-27 2025-12-25 "
    "2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07 "
    "2026-10-12 2026-11-11 2026-11-26 2026-12-25 "
    "2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 2027-06-18 2027-07-05 2027-09-06 "
    "2027-10-11 2027-11-11 2027-11-25 2027-12-24"
)
SETTLE_HOLIDAYS = frozenset(_HOLIDAYS.split())


def tax_settle(filled_at: str) -> str:
    """체결 시각(ISO, 시간대 포함) → 양도세 결제일 (뉴욕 거래일 + 2 영업일).

    토스 양도세 화면 설명: "구매 · 판매 후 2 영업일 뒤인 결제일의 기준 환율". 2026 실제 계좌에서
    이 기준이 토스 숫자와 1.1% 안으로 맞았다(미국 T+1 결제일로는 6.6% 차이). 토스 주문 목록의
    ``settlementDate`` 가 비어 와서 직접 센다. 휴일표 밖의 해는 주말만 건너뛴다.
    """
    day = datetime.fromisoformat(filled_at).astimezone(NY).date()
    left = 2
    while left:
        day += timedelta(days=1)
        if day.weekday() < 5 and day.isoformat() not in SETTLE_HOLIDAYS:
            left -= 1
    return day.isoformat()


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    side: str
    """BUY · SELL."""
    qty: float
    amount_usd: float
    fee_usd: float
    settle: str
    """결제일 YYYY-MM-DD."""
    at: str
    """체결 시각 (정렬용)."""
    tag: str = ""
    """보고용 묶음 (예: 'M3')."""


@dataclass(frozen=True, slots=True)
class Opening:
    """주문 기록보다 먼저 가진 주식 — 평균 단가로 어림."""

    symbol: str
    qty: float
    avg_usd: float
    date: str


@dataclass(slots=True)
class Realized:
    symbol: str
    settle: str
    qty: float
    proceeds_krw: float
    cost_krw: float
    tag: str
    estimated: bool

    @property
    def gain_krw(self) -> float:
        return self.proceeds_krw - self.cost_krw


@dataclass(slots=True)
class Book:
    realized: list[Realized] = field(default_factory=list)
    realized_fifo: list[Realized] = field(default_factory=list)
    qty: dict[str, float] = field(default_factory=dict)
    cost_krw: dict[str, float] = field(default_factory=dict)
    """이동평균 남은 원가(원)."""
    estimated: set[str] = field(default_factory=set)
    """원가에 어림(openings)이 섞인 종목."""
    unmatched: list[str] = field(default_factory=list)
    """기록보다 많이 판 매도 (원가 모름)."""


def fx_on(rates: Mapping[str, float], day: str) -> float:
    """``day`` 의 매매기준율, 없으면 바로 앞 날짜 값. 앞 값이 하나도 없으면 가장 이른 값."""
    keys = sorted(rates)
    if not keys:
        raise ValueError("환율 자료가 없다")
    i = bisect.bisect_right(keys, day) - 1
    return rates[keys[max(i, 0)]]


def build(
    trades: Iterable[Trade], rates: Mapping[str, float], openings: Sequence[Opening] = ()
) -> Book:
    """체결 기록 → 이동평균 · 선입선출 실현 손익과 남은 원가."""
    book = Book()
    lots: dict[str, deque[list[float]]] = defaultdict(deque)  # [수량, 주당 원가(원)]
    for op in openings:
        if op.qty <= 0:
            continue
        unit = op.avg_usd * fx_on(rates, op.date)
        book.qty[op.symbol] = book.qty.get(op.symbol, 0.0) + op.qty
        book.cost_krw[op.symbol] = book.cost_krw.get(op.symbol, 0.0) + op.qty * unit
        lots[op.symbol].append([op.qty, unit])
        book.estimated.add(op.symbol)
    for t in sorted(trades, key=lambda x: (x.at, x.side != "BUY")):
        fx = fx_on(rates, t.settle)
        q0, c0 = book.qty.get(t.symbol, 0.0), book.cost_krw.get(t.symbol, 0.0)
        if t.side == "BUY":
            cost = (t.amount_usd + t.fee_usd) * fx
            book.qty[t.symbol], book.cost_krw[t.symbol] = q0 + t.qty, c0 + cost
            lots[t.symbol].append([t.qty, cost / t.qty])
            continue
        proceeds = (t.amount_usd - t.fee_usd) * fx
        sell = t.qty
        if q0 + 1e-9 < sell:
            book.unmatched.append(f"{t.symbol} {t.settle} {sell - q0:g}주 원가 모름")
            sell_known = q0
        else:
            sell_known = sell
        avg_cost = c0 / q0 * sell_known if q0 > 0 else 0.0
        est = t.symbol in book.estimated
        book.realized.append(Realized(t.symbol, t.settle, t.qty, proceeds, avg_cost, t.tag, est))
        book.qty[t.symbol] = max(q0 - sell_known, 0.0)
        book.cost_krw[t.symbol] = max(c0 - avg_cost, 0.0)
        # 선입선출 (참고)
        left, fifo_cost = sell_known, 0.0
        while left > 1e-9 and lots[t.symbol]:
            lot = lots[t.symbol][0]
            take = min(left, lot[0])
            fifo_cost += take * lot[1]
            lot[0] -= take
            left -= take
            if lot[0] <= 1e-9:
                lots[t.symbol].popleft()
        book.realized_fifo.append(
            Realized(t.symbol, t.settle, t.qty, proceeds, fifo_cost, t.tag, est)
        )
    return book


def year_summary(book: Book, year: str) -> dict[str, float]:
    """그 해(매도 결제일 기준) 합계 · 공제 남음 · 예상 세금 (이동평균 · 선입선출).

    ``tax_free_room_krw`` = 올해 세금 없이 더 실현할 수 있는 이익 (손실이 있으면 공제보다 크다).
    """

    def tot(rows: Sequence[Realized]) -> float:
        return sum(r.gain_krw for r in rows if r.settle[:4] == year)

    gain, fifo = tot(book.realized), tot(book.realized_fifo)
    return {
        "gain_krw": gain,
        "gain_fifo_krw": fifo,
        "deduction_left_krw": min(max(DEDUCTION_KRW - gain, 0.0), DEDUCTION_KRW),
        "tax_free_room_krw": max(DEDUCTION_KRW - gain, 0.0),
        "tax_krw": max(gain - DEDUCTION_KRW, 0.0) * TAX_RATE,
        "tax_fifo_krw": max(fifo - DEDUCTION_KRW, 0.0) * TAX_RATE,
    }


def by_tag(book: Book, year: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in book.realized:
        if r.settle[:4] == year:
            out[r.tag or "개별"] += r.gain_krw
    return dict(out)


def unrealized(
    book: Book, prices_usd: Mapping[str, float], fx_today: float
) -> dict[str, dict[str, float]]:
    """보유 종목마다 지금 팔면 생길 손익(원, 이동평균, 매도 경비 뺌 전)."""
    out = {}
    for sym, q in book.qty.items():
        if q <= 1e-9 or sym not in prices_usd:
            continue
        value = q * prices_usd[sym] * fx_today
        out[sym] = {"qty": q, "value_krw": value, "gain_krw": value - book.cost_krw.get(sym, 0.0)}
    return out


def tax_lines(
    *, gain_krw: float, deneb_krw: float, tax_krw: float, room_krw: float, unreal_krw: float
) -> list[str]:
    """매달 Deneb 알림 아래 양도세 줄 (순수)."""
    from auto_trading.notify.style import sub

    return [
        "🧾 올해 해외주식 양도 (추정 · 토스 결제일 환율)",
        f"· 실현 손익 {gain_krw:+,.0f}원",
        sub(f"Deneb {deneb_krw:+,.0f}원 · 그 밖 {gain_krw - deneb_krw:+,.0f}원"),
        f"· 예상 세금 {tax_krw:,.0f}원 (250만 원 공제 뒤 22%)",
        f"· 세금 없이 더 실현할 수 있는 이익 {room_krw:,.0f}원",
        f"· 아직 안 판 평가 손익 {unreal_krw:+,.0f}원",
    ]
