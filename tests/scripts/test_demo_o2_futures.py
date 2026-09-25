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


def test_messages_say_what_and_why() -> None:
    from scripts.demo_o2_futures import leg_message, state_message

    from auto_trading.execution.overlay_futures import LegReport, OverlayExecutionReport

    p = plan_leg(
        "ETHUSDT",
        Decimal("0.254"),
        Decimal(5000),
        Decimal(2500),
        Decimal(2686),
        Decimal(0),
        STEP,
        TICK,
    )
    enter = leg_message(p, Decimal(0), Decimal("0.254"), 3, Decimal(5000))
    assert "선물 롱 추가 진입 0 → 0.254" in enter
    assert "현물 T/v2 가 롱 중(20·60·120일 추세 3/3 상승" in enter and "총 1.5배" in enter
    assert "비상 스톱 2148.8" in enter
    hold = leg_message(p, Decimal("0.254"), Decimal("0.254"), 3, Decimal(5000))
    assert "오늘 주문 없음" in hold and "밴드 안" in hold
    out = plan_leg(
        "ETHUSDT",
        Decimal(0),
        Decimal(5000),
        Decimal(2500),
        Decimal(2686),
        Decimal("0.254"),
        STEP,
        TICK,
    )
    close = leg_message(out, Decimal("0.254"), Decimal(0), 1, Decimal(5000))
    assert "선물 추가분 청산" in close and "숏은 하지 않는다" in close

    rep = OverlayExecutionReport("DEGRADED", "DEGRADED", False)
    rep.legs.append(
        LegReport("ETHUSDT", Decimal(0), notes=["노출 증가 금지 — 목표 0.236 대신 0 유지"])
    )
    text = state_message(rep)
    assert "노출을 늘리지 않고" in text and "시작 점검(거래소 대조) DEGRADED" in text
    assert "ETHUSDT: 노출 증가 금지" in text
