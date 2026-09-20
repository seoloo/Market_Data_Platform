"""clock 테스트."""

from __future__ import annotations

import pytest

from auto_trading.core.clock import Clock, SimClock
from auto_trading.ops.clock import RealClock


class TestSimClock:
    def test_does_not_advance_on_its_own(self):
        c = SimClock(1000)
        assert c.now_ms() == 1000
        assert c.now_ms() == 1000

    def test_advance(self):
        c = SimClock(1000)
        assert c.advance(500) == 1500
        assert c.now_ms() == 1500

    def test_rejects_negative_advance(self):
        with pytest.raises(ValueError, match="뒤로"):
            SimClock(1000).advance(-1)

    def test_set_allows_rewind_for_replay(self):
        c = SimClock(1000)
        c.set(500)
        assert c.now_ms() == 500

    def test_satisfies_protocol(self):
        assert isinstance(SimClock(0), Clock)


class TestRealClock:
    def test_satisfies_protocol(self):
        assert isinstance(RealClock(), Clock)

    def test_returns_plausible_epoch_ms(self):
        now = RealClock().now_ms()
        assert isinstance(now, int)
        # 2020-01-01 이후, 2100-01-01 이전
        assert 1_577_836_800_000 < now < 4_102_444_800_000

    def test_is_monotonic_enough(self):
        c = RealClock()
        assert c.now_ms() <= c.now_ms()
