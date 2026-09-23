"""WA 판정: 거래 수 관문 우선, 방향 선택, 방향별 별도 기록 사용, T1~T4·안전."""

from __future__ import annotations

import json

import pytest

from auto_trading.backtest import wa_verdict
from auto_trading.backtest.a1_verdict import TradeRecord
from auto_trading.backtest.wa_verdict import MIN_TRADES, judge_wa
from auto_trading.core.enums import PositionSide

LONG, SHORT = PositionSide.LONG, PositionSide.SHORT


def records(
    n: int, *, side: PositionSide, net: float, pre: float, spread: float = 5.0
) -> list[TradeRecord]:
    return [
        TradeRecord(
            net_bps=net + (spread if i % 2 else -spread),
            pre_cost_bps=pre + (spread if i % 2 else -spread),
            side=side,
            year=2023 if i % 4 < 2 else 2024,
            liquidated=False,
        )
        for i in range(n)
    ]


def test_count_gate_uses_smaller_main_arm_and_computes_no_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(values: object) -> None:
        raise AssertionError("관문 미달이면 통계를 계산하지 않는다")

    monkeypatch.setattr(wa_verdict, "_stat", forbidden)
    verdict = judge_wa(
        records(300, side=LONG, net=50, pre=70),
        records(MIN_TRADES - 1, side=SHORT, net=50, pre=70),
        records(300, side=LONG, net=0, pre=15),
        records(300, side=SHORT, net=0, pre=15),
    )
    assert verdict.status == "DATA_INSUFFICIENT"
    assert verdict.direction is None and verdict.criteria == ()
    assert verdict.account_replay == "NOT_RUN"


def test_positive_long_mean_selects_long_and_passes() -> None:
    verdict = judge_wa(
        records(200, side=LONG, net=50, pre=70),
        records(200, side=SHORT, net=-80, pre=-70),
        records(300, side=LONG, net=0, pre=15),
        records(300, side=SHORT, net=-20, pre=-5),
    )
    assert verdict.direction == "long"
    assert verdict.status == "PASS"
    json.dumps(verdict.to_dict(), allow_nan=False)


def test_negative_long_mean_uses_separately_replayed_short_records() -> None:
    # 숏 재생 기록이 롱의 단순 부호 반전과 달라도 숏 기록만 쓴다.
    verdict = judge_wa(
        records(200, side=LONG, net=-90, pre=-70),
        records(200, side=SHORT, net=10, pre=30),
        records(300, side=LONG, net=0, pre=15),
        records(300, side=SHORT, net=0, pre=15),
    )
    assert verdict.direction == "short"
    results = {c.name: c.passed for c in verdict.criteria}
    assert results["T2"] is False  # 숏 재생 비용 전 30bps < 60
    assert verdict.status == "REJECT"


def test_trend_holding_control_can_reject_via_t3() -> None:
    verdict = judge_wa(
        records(200, side=LONG, net=50, pre=70),
        records(200, side=SHORT, net=-80, pre=-70),
        records(300, side=LONG, net=50, pre=70),
        records(300, side=SHORT, net=-50, pre=-40),
    )
    assert {c.name: c.passed for c in verdict.criteria}["T3"] is False
    assert verdict.status == "REJECT"


def test_losing_year_and_liquidation_reject() -> None:
    main = records(200, side=LONG, net=50, pre=70)
    bad_year = [
        TradeRecord(-100.0, r.pre_cost_bps, r.side, r.year, False) if r.year == 2024 else r
        for r in main
    ]
    verdict = judge_wa(
        bad_year,
        records(200, side=SHORT, net=0, pre=0),
        records(300, side=LONG, net=-60, pre=-40),
        records(300, side=SHORT, net=0, pre=0),
    )
    assert {c.name: c.passed for c in verdict.criteria}["T4"] is False
    liquidated = [TradeRecord(-500.0, -500.0, LONG, 2023, True), *main[1:]]
    verdict = judge_wa(
        liquidated,
        records(200, side=SHORT, net=0, pre=0),
        records(300, side=LONG, net=0, pre=15),
        records(300, side=SHORT, net=0, pre=0),
    )
    assert {c.name: c.passed for c in verdict.criteria}["SAFETY"] is False
