"""H3 원자료 재판정: 불완전 결과를 가설 기각으로 오독하지 않는다."""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from auto_trading.core.hashing import sha256_hex


@pytest.fixture
def runner() -> dict[str, Any]:
    return runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/run_h3_screen.py"))


@pytest.fixture
def payload(runner: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_edge_bps": 22.0,
        "min_samples": 200,
        "coverage": {"candles": 1.0, "perp_metrics": 1.0, "funding_rates": 1.0},
        "signals": {
            name: [
                {
                    "horizon": horizon,
                    "n": 300,
                    "mean_bps": 5.0,
                    "stderr_bps": 1.0,
                    "positive_rate": 0.55,
                    "quantiles": [-2.0, -1.0, 0.0, 1.0, 2.0],
                    "yearly": {"2023": 4.0, "2024": 6.0},
                    "passes": True,
                    "monotone": False,
                }
                for horizon in ("2h", "4h", "6h")
            ]
            for name in runner["SIGNALS"]
        },
    }


def write_source(path: Path, payload: dict[str, Any]) -> bytes:
    payload["digest"] = sha256_hex(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path.read_bytes()


def test_replay_preserves_source_and_recomputes_derived_flags(
    tmp_path: Path, runner: dict[str, Any], payload: dict[str, Any]
) -> None:
    source, output = tmp_path / "raw.json", tmp_path / "final.json"
    original = write_source(source, payload)
    runner["finalize_existing"](source, output)
    first = output.read_bytes()
    runner["finalize_existing"](source, output)
    assert output.read_bytes() == first
    assert source.read_bytes() == original
    result = json.loads(first)
    assert result["source_digest"] == payload["digest"]
    digest = result.pop("digest")
    assert digest == sha256_hex(result)
    for rows in result["signals"].values():
        assert all(not row["passes_numeric"] and row["monotone"] for row in rows)
    for verdict in result["signal_verdicts"].values():
        assert not verdict["passes"]
        assert verdict["failure_reasons"] == ["EDGE_OR_SAMPLE"]


@pytest.mark.parametrize(
    "defect", ["missing_signal", "missing_horizon", "duplicate_horizon", "cost"]
)
def test_incomplete_or_wrong_experiment_cannot_produce_verdict(
    tmp_path: Path, runner: dict[str, Any], payload: dict[str, Any], defect: str
) -> None:
    name = runner["SIGNALS"][0]
    if defect == "missing_signal":
        del payload["signals"][name]
    elif defect == "missing_horizon":
        payload["signals"][name].pop()
    elif defect == "duplicate_horizon":
        payload["signals"][name][2]["horizon"] = "2h"
    else:
        payload["required_edge_bps"] = 18.0
    source, output = tmp_path / "raw.json", tmp_path / "final.json"
    write_source(source, payload)
    output.write_text("existing result", encoding="utf-8")
    with pytest.raises(SystemExit):
        runner["finalize_existing"](source, output)
    assert output.read_text(encoding="utf-8") == "existing result"


def test_changed_source_digest_is_rejected(
    tmp_path: Path, runner: dict[str, Any], payload: dict[str, Any]
) -> None:
    source, output = tmp_path / "raw.json", tmp_path / "final.json"
    write_source(source, payload)
    payload["signals"][runner["SIGNALS"][0]][0]["mean_bps"] = 99.0
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit, match="digest"):
        runner["finalize_existing"](source, output)
    assert not output.exists()


def test_source_cannot_be_overwritten(
    tmp_path: Path, runner: dict[str, Any], payload: dict[str, Any]
) -> None:
    source = tmp_path / "raw.json"
    original = write_source(source, payload)
    with pytest.raises(SystemExit, match="경로"):
        runner["finalize_existing"](source, source)
    assert source.read_bytes() == original
