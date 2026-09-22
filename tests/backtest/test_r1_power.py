"""R1-A 검정력 측정의 인과성·결정론 테스트."""

from __future__ import annotations

import runpy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def module() -> dict[str, Any]:
    return runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/r1_power.py"))


def series(module: dict[str, Any], symbols: int = 8, days: int = 80) -> dict[str, Any]:
    start = date(2019, 12, 1)
    return {
        f"S{symbol}": {
            module["to_ms"](start + timedelta(days=day)): (
                100.0 + symbol * 10 + day,
                30_000_000.0 + symbol * 1_000_000,
            )
            for day in range(days)
        }
        for symbol in range(symbols)
    }


def test_universe_does_not_use_future_volume(module: dict[str, Any]) -> None:
    daily = series(module)
    before = module["build_universe"](
        daily, from_date=date(2020, 1, 1), to_date=date(2020, 1, 31), top=6
    )
    future_ms = module["to_ms"](date(2020, 1, 15))
    daily["S0"][future_ms] = (999.0, 9_000_000_000.0)
    after = module["build_universe"](
        daily, from_date=date(2020, 1, 1), to_date=date(2020, 1, 31), top=6
    )
    assert before == after


def test_random_spreads_are_deterministic_and_embargoed(module: dict[str, Any]) -> None:
    daily = series(module)
    universe = {"2020-01": sorted(daily)}
    kwargs = {
        "from_date": date(2020, 1, 1),
        "to_date": date(2020, 1, 28),
        "horizon_days": 3,
    }
    first = module["random_spreads"](daily, universe, **kwargs)
    assert first == module["random_spreads"](daily, universe, **kwargs)
    assert len(first) == 10


def test_required_samples_respects_cost_boundary(module: dict[str, Any]) -> None:
    assert module["required_samples"](50.0, 22.0, 22.0) is None
    assert module["required_samples"](50.0, 22.0, 32.0) == 100
