"""P1 실행기의 1회 실행 안전장치와 판정 규칙."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts import run_p1


def _must_not_load(*_: object) -> None:
    raise AssertionError("거부해야 할 상태에서 데이터를 읽었다")


def test_existing_verdict_blocks_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "verdict.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_p1, "load", _must_not_load)
    assert run_p1.run(tmp_path) == 3


def test_dirty_tree_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_p1, "git_state", lambda: ("abc", True))
    monkeypatch.setattr(run_p1, "load", _must_not_load)
    assert run_p1.run(tmp_path) == 3


def _report(lower: float, first: float, second: float, mdd: float) -> dict[str, Any]:
    return {
        "full": {"lower_2se": lower, "mean_daily": 0.001, "mdd": mdd},
        "first_half": {"mean_daily": first},
        "second_half": {"mean_daily": second},
    }


@pytest.mark.parametrize(
    ("report", "liquidations", "status"),
    [
        (_report(0.0001, 0.001, 0.0005, 0.2), 0, "PASS"),
        (_report(-0.0001, 0.001, 0.0005, 0.2), 0, "REJECT"),
        (_report(0.0001, -0.001, 0.0005, 0.2), 0, "REJECT"),
        (_report(0.0001, 0.001, 0.0005, 0.35), 0, "REJECT"),
        (_report(0.0001, 0.001, 0.0005, 0.2), 1, "REJECT"),
    ],
)
def test_judge(report: dict[str, Any], liquidations: int, status: str) -> None:
    assert run_p1.judge(report, liquidations)["status"] == status


def test_period() -> None:
    assert run_p1.FROM_MS < run_p1.SPLIT_MS < run_p1.TO_MS
