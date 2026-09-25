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


def test_scenario_list_top_agrees_with_top1_and_ranks_pessimistically() -> None:
    from auto_trading.research.elliott.scanner import SCENARIO_SET_SIZE, scenario_list

    path = build_path(random.Random(5), "IMPULSE", 0.5, 0.04, 0.001)
    triggers, hours = _scan(path.minutes, path.four_hour)
    checked = 0
    for trig in triggers:
        res = evaluate_trigger(trig, hours)
        scen = scenario_list(trig, hours)
        assert scen.total == res.candidates
        if not scen.ranked:
            continue
        best_key = scen.ranked[0][1]
        best = {c.bounds for c, k in scen.ranked if k == best_key}
        assert best == set(res.top_bounds)
        first = scen.ranked[0][0]
        ties = sum(1 for _, k in scen.ranked if k == best_key)
        assert scen.pessimistic_rank(first) == ties
        assert len(scen.top) == min(SCENARIO_SET_SIZE, scen.total)
        checked += 1
    assert checked > 0


def test_all_new_pivots_option_only_adds_triggers() -> None:
    """문서 58: 한 마감에 확정된 4h 가시 꼭짓점 각각에서 발동 — 기본 발동은 그 부분집합이다."""
    import random

    from scripts.audit_ew3c_hourly import hour_bars

    from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder
    from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT
    from auto_trading.research.elliott.scanner import causal_triggers
    from auto_trading.research.elliott.synthetic import build_path

    path = build_path(random.Random(7), "IMPULSE", 0.5, 0.04, 0.001)
    four, hours = path.four_hour, hour_bars(path.minutes)
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,))
    base = causal_triggers(replay, hours, four)
    every = causal_triggers(replay, hours, four, all_new_pivots=True)
    as_key = lambda t: (t.close_ms, t.segment[-1].hour_open_ms)  # noqa: E731
    assert {as_key(t) for t in base} <= {as_key(t) for t in every}
    assert all(t.segment[-1].four_hour_visible for t in every)
    assert all(t.segment[-1].available_at_ms == t.close_ms for t in every)  # 그 마감에 알려진 것만
