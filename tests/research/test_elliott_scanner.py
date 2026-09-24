"""문서 34 §3 인과적 스캐너 — 합성 경로에서 미래 무관성과 파서 재사용 동등성."""

from __future__ import annotations

import random
from collections.abc import Sequence

import pytest
from scripts.audit_ew3c_hourly import hour_bars

from auto_trading.core.types import Candle
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT
from auto_trading.research.elliott.scanner import (
    Trigger,
    causal_triggers,
    evaluate_trigger,
    evaluate_triggers,
)
from auto_trading.research.elliott.synthetic import build_path


def _scan(
    minutes: Sequence[Candle], four: Sequence[Candle]
) -> tuple[list[Trigger], tuple[Candle, ...]]:
    groups = (minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,))
    hours = hour_bars(minutes)
    return causal_triggers(replay, hours, four), hours


@pytest.mark.leakage
def test_triggers_and_results_never_depend_on_later_bars() -> None:
    path = build_path(random.Random(11), "IMPULSE", 0.5, 0.04, 0.001)
    full, hours = _scan(path.minutes, path.four_hour)
    assert full
    for trig in full:
        assert trig.segment[-1].available_at_ms == trig.close_ms
        assert trig.segment[-1].four_hour_visible
    cut_bar = path.four_hour[len(path.four_hour) // 2]
    k = path.four_hour.index(cut_bar) + 1
    cut, cut_hours = _scan(path.minutes[: 240 * k], path.four_hour[:k])
    assert cut == [t for t in full if t.close_ms <= cut_bar.close_ms]
    grouped = evaluate_triggers(full, hours)
    for trig, res in zip(full, grouped, strict=True):
        if trig.close_ms <= cut_bar.close_ms:
            assert evaluate_trigger(trig, cut_hours) == res
