"""K/v1 펀딩 캐리 시뮬레이터 합성 데이터 테스트 (10 사전등록 §2)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest

from auto_trading.backtest.carry import (
    CarryConfig,
    CarryHour,
    CarryResult,
    FundingEvent,
    judge_carry,
    max_drawdown,
    monthly_returns,
    simulate_carry,
    yearly_returns,
)

H = 3_600_000
START = 1_672_531_200_000  # 2023-01-01 00:00 UTC
EQUITY = Decimal(10_000)
NO_COST = CarryConfig(spot_fee=Decimal(0), perp_fee=Decimal(0), slippage=Decimal(0))


def flat_hours(n: int, price: Decimal = Decimal(100), start: int = START) -> list[CarryHour]:
    return [CarryHour(start + i * H, price, price, price, price, price) for i in range(n)]


def funding_every_8h(
    n_hours: int, rate: Decimal, *, start: int = START, history: int = 21, offset_ms: int = 0
) -> list[FundingEvent]:
    """구간 전 ``history``회 + 구간 안 8시간마다 같은 rate."""
    first = start - history * 8 * H
    return [
        FundingEvent(ms + offset_ms, rate, Decimal(100))
        for ms in range(first, start + n_hours * H, 8 * H)
    ]


def run(
    hours: Sequence[CarryHour],
    funding: Sequence[FundingEvent],
    cfg: CarryConfig = NO_COST,
    *,
    to_ms: int | None = None,
) -> CarryResult:
    return simulate_carry(
        hours,
        funding,
        from_ms=START,
        to_ms=to_ms if to_ms is not None else hours[-1].open_ms,
        initial_equity=EQUITY,
        cfg=cfg,
    )


class TestFunding:
    def test_positive_funding_accrues_to_short_without_price_exposure(self):
        hours = flat_hours(24 * 10)
        result = run(hours, funding_every_8h(24 * 10, Decimal("0.0001")))
        assert result.entries == 1
        # 첫 정산(00:00)에 판단 → 01:00 체결. 이후 정산 29회 수취, 수량 = 10000·2/3/100
        qty = EQUITY * 2 / 3 / 100
        expected = qty * 100 * Decimal("0.0001") * 29
        assert result.funding_income == pytest.approx(expected)
        assert result.final_equity == pytest.approx(EQUITY + expected)

    def test_settlement_timestamp_offset_is_aligned_to_hour(self):
        hours = flat_hours(24 * 3)
        result = run(hours, funding_every_8h(24 * 3, Decimal("0.0001"), offset_ms=3))
        assert result.entries == 1
        assert result.funding_income > 0

    def test_fill_is_one_hour_after_settlement(self):
        hours = flat_hours(24)
        result = run(hours, funding_every_8h(24, Decimal("0.0001")))
        assert result.hours_in_market == 23  # 00:00 판단, 01:00~23:00 보유

    def test_below_enter_threshold_stays_flat(self):
        hours = flat_hours(24 * 5)
        result = run(hours, funding_every_8h(24 * 5, Decimal("0.00004")))
        assert result.entries == 0
        assert result.final_equity == EQUITY

    def test_negative_signal_exits_and_hysteresis_holds_between(self):
        hours = flat_hours(24 * 30)
        positive = funding_every_8h(24 * 10, Decimal("0.0001"))
        # 10일 뒤부터 0.00002 (진입 문턱 아래지만 0 이상) → 해제하지 않는다
        mid = [
            FundingEvent(START + 24 * 10 * H + k * 8 * H, Decimal("0.00002"), Decimal(100))
            for k in range(30)
        ]
        negative = [
            FundingEvent(START + 24 * 20 * H + k * 8 * H, Decimal("-0.0003"), Decimal(100))
            for k in range(30)
        ]
        result = run(hours, positive + mid + negative)
        assert result.entries == 1
        assert result.exits == 1

    def test_control_enters_immediately_and_ignores_signal(self):
        hours = flat_hours(24 * 3)
        cfg = CarryConfig(
            spot_fee=Decimal(0), perp_fee=Decimal(0), slippage=Decimal(0), use_filter=False
        )
        result = run(hours, funding_every_8h(24 * 3, Decimal("-0.0001")), cfg)
        assert result.entries == 1
        assert result.hours_in_market == 24 * 3
        assert result.funding_income < 0


class TestCostsAndHedge:
    def test_round_trip_costs_match_fee_schedule(self):
        hours = flat_hours(24 * 2)
        result = run(
            hours, funding_every_8h(24 * 2, Decimal(0)), CarryConfig(enter_rate=Decimal(0))
        )
        notional = EQUITY * 2 / 3
        # 진입+청산: 현물 0.10%·선물 0.045%·다리별 슬리피지 0.02% ×2 → 명목의 약 0.37%
        loss = float(EQUITY - result.final_equity)
        assert loss == pytest.approx(float(notional) * 0.0037, rel=0.01)

    def test_hedged_book_is_flat_to_price_moves(self):
        hours = [
            CarryHour(START + i * H, p, p, p, p, p)
            for i, p in enumerate(Decimal(100 + i) for i in range(48))
        ]
        result = run(
            hours,
            funding_every_8h(48, Decimal(0)),
            CarryConfig(
                enter_rate=Decimal(0), spot_fee=Decimal(0), perp_fee=Decimal(0), slippage=Decimal(0)
            ),
        )
        assert result.final_equity == pytest.approx(EQUITY)

    def test_rebalance_after_20pct_move_keeps_equity_and_avoids_liquidation(self):
        prices = [Decimal(100)] * 5 + [Decimal(100) * Decimal("1.02") ** k for k in range(1, 40)]
        hours = [CarryHour(START + i * H, p, p, p, p, p) for i, p in enumerate(prices)]
        result = run(
            hours,
            funding_every_8h(len(prices), Decimal(0)),
            CarryConfig(
                enter_rate=Decimal(0), spot_fee=Decimal(0), perp_fee=Decimal(0), slippage=Decimal(0)
            ),
        )
        assert result.rebalances >= 2
        assert result.liquidations == 0
        assert result.final_equity == pytest.approx(EQUITY)

    def test_liquidation_on_intrahour_spike(self):
        hours = flat_hours(5)
        spike = Decimal(160)
        hours.append(
            CarryHour(START + 5 * H, Decimal(100), spike, Decimal(100), Decimal(100), Decimal(100))
        )
        result = run(hours, funding_every_8h(6, Decimal(0)), CarryConfig(enter_rate=Decimal(0)))
        assert result.liquidations == 1

    def test_missing_spot_bar_defers_fill(self):
        hours = flat_hours(4)
        hours[1] = CarryHour(START + H, Decimal(100), Decimal(100), Decimal(100), None, None)
        result = run(hours, funding_every_8h(4, Decimal("0.0001")))
        assert result.deferred_fills == 1
        assert result.entries == 1
        assert result.hours_in_market == 2


class TestJudge:
    def test_returns_and_drawdown_helpers(self):
        hours = flat_hours(24 * 40)
        result = run(hours, funding_every_8h(24 * 40, Decimal("0.0001")))
        months = monthly_returns(result)
        assert len(months) == 2
        assert all(m > 0 for m in months)
        assert set(yearly_returns(result)) == {2023}
        assert max_drawdown(result) == 0.0

    def test_verdict_pass_and_reject(self):
        hours = flat_hours(24 * 90)
        good = run(hours, funding_every_8h(24 * 90, Decimal("0.0002")))
        verdict = judge_carry(good, years=[2023])
        assert verdict.status == "PASS", verdict.to_dict()
        weak = run(hours, funding_every_8h(24 * 90, Decimal("0.00006")))
        assert judge_carry(weak, years=[2023]).status == "REJECT"

    def test_stress_mode_checks_only_safety_and_drawdown(self):
        hours = flat_hours(24 * 5)
        result = run(hours, funding_every_8h(24 * 5, Decimal(0)))
        verdict = judge_carry(result, years=[], require_return=False, max_mdd=0.10)
        assert [name for name, _, _ in verdict.criteria] == ["K4", "SAFETY"]
        assert verdict.status == "PASS"
