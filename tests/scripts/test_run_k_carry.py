"""K 캐리 실행기의 1회 실행·순서 안전장치."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_k_carry


def _must_not_load(*_: object) -> None:
    raise AssertionError("거부해야 할 상태에서 데이터를 읽었다")


def test_existing_verdict_blocks_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "verdict.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_k_carry, "load", _must_not_load)
    assert run_k_carry.run("train", tmp_path) == 3


def test_dirty_tree_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_k_carry, "git_state", lambda: ("abc", True))
    monkeypatch.setattr(run_k_carry, "load", _must_not_load)
    assert run_k_carry.run("train", tmp_path) == 3


@pytest.mark.parametrize("train_status", [None, "REJECT"])
def test_validation_requires_train_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, train_status: str | None
) -> None:
    monkeypatch.setattr(run_k_carry, "git_state", lambda: ("abc", False))
    monkeypatch.setattr(run_k_carry, "load", _must_not_load)
    if train_status is not None:
        (tmp_path / "train").mkdir()
        record = {"train": {"verdict": {"status": train_status}}}
        (tmp_path / "train" / "verdict.json").write_text(json.dumps(record), encoding="utf-8")
    assert run_k_carry.run("validate", tmp_path) == 3


def test_periods_match_preregistration() -> None:
    train, validation, stress = (run_k_carry.PERIODS[k] for k in ("train", "validation", "stress"))
    assert train[1] + 1 == validation[0]
    assert stress[1] + 1 == train[0]
    assert train[2] == (2023, 2024)
    assert validation[2] == (2025, 2026)
