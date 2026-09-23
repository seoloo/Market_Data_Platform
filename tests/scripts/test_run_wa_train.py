"""WA TRAIN 실행기의 1회 실행·재현성 안전장치와 설정 구성."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.run_wa_train import ARMS, wa_config

from auto_trading.config.schema import TightStopAction
from scripts import run_wa_train


def test_configs_fix_wa_rules_and_differ_only_by_arm() -> None:
    configs = {name: wa_config(direction, control) for name, direction, control in ARMS}
    for name, cfg in configs.items():
        assert cfg.strategy_id == "wa_v1" and cfg.wa is not None
        assert cfg.risk.tight_stop_action is TightStopAction.REDUCE_POSITION
        assert not cfg.risk.take_profit.enabled
        assert cfg.risk.volatility_gate.enabled  # G5는 우위 측정에서도 유지
        assert cfg.edge_table is None
        assert cfg.wa.control_without_state is name.startswith("control")
    assert configs["main_long"].wa is not None and configs["main_long"].wa.direction == "long"
    assert configs["main_short"].wa is not None and configs["main_short"].wa.direction == "short"


def test_existing_verdict_blocks_second_train_run(tmp_path: Path) -> None:
    (tmp_path / "verdict.json").write_text("{}", encoding="utf-8")
    assert run_wa_train.run(tmp_path) == 3


def test_dirty_tree_blocks_train_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_wa_train, "git_state", lambda: ("abc", True))

    def must_not_load(*_: object) -> None:
        raise AssertionError("dirty 상태에서는 데이터를 읽지 않는다")

    monkeypatch.setattr(run_wa_train, "load_inputs", must_not_load)
    assert run_wa_train.run(tmp_path) == 3
