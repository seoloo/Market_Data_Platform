"""개별주 매수 비중 추천 — '틀리면 계좌의 1% 까지만 잃게' (I/O 없음).

사용자 (2026-10-15): "이 종목을 현재 돈의 몇 퍼센트 비율로 구매하면 될지를 파악해서 추천" →
기준 돈 = 토스 계좌의 원화 현금 + 달러 현금, 한 번에 잃어도 되는 돈 = 기준의 1%,
손절 = 산 종목이 −3% 면 자동 매도, 한 종목 상한 25%, 매수는 봇 버튼 · 장 시작에.
2026-10-17 사용자 "손절 −12만원이라 좀 큰데" → 한 번에 잃는 돈 **0.3%** · 한 종목 **10%**
(연구 249).

* 비중 = min(0.3% ÷ 3% = 10%, 상한 10%). 손절 한 번 계획 손실 ≈ 기준 돈의 0.3%.
* 지수(미국 SPY · 국내 KODEX 200)가 200 일선 아래면 절반 — 연구 237: 개별 주도주 규칙은
  시장 조건 없이 사면 하락장에 크게 잃었다.
* 실적 발표가 3 거래일 안이면 사지 않는다(발표 갭은 −3% 손절로 못 막는다).
* 추세(20 · 60 · 120 일 수익 부호 평균 > 0 그리고 종가 > 50 일선)가 아니면 사지 않는다.
* Deneb 의 이월 달러(T 브레이크로 팔고 기다리는 돈)는 기준 돈 · 쓸 돈에서 뺀다.

언제 쓰나: 텔레그램 💹 개별주 → ``scripts/size_guard.advise`` · 예약 직전 다시 확인(``tick``).
읽고 쓰는 것: 없음 — 받은 값(시세 · 현금 · 일봉 · 발표 일정)으로 계산과 알림 글만.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from auto_trading.ops.entry_events import EntryEvents

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
    "confirm_text",
    "qty_prompt_text",
    "recommend",
    "round_price",
    "stop_price",
    "trend_check",
]

RISK = 0.003
"""한 번에 잃어도 되는 돈 — 기준 돈의 0.3% (사용자 2026-10-17, 처음 1%)."""
STOP = 0.03
"""손절 — 산 값보다 3% 아래 (사용자, 종목 기준)."""
CAP = 0.10
"""한 종목 상한 — 기준 돈의 10% (사용자 2026-10-17, 처음 25% · 연구 249)."""
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
    """[쓰임] 국내 종목인가 (6 자리 숫자 코드)."""
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
    count = price / step
    steps = math.ceil(count - 1e-9) if up else math.floor(count + 1e-9)
    return round(steps * step, 0 if kr else (2 if step == 0.01 else 4))


def buy_limit(price: float, kr: bool) -> float:
    """수량을 셀 때 쓰는 지정가 = 지금 가격 + 0.5%, 호가 단위로 올림."""
    return round_price(price * (1 + PRICE_BUFFER), kr, up=True)


def stop_price(avg: float, kr: bool) -> float:
    """손절가 = 평균 단가 −3%, 호가 단위로 내림."""
    return round_price(avg * (1 - STOP), kr, up=False)


def base_krw(krw_cash: float, usd_cash: float, fx: float, deneb_usd: float) -> float:
    """기준 돈 = 원화 현금 + (달러 현금 − Deneb 이월 달러) × 환율. 보유 주식은 넣지 않는다."""
    return krw_cash + max(0.0, usd_cash - deneb_usd) * fx


def _sma(values: Sequence[float], count: int) -> float | None:
    """[쓰임] 마지막 ``count`` 개 평균 (자료가 모자라면 None)."""
    return sum(values[-count:]) / count if len(values) >= count else None


def trend_check(closes: Sequence[float]) -> tuple[float | None, bool | None]:
    """(추세 점수 −1 ~ 1, 추세 맞음). 자료가 모자라면 (None, None)."""
    if len(closes) <= max(LOOKS):
        return None, None
    last = closes[-1]
    signs = [1.0 if last > closes[-1 - days] else -1.0 for days in LOOKS]
    score = sum(signs) / len(signs)
    ma = _sma(closes, MA_SHORT)
    return round(score, 2), bool(score > 0 and ma is not None and last > ma)


def above_long(closes: Sequence[float]) -> bool | None:
    """[쓰임] 지수가 200 일선 위인가 (자료가 모자라면 None — 확인 못 함)."""
    ma = _sma(closes, MA_LONG)
    return None if ma is None else closes[-1] > ma


@dataclass(frozen=True, slots=True)
class Inputs:
    """추천에 넣는 값 (토스 조회 · 발표 일정을 실행기가 모아 넣는다)."""

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
    event_risk: EntryEvents | None = None


@dataclass(slots=True)
class Advice:
    """추천 결과 — 알림 글 · 예약 장부가 그대로 쓴다."""

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
    entry_session: str = ""
    max_qty: int = 0
    """사람이 직접 정할 때의 최대 수량 — 한 종목 상한(CAP) · 현금 (지수 절반 보정 전)."""
    hard: list[str] = field(default_factory=list)
    """'그래도 매수' 로도 넘을 수 없는 이유 (자동 전략 종목 · 돈 없음)."""
    fx: float = 1.0
    """원/달러 (국내 종목은 1)."""

    @property
    def ok(self) -> bool:
        """[쓰임] 추천대로 살 수 있나 (막는 이유 없음 · 수량 1 이상)."""
        return not self.blocked and self.qty > 0

    @property
    def can_override(self) -> bool:
        """규칙에 걸려도 사람이 수량을 정해 살 수 있나 (2026-10-16 사용자 '그래도 매수')."""
        return not self.hard and self.max_qty > 0


def recommend(inputs: Inputs) -> Advice:
    """[쓰임] 종목 하나의 매수 비중 · 수량 추천 (막는 이유 · 확인한 것 · 사람이 정할 최대 수량).
    [이유] 비중 = 0.3% ÷ 3% = 10% (상한 10%), 지수 200 일선 아래면 절반 — 손절 한 번에
           기준 돈의 0.3% 쯤만 잃게 (사용자 2026-10-17). 모르는 값은 막는 쪽(fail-closed)."""
    kr = is_kr(inputs.symbol)
    fx = 1.0 if kr else inputs.fx
    base = base_krw(inputs.krw_cash, inputs.usd_cash, inputs.fx, inputs.deneb_usd)
    lines: list[str] = []
    blocked: list[str] = []
    if inputs.event_risk is None:
        blocked.append("발표 위험 미확인 — 신규 매수 보류")
    else:
        blocked.extend(inputs.event_risk.blocked)
        lines.extend(inputs.event_risk.lines)
    weight = min(RISK / STOP, CAP)
    hard: list[str] = []
    if inputs.blocked_symbol:
        hard.append(f"{inputs.blocked_symbol} 가 자동으로 사고파는 종목 — 같은 계좌라 섞여요")
        blocked.append(hard[-1])
    idx_up = above_long(inputs.index_closes)
    if idx_up is None:
        blocked.append(f"{inputs.index_name or '지수'} 200일선 확인 못 함 — 신규 매수 보류")
    elif idx_up:
        lines.append(f"✅ {inputs.index_name} 200일선 위")
    else:
        weight /= 2
        lines.append(f"⚠️ {inputs.index_name} 200일선 아래 → 비중 절반")
    if inputs.earnings_in_days is None:
        blocked.append("실적 발표일 미확인" + (" (국내 원천 미확보)" if kr else ""))
    elif inputs.earnings_in_days <= EARN_BLOCK_DAYS:
        blocked.append(
            f"실적 발표 {inputs.earnings_date} ({inputs.earnings_in_days} 거래일 뒤)"
            " — 발표 뒤에 사세요"
        )
    elif not inputs.earnings_date:
        lines.append("✅ 실적 발표 조회 범위 내 명단에 없음 (확정 공시 보증 아님)")
    else:
        lines.append(f"✅ 실적 발표 {inputs.earnings_date} ({inputs.earnings_in_days} 거래일 뒤)")
    score, trend_ok = trend_check(inputs.closes)
    if trend_ok is None:
        blocked.append("일봉이 모자라 추세 확인 못 함 — 신규 매수 보류")
    elif trend_ok:
        lines.append(f"✅ 추세 좋음 (20·60·120일 점수 {score:+.2f}, 50일선 위)")
    else:
        why = [f"20·60·120일 점수 {score:+.2f}"] if score is not None and score <= 0 else []
        ma = _sma(inputs.closes, MA_SHORT)
        if ma is not None and inputs.closes[-1] <= ma:
            why.append(f"50일선 아래 ({inputs.closes[-1] / ma - 1:+.1%})")
        blocked.append("추세 아님 — " + " · ".join(why))
    limit = buy_limit(inputs.price, kr)
    held_krw = inputs.held_qty * inputs.price * fx
    want_krw = max(0.0, base * weight - held_krw)
    if held_krw > 0:
        lines.append(f"ℹ️ 이미 {inputs.held_qty:g} 주 보유 — 상한까지 모자란 만큼만")
    qty = int(want_krw / fx // limit) if limit > 0 else 0
    cash = inputs.krw_cash if kr else max(0.0, inputs.usd_cash - inputs.deneb_usd)
    can = int(cash // limit) if limit > 0 else 0
    if can < qty:
        cur = "원화" if kr else "달러"
        lines.append(f"⚠️ {cur}가 모자라 {qty} → {can} 주로 줄임")
        qty = can
    if qty <= 0:
        blocked.append("살 수 있는 수량이 0 주 (돈 · 상한)")
    cap_qty = int(max(0.0, base * CAP - held_krw) / fx // limit) if limit > 0 else 0
    max_qty = min(cap_qty, can)
    if max_qty <= 0:
        hard.append(f"살 수 있는 수량이 0 주 (현금 · 한 종목 {CAP:.0%} 상한)")
    amount = qty * limit * fx
    if not kr and qty > 0:
        after = (inputs.us_stock_krw + amount) / max(1.0, base + inputs.us_stock_krw)
        if after >= 0.5:
            lines.append(f"⚠️ 사면 미국 주식이 현금 + 주식의 {after:.0%} (Deneb 포함)")
    stop = stop_price(limit, kr)
    return Advice(
        symbol=inputs.symbol,
        name=inputs.name,
        currency="KRW" if kr else "USD",
        price=inputs.price,
        base_krw=base,
        weight=amount / base if base > 0 else 0.0,
        limit=limit,
        qty=qty,
        amount_krw=amount,
        stop=stop,
        risk_krw=qty * (limit - stop) * fx,
        lines=lines,
        blocked=blocked,
        max_qty=max_qty,
        hard=hard,
        fx=fx,
        entry_session=(
            f"{'KR' if kr else 'US'}:{inputs.event_risk.entry_day}"
            if inputs.event_risk is not None and inputs.event_risk.entry_day is not None
            else ""
        ),
    )


def _px(value: float, currency: str) -> str:
    """[쓰임] 가격 글 — 원화 '70,000원' · 달러 '$180.90'."""
    return f"{value:,.0f}원" if currency == "KRW" else f"${value:,.2f}"


def _won(value: float) -> str:
    """[쓰임] 원화 금액 글 — 1 만 원 넘으면 '76.0만 원'."""
    return f"{value / 10_000:,.1f}만 원" if abs(value) >= 10_000 else f"{value:,.0f}원"


def advice_text(advice: Advice) -> str:
    """텔레그램용 추천 (순수)."""
    head = f"💹 {advice.name} ({advice.symbol}) 매수 비중 추천"
    if advice.ok:
        top = (
            f"추천: {advice.weight:.0%} · {_won(advice.amount_krw)} · {advice.qty}주 "
            f"(지정가 {_px(advice.limit, advice.currency)} 까지)"
        )
    else:
        word = "⚠️ 규칙상 지금은 비추천" if advice.can_override else "⛔ 지금은 사지 않아요"
        top = word + "\n" + "\n".join(f"• {reason}" for reason in advice.blocked)
        if advice.qty > 0:
            top += (
                f"\n(참고: 조건이 맞았다면 {advice.weight:.0%} · {_won(advice.amount_krw)}"
                f" · {advice.qty}주)"
            )
    cap_note = (
        f"({RISK:.1%} ÷ {STOP:.0%} = {RISK / STOP:.0%} 이지만 한 종목 상한 {CAP:.0%})"
        if RISK / STOP > CAP + 1e-9
        else ""
    )
    body = [
        head,
        "",
        top,
        f"• 기준 돈: {_won(advice.base_krw)} (원화 + 달러 현금, Deneb 이월 제외)",
        f"• 손절: 산 값보다 −{STOP:.0%} (지금 기준 {_px(advice.stop, advice.currency)}) "
        "이면 지정가 매도 시도",
        f"• 계획 손실(보장 아님): {_won(advice.risk_krw)} "
        f"= 기준의 {advice.risk_krw / max(advice.base_krw, 1):.1%} " + cap_note,
        "",
        "확인한 것",
        *advice.lines,
        "",
        "매수는 정규장 첫 1분봉 시가 확인 후 지정가로 (장중이면 재확인). "
        "갭·미체결·PC 꺼짐으로 −3%보다 더 잃을 수 있어요. 보류 후에는 새 추천·예약이 필요해요.",
    ]
    return "\n".join(body)


def qty_prompt_text(advice: Advice) -> str:
    """'몇 주 살까요?' (순수) — 추천 수량과 사람이 정할 수 있는 최대."""
    cash = "원화" if advice.currency == "KRW" else "달러"
    rec = f"추천 {advice.qty}주 · " if advice.ok else ""
    return (
        f"💹 {advice.name} 몇 주 살까요?\n"
        f"{rec}최대 {advice.max_qty}주 (한 종목 {CAP:.0%} · {cash} 현금 기준)\n"
        "숫자만 답장해 주세요 (예: 5)"
    )


def confirm_text(advice: Advice, qty: int) -> str:
    """수량을 정한 뒤 마지막 확인 (순수)."""
    rate = 1.0 if advice.currency == "KRW" else advice.fx
    amount = qty * advice.limit * rate
    risk = qty * (advice.limit - advice.stop) * rate
    lines = [
        f"💹 {advice.name} {qty}주 · 약 {_won(amount)}"
        f" (기준의 {amount / max(advice.base_krw, 1):.1%})",
        f"• 지정가 {_px(advice.limit, advice.currency)} 까지 · 정규장 시작 직후(장중이면 바로)",
        f"• 손절: 산 값 −{STOP:.0%} 면 자동 매도 (약 {_won(risk)} 손실, 갭이면 더 클 수 있어요)",
    ]
    if not advice.ok:
        lines.append("⚠️ 규칙 밖 매수예요 — " + " · ".join(advice.blocked))
        if any("실적 발표" in reason and "거래일 뒤" in reason for reason in advice.blocked):
            lines.append("🚨 실적 발표가 코앞이에요 — 발표 날 하루에 ±10% 넘게 움직이기도 해요")
    elif qty > advice.qty:
        lines.append(f"ℹ️ 추천({advice.qty}주)보다 많아요 — 틀리면 기준의 1% 보다 더 잃어요")
    return "\n".join(lines)
