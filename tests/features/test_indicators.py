"""지표 수학 테스트.

외부 라이브러리와 비교하지 않고 **고정 숫자**로 검증한다.
라이브러리 버전이 바뀌었다고 우리 테스트가 흔들리면 안 된다 (11-testing.md §2.1).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.types import Candle
from auto_trading.features.indicators import (
    EMA_CONVERGENCE_MULTIPLE,
    atr_series,
    ema_series,
    ema_warmup_bars,
    rsi_series,
    sma_series,
    true_range_series,
)

D = Decimal


def candle(o: str, h: str, low: str, c: str, *, index: int = 0, volume: str = "1") -> Candle:
    return Candle(
        open_ms=index * 60_000,
        close_ms=index * 60_000 + 59_999,
        open=D(o),
        high=D(h),
        low=D(low),
        close=D(c),
        volume=D(volume),
    )


class TestSma:
    def test_simple_case(self):
        assert sma_series([D(1), D(2), D(3), D(4)], 2) == [None, D("1.5"), D("2.5"), D("3.5")]

    def test_warmup_is_none(self):
        assert sma_series([D(1), D(2)], 3) == [None, None]

    def test_full_window(self):
        out = sma_series([D(10), D(20), D(30)], 3)
        assert out == [None, None, D(20)]

    def test_period_one_is_identity(self):
        assert sma_series([D(5), D(7)], 1) == [D(5), D(7)]

    def test_rolling_window_drops_old_values(self):
        """누적합 구현이 오래된 값을 제대로 빼는지 확인한다."""
        out = sma_series([D(100), D(1), D(1), D(1)], 3)
        assert out[2] == D(34)  # (100+1+1)/3
        assert out[3] == D(1)  # (1+1+1)/3

    def test_rejects_zero_period(self):
        with pytest.raises(ValueError, match="period"):
            sma_series([D(1)], 0)

    def test_empty(self):
        assert sma_series([], 3) == []


class TestEma:
    def test_seed_is_sma(self):
        out = ema_series([D(1), D(2), D(3)], 3)
        assert out[:2] == [None, None]
        assert out[2] == D(2)  # SMA(1,2,3)

    def test_recursion(self):
        """multiplier = 2/(period+1) = 0.5 (period=3)."""
        out = ema_series([D(1), D(2), D(3), D(5)], 3)
        # seed=2, next = (5-2)*0.5 + 2 = 3.5
        assert out[3] == D("3.5")

    def test_constant_series_stays_constant(self):
        out = ema_series([D(10)] * 20, 5)
        assert out[-1] == D(10)

    def test_converges_toward_new_level(self):
        values = [D(10)] * 10 + [D(20)] * 50
        out = ema_series(values, 5)
        assert out[-1] is not None
        assert D("19.9") < out[-1] < D(20)

    def test_warmup_helper(self):
        assert ema_warmup_bars(50) == 50 * EMA_CONVERGENCE_MULTIPLE

    def test_insufficient_data(self):
        assert ema_series([D(1), D(2)], 5) == [None, None]


class TestTrueRange:
    def test_first_bar_uses_high_low(self):
        out = true_range_series([candle("100", "110", "90", "105")])
        assert out == [D(20)]

    def test_gap_up_is_captured(self):
        """직전 종가에서 갭이 뜨면 high-low 보다 큰 값이 나와야 한다."""
        bars = [
            candle("100", "101", "99", "100", index=0),
            candle("120", "121", "119", "120", index=1),  # 갭 상승
        ]
        out = true_range_series(bars)
        assert out[1] == D(21)  # |121 - 100|

    def test_gap_down_is_captured(self):
        bars = [
            candle("100", "101", "99", "100", index=0),
            candle("80", "81", "79", "80", index=1),
        ]
        assert true_range_series(bars)[1] == D(21)  # |79 - 100|

    def test_inside_bar_uses_range(self):
        bars = [
            candle("100", "110", "90", "100", index=0),
            candle("100", "102", "98", "100", index=1),
        ]
        assert true_range_series(bars)[1] == D(4)


class TestAtr:
    def test_seed_is_mean_of_true_ranges(self):
        bars = [candle("100", "110", "90", "100", index=i) for i in range(3)]
        # TR: 20, 20, 20  -> ATR(3) = 20
        assert atr_series(bars, 3)[2] == D(20)

    def test_wilder_smoothing(self):
        bars = [candle("100", "110", "90", "100", index=i) for i in range(3)]
        bars.append(candle("100", "140", "100", "100", index=3))  # TR = 40
        out = atr_series(bars, 3)
        # (20*2 + 40)/3 = 26.666...
        assert out[3] is not None
        assert D("26.66") < out[3] < D("26.67")

    def test_warmup(self):
        bars = [candle("100", "110", "90", "100", index=i) for i in range(2)]
        assert atr_series(bars, 5) == [None, None]

    def test_atr_is_positive(self):
        bars = [candle("100", "101", "99", "100", index=i) for i in range(30)]
        out = atr_series(bars, 14)
        assert all(v > 0 for v in out[13:] if v is not None)


class TestRsi:
    def test_all_gains_is_hundred(self):
        values = [D(i) for i in range(1, 20)]
        assert rsi_series(values, 14)[-1] == D(100)

    def test_all_losses_is_zero(self):
        values = [D(i) for i in range(20, 1, -1)]
        assert rsi_series(values, 14)[-1] == D(0)

    def test_flat_series_is_fifty(self):
        """변화가 없으면 과매수도 과매도도 아니다."""
        assert rsi_series([D(100)] * 20, 14)[-1] == D(50)

    def test_known_value(self):
        """상승 7 / 하락 7 이 번갈아 같은 크기면 RSI 50."""
        values: list[Decimal] = [D(100)]
        for i in range(20):
            values.append(values[-1] + (D(1) if i % 2 == 0 else D(-1)))
        result = rsi_series(values, 14)[-1]
        assert result is not None
        assert D(45) < result < D(55)

    def test_bounded(self):
        values = [D(100) + D((i * 37) % 23) for i in range(100)]
        for value in rsi_series(values, 14):
            if value is not None:
                assert D(0) <= value <= D(100)

    def test_warmup_needs_period_plus_one(self):
        out = rsi_series([D(i) for i in range(15)], 14)
        assert out[13] is None
        assert out[14] is not None

    def test_insufficient_data(self):
        assert rsi_series([D(1), D(2)], 14) == [None, None]

    def test_rejects_period_one(self):
        with pytest.raises(ValueError, match="period"):
            rsi_series([D(1), D(2), D(3)], 1)


class TestCausality:
    """지표도 미래를 보면 안 된다."""

    @pytest.mark.parametrize("func", [sma_series, ema_series])
    def test_appending_does_not_change_past(self, func):
        base = [D(i % 7) + D(1) for i in range(40)]
        short = func(base, 5)
        extended = func([*base, D(9999), D(-9999)], 5)
        assert short == extended[: len(base)]

    def test_rsi_appending_does_not_change_past(self):
        base = [D(100) + D((i * 13) % 11) for i in range(60)]
        short = rsi_series(base, 14)
        extended = rsi_series([*base, D(99999)], 14)
        assert short == extended[: len(base)]

    def test_atr_appending_does_not_change_past(self):
        bars = [candle("100", "110", "90", "100", index=i) for i in range(40)]
        short = atr_series(bars, 14)
        extended = atr_series([*bars, candle("100", "999", "1", "100", index=40)], 14)
        assert short == extended[: len(bars)]


class TestDeterminism:
    def test_repeated_calls_identical(self):
        values = [D(100) + D(i) / D(7) for i in range(200)]
        first = ema_series(values, 20)
        for _ in range(10):
            assert ema_series(values, 20) == first

    def test_decimal_precision_preserved(self):
        """float 로 계산하면 나올 수 없는 정확한 값이어야 한다."""
        out = sma_series([D("0.1"), D("0.2")], 2)
        assert out[1] == D("0.15")
