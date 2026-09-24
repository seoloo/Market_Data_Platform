"""교과서 합성 경로 생성기 (33 §22) — 결정성·봉 일관성·하드 규칙·파이프라인 연결."""

from __future__ import annotations

import random

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.degrees import HALF_OCTAVE_GRID
from auto_trading.research.elliott.h4_evidence import map_minute_path_to_four_hour_evidence
from auto_trading.research.elliott.intrabar import ExtremumOrder, audit_intrabar_extrema
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder
from auto_trading.research.elliott.pivots import closed_ew0_candles
from auto_trading.research.elliott.synthetic import CORRECTIVE, MOTIVE, SyntheticPath, build_path


def _path(pattern: str, seed: int = 7, noise: float = 0.0) -> SyntheticPath:
    return build_path(random.Random(seed), pattern, 0.5, 0.04, noise)


def test_same_seed_same_path() -> None:
    a = _path("IMPULSE", noise=0.001)
    b = _path("IMPULSE", noise=0.001)
    assert a == b
    assert a != _path("IMPULSE", seed=8, noise=0.001)


@pytest.mark.parametrize("pattern", ["IMPULSE", "ZIGZAG"])
def test_minutes_aggregate_to_valid_four_hour_bars(pattern: str) -> None:
    p = _path(pattern, noise=0.001)
    assert len(p.minutes) == 240 * len(p.four_hour)
    closed_ew0_candles(p.four_hour, p.four_hour[-1].close_ms)  # 격자·거래량 검증
    for i, bar in enumerate(p.four_hour):
        group = p.minutes[240 * i : 240 * (i + 1)]
        assert (bar.high, bar.low) == (max(c.high for c in group), min(c.low for c in group))
        assert audit_intrabar_extrema(bar, group, bar.close_ms).order is not ExtremumOrder.UNKNOWN


def test_impulse_truth_respects_hard_rules() -> None:
    for seed in range(20):
        p = _path("IMPULSE", seed=seed)
        assert p.leg_truth == (MOTIVE, CORRECTIVE, MOTIVE, CORRECTIVE, MOTIVE)
        assert p.vertex_kinds == (SwingKind.LOW, SwingKind.HIGH) * 3
        v = [
            p.minutes[t].low if k is SwingKind.LOW else p.minutes[t].high
            for t, k in zip(p.vertex_minutes, p.vertex_kinds, strict=True)
        ]
        assert v[0] < v[2] < v[1] < v[3]  # 2파는 시작점 위, 3파는 1파 끝 위
        assert v[1] < v[4] < v[3]  # 4파는 1파 영역을 침범하지 않음
        assert v[5] > v[4]
        assert list(p.vertex_minutes) == sorted(p.vertex_minutes)


def test_zigzag_truth() -> None:
    p = _path("ZIGZAG")
    assert p.leg_truth == (MOTIVE, CORRECTIVE, MOTIVE)
    assert len(p.vertex_minutes) == 4


def test_pipeline_detects_first_parent_vertices_on_clean_path() -> None:
    p = _path("IMPULSE")
    as_of = p.four_hour[-1].close_ms
    groups = (p.minutes[240 * i : 240 * (i + 1)] for i in range(len(p.four_hour)))
    thresholds = tuple(d.threshold_pct for d in HALF_OCTAVE_GRID[:3])
    replays = replay_minute_groups_ladder(p.four_hour, groups, as_of, thresholds)
    parent = map_minute_path_to_four_hour_evidence(p.four_hour, replays[-1], as_of)
    found = {(c.kind, c.extreme_four_hour_open_ms) for c in (e.candidate for e in parent) if c}
    for t, kind in list(zip(p.vertex_minutes, p.vertex_kinds, strict=True))[:2]:
        bar_open = Timeframe.H4.open_ms_of(p.minutes[t].open_ms)
        assert (kind, bar_open) in found
