"""🧭 Polaris — 나무증권 안정 적립 (순수 계산, I/O 없음). 주문 · 알림은 ``scripts/polaris.py``.

사용자 (2026-10-17): 나무는 "정말 안정적이게" · 토스와 **완전히 별개** · "polaris로 가자" ·
"11/2 부터" ·
"700만원 한번에 하지 않았으면" → 400 만 원을 4 달에 나눠(매달 100 만) · 나무에서 직접 사는 개별주는
계산에서 뺀다.

* 비율 SPYM 30 · QQQM 30 · IEF 20 · GLDM 20 (참고 계산 ``run_namu_core_compare.py`` half · dca20).
* 회차: 매달 넣는 돈으로 목표 비율에 모자란 종목부터 정수 주로 산다(사기만).
* **12 월 맞추기** (사용자 2026-10-17 "4개월 하지" · 12 월마다 확인 · 채권도 익절): 4 회차 뒤
  매년 12 월 첫 평일에 버튼 — 누르면 목표보다 1 주 넘게 많은 종목을 팔고(익절) 그 돈(달러)으로
  모자란 종목을 산다. 참고 계산 ``run_namu_core_compare.py rebal``: 사기만은 주식 82 ~ 90% 로
  쏠림, 12 월 한 번 ≈ 5%p 기준.
  그 밖의 자동 매도 · 손절 없음.
* 매달 한 번(첫 거래일 무렵) 텔레그램 버튼을 눌러야 그 회차를 산다. 건너뛰면 회차를 쓰지 않는다.
* 성적은 **Polaris 장부 체결만**으로 — 같은 계좌의 개별주 · 같은 종목 직접 매수는 섞지 않는다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "FEE",
    "FX_BUFFER",
    "LIMIT_UP",
    "WEIGHTS",
    "PolarisState",
    "Rebal",
    "Round",
    "budget_usd",
    "card_text",
    "plan_buys",
    "plan_rebalance",
    "rebal_text",
    "result_text",
]

WEIGHTS = {"SPYM": 0.30, "QQQM": 0.30, "IEF": 0.20, "GLDM": 0.20}
SYMBOLS = tuple(WEIGHTS)
START_MONTH = "2026-11"
ROUNDS = 4
FEE = 0.0009
"""나무 해외주식 수수료 0.09%."""
FX_BUFFER = 0.01
"""원화 주문 환율이 조회 환율보다 높을 수 있어 1% 덜 쓴다."""
LIMIT_UP = 0.002
"""지정가 = 매도 1 호가 + 0.2% (센트 올림). 안 차면 다음 확인 때 다시."""
MAX_TRIES = 3
"""한 회차에 주문을 내 보는 정규장 확인 횟수 — 넘으면 남은 돈은 다음 회차로."""


@dataclass(slots=True)
class Round:
    month: str
    krw: float
    status: str = "offered"
    """offered(버튼 보냄) · approved(누름) · done · skipped."""
    offered_at: int = 0
    approved_at: int = 0
    tries: int = 0
    spent_usd: float = 0.0
    spent_krw: float = 0.0
    nonce: str = ""
    """버튼 확인 값."""
    note: str = ""


@dataclass(slots=True)
class Rebal:
    """그해 12 월 맞추기."""

    year: str
    status: str = "offered"
    """offered · approved · done · skipped · none(맞출 것 없음)."""
    nonce: str = ""
    tries: int = 0
    note: str = ""


@dataclass(slots=True)
class PolarisState:
    rounds: list[Round] = field(default_factory=list)
    qty: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SYMBOLS, 0.0))
    cost_usd: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SYMBOLS, 0.0))
    """수수료 포함 매입 금액(달러)."""
    cost_krw: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SYMBOLS, 0.0))
    """체결 때 조회 환율로 바꾼 원화 매입 금액(추정)."""
    orders: list[dict[str, Any]] = field(default_factory=list)
    """체결 (장부) — 성적은 이것만으로."""
    sent: list[dict[str, Any]] = field(default_factory=list)
    """낸 주문 — 보내기 전에 적는다(``sent`` 0 = 결과 모름 · 1 = 접수 · −1 = 거절)."""
    prepaid_krw: float = 0.0
    """첫 회차 전 1 주 시험 매수 — 첫 회차 돈에서 뺀다."""
    halted: str = ""
    """주문 결과를 모르는 등 — 비어 있지 않으면 새 주문 없음."""
    rebals: list[Rebal] = field(default_factory=list)
    realized_usd: dict[str, float] = field(default_factory=dict)
    """해(YYYY) → 판 이익(달러, 평균 단가 · 수수료 뺌)."""
    realized_krw: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> PolarisState:
        st = cls()
        st.rounds = [Round(**r) for r in d.get("rounds") or []]
        for name in ("qty", "cost_usd", "cost_krw"):
            getattr(st, name).update({k: float(v) for k, v in (d.get(name) or {}).items()})
        st.orders = list(d.get("orders") or [])
        st.sent = list(d.get("sent") or [])
        st.prepaid_krw = float(d.get("prepaid_krw") or 0)
        st.halted = str(d.get("halted") or "")
        st.rebals = [Rebal(**x) for x in d.get("rebals") or []]
        st.realized_usd = {k: float(v) for k, v in (d.get("realized_usd") or {}).items()}
        st.realized_krw = {k: float(v) for k, v in (d.get("realized_krw") or {}).items()}
        return st

    @property
    def done_rounds(self) -> int:
        return sum(r.status == "done" for r in self.rounds)

    def round_for(self, month: str) -> Round | None:
        return next((r for r in self.rounds if r.month == month), None)

    def due_month(self, month: str, rounds: int = ROUNDS) -> bool:
        """이 달에 새 회차 버튼을 보낼 때인가."""
        return month >= START_MONTH and self.done_rounds < rounds and self.round_for(month) is None

    def apply_fill(
        self, symbol: str, qty: float, usd: float, fee_usd: float, fx: float, order_id: str
    ) -> None:
        self.qty[symbol] = self.qty.get(symbol, 0.0) + qty
        self.cost_usd[symbol] = self.cost_usd.get(symbol, 0.0) + usd + fee_usd
        self.cost_krw[symbol] = self.cost_krw.get(symbol, 0.0) + (usd + fee_usd) * fx
        self.orders.append(
            {"id": order_id, "symbol": symbol, "qty": qty, "usd": usd, "fee": fee_usd, "fx": fx}
        )

    def apply_sell(
        self,
        symbol: str,
        qty: float,
        usd: float,
        fee_usd: float,
        fx: float,
        order_id: str,
        year: str,
    ) -> float:
        """판 체결 — 평균 단가로 원가를 빼고 그해 실현 이익(달러)을 적는다. 이익을 돌려준다."""
        held = self.qty.get(symbol, 0.0)
        if qty <= 0 or held <= 0:
            return 0.0
        part = min(1.0, qty / held)
        cost = self.cost_usd[symbol] * part
        cost_krw = self.cost_krw[symbol] * part
        gain = usd - fee_usd - cost
        self.qty[symbol] = held - qty
        self.cost_usd[symbol] -= cost
        self.cost_krw[symbol] -= cost_krw
        self.realized_usd[year] = self.realized_usd.get(year, 0.0) + gain
        self.realized_krw[year] = self.realized_krw.get(year, 0.0) + (usd - fee_usd) * fx - cost_krw
        self.orders.append(
            {
                "id": order_id,
                "symbol": symbol,
                "qty": -qty,
                "usd": usd,
                "fee": fee_usd,
                "fx": fx,
                "gain": gain,
            }
        )
        return gain

    def rebal_for(self, year: str) -> Rebal | None:
        return next((x for x in self.rebals if x.year == year), None)


def budget_usd(krw: float, fx: float) -> float:
    """원화 → 쓸 수 있는 달러 (환율 여유 · 수수료 뺌)."""
    if fx <= 0:
        return 0.0
    return krw / (fx * (1 + FX_BUFFER)) / (1 + FEE)


def limit_price(ask: float) -> float:
    return math.ceil(ask * (1 + LIMIT_UP) * 100) / 100


def sell_price(bid: float) -> float:
    """파는 지정가 = 매수 1 호가 − 0.2% (센트 내림)."""
    return math.floor(bid * (1 - LIMIT_UP) * 100) / 100


def plan_rebalance(
    qty: Mapping[str, float],
    bids: Mapping[str, float],
    asks: Mapping[str, float],
    weights: Mapping[str, float] = WEIGHTS,
) -> tuple[dict[str, int], dict[str, int]]:
    """12 월 맞추기 (팔 것, 살 것). 목표보다 1 주 넘게 많은 만큼 정수 주로 팔고,
    판 돈(수수료 뺌)으로 :func:`plan_buys` 처럼 모자란 종목을 산다."""
    if any(bids.get(s, 0) <= 0 or asks.get(s, 0) <= 0 for s in weights):
        return {}, {}
    mid = {s: (bids[s] + asks[s]) / 2 for s in weights}
    total = sum(qty.get(s, 0.0) * mid[s] for s in weights)
    if total <= 0:
        return {}, {}
    sells = {}
    for s, w in weights.items():
        extra = qty.get(s, 0.0) - total * w / mid[s]
        if extra >= 1:
            sells[s] = int(extra)
    cash = sum(n * sell_price(bids[s]) for s, n in sells.items()) * (1 - FEE) / (1 + FEE)
    after = {s: qty.get(s, 0.0) - sells.get(s, 0) for s in weights}
    buys = plan_buys(after, asks, cash, weights) if cash > 0 else {}
    buys = {s: n for s, n in buys.items() if s not in sells}
    return sells, buys


def plan_buys(
    qty: Mapping[str, float],
    asks: Mapping[str, float],
    usd: float,
    weights: Mapping[str, float] = WEIGHTS,
) -> dict[str, int]:
    """사기만 하는 맞추기 — (지금 보유 + 새 돈) × 비율에 모자란 만큼 정수 주.
    남은 돈은 가장 많이 모자란 종목부터 한 주씩. 지정가(매도 1 호가 + 0.2%) 기준."""
    px = {s: limit_price(asks[s]) for s in weights if asks.get(s, 0) > 0}
    if len(px) != len(weights):
        return {}
    held = {s: qty.get(s, 0.0) * px[s] for s in weights}
    total = sum(held.values()) + usd
    need = {s: max(0.0, total * w - held[s]) for s, w in weights.items()}
    buy = {s: int(need[s] // px[s]) for s in weights}
    left = usd - sum(buy[s] * px[s] for s in weights)
    if left < 0:  # 모자란 몫 합이 새 돈보다 큼(보유가 크게 치우침) → 비율대로 줄임
        scale = usd / sum(need.values())
        buy = {s: int(need[s] * scale // px[s]) for s in weights}
        left = usd - sum(buy[s] * px[s] for s in weights)
    while True:
        gaps = {s: need[s] - buy[s] * px[s] for s in weights if px[s] <= left}
        if not gaps:
            break
        s = max(gaps, key=lambda k: (gaps[k], k))
        buy[s] += 1
        left -= px[s]
    return {s: n for s, n in buy.items() if n > 0}


def _won(v: float) -> str:
    return f"{v / 10_000:,.1f}만 원" if abs(v) >= 10_000 else f"{v:,.0f}원"


def holdings_lines(state: PolarisState, prices: Mapping[str, float], fx: float) -> list[str]:
    out = []
    for s in SYMBOLS:
        q = state.qty.get(s, 0.0)
        if q <= 0:
            continue
        avg = state.cost_usd[s] / q
        now = prices.get(s, 0.0)
        pnl = (now * q - state.cost_usd[s]) * fx
        out.append(
            f"   └ {s} {q:g}주 · 평단 ${avg:,.2f} → ${now:,.2f}"
            f" · {'+' if pnl >= 0 else '−'}{_won(abs(pnl))} ({now / avg - 1:+.1%})"
        )
    return out


def card_text(
    state: PolarisState,
    month: str,
    krw: float,
    plan: Mapping[str, int],
    asks: Mapping[str, float],
    fx: float,
    rounds: int = ROUNDS,
) -> str:
    n = state.done_rounds + 1
    est = sum(limit_price(asks[s]) * q for s, q in plan.items())
    lines = [
        f"🧭 [Polaris {n}/{rounds}회차 · {int(month[5:])}월]",
        "━━━━━━━━━━━━",
        f"💰 이번 달 {_won(krw)} · 나무 원화 결제 · 지정가",
        f"📋 예상 매수 (지금 호가 · 환율 {fx:,.0f}원, 실제는 주문 때 다시 계산)",
    ]
    lines += [f"   └ {s} {plan[s]}주 (약 ${limit_price(asks[s]) * plan[s]:,.0f})" for s in plan]
    lines.append(f"   └ 합계 약 ${est:,.0f} ≈ {_won(est * fx)}")
    held = holdings_lines(state, asks, fx)
    lines.append("📦 지금 Polaris" + ("" if held else ": 없음"))
    lines += held
    lines += [
        "",
        "비율 SPYM 30 · QQQM 30 · IEF 20 · GLDM 20 · 사기만 함(자동 매도 없음)",
        "누르면 미국 정규장(밤 11:30 이후)에 삽니다. 건너뛰면 이번 달은 안 사요.",
    ]
    return "\n".join(lines)


def rebal_text(
    state: PolarisState,
    year: str,
    sells: Mapping[str, int],
    buys: Mapping[str, int],
    bids: Mapping[str, float],
    asks: Mapping[str, float],
    fx: float,
) -> str:
    """12 월 맞추기 카드 — 지금 비율 · 팔 것(예상 이익) · 살 것 · 올해 판 이익."""
    mid = {s: (bids[s] + asks[s]) / 2 for s in SYMBOLS}
    total = sum(state.qty.get(s, 0.0) * mid[s] for s in SYMBOLS)
    lines = [f"🧭 [Polaris {year}년 12월 비율 맞추기]", "━━━━━━━━━━━━", "📊 지금 비율 → 목표"]
    for s, w in WEIGHTS.items():
        now = state.qty.get(s, 0.0) * mid[s] / total if total else 0.0
        lines.append(f"   └ {s} {now:.0%} → {w:.0%}")
    gain_krw = 0.0
    if sells:
        lines.append("💰 팔 것 (많아진 몫 익절)")
        for s, n in sells.items():
            q = state.qty.get(s, 0.0)
            avg = state.cost_usd[s] / q if q else 0.0
            g = (sell_price(bids[s]) - avg) * n * fx
            gain_krw += g
            lines.append(f"   └ {s} {n}주 (약 ${sell_price(bids[s]) * n:,.0f}, 이익 약 {_won(g)})")
    if buys:
        lines.append("🛒 살 것 (판 돈으로)")
        lines += [f"   └ {s} {n}주 (약 ${limit_price(asks[s]) * n:,.0f})" for s, n in buys.items()]
    done = state.realized_krw.get(year, 0.0)
    lines += [
        "",
        f"🧾 Polaris 올해 판 이익 {_won(done)} + 이번 약 {_won(gain_krw)}",
        "   해외주식 양도세는 사람 기준 1 년 250만 원 공제 — 토스 등 다른 매도 이익과 합쳐요",
        "누르면 미국 정규장에 팔고 → 그 달러로 삽니다. 건너뛰면 올해는 그대로 둬요.",
    ]
    return "\n".join(lines)


def result_text(
    month: str,
    fills: Mapping[str, tuple[float, float]],
    left: Mapping[str, int],
    spent_usd: float,
    fx: float,
    state: PolarisState,
    prices: Mapping[str, float],
    *,
    final: bool,
) -> str:
    """회차 체결 결과. ``fills`` = 종목 → (수량, 달러), ``left`` = 못 산 주."""
    lines = [f"🧭 Polaris {int(month[5:])}월 매수 {'끝' if final else '중간'}", "━━━━━━━━━━━━"]
    for s, (q, usd) in fills.items():
        lines.append(f"✅ {s} {q:g}주 · 평균 ${usd / q:,.2f}")
    for s, q in left.items():
        lines.append(
            f"⏳ {s} {q}주 못 삼 — "
            + ("이번 회차는 여기까지 (돈은 계좌에 그대로)" if final else "다음 확인 때 다시")
        )
    lines.append(f"💸 이번에 쓴 돈 ${spent_usd:,.2f} ≈ {_won(spent_usd * fx)} (수수료 포함 추정)")
    held = holdings_lines(state, prices, fx)
    if held:
        lines.append("📦 Polaris 전체")
        lines += held
    return "\n".join(lines)
