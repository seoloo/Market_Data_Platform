"""사전등록 전 검정력 계산.

이 프로젝트는 같은 벽에 세 번 부딪혔다 — 파이프라인을 다 만든 뒤에야 **애초에
판정할 수 없는 설계**였음을 알았다. H1 은 선별 후에, H2 는 재현 검정 후에 알았다.
그래서 신호를 만들기 전에 이 모듈로 먼저 계산한다.

판정 기준이 ``관측평균 − z·SE > 비용`` 일 때, 참효과 ``e`` 를 잡아낼 확률은::

    검정력 = P(Z > z − Δ·√n / SD),   Δ = e − 비용

여기서 ``검정력 = 0.5`` 로 두면 ``n = (z·SD/Δ)²`` 이 나온다. 이것이 예전 문서
(archive/research/22 §6) 가 쓰던 식이다. **그 식은 검정력 50% 짜리다** — 참으로
그만한 효과가 있어도 절반은 놓친다. 같은 기준에서 80% 를 얻으려면 표본이 2.02배
필요하다.

실제로 H2 VALIDATION 이 여기서 무너졌다. TRAIN 효과 34.57bps · 비용 18bps ·
SD 122.8bps 였으므로 80% 검정력에는 443개가 필요했는데 189개로 사전등록했고,
결과는 "0 인지 양수인지 구분 불가" 였다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

__all__ = [
    "DEFAULT_POWER",
    "DEFAULT_Z_ALPHA",
    "PowerCheck",
    "achieved_power",
    "detectable_effect_bps",
    "plan",
    "required_samples",
]

DEFAULT_Z_ALPHA = 2.0
"""판정 기준의 SE 배수. ``평균 − 2SE > 비용`` 이 이 프로젝트의 관행이다."""

DEFAULT_POWER = 0.80
"""사전등록 기본 검정력.

50% 는 동전 던지기이므로 쓰지 않는다. 90% 는 2.69배 표본을 요구해 대부분의
가설을 시작조차 못 하게 한다. 80% 는 통상적인 절충이며, **결과를 보기 전에**
정해야 하는 값이라 여기에 상수로 고정한다.
"""

_NORMAL = NormalDist()


def _z_beta(power: float) -> float:
    if not 0.0 < power < 1.0:
        raise ValueError(f"power 는 0 과 1 사이여야 한다: {power}")
    return _NORMAL.inv_cdf(power)


def _check_inputs(sd_bps: float, effect_bps: float, cost_bps: float) -> float:
    if sd_bps <= 0.0:
        raise ValueError(f"sd_bps 는 양수여야 한다: {sd_bps}")
    margin = effect_bps - cost_bps
    if margin <= 0.0:
        raise ValueError(
            f"효과({effect_bps}bps)가 비용({cost_bps}bps) 이하다. "
            "표본을 아무리 늘려도 판정이 무의미하다."
        )
    return margin


def required_samples(
    sd_bps: float,
    effect_bps: float,
    cost_bps: float,
    *,
    power: float = DEFAULT_POWER,
    z_alpha: float = DEFAULT_Z_ALPHA,
) -> int:
    """``effect_bps`` 만한 효과를 ``power`` 확률로 잡아내는 데 필요한 독립 표본 수.

    ``sd_bps`` 는 **관측 단위 하나의 표준편차**다. 선별이면 한 단면의 수익률 SD,
    전략이면 거래 한 건의 손익 SD 다. 둘을 섞으면 안 된다.
    """
    margin = _check_inputs(sd_bps, effect_bps, cost_bps)
    return math.ceil(((z_alpha + _z_beta(power)) * sd_bps / margin) ** 2)


def detectable_effect_bps(
    sd_bps: float,
    samples: int,
    cost_bps: float,
    *,
    power: float = DEFAULT_POWER,
    z_alpha: float = DEFAULT_Z_ALPHA,
) -> float:
    """``samples`` 개로 증명할 수 있는 **가장 작은 효과**.

    "표본이 이만큼인데 무엇을 증명할 수 있나" 를 묻는 역방향 계산이다.
    후보 비교에서 이 값이 현실적인 효과 크기를 넘으면 그 후보는 시작할 수 없다.
    """
    if sd_bps <= 0.0:
        raise ValueError(f"sd_bps 는 양수여야 한다: {sd_bps}")
    if samples < 1:
        raise ValueError(f"samples 는 1 이상이어야 한다: {samples}")
    return cost_bps + (z_alpha + _z_beta(power)) * sd_bps / math.sqrt(samples)


def achieved_power(
    sd_bps: float,
    samples: int,
    effect_bps: float,
    cost_bps: float,
    *,
    z_alpha: float = DEFAULT_Z_ALPHA,
) -> float:
    """이 설계가 실제로 갖는 검정력. 사후 변명이 아니라 **사전 점검**용이다."""
    margin = _check_inputs(sd_bps, effect_bps, cost_bps)
    if samples < 1:
        raise ValueError(f"samples 는 1 이상이어야 한다: {samples}")
    return 1.0 - _NORMAL.cdf(z_alpha - margin * math.sqrt(samples) / sd_bps)


@dataclass(frozen=True)
class PowerCheck:
    """사전등록에 그대로 옮겨 적을 검정력 판정."""

    sd_bps: float
    available_samples: int
    effect_bps: float
    cost_bps: float
    power: float
    required_samples: int
    detectable_effect_bps: float
    achieved_power: float

    @property
    def decidable(self) -> bool:
        """가용 표본으로 이 효과를 판정할 수 있는가."""
        return self.available_samples >= self.required_samples

    def summary(self) -> str:
        verdict = "판정 가능" if self.decidable else "판정 불가 — 사전등록 금지"
        return (
            f"SD {self.sd_bps:.1f}bps · 효과 {self.effect_bps:.1f} · 비용 {self.cost_bps:.1f}"
            f" · 검정력 {self.power:.0%}\n"
            f"필요 표본 {self.required_samples:,} / 가용 {self.available_samples:,}"
            f" → {verdict}\n"
            f"가용 표본으로 증명 가능한 최소 효과: {self.detectable_effect_bps:.1f}bps"
            f" (현 설계 검정력 {self.achieved_power:.0%})"
        )


def plan(
    sd_bps: float,
    available_samples: int,
    effect_bps: float,
    cost_bps: float,
    *,
    power: float = DEFAULT_POWER,
    z_alpha: float = DEFAULT_Z_ALPHA,
) -> PowerCheck:
    """후보 하나에 대한 검정력 판정 일체."""
    return PowerCheck(
        sd_bps=sd_bps,
        available_samples=available_samples,
        effect_bps=effect_bps,
        cost_bps=cost_bps,
        power=power,
        required_samples=required_samples(
            sd_bps, effect_bps, cost_bps, power=power, z_alpha=z_alpha
        ),
        detectable_effect_bps=detectable_effect_bps(
            sd_bps, available_samples, cost_bps, power=power, z_alpha=z_alpha
        ),
        achieved_power=achieved_power(
            sd_bps, available_samples, effect_bps, cost_bps, z_alpha=z_alpha
        ),
    )
