"""O 하루 실행 순서 계획 — 현물·선물·지갑 이체를 어떤 순서로, 얼마나 (docs/research/53, O/v2 V3).

순수 함수다(I/O 없음). 실행은 현물 주문 관리자·선물 실행기(:mod:`overlay_futures`)·이체 관리자
(:mod:`wallet_transfers`)가 이 계획의 순서대로 하고, **앞 단계가 확인돼야 다음 단계를 한다**.

순서 원칙 — 위험을 줄이는 일을 먼저, 돈을 옮기는 일은 필요한 만큼만:

1. **선물 줄이기** (증거금이 풀린다)
2. **현물 팔기**
3. **선물 → 현물 이체** — 현물 매수에 현금이 모자랄 때만. 선물 지갑에 남길 최소 금액을 지킨다
4. **현물 사기** — 이체가 모자라면 매수를 비례해 줄인다(기록)
5. **현물 → 선물 이체** — O 는 현물 밖 자본을 선물 지갑(교차 증거금)에 둔다
6. **선물 늘리기** — 선물 명목 ≤ ``max_leverage`` × 선물 지갑. 넘으면 늘리기를 줄인다(O 규칙)

선물 지갑 최소 금액 =
max(선물 명목 / ``max_leverage``, ``margin_buffer`` × 선물 명목 / ``exchange_leverage``).
앞은 O 의 레버리지 상한(3배), 뒤는 TRADING_CORE 의 "필요 증거금의 1.5배 가용 잔고"다.
거래소 레버리지 설정을 5배로 두면 필요 증거금은 명목의 20%, 1.5배면 30% 라서 O 의 33% 가 더 크다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Literal

__all__ = ["OverlayBook", "OverlayPlan", "PlanStep", "plan_overlay_day"]

_ZERO = Decimal(0)
_CENT = Decimal("0.01")
StepKind = Literal[
    "FUTURES_REDUCE",
    "SPOT_SELL",
    "TRANSFER_TO_SPOT",
    "SPOT_BUY",
    "TRANSFER_TO_FUTURES",
    "FUTURES_INCREASE",
]


@dataclass(frozen=True, slots=True)
class OverlayBook:
    """실행 직전 계좌 (거래소 조회 값)."""

    spot_usdt: Decimal
    spot_qty: Mapping[str, Decimal]
    futures_wallet: Decimal
    """선물 지갑의 증거금 잔고 (지갑 + 미실현 손익)."""
    futures_qty: Mapping[str, Decimal]
    prices: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class PlanStep:
    kind: StepKind
    symbol: str | None
    amount: Decimal
    """주문이면 수량, 이체면 USDT."""


@dataclass(slots=True)
class OverlayPlan:
    steps: list[PlanStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    futures_wallet_after: Decimal = _ZERO
    futures_notional_after: Decimal = _ZERO


def _cents(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_DOWN)


_FINE = Decimal("1e-12")


def _scaled_down(q: Decimal, factor: Decimal) -> Decimal:
    """``q × factor`` 를 아래로 자른다 — 상한에 맞춰 줄인 양이 반올림으로 상한을 넘지 않게.

    Decimal 나눗셈으로 만든 ``factor`` 는 마지막 자리에서 올림될 수 있다
    (hypothesis 반례, 2026-09-25:
    명목 213.63…0001 > 상한 213.63).
    """
    return (q * factor).quantize(_FINE, rounding=ROUND_DOWN)


def plan_overlay_day(
    book: OverlayBook,
    spot_target: Mapping[str, Decimal],
    futures_target: Mapping[str, Decimal],
    *,
    max_leverage: Decimal = Decimal(3),
    exchange_leverage: Decimal = Decimal(5),
    margin_buffer: Decimal = Decimal("1.5"),
    spot_cost: Decimal = Decimal("0.0015"),
    spot_reserve: Decimal = Decimal(1),
    min_transfer: Decimal = Decimal(1),
) -> OverlayPlan:
    """목표 수량(현물·선물, 종목별)까지 가는 순서와 이체 금액."""
    plan = OverlayPlan()
    symbols = sorted(set(spot_target) | set(futures_target) | set(book.prices))
    price = book.prices
    cur_spot = {s: book.spot_qty.get(s, _ZERO) for s in symbols}
    cur_fut = {s: book.futures_qty.get(s, _ZERO) for s in symbols}
    want_spot = {s: max(spot_target.get(s, _ZERO), _ZERO) for s in symbols}
    want_fut = {s: max(futures_target.get(s, _ZERO), _ZERO) for s in symbols}

    # 1. 선물 줄이기 · 2. 현물 팔기
    for s in symbols:
        if want_fut[s] < cur_fut[s]:
            plan.steps.append(PlanStep("FUTURES_REDUCE", s, cur_fut[s] - want_fut[s]))
    cash = book.spot_usdt
    for s in symbols:
        if want_spot[s] < cur_spot[s]:
            qty = cur_spot[s] - want_spot[s]
            plan.steps.append(PlanStep("SPOT_SELL", s, qty))
            cash += qty * price[s] * (1 - spot_cost)

    # 선물 지갑에 남겨야 할 최소 금액 (목표 선물 명목 기준)
    notional = sum((want_fut[s] * price[s] for s in symbols), _ZERO)
    need_wallet = max(notional / max_leverage, margin_buffer * notional / exchange_leverage)
    wallet = book.futures_wallet

    # 3. 선물 → 현물 이체 (매수 현금이 모자랄 때만) · 4. 현물 사기
    buys = {s: want_spot[s] - cur_spot[s] for s in symbols if want_spot[s] > cur_spot[s]}
    need_cash = sum((q * price[s] * (1 + spot_cost) for s, q in buys.items()), _ZERO)
    shortfall = need_cash + spot_reserve - cash
    if buys and shortfall > 0:
        room = max(wallet - need_wallet, _ZERO)
        amount = _cents(min(shortfall + _CENT, room))
        if amount >= min_transfer:
            plan.steps.append(PlanStep("TRANSFER_TO_SPOT", None, amount))
            cash += amount
            wallet -= amount
        if cash - spot_reserve < need_cash:
            factor = max(cash - spot_reserve, _ZERO) / need_cash if need_cash else _ZERO
            plan.notes.append(
                f"현물 매수 현금 부족 — 선물 지갑 최소 {_cents(need_wallet)} 를 지키려고 "
                f"매수를 {factor:.3f} 배로 줄인다"
            )
            buys = {s: _scaled_down(q, factor) for s, q in buys.items()}
    for s, q in buys.items():
        if q > 0:
            plan.steps.append(PlanStep("SPOT_BUY", s, q))
            cash -= q * price[s] * (1 + spot_cost)

    # 5. 현물 → 선물 이체 (남는 현금은 선물 지갑으로)
    surplus = _cents(cash - spot_reserve)
    if surplus >= min_transfer:
        plan.steps.append(PlanStep("TRANSFER_TO_FUTURES", None, surplus))
        wallet += surplus

    # 6. 선물 늘리기 (레버리지 상한 안에서)
    incs = {s: want_fut[s] - cur_fut[s] for s in symbols if want_fut[s] > cur_fut[s]}
    kept = sum((min(want_fut[s], cur_fut[s]) * price[s] for s in symbols), _ZERO)
    add = sum((q * price[s] for s, q in incs.items()), _ZERO)
    cap = wallet * max_leverage
    if incs and kept + add > cap:
        factor = max(cap - kept, _ZERO) / add
        plan.notes.append(
            f"선물 명목이 지갑의 {max_leverage}배를 넘는다 — 늘리기를 {factor:.3f} 배로"
        )
        incs = {s: _scaled_down(q, factor) for s, q in incs.items()}
    for s, q in incs.items():
        if q > 0:
            plan.steps.append(PlanStep("FUTURES_INCREASE", s, q))
    plan.futures_wallet_after = wallet
    plan.futures_notional_after = kept + sum((q * price[s] for s, q in incs.items()), _ZERO)
    return plan
