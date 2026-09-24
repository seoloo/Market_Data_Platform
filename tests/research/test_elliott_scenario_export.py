"""EW-3d′ 시나리오 내보내기 — 계약 검증·순위 정보 분리·결정성."""

from __future__ import annotations

import random

from auto_trading.core.types import Candle
from auto_trading.research.elliott.scanner import Trigger, scenario_list, scenario_lists
from auto_trading.research.elliott.scenario_contract import (
    scenario_set_digest,
    to_json_dict,
    validate_scenario_set_dict,
)
from auto_trading.research.elliott.scenario_export import export_scenario_set
from auto_trading.research.elliott.synthetic import build_path
from tests.research.test_elliott_scanner import _scan


def _keys(obj: object) -> list[str]:
    if isinstance(obj, dict):
        return [str(k) for k in obj] + [k for v in obj.values() for k in _keys(v)]
    if isinstance(obj, list):
        return [k for v in obj for k in _keys(v)]
    return []


def _sets() -> tuple[list[Trigger], tuple[Candle, ...]]:
    path = build_path(random.Random(21), "IMPULSE", 0.5, 0.04, 0.001)
    triggers, hours = _scan(path.minutes, path.four_hour)
    return triggers, hours


def test_export_is_valid_contract_without_rank_leak() -> None:
    triggers, hours = _sets()
    exported = 0
    for trig in triggers:
        scen = scenario_list(trig, hours)
        if not scen.ranked:
            continue
        data_set, sidecar = export_scenario_set(trig, scen, venue="synthetic", instrument="X")
        data = to_json_dict(data_set)
        assert validate_scenario_set_dict(data) == []
        ids = [s["scenario_id"] for s in data["scenarios"]]
        assert ids == sorted(ids)  # 기준 순위 순서가 아니다
        assert set(sidecar) == set(ids)
        assert not any("rank" in key for key in _keys(data))  # 순위 필드 없음
        for s in data["scenarios"]:
            assert s["rule_support"]["probability"] is None
            fibs = [e for e in s["evidence"] if e["family"] == "FIB_LEVEL"]
            assert len(fibs) == 6
            assert s["current_state"].endswith("_END_CANDIDATE")
        exported += 1
    assert exported > 0


def test_export_deterministic_and_batch_equals_single() -> None:
    triggers, hours = _sets()
    batch = scenario_lists(triggers, hours)
    for trig, scen in zip(triggers, batch, strict=True):
        single = scenario_list(trig, hours)
        assert [c.bounds for c, _ in single.top] == [c.bounds for c, _ in scen.top]
        a, _ = export_scenario_set(trig, scen, venue="v", instrument="i")
        b, _ = export_scenario_set(trig, single, venue="v", instrument="i")
        assert scenario_set_digest(a) == scenario_set_digest(b)
