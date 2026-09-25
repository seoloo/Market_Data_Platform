"""O 하루 실행 순서 계획 (53 V3): 위험 축소 먼저, 이체는 필요한 만큼, 선물 지갑 최소 금액·상한."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from auto_trading.execution.overlay_plan import OverlayBook, plan_overlay_day

P = {"BTCUSDT": Decimal(50_000), "ETHUSDT": Decimal(2_500)}
ORDER = [
    "FUTURES_REDUCE",
    "SPOT_SELL",
    "TRANSFER_TO_SPOT",
    "SPOT_BUY",
    "TRANSFER_TO_FUTURES",
    "FUTURES_INCREASE",
]
Z = Decimal(0)


def book(usdt: str, spot: dict[str, str], wallet: str, fut: dict[str, str]) -> OverlayBook:
    return OverlayBook(
        Decimal(usdt),
        {k: Decimal(v) for k, v in spot.items()},
        Decimal(wallet),
        {k: Decimal(v) for k, v in fut.items()},
        P,
    )


def d(x: dict[str, str]) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in x.items()}


def test_steps_follow_risk_first_order() -> None:
    b = book("100", {"BTCUSDT": "0.1", "ETHUSDT": "1"}, "5000", {"BTCUSDT": "0.05"})
    plan = plan_overlay_day(
        b, d({"BTCUSDT": "0.05", "ETHUSDT": "2"}), d({"BTCUSDT": "0.02", "ETHUSDT": "0.5"})
    )
    kinds = [s.kind for s in plan.steps]
    assert kinds == sorted(kinds, key=ORDER.index)
    assert kinds[0] == "FUTURES_REDUCE" and kinds[-1] == "FUTURES_INCREASE"


def test_buy_shortfall_is_transferred_from_futures_within_the_margin_floor() -> None:
    # 현물 현금 0, 0.1 BTC(5,000 + 비용) 매수 필요, 선물 목표 0 → 지갑 전부 옮길 수 있다
    plan = plan_overlay_day(book("0", {}, "10000", {}), d({"BTCUSDT": "0.1"}), {})
    t = next(s for s in plan.steps if s.kind == "TRANSFER_TO_SPOT")
    assert Decimal("5008.5") <= t.amount <= Decimal("5009.6")  # 5,000 × 1.0015 + 예비 1
    assert any(s.kind == "SPOT_BUY" and s.amount == Decimal("0.1") for s in plan.steps)
    assert not plan.notes


def test_margin_floor_scales_buys_instead_of_draining_futures() -> None:
    # 선물 명목 목표 0.3 BTC = 15,000 → 지갑 최소 5,000 (3배). 지갑 6,000 → 옮길 수 있는 것 1,000
    b = book("0", {}, "6000", {"BTCUSDT": "0.3"})
    plan = plan_overlay_day(b, d({"BTCUSDT": "0.1"}), d({"BTCUSDT": "0.3"}))
    t = next(s for s in plan.steps if s.kind == "TRANSFER_TO_SPOT")
    assert t.amount == Decimal(1000)
    buy = next(s for s in plan.steps if s.kind == "SPOT_BUY")
    assert buy.amount < Decimal("0.1") and plan.notes
    assert plan.futures_wallet_after >= plan.futures_notional_after / 3


def test_surplus_cash_goes_to_futures_wallet() -> None:
    plan = plan_overlay_day(book("3000", {}, "1000", {}), {}, {})
    (t,) = plan.steps
    assert t.kind == "TRANSFER_TO_FUTURES" and t.amount == Decimal(2999)  # 예비 1 USDT 남김


def test_futures_increase_is_capped_at_three_times_wallet() -> None:
    plan = plan_overlay_day(book("1", {}, "1000", {}), {}, d({"BTCUSDT": "0.1"}))  # 5,000 명목
    inc = next(s for s in plan.steps if s.kind == "FUTURES_INCREASE")
    assert inc.amount == Decimal("0.06")  # 3,000 / 50,000
    assert plan.futures_notional_after <= plan.futures_wallet_after * 3
    assert plan.notes


def test_nothing_to_do() -> None:
    b = book("0.5", {"BTCUSDT": "0.1"}, "2000", {"BTCUSDT": "0.05"})
    assert plan_overlay_day(b, d({"BTCUSDT": "0.1"}), d({"BTCUSDT": "0.05"})).steps == []


def test_margin_buffer_rule_can_dominate_with_low_exchange_leverage() -> None:
    # 거래소 레버리지 3배면 1.5 × 명목/3 = 명목/2 > 명목/3
    b = book("0", {}, "10000", {"BTCUSDT": "0.2"})  # 명목 10,000 → 최소 지갑 5,000
    plan = plan_overlay_day(
        b, d({"BTCUSDT": "0.2"}), d({"BTCUSDT": "0.2"}), exchange_leverage=Decimal(3)
    )
    t = next(s for s in plan.steps if s.kind == "TRANSFER_TO_SPOT")
    assert t.amount == Decimal(5000)


amounts = st.decimals(min_value=0, max_value=3, places=3)


@settings(max_examples=150, deadline=None)
@given(
    usdt=st.decimals(min_value=0, max_value=20_000, places=2),
    wallet=st.decimals(min_value=0, max_value=20_000, places=2),
    spot_now=amounts,
    spot_want=amounts,
    fut_now=amounts,
    fut_want=amounts,
)
def test_plan_invariants(
    usdt: Decimal,
    wallet: Decimal,
    spot_now: Decimal,
    spot_want: Decimal,
    fut_now: Decimal,
    fut_want: Decimal,
) -> None:
    """어떤 계좌·목표에서도: 순서 지킴, 선물로 옮긴 돈 뒤 지갑은 최소 금액 이상,
    늘리기 뒤 선물 명목은 지갑 3배 이하(이미 든 것을 줄이지 못한 경우 제외), 이체는 양수."""
    b = OverlayBook(usdt, {"ETHUSDT": spot_now}, wallet, {"ETHUSDT": fut_now}, P)
    plan = plan_overlay_day(b, {"ETHUSDT": spot_want}, {"ETHUSDT": fut_want})
    kinds = [s.kind for s in plan.steps]
    assert kinds == sorted(kinds, key=ORDER.index)
    assert all(s.amount > 0 for s in plan.steps)
    notional_target = fut_want * P["ETHUSDT"]
    if "TRANSFER_TO_SPOT" in kinds:
        floor = max(notional_target / 3, Decimal("1.5") * notional_target / 5)
        assert plan.futures_wallet_after >= floor - Decimal("0.01")
    kept = min(fut_now, fut_want) * P["ETHUSDT"]
    if "FUTURES_INCREASE" in kinds:
        assert plan.futures_notional_after <= max(plan.futures_wallet_after * 3, kept) + Z


def test_scaled_increase_never_exceeds_leverage_cap_by_rounding() -> None:
    """hypothesis 가 찾은 반례 (2026-09-25): 명목 213.63…0001 > 상한 3 × 71.21."""
    b = OverlayBook(
        Decimal("0.00"), {"ETHUSDT": Decimal(0)}, Decimal("71.21"), {"ETHUSDT": Decimal(0)}, P
    )
    plan = plan_overlay_day(b, {"ETHUSDT": Decimal(0)}, {"ETHUSDT": Decimal("0.820")})
    assert plan.futures_notional_after <= plan.futures_wallet_after * 3
