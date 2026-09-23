"""A1 TRAIN 실행기의 1회 실행·재현성·달력 대조 안전장치."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.run_a1_train import verify_calendar

from auto_trading.backtest.calendar_manifest import CalendarEntry, CalendarManifest
from auto_trading.config.schema import BlackoutWindow
from scripts import run_a1_train

MIN = 60_000


def _manifest() -> CalendarManifest:
    entry = CalendarEntry("CPI", "2021-01", 30 * MIN, 2 * MIN, None, "src", "c" * 64)
    return CalendarManifest("t", 1, 100 * MIN, frozenset({"CPI"}), (), (entry,))


def test_calendar_requires_exact_db_match() -> None:
    manifest = _manifest()
    calendar = verify_calendar(manifest, manifest.events())
    assert calendar.status_at(28 * MIN, {"CPI": _window()}) == (True, True)
    with pytest.raises(ValueError, match="매니페스트와 다르다"):
        verify_calendar(manifest, ())
    doubled = (*manifest.events(), *manifest.events())
    with pytest.raises(ValueError, match="매니페스트와 다르다"):
        verify_calendar(manifest, doubled)


def _window() -> BlackoutWindow:
    return BlackoutWindow(before_min=5, after_min=5)


def test_existing_verdict_blocks_second_train_run(tmp_path: Path) -> None:
    (tmp_path / "verdict.json").write_text("{}", encoding="utf-8")
    assert run_a1_train.run(tmp_path) == 3


def test_dirty_tree_blocks_train_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_a1_train, "git_state", lambda: ("abc", True))

    def must_not_load() -> None:
        raise AssertionError("dirty 상태에서는 데이터를 읽지 않는다")

    monkeypatch.setattr(run_a1_train, "configs", must_not_load)
    assert run_a1_train.run(tmp_path) == 3
    assert not (tmp_path / "verdict.json").exists()
