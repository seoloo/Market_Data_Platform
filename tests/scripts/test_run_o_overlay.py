"""O 실행기의 1회 실행 안전장치와 판정 규칙."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts import run_o_overlay


def _must_not_load(*_: object) -> None:
    raise AssertionError("거부해야 할 상태에서 데이터를 읽었다")


def test_existing_verdict_blocks_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "verdict.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_o_overlay, "load", _must_not_load)
    assert run_o_overlay.run(tmp_path) == 3


def test_dirty_tree_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_o_overlay, "git_state", lambda: ("abc", True))
    monkeypatch.setattr(run_o_overlay, "load", _must_not_load)
    assert run_o_overlay.run(tmp_path) == 3


def _side(cagr: float, first: float, second: float, **extra: Any) -> dict[str, Any]:
    return {
        "full": {"cagr": cagr, "mdd": extra.get("mdd", 0.2)},
        "first_half": {"cagr": first},
        "second_half": {"cagr": second},
        "halted_day": extra.get("halted_day"),
        "liquidations": extra.get("liquidations", 0),
    }


A = _side(0.20, 0.30, 0.10)


@pytest.mark.parametrize(
    ("b", "status"),
    [
        (_side(0.25, 0.40, 0.12), "PASS"),
        (_side(0.25, 0.40, 0.12, halted_day=1), "REJECT"),
        (_side(0.25, 0.40, 0.12, liquidations=1), "REJECT"),
        (_side(0.25, 0.40, 0.12, mdd=0.26), "REJECT"),
        (_side(0.19, 0.40, 0.12), "REJECT"),
        (_side(0.25, 0.40, 0.09), "REJECT"),
    ],
)
def test_judge(b: dict[str, Any], status: str) -> None:
    assert run_o_overlay.judge(A, b)["status"] == status


def test_period() -> None:
    assert run_o_overlay.FROM_MS < run_o_overlay.SPLIT_MS < run_o_overlay.TO_MS
