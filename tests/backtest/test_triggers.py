"""후보 트리거 정의 테스트.

이 정의가 흔들리면 "연 282회" 와 "그 282회의 SD" 가 다른 사건을 가리키게 되고
검정력 게이트가 조용히 무효가 된다. 특히 **causal 성**과 **중복 차단**을 고정한다.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from auto_trading.backtest.triggers import (
    CASCADE_WINDOW,
    RANGE_BARS,
    RANK_WINDOW_BARS,
    TREND_BARS,
    VOL_WINDOW_BARS,
    breakout_triggers,
    cascade_triggers,
    contraction_flags,
    rolling_vol,
)

STEP = 300_000
BASE = 1_672_531_200_000
WARMUP = VOL_WINDOW_BARS + RANK_WINDOW_BARS


def flat(count: int, price: float = 100.0) -> list[float]:
    return [price] * count


class TestRollingVol:
    def test_warmup_is_empty(self) -> None:
        vol = rolling_vol(flat(VOL_WINDOW_BARS + 5))
        assert all(v is None for v in vol[:VOL_WINDOW_BARS])
        assert all(v is not None for v in vol[VOL_WINDOW_BARS:])

    def test_constant_price_has_zero_volatility(self) -> None:
        vol = rolling_vol(flat(VOL_WINDOW_BARS + 50))
        assert vol[-1] == pytest.approx(0.0, abs=1e-12)

    def test_a_noisy_series_has_higher_volatility_than_a_calm_one(self) -> None:
        calm = [100.0 + 0.01 * (i % 2) for i in range(VOL_WINDOW_BARS + 50)]
        wild = [100.0 + 5.0 * (i % 2) for i in range(VOL_WINDOW_BARS + 50)]
        assert rolling_vol(wild)[-1] > rolling_vol(calm)[-1]  # type: ignore[operator]

    def test_short_history_yields_nothing(self) -> None:
        assert all(v is None for v in rolling_vol(flat(10)))

    def test_future_bars_do_not_change_past_values(self) -> None:
        """causal 성. 뒤에 봉을 붙여도 앞의 값이 바뀌면 안 된다."""
        prices = [100.0 + (i % 7) * 0.3 for i in range(VOL_WINDOW_BARS + 100)]
        before = rolling_vol(prices)
        after = rolling_vol([*prices, 500.0, 600.0, 700.0])
        assert after[: len(prices)] == before


class TestContractionFlags:
    def test_nothing_is_flagged_before_the_rank_window_fills(self) -> None:
        vol: list[float | None] = [0.001] * (RANK_WINDOW_BARS - 10)
        assert not any(contraction_flags(vol))

    def test_the_calmest_point_is_flagged(self) -> None:
        vol: list[float | None] = [0.01 + 0.001 * (i % 50) for i in range(RANK_WINDOW_BARS + 10)]
        vol[-1] = 0.0001  # 직전 30일 중 가장 조용하다
        assert contraction_flags(vol)[-1]

    def test_the_wildest_point_is_not(self) -> None:
        vol: list[float | None] = [0.01 + 0.001 * (i % 50) for i in range(RANK_WINDOW_BARS + 10)]
        vol[-1] = 1.0
        assert not contraction_flags(vol)[-1]

    def test_missing_volatility_is_never_flagged(self) -> None:
        vol: list[float | None] = [None] * (RANK_WINDOW_BARS + 10)
        assert not any(contraction_flags(vol))


class TestBreakoutTriggers:
    def _prices(self) -> list[float]:
        # 완만한 상승 추세 위의 평탄한 레인지
        return [100.0 + i * 0.001 for i in range(TREND_BARS + RANGE_BARS + 200)]

    def test_a_flat_range_never_breaks_out(self) -> None:
        prices = flat(TREND_BARS + RANGE_BARS + 200)
        flags = [True] * len(prices)
        assert breakout_triggers(prices, flags) == []

    def test_an_upside_break_in_an_uptrend_is_long(self) -> None:
        prices = self._prices()
        at = len(prices) - 1
        prices[at] = max(prices[at - RANGE_BARS : at]) * 1.05
        flags = [False] * len(prices)
        flags[at] = True
        got = breakout_triggers(prices, flags)
        assert [(t.index, t.direction) for t in got] == [(at, 1)]

    def test_a_downside_break_in_a_downtrend_is_short(self) -> None:
        prices = [100.0 - i * 0.001 for i in range(TREND_BARS + RANGE_BARS + 200)]
        at = len(prices) - 1
        prices[at] = min(prices[at - RANGE_BARS : at]) * 0.95
        flags = [False] * len(prices)
        flags[at] = True
        got = breakout_triggers(prices, flags)
        assert [(t.index, t.direction) for t in got] == [(at, -1)]

    def test_a_break_against_the_trend_is_ignored(self) -> None:
        """상승 추세에서 아래로 깨지는 것은 이 후보의 진입이 아니다."""
        prices = self._prices()
        at = len(prices) - 1
        prices[at] = min(prices[at - RANGE_BARS : at]) * 0.95
        flags = [False] * len(prices)
        flags[at] = True
        assert breakout_triggers(prices, flags) == []

    def test_outside_contraction_nothing_triggers(self) -> None:
        prices = self._prices()
        at = len(prices) - 1
        prices[at] = max(prices[at - RANGE_BARS : at]) * 1.05
        assert breakout_triggers(prices, [False] * len(prices)) == []

    def test_one_breakout_is_not_counted_many_times(self) -> None:
        """돌파 후 가격이 계속 높으면 매 봉이 '신고가' 다. 잠금이 없으면 표본이 부풀려진다."""
        prices = self._prices()
        start = TREND_BARS + RANGE_BARS + 10
        for i in range(start, len(prices)):
            prices[i] = 200.0 + i * 0.001
        flags = [True] * len(prices)
        got = breakout_triggers(prices, flags)
        gaps = [b.index - a.index for a, b in pairwise(got)]
        assert all(gap >= RANGE_BARS for gap in gaps)


class TestCascadeTriggers:
    def _series(self, count: int = 200) -> tuple[list[int], list[float], list[float | None]]:
        times = [BASE + i * STEP for i in range(count)]
        closes = [100.0 + (i % 3) * 0.01 for i in range(count)]
        vol: list[float | None] = [0.0005] * count
        return times, closes, vol

    def _oi(self, times: list[int], value: float = 1000.0) -> dict[int, float]:
        return dict.fromkeys(times, value)

    def test_a_crash_with_falling_open_interest_is_a_long(self) -> None:
        times, closes, vol = self._series()
        at = len(closes) - 1
        closes[at] = closes[at - CASCADE_WINDOW] * 0.90
        oi = self._oi(times)
        oi[times[at]] = 900.0  # 10% 감소 — 강제 청산
        got, _ = cascade_triggers(times, closes, vol, oi)
        assert [(t.index, t.direction) for t in got] == [(at, 1)]

    def test_a_spike_with_falling_open_interest_is_a_short(self) -> None:
        times, closes, vol = self._series()
        at = len(closes) - 1
        closes[at] = closes[at - CASCADE_WINDOW] * 1.10
        oi = self._oi(times)
        oi[times[at]] = 900.0
        got, _ = cascade_triggers(times, closes, vol, oi)
        assert [(t.index, t.direction) for t in got] == [(at, -1)]

    def test_a_crash_with_rising_open_interest_is_not_a_cascade(self) -> None:
        """이 구분이 후보 B 의 전부다 — 신규 투기는 강제 청산이 아니다."""
        times, closes, vol = self._series()
        at = len(closes) - 1
        closes[at] = closes[at - CASCADE_WINDOW] * 0.90
        oi = self._oi(times)
        oi[times[at]] = 1100.0
        got, _ = cascade_triggers(times, closes, vol, oi)
        assert got == []

    def test_a_small_move_is_not_a_cascade(self) -> None:
        times, closes, vol = self._series()
        oi = self._oi(times)
        oi[times[-1]] = 900.0
        got, _ = cascade_triggers(times, closes, vol, oi)
        assert got == []

    def test_bars_without_open_interest_are_not_counted_as_usable(self) -> None:
        times, closes, vol = self._series()
        got, usable = cascade_triggers(times, closes, vol, {})
        assert got == []
        assert usable == 0

    def test_usable_counts_only_bars_with_both_snapshots(self) -> None:
        times, closes, vol = self._series()
        partial = dict.fromkeys(times[:100], 1000.0)
        _, usable = cascade_triggers(times, closes, vol, partial)
        assert 0 < usable <= 100
