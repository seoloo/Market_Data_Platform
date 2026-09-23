"""V/v1 변동성 돌파 계산 테스트 (18 사전등록)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from auto_trading.backtest.vbreak import (
    DayBar,
    HourBar,
    breakout_trades,
    resolved_breakout_trades,
)

D = 86_400_000


def bars(*days: tuple[float, float, float, float]) -> list[DayBar]:
    return [DayBar(k * D, o, h, lo, c) for k, (o, h, lo, c) in enumerate(days)]


def test_upside_breakout_goes_long_at_trigger_and_exits_at_close() -> None:
    # 전날 폭 20 → k=0.5면 10. 시가 100 → 위 110, 아래 90
    trades, ambiguous = breakout_trades(
        bars((100, 110, 90, 100), (100, 120, 95, 115)), k=0.5, cost_bps=24
    )
    assert ambiguous == 0
    (trade,) = trades
    assert trade.side == 1
    assert trade.gross_bps == pytest.approx((115 / 110 - 1) * 1e4)
    assert trade.net_bps == pytest.approx(trade.gross_bps - 24)


def test_downside_breakout_goes_short() -> None:
    trades, _ = breakout_trades(bars((100, 110, 90, 100), (100, 105, 80, 85)), k=0.5, cost_bps=0)
    (trade,) = trades
    assert trade.side == -1
    assert trade.gross_bps == pytest.approx(-(85 / 90 - 1) * 1e4)


def test_both_sides_touched_is_skipped() -> None:
    trades, ambiguous = breakout_trades(
        bars((100, 110, 90, 100), (100, 115, 85, 100)), k=0.5, cost_bps=0
    )
    assert trades == []
    assert ambiguous == 1


def test_no_touch_no_trade() -> None:
    trades, ambiguous = breakout_trades(
        bars((100, 110, 90, 100), (100, 105, 95, 101)), k=0.5, cost_bps=0
    )
    assert (trades, ambiguous) == ([], 0)


def test_uses_only_previous_day_range() -> None:
    """전날 폭만 쓴다 — 당일 고저는 돌파 가격에 들어가지 않는다."""
    wide_today = bars((100, 101, 99, 100), (100, 150, 99.5, 140))
    (trade,) = breakout_trades(wide_today, k=0.5, cost_bps=0)[0]
    assert trade.gross_bps == pytest.approx((140 / 101 - 1) * 1e4)


def test_gap_in_days_raises() -> None:
    broken = [DayBar(0, 100, 110, 90, 100), DayBar(2 * D, 100, 120, 95, 115)]
    with pytest.raises(ValueError, match="연속"):
        breakout_trades(broken, k=0.5, cost_bps=0)


class TestResolvedV11:
    """V/v1.1 — 양방향 돌파일도 거래한다 (미래 정보로 빼지 않는다)."""

    H = 3_600_000

    def _hours(self, day: int, path: Sequence[tuple[float, float]]) -> list[HourBar]:
        return [HourBar(day + i * self.H, hi, lo) for i, (hi, lo) in enumerate(path)]

    def test_up_first_then_crash_is_a_losing_long(self) -> None:
        days = bars((100, 110, 90, 100), (100, 115, 85, 88))  # 위 110·아래 90 모두 닿음
        hours = {D: self._hours(D, [(105, 99), (112, 104), (108, 86)] + [(95, 87)] * 21)}
        trades, res = resolved_breakout_trades(days, hours, k=0.5, cost_bps=0)
        (trade,) = trades
        assert trade.side == 1
        assert trade.gross_bps == pytest.approx((88 / 110 - 1) * 1e4)
        assert (res.by_hour, res.worse_of) == (1, 0)

    def test_same_hour_both_sides_takes_worse_outcome(self) -> None:
        days = bars((100, 110, 90, 100), (100, 115, 85, 112))
        hours = {D: self._hours(D, [(115, 85)] + [(113, 105)] * 23)}
        (trade,), res = resolved_breakout_trades(days, hours, k=0.5, cost_bps=0)
        long_gross, short_gross = (112 / 110 - 1) * 1e4, -(112 / 90 - 1) * 1e4
        assert trade.gross_bps == pytest.approx(min(long_gross, short_gross))
        assert trade.side == -1
        assert res.worse_of == 1

    def test_missing_hour_before_first_touch_takes_worse_outcome(self) -> None:
        days = bars((100, 110, 90, 100), (100, 115, 85, 100))
        hours = {D: [*self._hours(D, [(105, 95)]), HourBar(D + 2 * self.H, 115, 100)]}
        (_, res) = resolved_breakout_trades(days, hours, k=0.5, cost_bps=0)
        assert res.worse_of == 1

    def test_single_side_days_match_v1(self) -> None:
        days = bars((100, 110, 90, 100), (100, 120, 95, 115), (115, 130, 110, 125))
        v1, _ = breakout_trades(days, k=0.5, cost_bps=24)
        v11, res = resolved_breakout_trades(days, {}, k=0.5, cost_bps=24)
        assert v1 == v11
        assert (res.by_hour, res.worse_of) == (0, 0)
