"""Elliott 복수 시나리오 출력 계약과 평가자(JEV 자리) 인터페이스 — 합성 자료."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from auto_trading.research.elliott.candidates import generate_bounded_candidates
from auto_trading.research.elliott.evaluation import (
    AbstainEvaluator,
    ScenarioEvaluation,
    ScenarioJudgement,
    Verdict,
    validate_evaluation,
)
from auto_trading.research.elliott.grammar import WavePattern
from auto_trading.research.elliott.scenario_contract import (
    SCHEMA_VERSION,
    CheckStatus,
    EvidenceState,
    ScenarioSet,
    SetStatus,
    from_candidate_snapshot,
    scenario_set_digest,
    to_json_dict,
    validate_scenario_set_dict,
)
from tests.research.test_elliott_grammar import IMPULSE_PRICES, synthetic_scenario


def _impulse_set(direction: int = 1) -> ScenarioSet:
    bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE, direction=direction)
    snapshot = generate_bounded_candidates(bars, scenario.evidence, bars[-1].close_ms)
    return from_candidate_snapshot(
        snapshot, venue="binance_usdm", instrument="BTCUSDT", flags=("CROSS_VENUE_PROXY",)
    )


class TestScenarioSet:
    def test_multiple_scenarios_never_a_single_forced_count(self) -> None:
        s = _impulse_set()
        assert s.schema_version == SCHEMA_VERSION
        assert s.status is SetStatus.SCENARIOS
        assert len(s.scenarios) == 3  # impulse · zigzag · ending diagonal 모두 싣는다
        assert s.exhaustive is False and s.other_or_unknown == "ALWAYS_POSSIBLE"
        for sc in s.scenarios:
            assert sc.rule_support.probability is None
            assert sc.rule_support.kind == "RULE_SUPPORT_UNCALIBRATED"
            assert sc.uncertainty.other_or_unknown_possible is True
            assert "CROSS_VENUE_PROXY" in sc.uncertainty.flags
            assert all(level.scope == "ANALYSIS_ONLY" for level in sc.invalidation_levels)

    def test_impulse_fields_state_anchors_fib_and_not_computed(self) -> None:
        impulse = next(sc for sc in _impulse_set().scenarios if sc.pattern == "IMPULSE")
        assert impulse.wave_count == ("0", "1", "2", "3", "4", "5")
        assert [a.label for a in impulse.anchors] == ["0", "1", "2", "3", "4", "5"]
        assert impulse.current_state in {"WAVE_5_END_UNVERIFIED", "END_INVALIDATED"}
        fib = {e.key: e for e in impulse.evidence if e.family == "FIBONACCI"}
        # 100→110→105: 2파 되돌림 0.5 (가이드라인 0.382~0.786 안)
        assert fib["wave2_retrace_of_wave1"].observed == pytest.approx(0.5)
        assert fib["wave2_retrace_of_wave1"].in_band is True
        # 105→130→120: 4파 되돌림 0.4
        assert fib["wave4_retrace_of_wave3"].observed == pytest.approx(0.4)
        not_computed = {e.family for e in impulse.evidence if e.state is EvidenceState.NOT_COMPUTED}
        assert {"MOMENTUM", "VOLUME", "MULTI_TIMEFRAME"} <= not_computed
        assert any(
            c.rule_id == "SUBDIVISIONS" and c.status is CheckStatus.UNVERIFIED
            for c in impulse.hard_checks
        )
        rules = {level.rule_id for level in impulse.invalidation_levels}
        assert {"WAVE2_BEYOND_ORIGIN", "WAVE4_OVERLAPS_WAVE1"} <= rules

    def test_down_structure_uses_mirrored_comparators(self) -> None:
        impulse = next(sc for sc in _impulse_set(-1).scenarios if sc.pattern == "IMPULSE")
        origin = next(
            lv for lv in impulse.invalidation_levels if lv.rule_id == "WAVE2_BEYOND_ORIGIN"
        )
        assert (origin.comparator, origin.price_basis) == (">=", "HIGH")

    def test_json_round_trip_is_deterministic_and_valid(self) -> None:
        s = _impulse_set()
        data = to_json_dict(s)
        assert json.loads(json.dumps(data)) == data
        assert validate_scenario_set_dict(data) == []
        assert (
            scenario_set_digest(s)
            == scenario_set_digest(data)
            == scenario_set_digest(_impulse_set())
        )

    def test_validator_rejects_forced_certainty(self) -> None:
        data = to_json_dict(_impulse_set())
        data["exhaustive"] = True
        data["scenarios"][0]["rule_support"]["probability"] = 0.87
        data["scenarios"][1]["uncertainty"]["other_or_unknown_possible"] = False
        problems = validate_scenario_set_dict(data)
        assert len(problems) == 3

    def test_insufficient_anchors_is_explicit(self) -> None:
        bars, scenario = synthetic_scenario(IMPULSE_PRICES, WavePattern.IMPULSE)
        snapshot = generate_bounded_candidates(bars[:2], scenario.evidence[:2], bars[1].close_ms)
        s = from_candidate_snapshot(snapshot, venue="v", instrument="i")
        assert s.status is SetStatus.INSUFFICIENT_ANCHORS and s.scenarios == ()


class TestEvaluatorInterface:
    def test_abstain_placeholder_is_valid_and_not_an_opinion(self) -> None:
        data = to_json_dict(_impulse_set())
        result = AbstainEvaluator().evaluate(data, evaluated_at_ms=1)
        assert result.verdict is Verdict.ABSTAIN
        assert validate_evaluation(result, data) == []

    def test_relative_support_contract(self) -> None:
        data = to_json_dict(_impulse_set())
        ids = [s["scenario_id"] for s in data["scenarios"]]
        good = ScenarioEvaluation(
            "ew-scenario-eval/1", scenario_set_digest(data), "jev-test", "m1", "p1", 1,
            Verdict.RELATIVE_SUPPORT,
            tuple(ScenarioJudgement(i, 0.4, "근거") for i in ids),
            0.6, "raw",
        )  # fmt: skip
        assert validate_evaluation(good, data) == []
        bad = replace(
            good,
            input_digest="0" * 64,
            judgements=(*good.judgements, ScenarioJudgement("ghost", 1.5, "")),
            interpretation="PROBABILITY",
        )
        assert len(validate_evaluation(bad, data)) == 4  # 해시·확률 표시·없는 ID·범위 밖

    def test_unavailable_must_not_carry_opinions(self) -> None:
        data = to_json_dict(_impulse_set())
        failed = replace(
            AbstainEvaluator().evaluate(data, evaluated_at_ms=1),
            verdict=Verdict.UNAVAILABLE,
            judgements=(ScenarioJudgement(data["scenarios"][0]["scenario_id"], 0.5, "기본값"),),
        )
        assert validate_evaluation(failed, data)  # 실패를 중립 의견으로 위장하면 거부
