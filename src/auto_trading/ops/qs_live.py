"""QS 실거래 (docs/research/118) — QNDX 적립을 문서 110 QS 규칙으로 바꾸는 순수 판단. I/O 없음.

규칙 (110 그대로, 사용자 결정 2026-10-04 "실거래를 하자"):

* QQQ T 규칙(20·60·120 거래일 부호 평균 > 0)을 **끝난 미국 거래일 종가까지로** 매일 계산한다.
* 켜짐: QNDX 적립 그대로 (문서 89).
* 꺼짐: QNDX 매수를 멈춘다. 적립금(매일 1 만 원)은 그대로 쌓인다 — 이것이 현금이다.
* 꺼짐 → 켜짐: 쌓인 적립금 전부로 TQQQ 예약 매수(정수 주). 산 뒤 남은 돈은 QNDX 로 이어진다.
* 켜짐 → 꺼짐: QS 가 산 TQQQ 만 전부 예약 매도. 판 돈은 적립금으로 돌아가 다음 TQQQ 매수에 쓴다.
* QNDX 는 팔지 않는다. 사용자가 원래 가진 TQQQ(시작 때 기준 수량)는 건드리지 않는다.

적립금 계산은 원화 하나로 한다: QNDX 적립금에서 (TQQQ 산 돈 − TQQQ 판 돈) 을 더 뺀다.
판 돈은 체결가를 모르므로 **다음 실행 때 TQQQ 직전 종가 × 수량 × (1 − 수수료)** 로 추정한다
(예약 매도는 장 시작 때 체결 — 실제와 몇 % 다를 수 있다. 실제 돈은 계좌에 그대로 있다).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from auto_trading.ops.qndx_alert import FEE, LIMIT_BUFFER, OPEN_ORDER, ORDER_WINDOW, Order

__all__ = [
    "MAX_ORDER_USD",
    "MAX_TQQQ_QTY",
    "TQQQ",
    "QsLiveState",
    "QsTrade",
    "TqqqOrder",
    "plan_tqqq_order",
    "qndx_paused",
    "reconcile_tqqq",
    "resolve_tqqq_orders",
    "trend_parts",
    "update_signal",
]

TQQQ = "TQQQ"
TQQQ_QUOTE_EXCHANGE = "NAS"
TQQQ_ORDER_EXCHANGE = "NASD"
MAX_TQQQ_LIMIT_USD = 250.0
"""지정가 상한 (2026-10-02 종가 81 USD) — 설정·시세 오류로 비싼 주문이 나가지 않게."""
MAX_ORDER_USD = 5_000.0
"""한 번에 TQQQ 를 사는 금액 상한. 월 30 만 원 적립으로는 꺼진 기간이 몇 달이어도 넘지 않는다."""
MAX_TQQQ_QTY = 60
"""어댑터에 주는 수량 상한 (5,000 USD ÷ 약 81 USD)."""
LOOKBACKS = (20, 60, 120)


def trend_parts(closes: Sequence[float]) -> dict[str, float]:
    """마지막 종가 기준 20·60·120 거래일 수익."""
    return {f"r{k}": closes[-1] / closes[-1 - k] - 1 for k in LOOKBACKS}


@dataclass(frozen=True, slots=True)
class QsTrade:
    date: str
    side: str
    """BUY · SELL."""
    qty: float
    usd: float
    """BUY: 매입 금액(평균가 차이). SELL: 추정 매도 금액(수수료 뺌)."""
    fx_rate: float
    krw: float
    """적립금에 미치는 원화 — BUY 는 수수료 포함 쓴 돈, SELL 은 돌아온 돈."""
    estimated: bool = False


@dataclass(slots=True)
class QsLiveState:
    start_date: str
    baseline_qty: float
    """시작 때 이미 가진 TQQQ — QS 가 팔지 않는다."""
    last_qty: float
    last_cost_usd: float
    prev_on: bool | None = None
    want: str = "NONE"
    """NONE · BUY (꺼짐→켜짐 뒤 아직 못 삼) · SELL (켜짐→꺼짐 뒤 아직 못 팖)."""
    trades: list[QsTrade] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    """[{"date", "as_of", "on"}] 최근 기록 (보고·점검용)."""

    @property
    def strategy_qty(self) -> float:
        return max(self.last_qty - self.baseline_qty, 0.0)

    def net_krw(self) -> float:
        """QNDX 적립금에서 더 뺄 돈 = TQQQ 산 돈 − 판 돈."""
        return sum(t.krw if t.side == "BUY" else -t.krw for t in self.trades)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QsLiveState:
        return cls(
            start_date=str(d["start_date"]),
            baseline_qty=float(d["baseline_qty"]),
            last_qty=float(d["last_qty"]),
            last_cost_usd=float(d["last_cost_usd"]),
            prev_on=d.get("prev_on"),
            want=str(d.get("want", "NONE")),
            trades=[QsTrade(**t) for t in d.get("trades", [])],
            orders=[Order(**o) for o in d.get("orders", [])],
            signals=list(d.get("signals", [])),
        )


def update_signal(state: QsLiveState, today: str, as_of: str, on: bool) -> list[str]:
    """오늘 쓸 신호(``as_of`` 미국 종가까지)로 할 일(``want``)을 정한다.

    같은 날 두 번째부터는 그대로 둔다.
    """
    notes: list[str] = []
    if state.signals and state.signals[-1]["date"] == today:
        return notes  # 같은 날 다시 실행(재시도·밀린 작업) — 전환은 그날 첫 실행에서만
    prev = state.prev_on
    state.signals = [*state.signals[-59:], {"date": today, "as_of": as_of, "on": on}]
    if prev is None:
        notes.append(f"QS 시작 — 추세 {'켜짐' if on else '꺼짐'}에서 출발 (전환 없음)")
    elif on and not prev:
        if state.want == "SELL":
            notes.append("QS: TQQQ 를 팔기 전에 추세가 다시 켜졌다 — 그대로 두고 더 산다")
        state.want = "BUY"
        notes.append("QS: 추세가 다시 켜졌다 — 쌓인 적립금으로 TQQQ 산다")
    elif not on and prev:
        if state.want == "BUY":
            notes.append("QS: TQQQ 를 사기 전에 추세가 다시 꺼졌다 — 사지 않는다")
        state.want = "SELL" if state.strategy_qty > 0 else "NONE"
        notes.append(
            "QS: 추세가 꺾였다 — QNDX 매수를 멈추고 적립금을 모은다"
            + (" · QS 의 TQQQ 를 판다" if state.want == "SELL" else "")
        )
    state.prev_on = on
    return notes


def qndx_paused(state: QsLiveState) -> bool:
    """추세가 꺼졌거나 TQQQ 를 살 차례면 QNDX 를 사지 않는다 (적립금을 TQQQ 몫으로 남긴다)."""
    return state.prev_on is False or state.want == "BUY"


def reconcile_tqqq(
    state: QsLiveState,
    today: str,
    qty: float,
    avg_usd: float,
    last_usd: float,
    fx_rate: float,
    *,
    fee: float = FEE,
    sold_usd: float | None = None,
) -> list[str]:
    """TQQQ 잔고 변화로 체결을 찾는다. 늘면 산 것, 줄면 판 것.

    판 금액은 ``sold_usd``(체결 조회, 수수료 뺀 값)가 있으면 그것, 없으면 직전 종가로 추정.
    """
    notes: list[str] = []
    dq = qty - state.last_qty
    cost_total = qty * avg_usd
    if dq > 1e-9:
        usd = max(cost_total - state.last_cost_usd, 0.0)
        krw = usd * fx_rate * (1 + fee)
        state.trades.append(QsTrade(today, "BUY", dq, usd, fx_rate, krw))
        if state.want == "BUY":
            state.want = "NONE"
        notes.append(f"QS: TQQQ {dq:g}주 매수 확인 — {usd:,.2f} USD ({krw:,.0f}원) 적립금에서 뺌")
    elif dq < -1e-9:
        sold = min(-dq, state.strategy_qty)
        if sold > 1e-9:
            exact = sold_usd is not None
            usd = sold_usd if sold_usd is not None else sold * last_usd * (1 - fee)
            krw = usd * fx_rate
            trade = QsTrade(today, "SELL", sold, usd, fx_rate, krw, estimated=not exact)
            state.trades.append(trade)
            notes.append(
                f"QS: TQQQ {sold:g}주 매도 확인 — {'' if exact else '약 '}{usd:,.2f} USD"
                f" ({krw:,.0f}원{'' if exact else ', 직전 종가로 추정'}) 적립금으로 돌아옴"
            )
        if -dq - sold > 1e-9:
            notes.append(
                f"TQQQ 가 QS 몫보다 {-dq - sold:g}주 더 줄었다(직접 매도?) — 기준 수량을 줄인다"
            )
            state.baseline_qty = max(state.baseline_qty - (-dq - sold), 0.0)
    state.last_qty, state.last_cost_usd = qty, cost_total
    if state.want == "SELL" and state.strategy_qty <= 1e-9:
        state.want = "NONE"
    return notes


def resolve_tqqq_orders(state: QsLiveState, today: str, changed: bool) -> list[str]:
    """지난날 TQQQ 예약주문(당일 유효)을 정리.

    이번 확인에서 수량이 바뀌었으면 FILLED, 아니면 EXPIRED.
    """
    notes: list[str] = []
    open_past = [o for o in state.orders if o.status in OPEN_ORDER and o.date < today]
    for k, order in enumerate(open_past):
        filled = changed and k == len(open_past) - 1
        order.status = "FILLED" if filled else "EXPIRED"
        if not filled:
            notes.append(f"QS: {order.date} TQQQ 예약주문이 체결되지 않고 만료 — 오늘 다시 판단")
    return notes


@dataclass(frozen=True, slots=True)
class TqqqOrder:
    side: str
    """BUY · SELL · NONE."""
    qty: int
    limit_usd: float
    reason: str


def plan_tqqq_order(
    state: QsLiveState,
    today: str,
    *,
    accrued_krw: float,
    fx_rate: float,
    last_usd: float,
    weekday: int,
    kst_hhmm: str | None,
    trading_enabled: bool,
    halted: str | None,
    fee: float = FEE,
    window: tuple[str, str] = ORDER_WINDOW,
) -> TqqqOrder:
    """오늘 TQQQ 예약주문을 낼지. 하루 1 건, 열린 주문이 있으면 내지 않는다."""

    def none(reason: str) -> TqqqOrder:
        return TqqqOrder("NONE", 0, 0.0, reason)

    if state.want == "NONE":
        return none("할 일 없음")
    if state.want == "BUY":
        limit = math.floor(last_usd * (1 + LIMIT_BUFFER) * 100) / 100
        budget = min(max(accrued_krw, 0.0) / fx_rate, MAX_ORDER_USD)
        qty = min(int(budget // (limit * (1 + fee))), MAX_TQQQ_QTY)
        if qty < 1:
            state.want = "NONE"
            return none(
                f"적립금 {accrued_krw:,.0f}원이 TQQQ 1주({limit:.2f} USD)에 모자란다"
                " — 이번 회복에는 사지 않고 QNDX 적립으로 이어간다"
            )
    else:
        limit = math.floor(last_usd * (1 - LIMIT_BUFFER) * 100) / 100
        qty = int(state.strategy_qty)
        if qty < 1:
            state.want = "NONE"
            return none("팔 TQQQ 가 없다")
    side = state.want
    word = "매수" if side == "BUY" else "매도"
    if not trading_enabled:
        return none(f"자동 주문 꺼짐 — 앱에서 TQQQ {qty}주 {word} (지정가 {limit:.2f} USD)")
    if halted is not None:
        return none(f"긴급 정지 켜짐 (data/HALT: {halted})")
    if weekday >= 5:
        return none("주말이라 오늘 밤 미국 장이 없다 — 월요일 밤에")
    start, end = window
    if kst_hhmm is not None and not start <= kst_hhmm < end:
        return none(f"예약주문 접수 시간({start}~{end}) 밖 — 다음 실행에서")
    if any(o.date == today for o in state.orders):
        return none("오늘은 이미 TQQQ 주문을 냈다")
    if any(o.status in OPEN_ORDER for o in state.orders):
        return none("확인되지 않은 TQQQ 주문이 있다 — 다음 날 잔고로 확인한 뒤")
    if not 0 < limit <= MAX_TQQQ_LIMIT_USD:
        return none(f"지정가 {limit:.2f} USD 가 상한 {MAX_TQQQ_LIMIT_USD} USD 밖")
    return TqqqOrder(side, qty, limit, f"TQQQ {qty}주 예약 {word}")
