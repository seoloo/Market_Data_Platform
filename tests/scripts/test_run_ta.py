"""TA 실행기의 1회 실행 안전장치·유니버스 고정·판정 규칙."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts import run_ta


def _must_not_load(*_: object) -> None:
    raise AssertionError("거부해야 할 상태에서 데이터를 읽었다")


def test_existing_verdict_blocks_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "verdict.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_ta, "load", _must_not_load)
    assert run_ta.run(tmp_path) == 3


def test_dirty_tree_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_ta, "git_state", lambda: ("abc", True))
    monkeypatch.setattr(run_ta, "load", _must_not_load)
    assert run_ta.run(tmp_path) == 3


def test_changed_universe_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "universe.json"
    other.write_text('{"0": ["BTCUSDT"]}\n', encoding="utf-8")
    monkeypatch.setattr(run_ta, "UNIVERSE", other)
    with pytest.raises(SystemExit):
        run_ta.load_universe()


def _report(lower: float, first: float, second: float, mdd: float) -> dict[str, Any]:
    return {
        "full": {"lower_2se": lower, "mean_daily": 0.001, "mdd": mdd},
        "first_half": {"mean_daily": first},
        "second_half": {"mean_daily": second},
    }


@pytest.mark.parametrize(
    ("report", "liquidations", "status"),
    [
        (_report(0.0001, 0.001, 0.0005, 0.25), 0, "PASS"),
        (_report(-0.0001, 0.001, 0.0005, 0.25), 0, "REJECT"),  # A1
        (_report(0.0001, 0.001, -0.0001, 0.25), 0, "REJECT"),  # A2
        (_report(0.0001, 0.001, 0.0005, 0.31), 0, "REJECT"),  # A3 MDD 30% 초과
        (_report(0.0001, 0.001, 0.0005, 0.25), 1, "REJECT"),  # 청산
    ],
)
def test_judge(report: dict[str, Any], liquidations: int, status: str) -> None:
    assert run_ta.judge(report, liquidations)["status"] == status


def test_period_and_split() -> None:
    assert run_ta.FROM_MS < run_ta.SPLIT_MS < run_ta.TO_MS
