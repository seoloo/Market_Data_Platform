"""문서 35 노출 상태 — 인과성(이전 발동만)·최근성 12봉·피보나치 되돌림 구간."""

from __future__ import annotations

from decimal import Decimal

from auto_trading.core.enums import Timeframe
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.exposure import EW_STALENESS_BARS, ExposureIndex
from auto_trading.research.elliott.parser import FinePivot
from auto_trading.research.elliott.scanner import Trigger, TriggerResult

H4 = Timeframe.H4.ms


def _pivot(kind: SwingKind, price: str, bar: int) -> FinePivot:
    return FinePivot(kind, Decimal(price), bar * H4, bar * H4, (bar + 1) * H4 - 1, True)


def _index() -> ExposureIndex:
    low = _pivot(SwingKind.LOW, "100", 0)
    high = _pivot(SwingKind.HIGH, "200", 10)
    trig = Trigger(high.available_at_ms, (low, high), 1)
    res = TriggerResult(high.available_at_ms, 1, True, ("IMPULSE",), ((0, 1),), 1, 1)
    return ExposureIndex([trig], [res])


def test_elliott_end_is_causal_and_stale_after_twelve_bars() -> None:
    idx = _index()
    close = 11 * H4 - 1
    assert idx.elliott_end(close) is None  # 같은 시각의 발동은 쓰지 않는다
    assert idx.elliott_end(close + 1) == 1
    assert idx.elliott_end(close + EW_STALENESS_BARS * H4) == 1
    assert idx.elliott_end(close + EW_STALENESS_BARS * H4 + 1) is None


def test_fib_zone_on_retracement_of_last_leg() -> None:
    idx = _index()
    t = 11 * H4
    assert idx.in_fib_zone(t, Decimal("150"), 1)  # 50% 되돌림
    assert not idx.in_fib_zone(t, Decimal("190"), 1)  # 10% 되돌림
    assert not idx.in_fib_zone(t, Decimal("150"), -1)  # 다리 방향과 다름
