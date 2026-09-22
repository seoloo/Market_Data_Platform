"""post-only 진입 모델 테스트.

체결을 낙관적으로 판정하면 maker 전환이 비용 절감이 아니라 희망이 된다.
그래서 "뚫고 지나가야 체결" 과 "채움봉에서는 체결 없음" 두 규칙을 고정한다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.backtest.maker import (
    MakerOutcome,
    TimeoutPolicy,
    simulate_post_only,
)
from auto_trading.core.enums import PositionSide
from auto_trading.core.types import Candle

D = Decimal
MINUTE = 60_000
MAKER_FEE = D("0.0002")
TAKER_FEE = D("0.00045")
CHASE_SLIP = D("2")


def bar(
    index: int, *, low: str = "99", high: str = "101", open_: str = "100", volume: str = "10"
) -> Candle:
    body_high = max(D(high), D(open_))
    body_low = min(D(low), D(open_))
    return Candle(
        open_ms=index * MINUTE,
        close_ms=index * MINUTE + MINUTE - 1,
        open=D(open_),
        high=body_high,
        low=body_low,
        close=D(open_),
        volume=D(volume),
        quote_volume=D(volume) * D(open_),
    )


def attempt(bars: list[Candle], **overrides: object):
    kwargs: dict[str, object] = {
        "maker_fee_rate": MAKER_FEE,
        "taker_fee_rate": TAKER_FEE,
        "chase_slippage_bps": CHASE_SLIP,
        "timeout_minutes": 3,
        "policy": TimeoutPolicy.CANCEL,
    }
    kwargs.update(overrides)
    side = kwargs.pop("side", PositionSide.LONG)
    price = kwargs.pop("price", D("100"))
    return simulate_post_only(side, price, bars, **kwargs)  # type: ignore[arg-type]


class TestFillRule:
    def test_buy_fills_when_price_trades_below_the_limit(self) -> None:
        result = attempt([bar(1, low="99.5")])
        assert result.outcome is MakerOutcome.FILLED
        assert result.fill_price == D("100")

    def test_touching_the_limit_is_not_a_fill(self) -> None:
        """같은 가격에 먼저 들어온 주문들 뒤에 서 있다. 닿기만 해선 체결이 아니다."""
        result = attempt([bar(1, low="100"), bar(2, low="100")])
        assert result.outcome is MakerOutcome.MISSED

    def test_sell_fills_when_price_trades_above_the_limit(self) -> None:
        result = attempt([bar(1, high="100.5")], side=PositionSide.SHORT)
        assert result.outcome is MakerOutcome.FILLED

    def test_sell_does_not_fill_on_a_move_down(self) -> None:
        result = attempt([bar(1, low="95", high="99")], side=PositionSide.SHORT)
        assert result.outcome is MakerOutcome.MISSED

    def test_zero_volume_bar_never_fills(self) -> None:
        """채움봉 가격은 직전 체결가지 그 시점의 시장가가 아니다 (07 §2)."""
        result = attempt([bar(1, low="90", volume="0")])
        assert result.outcome is MakerOutcome.MISSED

    def test_wait_time_is_the_bar_index_of_the_fill(self) -> None:
        result = attempt([bar(1), bar(2), bar(3, low="98")], price=D("99"))
        assert result.outcome is MakerOutcome.FILLED
        assert result.wait_minutes == 3


class TestFees:
    def test_filled_order_pays_maker_fee_and_no_slippage(self) -> None:
        result = attempt([bar(1, low="99.5")])
        assert result.fee_bps == MAKER_FEE * D("10000")
        assert result.slippage_bps == D("0")

    def test_missed_order_costs_nothing(self) -> None:
        result = attempt([bar(1, low="100")])
        assert result.cost_bps == D("0")
        assert not result.traded


class TestTimeoutPolicy:
    def test_cancel_gives_up_and_does_not_trade(self) -> None:
        bars = [bar(i, low="100") for i in range(1, 6)]
        result = attempt(bars, policy=TimeoutPolicy.CANCEL)
        assert result.outcome is MakerOutcome.MISSED
        assert result.fill_price is None

    def test_chase_crosses_the_spread_after_the_timeout(self) -> None:
        bars = [bar(i, low="100", open_="105") for i in range(1, 6)]
        result = attempt(bars, policy=TimeoutPolicy.CHASE)
        assert result.outcome is MakerOutcome.CHASED
        assert result.fee_bps == TAKER_FEE * D("10000")
        assert result.fill_price is not None
        assert result.fill_price > D("105")  # 불리한 방향으로 밀린다

    def test_chase_skips_zero_volume_bars(self) -> None:
        bars = [bar(i, low="100") for i in range(1, 4)]
        bars += [bar(4, low="100", volume="0"), bar(5, low="100", open_="106")]
        result = attempt(bars, policy=TimeoutPolicy.CHASE)
        assert result.outcome is MakerOutcome.CHASED
        assert result.fill_price is not None
        assert result.fill_price > D("106")

    def test_chase_without_any_tradeable_bar_misses(self) -> None:
        bars = [bar(i, low="100") for i in range(1, 4)]
        result = attempt(bars, policy=TimeoutPolicy.CHASE)
        assert result.outcome is MakerOutcome.MISSED

    def test_only_bars_inside_the_timeout_can_fill(self) -> None:
        """제한 시간 밖의 봉으로 체결시키면 대기 시간이 무의미해진다."""
        bars = [bar(i, low="100") for i in (1, 2, 3)] + [bar(4, low="90")]
        result = attempt(bars, timeout_minutes=3)
        assert result.outcome is MakerOutcome.MISSED


class TestGuards:
    def test_non_positive_price_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="지정가"):
            attempt([bar(1)], price=D("0"))

    @pytest.mark.parametrize("timeout", [0, -5])
    def test_non_positive_timeout_is_rejected(self, timeout: int) -> None:
        with pytest.raises(ValueError, match="timeout_minutes"):
            attempt([bar(1)], timeout_minutes=timeout)

    def test_empty_bar_list_misses(self) -> None:
        assert attempt([]).outcome is MakerOutcome.MISSED
