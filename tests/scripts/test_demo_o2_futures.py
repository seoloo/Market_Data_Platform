"""데모 O/v2 선물 추가분 (59) — 목표 수량·밴드·스톱 계산."""

from __future__ import annotations

from decimal import Decimal

from scripts.demo_o2_futures import plan_leg

STEP, TICK = Decimal("0.001"), Decimal("0.01")


def test_target_is_half_of_t2_weight_times_spot_equity() -> None:
    p = plan_leg(
        "ETHUSDT",
        Decimal("0.25"),
        Decimal(5000),
        Decimal(2500),
        Decimal(2600),
        Decimal(0),
        STEP,
        TICK,
    )
    assert p.target_qty == Decimal("0.25")  # 0.25 × 0.5 × 5000 / 2500
    assert p.stop == Decimal("2080.00")  # 전날 무기한 종가 × 0.8
    assert not p.kept_by_band


def test_small_change_inside_band_keeps_current_quantity() -> None:
    # 목표 0.25, 현재 0.24 → 명목 변화 25 USDT = 자본의 0.5% < 2.5%
    p = plan_leg(
        "ETHUSDT",
        Decimal("0.25"),
        Decimal(5000),
        Decimal(2500),
        Decimal(2600),
        Decimal("0.24"),
        STEP,
        TICK,
    )
    assert p.kept_by_band and p.target_qty == Decimal("0.24")


def test_cash_signal_closes_and_drops_stop() -> None:
    p = plan_leg(
        "ETHUSDT",
        Decimal(0),
        Decimal(5000),
        Decimal(2500),
        Decimal(2600),
        Decimal("0.01"),
        STEP,
        TICK,
    )
    assert p.target_qty == 0 and p.stop is None  # 부호가 바뀌면 밴드와 상관없이 닫는다
