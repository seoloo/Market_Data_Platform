"""느린 Bias 층 해석.

B1 은 **방향을 허가**하고, B2 는 그 국면에서 **얼마나 걸 것인가**를 정한다.

D14 이전에는 B2 가 방향 점수 자체에 곱해졌다. 그 구조에서는 고변동 국면의 계수
0.5 가 "신호를 절반으로 줄인다" 가 아니라 **"거래를 금지한다"** 로 작동했다 —
``squash`` 출력이 유계라 70점 진입선에 도달하려면 T2 z 가 11 이상 필요해지기
때문이다. 실측에서 전체 봉의 48% 가 고변동이었고 진입은 전부 정상 국면에서 나왔다.

그래서 국면 계수는 ``risk_scale`` 이 되어 **포지션 크기**에만 적용되고, 거래를
막아야 하는 극단 국면은 리스크 게이트(G5.1/G5.2)가 **명시적으로** 차단한다.
완충장치와 차단장치를 분리한 것이 이 변경의 핵심이다.

사람이 읽는 설명: docs/STRATEGY-CATALOG.md §4-2
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import FeatureConfig, PolicyConfig, VolatilityGateConfig
from auto_trading.features.base import FeatureResult
from auto_trading.features.registry import float_param

__all__ = ["BiasResult", "compute_bias"]

REGIME_NORMAL = "normal"
REGIME_HIGH = "high"
REGIME_EXTREME = "extreme"
REGIME_UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BiasResult:
    """상위 추세 방향과 변동성 국면.

    ``risk_scale`` 은 **방향 점수에 곱하지 않는다.** 사이징의 거래당 허용 손실에만
    곱해진다. 이 구분이 깨지면 D14 이전의 "완충장치가 거부권이 되는" 문제가 돌아온다.
    """

    direction: int
    risk_scale: Decimal
    regime: str
    available: bool

    def __post_init__(self) -> None:
        if self.direction not in (-1, 0, 1):
            raise ValueError(f"direction 은 -1/0/1 이어야 한다: {self.direction}")
        if not Decimal(0) < self.risk_scale <= Decimal(1):
            raise ValueError(f"risk_scale 은 (0,1] 이어야 한다: {self.risk_scale}")

    @property
    def is_extreme(self) -> bool:
        """극단 국면인가. 차단은 리스크 게이트가 한다 — 여기서는 보고만 한다."""
        return self.regime == REGIME_EXTREME


def compute_bias(
    trend: FeatureResult,
    volatility: FeatureResult,
    policy: PolicyConfig,
    volatility_gate: VolatilityGateConfig,
    volregime_cfg: FeatureConfig | None = None,
) -> BiasResult:
    """B1 방향과 B2 변동성 국면을 판정한다."""
    if (
        not trend.available
        or trend.normalized is None
        or not volatility.available
        or volatility.normalized is None
    ):
        return BiasResult(
            direction=0,
            risk_scale=Decimal(1),
            regime=REGIME_UNAVAILABLE,
            available=False,
        )

    strength = trend.normalized
    direction = 0 if abs(strength) < policy.bias_threshold else 1 if strength > 0.0 else -1

    percentile = volatility.normalized
    if volatility_gate.enabled and (
        percentile < volatility_gate.atr_pct_lower or percentile > volatility_gate.atr_pct_upper
    ):
        # 극단 국면. 점수를 깎는 대신 게이트가 VOL_TOO_HIGH / VOL_TOO_LOW 로 막는다.
        return BiasResult(
            direction=direction,
            risk_scale=Decimal(1),
            regime=REGIME_EXTREME,
            available=True,
        )

    high_at = 0.70 if volregime_cfg is None else float_param(volregime_cfg, "high_percentile", 0.70)
    high_scale = (
        Decimal("0.5")
        if volregime_cfg is None
        else Decimal(str(float_param(volregime_cfg, "high_risk_scale", 0.5)))
    )
    if percentile > high_at:
        return BiasResult(
            direction=direction,
            risk_scale=high_scale,
            regime=REGIME_HIGH,
            available=True,
        )
    return BiasResult(
        direction=direction,
        risk_scale=Decimal(1),
        regime=REGIME_NORMAL,
        available=True,
    )
