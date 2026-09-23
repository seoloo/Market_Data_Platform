"""V 변동성 돌파 실행기의 1회 실행 안전장치와 판정 규칙."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_trading.backtest.vbreak import BreakoutTrade
from scripts import run_v_breakout


def _must_not_load(*_: object) -> None:
    raise AssertionError("거부해야 할 상태에서 데이터를 읽었다")


def test_existing_verdict_blocks_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "verdict.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_v_breakout, "load", _must_not_load)
    assert run_v_breakout.run(tmp_path) == 3


def test_dirty_tree_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_v_breakout, "git_state", lambda: ("abc", True))
    monkeypatch.setattr(run_v_breakout, "load", _must_not_load)
    assert run_v_breakout.run(tmp_path) == 3


def _trades(first: list[float], second: list[float]) -> list[BreakoutTrade]:
    early = [BreakoutTrade(0, 1, v, v) for v in first]
    late = [BreakoutTrade(run_v_breakout.SPLIT_MS, 1, v, v) for v in second]
    return early + late


def test_judge_requires_lower_bound_and_both_halves() -> None:
    good = _trades([10.0, 12.0, 11.0] * 50, [9.0, 10.0, 11.0] * 50)
    assert run_v_breakout.judge(good)["status"] == "PASS"
    weak_second = _trades([30.0, 32.0, 31.0] * 50, [-1.0, -2.0, 0.5] * 50)
    assert run_v_breakout.judge(weak_second)["status"] == "REJECT"
    noisy = _trades([500.0, -480.0] * 20, [500.0, -480.0] * 20)
    assert run_v_breakout.judge(noisy)["status"] == "REJECT"
