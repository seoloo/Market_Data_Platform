"""G6 달력 매니페스트의 항목 검증·digest 고정·달력 변환 검사."""

from __future__ import annotations

import pytest

from auto_trading.backtest.calendar import CoverageGap
from auto_trading.backtest.calendar_manifest import CalendarEntry, CalendarManifest
from auto_trading.config.schema import BlackoutWindow

MIN = 60_000
SHA = "a" * 64
WINDOWS = {
    "CPI": BlackoutWindow(before_min=5, after_min=10),
    "POWELL": BlackoutWindow(before_min=5, after_min=5),
}


def _entry(indicator: str = "CPI", key: str = "2021-01", scheduled_min: int = 30) -> CalendarEntry:
    return CalendarEntry(indicator, key, scheduled_min * MIN, 2 * MIN, None, "https://src", SHA)


def _manifest(*entries: CalendarEntry, gaps: tuple[CoverageGap, ...] = ()) -> CalendarManifest:
    return CalendarManifest(
        name="test",
        from_ms=1,
        to_ms=100 * MIN,
        indicators=frozenset({"CPI", "POWELL"}),
        gaps=gaps,
        entries=entries or (_entry(),),
    )


def test_entry_requires_hash_and_prior_vintage() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        CalendarEntry("CPI", "k", 30 * MIN, 2 * MIN, None, "src", "not-a-hash")
    with pytest.raises(ValueError, match="먼저"):
        CalendarEntry("CPI", "k", 30 * MIN, 30 * MIN, None, "src", SHA)
    assert _entry().db_source == f"https://src#sha256={SHA}"


def test_duplicates_and_unknown_indicators_are_rejected() -> None:
    with pytest.raises(ValueError, match="중복"):
        _manifest(_entry(), _entry(scheduled_min=40))
    with pytest.raises(ValueError, match="범위에 없는"):
        _manifest(_entry(indicator="FOMC"))


def test_digest_round_trip_and_tamper_detection() -> None:
    manifest = _manifest(_entry(), _entry(key="2021-02", scheduled_min=60))
    data = manifest.to_dict()
    assert CalendarManifest.from_dict(data) == manifest
    tampered = {**data, "entries": [{**data["entries"][0], "scheduled_ms": 31 * MIN}]}
    with pytest.raises(ValueError, match="digest"):
        CalendarManifest.from_dict(tampered)
    unsigned = {key: value for key, value in data.items() if key != "digest"}
    with pytest.raises(ValueError, match="digest"):
        CalendarManifest.from_dict(unsigned)


def test_digest_does_not_depend_on_entry_order() -> None:
    first, second = _entry(), _entry(key="2021-02", scheduled_min=60)
    assert _manifest(first, second).digest() == _manifest(second, first).digest()


def test_calendar_uses_manifest_digest_and_gaps() -> None:
    gap = CoverageGap(50 * MIN, 60 * MIN, frozenset({"POWELL"}), "판본 없음")
    manifest = _manifest(_entry(), gaps=(gap,))
    calendar = manifest.to_calendar()
    assert manifest.coverage().source_digest == manifest.digest()
    assert calendar.status_at(27 * MIN, WINDOWS) == (True, True)
    assert calendar.status_at(55 * MIN, WINDOWS) == (False, False)
    assert calendar.status_at(70 * MIN, WINDOWS) == (True, False)
