"""EW-4 2단계 (문서 61): 4파·B파 안 삼각형·확장 플랫 읽기, 순위 v3, 기존 결과 보존."""

from __future__ import annotations

import random

import pytest
from scripts.audit_ew3c_hourly import hour_bars

from auto_trading.core.enums import Timeframe
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder
from auto_trading.research.elliott.parser import NESTED_ALLOWED, Label
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT
from auto_trading.research.elliott.ranking_v2 import rank_key_v2
from auto_trading.research.elliott.ranking_v3 import rank_key_v3
from auto_trading.research.elliott.scanner import (
    EW3E_LOOKBACK_BARS,
    Trigger,
    causal_triggers,
    scenario_list,
)
from auto_trading.research.elliott.synthetic import NESTED_PATTERNS, build_path

_FORM = {"TRIANGLE": Label.TRIANGLE, "EXPFLAT": Label.FLAT}


def _truth(name: str, seed: str) -> tuple[Trigger, list[object], tuple[int, ...]]:
    path = build_path(random.Random(seed), name, 1.0, 0.08, 0.0)
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
    return hit, list(hours), tuple(where.get(v, -1) for v in truth)


@pytest.mark.parametrize("name", list(NESTED_PATTERNS))
def test_nested_leg_is_read_as_its_true_shape(name: str) -> None:
    top, leg, form = NESTED_PATTERNS[name]
    hit, hours, bounds = _truth(name, f"t61-{name}-1")
    scen = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True)
    parse = next(c for c, _ in scen.ranked if c.pattern is Label[top] and c.bounds == bounds)
    assert parse.leg_labels[leg] is _FORM[form]
    rank = scen.pessimistic_rank(parse)
    assert rank is not None and rank <= 50
    old = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True)
    before = next(
        (c for c, _ in old.ranked if c.pattern is Label[top] and c.bounds == bounds), None
    )
    if form == "EXPFLAT":
        assert before is None  # 확장 플랫 경계는 EW-4 에서 후보가 안 된다
    else:
        assert before is not None and before.leg_labels[leg] is Label.OPAQUE


def test_nested_keeps_every_old_candidate_and_wave2_stays_zigzag() -> None:
    hit, hours, _ = _truth("IMPULSE_W4_TRIANGLE", "t61-keep")
    old = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True)
    new = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True)
    new_by = {(c.pattern, c.bounds): c for c, _ in new.ranked}
    for c, _ in old.ranked:
        n = new_by[(c.pattern, c.bounds)]
        for k, (a, b) in enumerate(zip(c.leg_labels, n.leg_labels, strict=True)):
            # 바뀌는 것은 OPAQUE → 허용된 안 모양 뿐
            assert a is b or (a is Label.OPAQUE and b in NESTED_ALLOWED[c.pattern][k])
    for c, _ in new.ranked:
        if c.pattern is Label.IMPULSE:
            assert c.leg_labels[1] not in (Label.FLAT, Label.TRIANGLE)


def test_rank_v3_equals_v2_without_nested_readings() -> None:
    hit, hours, _ = _truth("ZIGZAG_B_TRIANGLE", "t61-rank")
    scen = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True)
    checked = 0
    for c, key in scen.ranked:
        nested = any(
            lab in (Label.FLAT, Label.TRIANGLE)
            for lab in c.leg_labels
            if c.pattern in (Label.IMPULSE, Label.ZIGZAG)
        )
        assert key == rank_key_v3(c, hit.segment)
        if not nested:
            assert key == rank_key_v2(c, hit.segment)
            checked += 1
    assert checked > 0


@pytest.mark.parametrize("name", list(NESTED_PATTERNS))
def test_nested_synthetic_paths_obey_absolute_rules(name: str) -> None:
    for seed in range(5):
        path = build_path(random.Random(f"t61-rules-{name}-{seed}"), name, 1.0, 0.08, 0.0)
        q = [
            float(path.minutes[t].low if k.value == "low" else path.minutes[t].high)
            for t, k in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
        ]
        if NESTED_PATTERNS[name][0] == "IMPULSE":
            assert q[0] < q[2] < q[1] < q[4] < q[3] and q[5] > q[4]  # 5파 절단형은 허용
        else:
            assert q[0] < q[2] < q[1] < q[3]
