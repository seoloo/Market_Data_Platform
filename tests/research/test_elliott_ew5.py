"""EW-5 (문서 100): 대각선·이중 지그재그·이중 3·확장/러닝 삼각형 후보, 순위 v5, 기존 결과 보존."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest
from scripts.audit_ew3c_hourly import hour_bars

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder
from auto_trading.research.elliott.parser import Label, Parse
from auto_trading.research.elliott.parser_ew5 import EW5_LABELS, ZigzagKind, zigzag_kind
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT
from auto_trading.research.elliott.ranking_v5 import GUIDELINE_TARGETS_V5, rank_key_v5
from auto_trading.research.elliott.scanner import (
    EW3E_LOOKBACK_BARS,
    Trigger,
    causal_triggers,
    scenario_list,
)
from auto_trading.research.elliott.synthetic import EW5_PATTERNS, build_path


def _truth(name: str, seed: str) -> tuple[Trigger, list[Candle], tuple[int, ...]]:
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


@pytest.mark.parametrize("name", list(EW5_PATTERNS))
def test_new_shape_is_a_candidate_only_with_ew5(name: str) -> None:
    label = Label[name]
    hit, hours, bounds = _truth(name, f"t100-{name}-1")
    assert -1 not in bounds
    new = scenario_list(
        hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True, ew5=True
    )
    parse = next((c for c, _ in new.ranked if c.pattern is label and c.bounds == bounds), None)
    assert parse is not None
    old = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True)
    assert all(c.pattern not in EW5_LABELS for c, _ in old.ranked)


def test_ew5_keeps_every_ew4n_candidate_with_same_leg_readings() -> None:
    hit, hours, _ = _truth("IMPULSE", "t100-keep")
    old = scenario_list(hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True)
    new = scenario_list(
        hit, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True, ew5=True
    )
    new_by = {(c.pattern, c.bounds): c for c, _ in new.ranked}
    for c, _ in old.ranked:
        assert new_by[(c.pattern, c.bounds)].leg_labels == c.leg_labels
    assert new.total >= old.total


def test_rank_v5_targets_cover_every_new_label() -> None:
    assert set(GUIDELINE_TARGETS_V5) >= EW5_LABELS
    for label in EW5_LABELS:
        legs = 3 if label in (Label.DOUBLE_ZIGZAG, Label.DOUBLE_THREE) else 5
        assert all(max(n, d) < legs for n, d, _ in GUIDELINE_TARGETS_V5[label])


class _P:
    def __init__(self, price: float) -> None:
        self.price = Decimal(str(price))


@pytest.mark.parametrize(
    ("c_end", "kind"),
    [(108.0, ZigzagKind.TRUNCATED), (115.0, ZigzagKind.NORMAL), (125.0, ZigzagKind.ELONGATED)],
)
def test_zigzag_kind_by_c_over_a(c_end: float, kind: ZigzagKind) -> None:
    # A: 100 → 110 (10), B: 110 → 105, C: 105 → c_end
    pivots = [_P(100), _P(110), _P(105), _P(c_end)]
    parse = Parse(Label.ZIGZAG, (0, 1, 2, 3), (Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE))
    assert zigzag_kind(parse, pivots) is kind  # type: ignore[arg-type]


def test_zigzag_kind_rejects_other_patterns() -> None:
    parse = Parse(Label.FLAT, (0, 1, 2, 3), (Label.ZIGZAG, Label.ZIGZAG, Label.IMPULSE))
    with pytest.raises(ValueError):
        zigzag_kind(parse, [_P(1), _P(2), _P(1.5), _P(3)])  # type: ignore[list-item]


def test_rank_v5_prefers_fully_verified_legs() -> None:
    pivots = [_P(100), _P(110), _P(104), _P(116)]
    full = Parse(Label.DOUBLE_ZIGZAG, (0, 1, 2, 3), (Label.ZIGZAG,) * 3)
    part = Parse(Label.DOUBLE_ZIGZAG, (0, 1, 2, 3), (Label.ZIGZAG, Label.OPAQUE, Label.ZIGZAG))
    assert rank_key_v5(full, pivots) < rank_key_v5(part, pivots)  # type: ignore[arg-type]
