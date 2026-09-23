"""V/v1 변동성 돌파 계산 테스트 (18 사전등록)."""

from __future__ import annotations

import pytest

from auto_trading.backtest.vbreak import DayBar, breakout_trades

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
