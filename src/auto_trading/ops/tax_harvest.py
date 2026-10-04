"""연말 해외주식 양도세 기본공제(250 만 원) 수확 계획 (docs/research/123) — 순수 계산.

해외주식 양도소득은 해마다 250 만 원까지 비과세고, 쓰지 않은 공제는 사라진다.
이익이 난 종목을 그해에 공제만큼 **팔았다가 다시 사면** 보유는 그대로인데 평균 매입가가
올라가 미래 세금이 줄어든다.
(한국에는 재매수 제한(워시세일)이 없다 — 2026-10-04 확인. 과세 연도는 결제일 기준, 원화 환산.)

여기서는 **얼마나 팔지**만 계산한다. 주문은 하지 않는다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

EXEMPTION_KRW = 2_500_000.0
MARGIN_KRW = 300_000.0
"""원화 환산·환율 차이·추정 오차 여유 — 공제를 넘겨 세금이 생기지 않게."""
TAX = 0.22


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    qty: float
    avg_usd: float
    last_usd: float
    strategy: bool
    """자동 적립 종목(QNDX·BTQ·WQTM·QS)이면 참. 아니면 사용자가 직접 정한다."""


@dataclass(frozen=True, slots=True)
class HarvestLine:
    symbol: str
    qty: int
    gain_krw: float
    amount_usd: float
    strategy: bool


@dataclass(frozen=True, slots=True)
class HarvestPlan:
    budget_krw: float
    lines: tuple[HarvestLine, ...]
    harvested_krw: float
    future_tax_saved_krw: float
    cost_usd: float
    """판 금액 + 다시 산 금액에 수수료."""


def qs_realized_krw(trades: Iterable[Mapping[str, object]], year: str) -> float:
    """QS TQQQ 의 그해 실현 손익(원화) — 이동평균 원가로 따라간다."""
    qty = cost = 0.0
    realized = 0.0
    for t in trades:
        q = float(t["qty"])  # type: ignore[arg-type]
        krw = float(t["krw"])  # type: ignore[arg-type]
        if t["side"] == "BUY":
            qty += q
            cost += krw
        elif qty > 0:
            part = cost * min(q, qty) / qty
            if str(t["date"])[:4] == year:
                realized += krw - part
            qty -= min(q, qty)
            cost -= part
    return realized


def plan(
    holdings: Iterable[Holding],
    fx: float,
    *,
    other_gains_krw: float,
    qs_gains_krw: float,
    fee: float,
) -> HarvestPlan:
    """공제에서 이미 쓴 이익(다른 매도·QS)과 여유를 빼고, 남은 만큼 이익 난 종목을 정수 주로 고른다.

    자동 적립 종목을 먼저, 그다음 주당 이익이 큰 순서.
    """
    budget = EXEMPTION_KRW - max(other_gains_krw, 0.0) - max(qs_gains_krw, 0.0) - MARGIN_KRW
    left = max(budget, 0.0)
    lines: list[HarvestLine] = []
    ranked = sorted(
        (h for h in holdings if h.qty >= 1 and h.last_usd > h.avg_usd > 0),
        key=lambda h: (not h.strategy, -(h.last_usd - h.avg_usd) / h.avg_usd),
    )
    for h in ranked:
        per_share = (h.last_usd - h.avg_usd) * fx
        if per_share <= 0 or left < per_share:
            continue
        q = min(math.floor(h.qty), int(left // per_share))
        if q < 1:
            continue
        gain = q * per_share
        lines.append(HarvestLine(h.symbol, q, gain, q * h.last_usd, h.strategy))
        left -= gain
    harvested = sum(x.gain_krw for x in lines)
    cost = sum(x.amount_usd for x in lines) * fee * 2
    return HarvestPlan(budget, tuple(lines), harvested, harvested * TAX, cost)
