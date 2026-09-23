"""A1 TRAIN 판정: 거래 수 관문 우선, T1~T5·안전 기준, 통계 미계산 보장."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from auto_trading.backtest import a1_verdict
from auto_trading.backtest.a1_verdict import (
    MIN_MAIN_TRADES,
    TradeRecord,
    judge,
    records_from_trades,
)
from auto_trading.backtest.portfolio import BacktestTrade
from auto_trading.core.enums import CloseReason, PositionSide

LONG, SHORT = PositionSide.LONG, PositionSide.SHORT


def _records(
    n: int, *, net: float = 30.0, pre: float = 60.0, spread: float = 5.0
) -> list[TradeRecord]:
    """연도·방향이 고르게 섞인 합성 기록. 값은 ±spread로 흔든다."""
    return [
        TradeRecord(
            net_bps=net + (spread if i % 2 else -spread),
            pre_cost_bps=pre + (spread if i % 2 else -spread),
            side=LONG if i % 4 < 2 else SHORT,
            year=2021 if i % 8 < 4 else 2022,
            liquidated=False,
        )
        for i in range(n)
    ]


def test_below_min_trades_is_insufficient_without_computing_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(values: object) -> None:
        raise AssertionError("거래 수 미달이면 통계를 계산하면 안 된다")

    monkeypatch.setattr(a1_verdict, "_stat", forbidden)
    verdict = judge(_records(MIN_MAIN_TRADES - 1), _records(400))
    assert verdict.status == "DATA_INSUFFICIENT"
    assert verdict.criteria == ()
    assert verdict.main_trades == MIN_MAIN_TRADES - 1


def test_all_criteria_pass() -> None:
    verdict = judge(_records(200), _records(300, net=-10.0, pre=15.0))
    assert verdict.status == "PASS"
    assert [c.name for c in verdict.criteria] == ["T1", "T2", "T3", "T4", "T5", "SAFETY"]
    json.dumps(verdict.to_dict(), allow_nan=False)


def test_t2_boundary_is_inclusive() -> None:
    at = judge(_records(200, pre=42.44, spread=0.0 + 1.0), _records(300, net=-10.0))
    names = {c.name: c.passed for c in at.criteria}
    assert names["T2"]
    below = judge(_records(200, pre=42.43), _records(300, net=-10.0))
    assert not {c.name: c.passed for c in below.criteria}["T2"]
    assert below.status == "REJECT"


def test_control_not_beaten_rejects_via_t3() -> None:
    verdict = judge(_records(200), _records(300, net=30.0))
    assert {c.name: c.passed for c in verdict.criteria}["T3"] is False
    assert verdict.status == "REJECT"


def test_one_losing_year_or_side_rejects() -> None:
    records = _records(200)
    bad_year = [
        TradeRecord(-50.0, r.pre_cost_bps, r.side, r.year, False) if r.year == 2022 else r
        for r in records
    ]
    verdict = judge(bad_year, _records(300, net=-80.0))
    assert {c.name: c.passed for c in verdict.criteria}["T4"] is False
    only_long = [TradeRecord(r.net_bps, r.pre_cost_bps, LONG, r.year, False) for r in records]
    verdict = judge(only_long, _records(300, net=-10.0))
    assert {c.name: c.passed for c in verdict.criteria}["T5"] is False


def test_liquidation_fails_safety() -> None:
    records = _records(200)
    records[0] = TradeRecord(-500.0, -500.0, LONG, 2021, True)
    verdict = judge(records, _records(300, net=-10.0))
    assert {c.name: c.passed for c in verdict.criteria}["SAFETY"] is False
    json.dumps(verdict.to_dict(), allow_nan=False)


def _trade(reason: CloseReason, exit_ref: Decimal | None) -> BacktestTrade:
    return BacktestTrade(
        symbol="BTCUSDT",
        side=LONG,
        entry_ms=1_640_995_200_000,  # 2022-01-01 UTC
        exit_ms=1_641_002_400_000,
        entry_price=Decimal("100.1"),
        exit_price=Decimal("101"),
        qty=Decimal("1"),
        close_reason=reason,
        gross_pnl=Decimal("0.9"),
        fees=Decimal("0.1"),
        funding_paid=Decimal("0"),
        planned_risk=Decimal("1"),
        leverage=5,
        mae=Decimal("0"),
        mfe=Decimal("1"),
        entry_reference_price=Decimal("100"),
        exit_reference_price=exit_ref,
    )


def test_records_convert_engine_trades() -> None:
    [record] = records_from_trades([_trade(CloseReason.SIGNAL, Decimal("101"))])
    assert record.year == 2022
    assert record.pre_cost_bps == pytest.approx(100.0)
    assert record.net_bps == pytest.approx(80.0)
    [liquidated] = records_from_trades([_trade(CloseReason.LIQUIDATION, None)])
    assert liquidated.liquidated
    with pytest.raises(ValueError, match="기준가"):
        records_from_trades([_trade(CloseReason.SIGNAL, None)])
