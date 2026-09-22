"""검정력 계산 테스트.

두 가지를 고정한다.

1. **과거 문서의 표를 재현한다** — 새 식이 옛 식의 일반화임을 보이기 위해서다.
   검정력 50% 로 두면 archive/research/22 §6 의 값이 그대로 나와야 한다.
2. **H2 VALIDATION 이 왜 실패했는지 계산으로 보인다** — 이 게이트가 막으려는
   실제 사건이다. 수치가 흔들리면 게이트의 근거도 흔들린다.
"""

from __future__ import annotations

import pytest

from auto_trading.backtest.power import (
    DEFAULT_POWER,
    PowerCheck,
    achieved_power,
    detectable_effect_bps,
    plan,
    required_samples,
)


class TestAgainstRecordedHistory:
    """옛 문서에 남은 실측값을 재현한다."""

    @pytest.mark.parametrize(
        ("sd_bps", "effect_bps", "expected"),
        [
            (53.6, 25.0, 1279),  # 6h 롱숏 · 비용 22 — 22 §6 의 가장 빡빡한 칸
            (53.6, 30.0, 180),
            (53.6, 40.0, 36),
            (28.8, 25.0, 370),  # 2h 롱숏 · 비용 22
        ],
    )
    def test_fifty_percent_power_reproduces_the_old_table(
        self, sd_bps: float, effect_bps: float, expected: int
    ) -> None:
        got = required_samples(sd_bps, effect_bps, 22.0, power=0.50)
        assert got == pytest.approx(expected, rel=0.01)

    def test_old_formula_was_a_coin_flip(self) -> None:
        """옛 식이 주는 표본에서 실제 검정력은 50% 다. 이것이 교체 이유다."""
        n = required_samples(53.6, 25.0, 22.0, power=0.50)
        assert achieved_power(53.6, n, 25.0, 22.0) == pytest.approx(0.50, abs=0.01)

    def test_eighty_percent_costs_about_twice_the_samples(self) -> None:
        cheap = required_samples(53.6, 25.0, 22.0, power=0.50)
        proper = required_samples(53.6, 25.0, 22.0, power=0.80)
        assert proper / cheap == pytest.approx(2.02, abs=0.02)


class TestH2ValidationPostMortem:
    """H2 재현 검정은 등록 시점에 이미 실패가 예정돼 있었다.

    TRAIN 효과 34.57bps · 비용 18 · SD 122.8bps · 등록 표본 189개.
    """

    SD = 122.8
    EFFECT = 34.57
    COST = 18.0
    REGISTERED = 189

    def test_the_registered_sample_size_was_insufficient(self) -> None:
        need = required_samples(self.SD, self.EFFECT, self.COST)
        assert need == pytest.approx(443, rel=0.02)
        assert need > self.REGISTERED

    def test_its_actual_power_was_a_little_better_than_a_coin_flip(self) -> None:
        """44% — TRAIN 효과가 참값이었어도 절반 이상 놓칠 설계였다."""
        got = achieved_power(self.SD, self.REGISTERED, self.EFFECT, self.COST)
        assert got == pytest.approx(0.44, abs=0.02)

    def test_the_gate_would_have_refused_it(self) -> None:
        check = plan(self.SD, self.REGISTERED, self.EFFECT, self.COST)
        assert not check.decidable
        assert "판정 불가" in check.summary()


class TestRequiredSamples:
    def test_more_noise_needs_more_samples(self) -> None:
        low = required_samples(50.0, 30.0, 20.0)
        high = required_samples(100.0, 30.0, 20.0)
        assert high == pytest.approx(low * 4, rel=0.01), "SD 2배 → 표본 4배"

    def test_a_thinner_margin_needs_more_samples(self) -> None:
        wide = required_samples(50.0, 40.0, 20.0)
        thin = required_samples(50.0, 30.0, 20.0)
        assert thin == pytest.approx(wide * 4, rel=0.01), "여유 1/2 → 표본 4배"

    def test_higher_power_needs_more_samples(self) -> None:
        assert required_samples(50.0, 30.0, 20.0, power=0.90) > required_samples(
            50.0, 30.0, 20.0, power=0.80
        )

    def test_the_result_is_a_whole_number_rounded_up(self) -> None:
        got = required_samples(53.6, 25.0, 22.0)
        assert isinstance(got, int)
        assert achieved_power(53.6, got, 25.0, 22.0) >= DEFAULT_POWER

    @pytest.mark.parametrize(("effect", "cost"), [(20.0, 20.0), (15.0, 22.0)])
    def test_an_effect_below_cost_is_rejected_outright(self, effect: float, cost: float) -> None:
        """표본으로 해결되지 않는 문제다. 큰 수를 돌려주면 착각을 부른다."""
        with pytest.raises(ValueError, match="비용"):
            required_samples(50.0, effect, cost)

    @pytest.mark.parametrize("sd", [0.0, -1.0])
    def test_non_positive_sd_is_rejected(self, sd: float) -> None:
        with pytest.raises(ValueError, match="sd_bps"):
            required_samples(sd, 30.0, 20.0)

    @pytest.mark.parametrize("power", [0.0, 1.0, 1.5, -0.2])
    def test_impossible_power_is_rejected(self, power: float) -> None:
        with pytest.raises(ValueError, match="power"):
            required_samples(50.0, 30.0, 20.0, power=power)


class TestDetectableEffect:
    def test_it_inverts_required_samples(self) -> None:
        n = required_samples(53.6, 25.0, 22.0)
        assert detectable_effect_bps(53.6, n, 22.0) == pytest.approx(25.0, abs=0.05)

    def test_more_samples_prove_smaller_effects(self) -> None:
        few = detectable_effect_bps(100.0, 200, 18.0)
        many = detectable_effect_bps(100.0, 2000, 18.0)
        assert many < few

    def test_it_never_drops_below_cost(self) -> None:
        """비용 아래의 효과는 표본이 아무리 많아도 거래 근거가 되지 않는다."""
        assert detectable_effect_bps(100.0, 1_000_000, 18.0) > 18.0


class TestPlan:
    def _check(self, available: int) -> PowerCheck:
        return plan(53.6, available, 25.0, 22.0)

    def test_enough_samples_are_decidable(self) -> None:
        assert self._check(5432).decidable

    def test_too_few_samples_are_not(self) -> None:
        assert not self._check(500).decidable

    def test_summary_reports_both_directions(self) -> None:
        text = self._check(5432).summary()
        assert "필요 표본" in text
        assert "증명 가능한 최소 효과" in text
        assert "판정 가능" in text

    def test_the_h3_design_was_correctly_judged_decidable(self) -> None:
        """H3 는 검정력 계산을 통과했고 실제로 판정까지 갔다 — 게이트가 옳았다."""
        check = plan(53.6, 5432, 25.0, 22.0, power=0.80)
        assert check.decidable
        assert check.achieved_power == pytest.approx(0.98, abs=0.01)
