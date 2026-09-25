"""EW-4 1단계 (문서 60): 플랫·수렴 삼각형 후보, 순위 v2 = v1 (충격파·지그재그)."""

from __future__ import annotations

import random

import pytest
from scripts.audit_ew3c_hourly import hour_bars

from auto_trading.core.enums import Timeframe
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder
from auto_trading.research.elliott.parser import Label
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT
from auto_trading.research.elliott.ranking import rank_key
from auto_trading.research.elliott.ranking_v2 import rank_key_v2
from auto_trading.research.elliott.scanner import (
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_list,
)
from auto_trading.research.elliott.synthetic import build_path


def truth_rank(pattern: str, seed: int, *, ew4: bool) -> tuple[int | None, list[str]]:
    path = build_path(random.Random(seed), pattern, 1.0, 0.08, 0.0)
    four, hours = path.four_hour, hour_bars(path.minutes)
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,))
    truth = [
        (k, Timeframe.H1.open_ms_of(path.minutes[t].open_ms))
        for t, k in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
    ]
    hit = next(
        t
        for t in causal_triggers(replay, hours, four, all_new_pivots=True)
        if (t.segment[-1].kind, t.segment[-1].hour_open_ms) == truth[-1]
    )
    where = {(p.kind, p.hour_open_ms): k for k, p in enumerate(hit.segment)}
    bounds = tuple(where.get(v, -1) for v in truth)
    scen = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=ew4)
    parse = next(
        (c for c, _ in scen.ranked if c.pattern is Label[pattern] and c.bounds == bounds), None
    )
    return (None if parse is None else scen.pessimistic_rank(parse)), [
        c.pattern.value for c, _ in scen.ranked[:10]
    ]


@pytest.mark.parametrize("pattern", ["FLAT", "TRIANGLE"])
def test_clean_synthetic_flat_and_triangle_are_found_only_with_ew4(pattern: str) -> None:
    ranks = [truth_rank(pattern, seed, ew4=True)[0] for seed in range(3)]
    assert any(r is not None and r <= 50 for r in ranks), ranks
    assert all(truth_rank(pattern, seed, ew4=False)[0] is None for seed in range(3))


def test_ranking_v2_matches_frozen_v1_for_impulse_and_zigzag() -> None:
    path = build_path(random.Random(3), "IMPULSE", 1.0, 0.08, 0.0)
    four, hours = path.four_hour, hour_bars(path.minutes)
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,))
    checked = 0
    for trig in causal_triggers(replay, hours, four, all_new_pivots=True)[-5:]:
        scen = scenario_list(trig, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True)
        for parse, _ in scen.ranked:
            if parse.pattern in (Label.IMPULSE, Label.ZIGZAG):
                assert rank_key_v2(parse, trig.segment) == rank_key(parse, trig.segment)
                checked += 1
    assert checked > 0


def test_flat_rules_reject_shallow_b_and_triangle_must_contract() -> None:
    from auto_trading.research.elliott.parser import FLAT_B_MAX, FLAT_B_MIN

    assert (FLAT_B_MIN, FLAT_B_MAX) == (0.618, 1.618)
    # 합성 플랫(깨끗한 경로)의 후보는 모두 규칙 범위 안이다
    path = build_path(random.Random(5), "FLAT", 1.0, 0.08, 0.0)
    four, hours = path.four_hour, hour_bars(path.minutes)
    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,))
    for trig in causal_triggers(replay, hours, four, all_new_pivots=True):
        scen = scenario_list(trig, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True)
        seg = trig.segment
        for parse, _ in scen.ranked:
            q = [float(seg[k].price) for k in parse.bounds]
            d = 1 if q[1] > q[0] else -1
            q = [d * x for x in q]
            if parse.pattern is Label.FLAT:
                a, b = q[1] - q[0], q[1] - q[2]
                assert FLAT_B_MIN <= b / a <= FLAT_B_MAX
            elif parse.pattern is Label.TRIANGLE:
                assert q[2] > q[0] and q[3] < q[1] and q[4] > q[2] and q[5] < q[3]


def test_flat_classes_follow_doc60_boundaries() -> None:
    from auto_trading.research.elliott.scenario_export import flat_classes

    assert flat_classes(0.7, 0.9) == ("B_FAILURE", "C_FAILURE")  # 이중 미달형
    assert flat_classes(0.81, 1.0) == ("NORMAL", "NORMAL")
    assert flat_classes(1.2, 1.5) == ("IRREGULAR", "ELONGATED")
    assert flat_classes(1.5, 1.2) == ("RUNNING", "NORMAL")


@pytest.mark.parametrize("pattern", ["FLAT", "TRIANGLE"])
def test_export_carries_flat_and_triangle_evidence(pattern: str) -> None:
    from auto_trading.research.elliott.scenario_contract import (
        to_json_dict,
        validate_scenario_set_dict,
    )
    from auto_trading.research.elliott.scenario_export import export_scenario_set

    found = []
    for seed in range(3):
        path = build_path(random.Random(seed), pattern, 1.0, 0.08, 0.0)
        four, hours = path.four_hour, hour_bars(path.minutes)
        groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
        (replay,) = replay_minute_groups_ladder(
            four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        trig = causal_triggers(replay, hours, four, all_new_pivots=True)[-1]
        scen = scenario_list(trig, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True)
        data = to_json_dict(export_scenario_set(trig, scen, venue="v", instrument="i")[0])
        assert validate_scenario_set_dict(data) == []
        found += [s for s in data["scenarios"] if s["pattern"] == pattern]
    assert found
    for s in found:
        keys = {e["key"] for e in s["evidence"]}
        rules = {lv["rule_id"] for lv in s["invalidation_levels"]}
        a0, a1, end = (float(s["anchors"][k]["price"]) for k in (0, 1, -1))
        if pattern == "FLAT":
            assert s["wave_count"] == ["0", "A", "B", "C"]
            assert any(k.startswith("flat_b=") for k in keys)
            assert any(k.startswith("flat_c=") for k in keys)
            assert "C_BEYOND_2618_OF_A" in rules and "B_BEYOND_A_START" not in rules
        else:
            assert s["wave_count"] == ["0", "A", "B", "C", "D", "E"]
            assert "E_BEYOND_C_END" in rules
            (thrust,) = (
                e["observed"] for e in s["evidence"] if e["key"] == "triangle_thrust_target"
            )
            # 스러스트는 A 의 반대 방향으로, A 폭만큼
            assert thrust == pytest.approx(end - (a1 - a0))
