"""A1 신호는 마감봉까지의 데이터만으로 동일하게 재생된다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features import a1


def test_scan_prefix_is_unchanged_by_future_candles(monkeypatch: pytest.MonkeyPatch) -> None:
    # 실제 파라미터는 A1Config가 고정한다. 여기서는 짧은 경계 창만 검증한다.
    monkeypatch.setattr(a1, "VOL_WINDOW_BARS", 4)
    monkeypatch.setattr(a1, "RANK_WINDOW_BARS", 5)
    monkeypatch.setattr(a1, "TREND_BARS", 8)
    monkeypatch.setattr(a1, "RANGE_BARS", 6)
    candles = tuple(
        Candle(
            open_ms=i * Timeframe.M5.ms,
            close_ms=(i + 1) * Timeframe.M5.ms - 1,
            open=Decimal(100) + Decimal(i) / 100,
            high=Decimal(100) + Decimal(i) / 100,
            low=Decimal(100) + Decimal(i) / 100,
            close=Decimal(100) + Decimal(i) / 100,
            volume=Decimal(1),
        )
        for i in range(45)
    )
    prefix = a1.scan_a1(candles[:35], control_without_contraction=True)
    whole = a1.scan_a1(candles, control_without_contraction=True)
    assert prefix
    assert prefix == {
        ms: observation for ms, observation in whole.items() if ms <= candles[34].close_ms
    }
    assert a1.latest_a1_observation(candles[:35], control_without_contraction=True) == prefix.get(
        candles[34].close_ms
    )
