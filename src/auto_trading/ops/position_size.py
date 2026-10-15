"""개별주 매수 비중 추천 — '틀리면 계좌의 1% 까지만 잃게' (I/O 없음).

사용자 (2026-10-15): "이 종목을 현재 돈의 몇 퍼센트 비율로 구매하면 될지를 파악해서 추천" →
기준 돈 = 토스 계좌의 원화 현금 + 달러 현금, 한 번에 잃어도 되는 돈 = 기준의 1%,
손절 = 산 종목이 −3% 면 자동 매도, 한 종목 상한 25%, 매수는 봇 버튼 · 장 시작에.

* 비중 = min(1% ÷ 3% = 33%, 상한 25%). 손절이 모든 종목 −3% 라 보통은 상한 25% 가 걸린다.
* 지수(미국 SPY · 국내 KODEX 200)가 200 일선 아래면 절반 — 연구 237: 개별 주도주 규칙은
  시장 조건 없이 사면 하락장에 크게 잃었다.
* 실적 발표가 3 거래일 안이면 사지 않는다(발표 갭은 −3% 손절로 못 막는다).
* 추세(20 · 60 · 120 일 수익 부호 평균 > 0 그리고 종가 > 50 일선)가 아니면 사지 않는다.
* Deneb 의 이월 달러(T 브레이크로 팔고 기다리는 돈)는 기준 돈 · 쓸 돈에서 뺀다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

__all__ = [
    "CAP",
    "EARN_BLOCK_DAYS",
    "GAP_UP_LIMIT",
    "PRICE_BUFFER",
    "RISK",
    "STOP",
    "Advice",
    "Inputs",
    "advice_text",
    "base_krw",
    "buy_limit",
    "recommend",
    "round_price",
    "stop_price",
    "trend_check",
]

RISK = 0.01
"""한 번에 잃어도 되는 돈 — 기준 돈의 1% (사용자)."""
STOP = 0.03
"""손절 — 산 값보다 3% 아래 (사용자, 종목 기준)."""
CAP = 0.25
"""한 종목 상한 — 기준 돈의 25% (사용자 2026-10-15)."""
EARN_BLOCK_DAYS = 3
"""실적 발표가 이 거래일 안이면 사지 않는다."""
PRICE_BUFFER = 0.005
"""수량을 셀 때 쓰는 가격 여유 — 장 시작 때 조금 올라도 돈이 모자라지 않게."""
GAP_UP_LIMIT = 0.03
"""장 시작 가격이 추천 때보다 이만큼 넘게 오르면 사지 않고 다시 묻는다."""
LOOKS = (20, 60, 120)
MA_SHORT = 50
MA_LONG = 200


def is_kr(symbol: str) -> bool:
    return symbol.isdigit() and len(symbol) == 6


def _kr_tick(price: float) -> int:
    """유가증권 · 코스닥 호가 단위 (2023-01 개편, 보통주)."""
    for top, step in (
        (2_000, 1),
        (5_000, 5),
        (20_000, 10),
        (50_000, 50),
        (200_000, 100),
        (500_000, 500),
    ):
        if price < top:
            return step
    return 1_000


def round_price(price: float, kr: bool, *, up: bool) -> float:
    """주문 가격 자릿수 — 국내 호가 단위, 미국 $1 이상 0.01 · 미만 0.0001."""
    step = float(_kr_tick(price)) if kr else (0.01 if price >= 1 else 0.0001)
    n = price / step
    k = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(k * step, 0 if kr else (2 if step == 0.01 else 4))


def buy_limit(price: float, kr: bool) -> float:
    """수량을 셀 때 쓰는 지정가 = 지금 가격 + 0.5%, 호가 단위로 올림."""
    return round_price(price * (1 + PRICE_BUFFER), kr, up=True)


def stop_price(avg: float, kr: bool) -> float:
    """손절가 = 평균 단가 −3%, 호가 단위로 내림."""
    return round_price(avg * (1 - STOP), kr, up=False)


def base_krw(krw_cash: float, usd_cash: float, fx: float, deneb_usd: float) -> float:
    """기준 돈 = 원화 현금 + (달러 현금 − Deneb 이월 달러) × 환율. 보유 주식은 넣지 않는다."""
    return krw_cash + max(0.0, usd_cash - deneb_usd) * fx


def _sma(xs: Sequence[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def trend_check(closes: Sequence[float]) -> tuple[float | None, bool | None]:
    """(추세 점수 −1 ~ 1, 추세 맞음). 자료가 모자라면 (None, None)."""
    if len(closes) <= max(LOOKS):
        return None, None
    last = closes[-1]
    signs = [1.0 if last > closes[-1 - n] else -1.0 for n in LOOKS]
    score = sum(signs) / len(signs)
    ma = _sma(closes, MA_SHORT)
    return round(score, 2), bool(score > 0 and ma is not None and last > ma)


def above_long(closes: Sequence[float]) -> bool | None:
    ma = _sma(closes, MA_LONG)
    return None if ma is None else closes[-1] > ma


@dataclass(frozen=True, slots=True)
class Inputs:
    symbol: str
    name: str
    price: float
    """지금 가격 (종목 통화)."""
    krw_cash: float
    usd_cash: float
    fx: float
    """원/달러."""
    deneb_usd: float = 0.0
    closes: Sequence[float] = ()
    """종목 일봉 종가 (오래된 것 먼저, 120 개 넘게)."""
    index_name: str = ""
    index_closes: Sequence[float] = ()
    """지수 대신 쓰는 ETF 종가 (200 개 이상)."""
    earnings_date: str | None = None
    """가장 가까운 실적 발표일 (YYYY-MM-DD), 모르면 None, 다음 8 일 안에 없으면 빈 문자열."""
    earnings_in_days: int | None = None
    """오늘부터 그 발표일까지 거래일 수 (오늘 = 0)."""
    held_qty: float = 0.0
    """이미 갖고 있는 같은 종목 수량."""
    us_stock_krw: float = 0.0
    """지금 들고 있는 미국 주식 평가액 (원)."""
    blocked_symbol: str = ""
    """자동 전략이 쓰는 종목이면 그 이름 (예: 'Deneb')."""


@dataclass(slots=True)
class Advice:
    symbol: str
    name: str
    currency: str
    price: float
    base_krw: float
    weight: float
    """최종 비중 (기준 돈 대비)."""
    limit: float
    """수량을 센 지정가."""
    qty: int
    amount_krw: float
    stop: float
    risk_krw: float
    lines: list[str] = field(default_factory=list)
    """확인한 것 (✅ · ⚠️ · ⛔)."""
    blocked: list[str] = field(default_factory=list)
    """사지 않는 이유 — 비어 있어야 매수 버튼."""

    @property
    def ok(self) -> bool:
        return not self.blocked and self.qty > 0


def recommend(inp: Inputs) -> Advice:
    kr = is_kr(inp.symbol)
    fx = 1.0 if kr else inp.fx
    base = base_krw(inp.krw_cash, inp.usd_cash, inp.fx, inp.deneb_usd)
    lines: list[str] = []
    blocked: list[str] = []
    weight = min(RISK / STOP, CAP)
    if inp.blocked_symbol:
        blocked.append(f"{inp.blocked_symbol} 가 자동으로 사고파는 종목 — 같은 계좌라 섞여요")
    idx_up = above_long(inp.index_closes)
    if idx_up is None:
        lines.append(f"⚠️ {inp.index_name or '지수'} 200일선 확인 못 함 — 보정 없음")
    elif idx_up:
        lines.append(f"✅ {inp.index_name} 200일선 위")
    else:
        weight /= 2
        lines.append(f"⚠️ {inp.index_name} 200일선 아래 → 비중 절반")
    if inp.earnings_in_days is None:
        lines.append("⚠️ 실적 발표일 모름" + (" (국내 종목)" if kr else ""))
    elif inp.earnings_in_days <= EARN_BLOCK_DAYS:
        blocked.append(
            f"실적 발표 {inp.earnings_date} ({inp.earnings_in_days} 거래일 뒤) — 발표 뒤에 사세요"
        )
    elif not inp.earnings_date:
        lines.append("✅ 실적 발표 다음 8일 안에 없음")
    else:
        lines.append(f"✅ 실적 발표 {inp.earnings_date} ({inp.earnings_in_days} 거래일 뒤)")
    score, trend_ok = trend_check(inp.closes)
    if trend_ok is None:
        lines.append("⚠️ 일봉이 모자라 추세 확인 못 함")
    elif trend_ok:
        lines.append(f"✅ 추세 좋음 (20·60·120일 점수 {score:+.2f}, 50일선 위)")
    else:
        why = [f"20·60·120일 점수 {score:+.2f}"] if score is not None and score <= 0 else []
        ma = _sma(inp.closes, MA_SHORT)
        if ma is not None and inp.closes[-1] <= ma:
            why.append(f"50일선 아래 ({inp.closes[-1] / ma - 1:+.1%})")
        blocked.append("추세 아님 — " + " · ".join(why))
    limit = buy_limit(inp.price, kr)
    held_krw = inp.held_qty * inp.price * fx
    want_krw = max(0.0, base * weight - held_krw)
    if held_krw > 0:
        lines.append(f"ℹ️ 이미 {inp.held_qty:g} 주 보유 — 상한까지 모자란 만큼만")
    qty = int(want_krw / fx // limit) if limit > 0 else 0
    cash = inp.krw_cash if kr else max(0.0, inp.usd_cash - inp.deneb_usd)
    can = int(cash // limit) if limit > 0 else 0
    if can < qty:
        cur = "원화" if kr else "달러"
        lines.append(f"⚠️ {cur}가 모자라 {qty} → {can} 주로 줄임")
        qty = can
    if qty <= 0:
        blocked.append("살 수 있는 수량이 0 주 (돈 · 상한)")
    amount = qty * limit * fx
    if not kr and qty > 0:
        after = (inp.us_stock_krw + amount) / max(1.0, base + inp.us_stock_krw)
        if after >= 0.5:
            lines.append(f"⚠️ 사면 미국 주식이 현금 + 주식의 {after:.0%} (Deneb 포함)")
    stop = stop_price(limit, kr)
    return Advice(
        symbol=inp.symbol,
        name=inp.name,
        currency="KRW" if kr else "USD",
        price=inp.price,
        base_krw=base,
        weight=amount / base if base > 0 else 0.0,
        limit=limit,
        qty=qty,
        amount_krw=amount,
        stop=stop,
        risk_krw=qty * (limit - stop) * fx,
        lines=lines,
        blocked=blocked,
    )


def _px(v: float, currency: str) -> str:
    return f"{v:,.0f}원" if currency == "KRW" else f"${v:,.2f}"


def _won(v: float) -> str:
    return f"{v / 10_000:,.1f}만 원" if abs(v) >= 10_000 else f"{v:,.0f}원"


def advice_text(a: Advice) -> str:
    """텔레그램용 추천 (순수)."""
    head = f"📐 {a.name} ({a.symbol}) 매수 비중 추천"
    if a.ok:
        top = (
            f"추천: {a.weight:.0%} · {_won(a.amount_krw)} · {a.qty}주 "
            f"(지정가 {_px(a.limit, a.currency)} 까지)"
        )
    else:
        top = "⛔ 지금은 사지 않아요\n" + "\n".join(f"• {b}" for b in a.blocked)
        if a.qty > 0:
            top += f"\n(참고: 조건이 맞았다면 {a.weight:.0%} · {_won(a.amount_krw)} · {a.qty}주)"
    cap_note = f"(1% ÷ 3% = 33% 이지만 한 종목 상한 {CAP:.0%})" if RISK / STOP > CAP else ""
    body = [
        head,
        "",
        top,
        f"• 기준 돈: {_won(a.base_krw)} (원화 + 달러 현금, Deneb 이월 제외)",
        f"• 손절: 산 값보다 −{STOP:.0%} (지금 기준 {_px(a.stop, a.currency)}) 이면 자동 매도",
        f"• 틀리면 손실: {_won(a.risk_krw)} = 기준의 {a.risk_krw / max(a.base_krw, 1):.1%} "
        + cap_note,
        "",
        "확인한 것",
        *a.lines,
        "",
        "매수는 정규장 시작 직후 지정가로 (장중이면 바로). 밤사이 갭 하락은 −3% 로 못 막아요.",
    ]
    return "\n".join(body)
