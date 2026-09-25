"""지표 방향 신호 테스트 — 고정 입력 기대값과 미래 무관성 (46)."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from auto_trading.backtest.indicator_signals import (
    INDICATOR_RULES,
    direction_series,
    ema,
    ichimoku_cloud,
    macd,
    rsi,
    sma,
)
from auto_trading.features.indicators import ema_series, rsi_series, sma_series


def wave(n: int, *, drift: float = 0.0) -> list[float]:
    return [
        100 * math.exp(drift * k + 0.08 * math.sin(k / 7) + 0.02 * math.sin(k)) for k in range(n)
    ]


def hl(closes: list[float]) -> tuple[list[float], list[float]]:
    return [c * 1.01 for c in closes], [c * 0.99 for c in closes]


class TestAverages:
    def test_sma_fixed(self) -> None:
        assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]

    def test_ema_fixed(self) -> None:
        # 시드 SMA(3)=2, k=0.5 → 3, 4
        assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]
        assert ema([1, 2, 3, 7], 3) == [None, None, 2, 4.5]

    @pytest.mark.parametrize("period", [3, 14, 50])
    def test_matches_decimal_features(self, period: int) -> None:
        xs = wave(200)
        ds = [Decimal(repr(x)) for x in xs]
        for mine, ref in (
            (sma(xs, period), sma_series(ds, period)),
            (ema(xs, period), ema_series(ds, period)),
        ):
            for a, b in zip(mine, ref, strict=True):
                assert (a is None) == (b is None)
                if a is not None and b is not None:
                    assert a == pytest.approx(float(b), rel=1e-9)

    def test_rejects_bad_period(self) -> None:
        with pytest.raises(ValueError):
            sma([1.0], 0)


class TestRsi:
    def test_matches_decimal_features(self) -> None:
        xs = wave(120)
        ref = rsi_series([Decimal(repr(x)) for x in xs], 14)
        for a, b in zip(rsi(xs, 14), ref, strict=True):
            assert (a is None) == (b is None)
            if a is not None and b is not None:
                assert a == pytest.approx(float(b), rel=1e-9)

    def test_boundaries(self) -> None:
        assert rsi([1, 2, 3, 4], 2)[-1] == 100
        assert rsi([4, 3, 2, 1], 2)[-1] == 0
        assert rsi([5, 5, 5, 5], 2)[-1] == 50


class TestMacd:
    def test_line_is_ema_difference(self) -> None:
        xs = wave(120)
        line, sig = macd(xs)
        e12, e26 = ema(xs, 12), ema(xs, 26)
        assert line[24] is None and line[25] is not None
        assert line[80] == pytest.approx(e12[80] - e26[80])  # type: ignore[operator]
        # 시그널 = MACD 선(26번째 봉부터)의 EMA9
        assert sig[25 + 7] is None and sig[25 + 8] is not None
        tail = [x for x in line[25:] if x is not None]
        assert sig[25 + 8] == pytest.approx(sum(tail[:9]) / 9)

    def test_fast_must_be_shorter(self) -> None:
        with pytest.raises(ValueError):
            macd([1.0] * 50, fast=26, slow=12)


class TestIchimoku:
    def test_cloud_uses_values_shift_bars_back(self) -> None:
        highs = [float(10 + k) for k in range(100)]
        lows = [float(k) for k in range(100)]
        cloud = ichimoku_cloud(highs, lows)
        assert all(c is None for c in cloud[: 26 + 51])
        i = 90
        j = i - 26
        tenkan = (highs[j] + lows[j - 8]) / 2
        kijun = (highs[j] + lows[j - 25]) / 2
        span_b = (highs[j] + lows[j - 51]) / 2
        a = (tenkan + kijun) / 2
        assert cloud[i] == pytest.approx((max(a, span_b), min(a, span_b)))

    def test_length_mismatch(self) -> None:
        with pytest.raises(ValueError):
            ichimoku_cloud([1.0, 2.0], [1.0])


class TestRules:
    def test_six_fixed_candidates(self) -> None:
        assert [r.key for r in INDICATOR_RULES] == ["I1", "I2", "I3", "I4", "I5", "I6"]

    def test_rsi_reversion_state_machine(self) -> None:
        rule = next(r for r in INDICATOR_RULES if r.key == "I2")
        # 14일 하락 → RSI 0 → 롱, 반등 중 RSI 가 50 을 넘기 전까지 유지
        closes = [100.0 - k for k in range(16)] + [85.0 + 0.5 * k for k in range(4)] + [100.0] * 3
        highs, lows = hl(closes)
        d = direction_series(rule, closes, highs, lows)
        assert d[13] is None and d[14] == 1
        assert 1 in d[16:19]
        assert d[-1] == 0  # 크게 오른 뒤 50 복귀 → 청산 (70 초과면 숏이 될 수 있다)

    def test_trend_rules_follow_direction(self) -> None:
        up = [100 * 1.01**k for k in range(300)]
        down = [1000 - 0.01 * k * k for k in range(300)]  # 가속 하락 (MACD 히스토그램도 음수)
        for rule in INDICATOR_RULES:
            if rule.key == "I2":
                continue
            assert direction_series(rule, up, *hl(up))[-1] == 1, rule.key
            assert direction_series(rule, down, *hl(down))[-1] == -1, rule.key

    def test_length_mismatch(self) -> None:
        with pytest.raises(ValueError):
            direction_series(INDICATOR_RULES[0], [1.0, 2.0], [1.0], [1.0])


@pytest.mark.leakage
@pytest.mark.parametrize("rule", INDICATOR_RULES, ids=lambda r: r.key)
def test_future_bars_do_not_change_past(rule: object) -> None:
    closes = wave(400, drift=0.001)
    highs, lows = hl(closes)
    full = direction_series(rule, closes, highs, lows)  # type: ignore[arg-type]
    cut = 300
    prefix = direction_series(rule, closes[:cut], highs[:cut], lows[:cut])  # type: ignore[arg-type]
    assert full[:cut] == prefix
    # 뒤를 크게 바꿔도 앞은 같다
    shocked = closes[:cut] + [c * 3 for c in closes[cut:]]
    sh, sl = hl(shocked)
    assert direction_series(rule, shocked, sh, sl)[:cut] == prefix  # type: ignore[arg-type]
