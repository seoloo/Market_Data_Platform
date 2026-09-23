"""T 추세 실행기의 1회 실행 안전장치와 판정 규칙."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_t_trend


def _must_not_load(*_: object) -> None:
    raise AssertionError("거부해야 할 상태에서 데이터를 읽었다")


def test_existing_verdict_blocks_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "verdict.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(run_t_trend, "load", _must_not_load)
    assert run_t_trend.run(tmp_path) == 3


def test_dirty_tree_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_t_trend, "git_state", lambda: ("abc", True))
    monkeypatch.setattr(run_t_trend, "load", _must_not_load)
    assert run_t_trend.run(tmp_path) == 3


def _report(mean: float, lower: float, first: float, second: float) -> dict[str, object]:
    return {
        "full": {"mean_daily": mean, "lower_2se": lower},
        "half_2018_2021": {"mean_daily": first},
        "half_2022_2026": {"mean_daily": second},
    }


@pytest.mark.parametrize(
    ("report", "liquidations", "status"),
    [
        (_report(0.001, 0.0002, 0.001, 0.0005), 0, "PASS"),
        (_report(0.001, -0.0001, 0.001, 0.0005), 0, "REJECT"),  # T1
        (_report(0.001, 0.0002, 0.002, -0.0001), 0, "REJECT"),  # T2 뒤 절반 음수
        (_report(0.001, 0.0002, 0.001, 0.0005), 1, "REJECT"),  # 청산
    ],
)
def test_judge(report: dict[str, object], liquidations: int, status: str) -> None:
    assert run_t_trend.judge(report, liquidations)["status"] == status


def test_period_and_halves_cover_the_same_span() -> None:
    assert run_t_trend.HALVES[0][0] == run_t_trend.FROM_MS
    assert run_t_trend.HALVES[1][1] == run_t_trend.TO_MS + 86_400_000
    assert run_t_trend.HALVES[0][1] == run_t_trend.HALVES[1][0]


def test_forward_without_new_data_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.backtest.trend import DailyBar

    old = {run_t_trend.FORWARD_FROM - 86_400_000: DailyBar(1.0, 1.0)}
    monkeypatch.setattr(run_t_trend, "load", lambda: ({"BTCUSDT": old, "ETHUSDT": old}, {}))
    assert run_t_trend.forward(tmp_path) == 0
    assert not (tmp_path / "forward").exists()
