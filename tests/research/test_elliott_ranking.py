"""EW-3d 후보 생성·순위 근거 (33 §28) — 합성 꼭짓점."""

from __future__ import annotations

import math
from decimal import Decimal

from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.parser import FinePivot, Label, Parse, WaveParser
from auto_trading.research.elliott.ranking import (
    guideline_distance,
    pessimistic_rank,
    rank_key,
)
from tests.research.test_elliott_degrees import LEG1, LEG2, LEG3, LEG4, LEG5
from tests.research.test_elliott_parser import _pivots


def _fine(prices: list[float]) -> list[FinePivot]:
    return [
        FinePivot(
            kind=SwingKind.LOW if i % 2 == 0 else SwingKind.HIGH,
            price=Decimal(str(round(p, 8))),
            hour_open_ms=i * 3_600_000,
            four_hour_open_ms=i * 14_400_000,
            available_at_ms=(i + 1) * 14_400_000 - 1,
            four_hour_visible=True,
        )
        for i, p in enumerate(prices)
    ]


def test_candidates_keep_truth_and_mark_verified_legs() -> None:
    bars, pivots = _pivots(LEG1, LEG2, LEG3, LEG4, LEG5)
    candidates = WaveParser(pivots, bars).top_candidates(0, len(pivots) - 1)
    truth = next(
        c for c in candidates if c.pattern is Label.IMPULSE and c.bounds == (0, 5, 8, 13, 16, 21)
    )
    assert truth.leg_labels == (
        Label.IMPULSE,
        Label.ZIGZAG,
        Label.IMPULSE,
        Label.ZIGZAG,
        Label.IMPULSE,
    )
    assert any(c.pattern is Label.ZIGZAG for c in candidates)  # 다른 패턴 후보도 함께


def test_unparseable_extremal_leg_is_kept_as_opaque() -> None:
    # 1파 자리 100→120→110→125→115→150: 3파 최단이라 impulse 로 못 읽는 5다리
    bars, pivots = _pivots(("100", "120", "110", "125", "115", "150"), LEG2, LEG3, LEG4, LEG5)
    candidates = WaveParser(pivots, bars).top_candidates(0, len(pivots) - 1)
    truth = next(
        c for c in candidates if c.pattern is Label.IMPULSE and c.bounds == (0, 5, 8, 13, 16, 21)
    )
    assert truth.leg_labels[0] is Label.OPAQUE


def test_guideline_distance_is_zero_on_textbook_ratios() -> None:
    a = 0.2
    zz = [0.0, a, a - 0.618 * a, a - 0.618 * a + 1.0 * a]
    fine = _fine([100 * math.exp(v) for v in zz])
    parse = Parse(Label.ZIGZAG, (0, 1, 2, 3), (Label.ATOM,) * 3)
    assert guideline_distance(parse, fine) < 1e-6
    w = [0.1, -0.0618, 0.1618, -0.1618 * 0.382, 0.1]
    levels = [0.0]
    for x in w:
        levels.append(levels[-1] + x)
    fine = _fine([100 * math.exp(v) for v in levels])
    parse = Parse(Label.IMPULSE, (0, 1, 2, 3, 4, 5), (Label.ATOM,) * 5)
    assert guideline_distance(parse, fine) < 1e-6


def test_verified_fraction_outranks_guideline_distance_and_ties_are_pessimistic() -> None:
    fine = _fine([100, 120, 110, 140])
    verified = Parse(Label.ZIGZAG, (0, 1, 2, 3), (Label.IMPULSE, Label.ZIGZAG, Label.IMPULSE))
    atoms = Parse(Label.ZIGZAG, (0, 1, 2, 3), (Label.ATOM,) * 3)
    assert rank_key(verified, fine) < rank_key(atoms, fine)
    twin = Parse(Label.ZIGZAG, (0, 1, 2, 3), (Label.ATOM, Label.ATOM, Label.OPAQUE))
    assert pessimistic_rank(atoms, [atoms, twin], fine) == 2  # 같은 근거면 뒤로 센다
    assert pessimistic_rank(atoms, [twin], fine) is None
