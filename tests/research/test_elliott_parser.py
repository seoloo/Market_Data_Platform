"""EW-3 Elliott 문법 파서 (33 §24) — 합성 꼭짓점 열."""

from __future__ import annotations

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.research.elliott.h4_evidence import FourHourPivotEvidence
from auto_trading.research.elliott.parser import Label, WaveParser
from tests.research.test_elliott_degrees import (
    LEG1,
    LEG2,
    LEG3,
    LEG4,
    LEG5,
    _anchors,
    ladder_fixture,
)


def _pivots(
    *legs: tuple[str, ...], direction: int = 1
) -> tuple[list[Candle], list[FourHourPivotEvidence]]:
    bars, _, child = ladder_fixture(*legs, direction=direction)
    return bars, _anchors(child)


@pytest.mark.parametrize("direction", [1, -1])
def test_five_three_five_three_five_contains_true_bounds_verified(direction: int) -> None:
    bars, pivots = _pivots(LEG1, LEG2, LEG3, LEG4, LEG5, direction=direction)
    parser = WaveParser(pivots, bars)
    parses = parser.impulses(0, len(pivots) - 1)
    truth = next(p for p in parses if p.bounds == (0, 5, 8, 13, 16, 21))
    assert truth.verified_one_level
    assert Label.IMPULSE in parser.labels(0, 5)  # 1파 자체가 impulse
    assert Label.ZIGZAG in parser.labels(5, 8)  # 2파 는 3다리


def test_six_pivot_impulse_is_one_unverified_parse() -> None:
    bars, pivots = _pivots(("100", "110", "105", "130", "120", "140"))
    parses = WaveParser(pivots, bars).impulses(0, 5)
    assert [p.bounds for p in parses] == [(0, 1, 2, 3, 4, 5)]
    assert parses[0].leg_labels == (Label.ATOM,) * 5
    assert not parses[0].verified_one_level


def test_wave_four_overlap_is_not_an_impulse() -> None:
    bars, pivots = _pivots(("100", "120", "110", "140", "115", "150"))  # 4파 115 < 1파 끝 120
    assert WaveParser(pivots, bars).impulses(0, 5) == ()


def test_non_extremal_span_has_no_structure() -> None:
    bars, pivots = _pivots(("100", "120", "95", "130"))  # 2파가 시작 아래
    parser = WaveParser(pivots, bars)
    assert parser.labels(0, 3) == frozenset()
    assert parser.zigzags(0, 3) == ()


def test_every_valid_reading_is_returned_not_one() -> None:
    bars, pivots = _pivots(LEG1, LEG2, LEG3, LEG4, LEG5)
    parses = WaveParser(pivots, bars).impulses(0, len(pivots) - 1)
    assert len(parses) > 1  # 모호도는 숨기지 않는다
    assert len({p.bounds for p in parses}) == len(parses)


def test_lenient_three_only_changes_motive_slots() -> None:
    # 1파 자리에 보이는 3다리(2파가 안 보이는 1파)
    bars, pivots = _pivots(("100", "130", "115", "150"), LEG2, LEG3, LEG4, LEG5)
    lenient = WaveParser(pivots, bars).impulses(0, len(pivots) - 1)
    strict = WaveParser(pivots, bars, lenient_three=False).impulses(0, len(pivots) - 1)
    truth = (0, 3, 6, 11, 14, 19)
    assert any(p.bounds == truth and p.leg_labels[0] is Label.THREE for p in lenient)
    assert all(p.bounds != truth for p in strict)


def test_rejects_non_alternating_pivots() -> None:
    bars, pivots = _pivots(LEG1)
    with pytest.raises(DataIntegrityError):
        WaveParser([pivots[0], pivots[2]], bars)


def test_path_pivots_break_on_sub_four_hour_legs_and_non_bar_extremes() -> None:
    from dataclasses import replace

    from auto_trading.research.elliott.minute_path import MinutePathReplay
    from auto_trading.research.elliott.parser import four_hour_path_pivots
    from tests.research.test_elliott_degrees import _replay, path_from

    bars, _, child = ladder_fixture(LEG1)
    events = path_from(child)
    clean = four_hour_path_pivots(_replay(events), bars)
    assert [len(s) for s in clean] == [6]
    # 같은 4h 봉에 연속 두 꼭짓점 → 두 사건 모두 빼고 끊는다
    same_bar = replace(
        events[2], extreme_minute_ms=events[1].extreme_minute_ms + 60_000, price=bars[2].low
    )
    broken = four_hour_path_pivots(_replay([*events[:2], same_bar, *events[3:]]), bars)
    assert [len(s) for s in broken] == [1, 3]
    # 봉의 high/low 가 아닌 국소 극값 → 끊는다
    off = replace(events[3], price=events[3].price - 1)
    assert [
        len(s) for s in four_hour_path_pivots(_replay([*events[:3], off, *events[4:]]), bars)
    ] == [3, 2]
    assert isinstance(_replay(events), MinutePathReplay)
