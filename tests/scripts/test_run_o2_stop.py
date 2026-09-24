"""O/v2 실행기의 1회 실행 안전장치와 판정 규칙."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts import run_o2_stop


def _must_not_load(*_: object) -> None:
    raise AssertionError("거부해야 할 상태에서 데이터를 읽었다")


def test_existing_verdict_blocks_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "verdict.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_o2_stop, "load", _must_not_load)
    assert run_o2_stop.run(tmp_path) == 3


def test_dirty_tree_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_o2_stop, "git_state", lambda: ("abc", True))
    monkeypatch.setattr(run_o2_stop, "load", _must_not_load)
    assert run_o2_stop.run(tmp_path) == 3


def _side(cagr: float, first: float = 0.3, second: float = 0.12, **extra: Any) -> dict[str, Any]:
    return {
        "full": {"cagr": cagr, "mdd": extra.get("mdd", 0.2)},
        "first_half": {"cagr": first},
        "second_half": {"cagr": second},
        "halted_day": extra.get("halted_day"),
        "liquidations": extra.get("liquidations", 0),
    }


A = _side(0.20, 0.25, 0.10)
B1 = _side(0.33, 0.50, 0.21)


@pytest.mark.parametrize(
    ("b2", "status"),
    [
        (_side(0.32, 0.49, 0.20), "PASS"),  # 보험 비용 1%p
        (_side(0.3099, 0.49, 0.20), "REJECT"),  # 2%p 초과
        (_side(0.32, 0.49, 0.20, mdd=0.26), "REJECT"),  # O3 실패
        (_side(0.32, 0.49, 0.09), "REJECT"),  # O5 실패 (뒤 절반 A 이하)
        (_side(0.34, 0.51, 0.22, liquidations=1), "REJECT"),
    ],
)
def test_judge(b2: dict[str, Any], status: str) -> None:
    assert run_o2_stop.judge_o2(A, B1, b2)["status"] == status


def test_config_is_o1_plus_stop_only() -> None:
    from dataclasses import replace

    from auto_trading.backtest.overlay import OverlayConfig

    assert run_o2_stop.o2_config() == replace(OverlayConfig(), catastrophe_stop=0.20)
