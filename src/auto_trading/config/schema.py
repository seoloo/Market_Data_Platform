"""설정 스키마.

설정 오타는 단 한 번의 거래로 계좌를 소멸시킬 수 있다 (09-failure-modes.md F5).
그래서 값의 범위뿐 아니라 **파라미터 사이의 관계**까지 검증한다 (13-decisions.md D9-3).

세 가지 방어선:

1. ``extra="forbid"`` — 키 이름 오타를 잡는다. 오타난 키가 무시되고 기본값이 쓰이는 것이
   가장 위험하다 (설정을 바꿨다고 믿지만 실제로는 안 바뀐 상태)
2. ``Field(gt=..., le=...)`` — 값 범위
3. ``@model_validator`` — 파라미터 간 정합성 (레버리지 캡이 상시 발동하는 조합 등)

이 모듈은 **순수**하다. 파일·환경변수 접근은 ``loader.py`` 가 담당한다.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from auto_trading.core.enums import Mode, PositionSide, Timeframe
from auto_trading.core.hashing import sha256_hex

__all__ = [
    "A1Config",
    "BlackoutWindow",
    "CostConfig",
    "EdgeAggregationMode",
    "EdgeBucketConfig",
    "EdgeTableConfig",
    "FeatureConfig",
    "FeaturesConfig",
    "KillswitchMode",
    "LeverageLimitAction",
    "PolicyConfig",
    "RiskConfig",
    "RootConfig",
    "SizingMode",
    "StopConfig",
    "StopPick",
    "TakeProfitConfig",
    "TightStopAction",
    "UiConfig",
    "VolatilityGateConfig",
    "WAConfig",
]


# --------------------------------------------------------------------- 타입


def _to_decimal(value: Any) -> Any:
    """YAML 숫자를 정확한 ``Decimal`` 로 바꾼다.

    YAML 의 ``0.01`` 은 파이썬 ``float`` 로 파싱된다. ``Decimal(0.01)`` 을 쓰면
    ``0.01000000000000000020816...`` 이 되어 사이징 계산에 오차가 들어간다.
    ``str()`` 을 거치면 파이썬의 최단 왕복 표현이 나오므로 정확히 ``Decimal("0.01")`` 이 된다.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, int | str):
        return Decimal(value)
    return value


#: 비율 값 (0.01 = 1%). 사이징 계산에 직접 들어가므로 Decimal 이어야 한다.
Rate = Annotated[Decimal, BeforeValidator(_to_decimal)]

#: 거래비용이 위험 예산에서 차지할 수 있는 최대 비율 (05 §3.7)
_MAX_COST_SHARE = Decimal("0.35")


class _Base(BaseModel):
    """모든 설정 모델의 공통 규약."""

    model_config = ConfigDict(
        extra="forbid",  # 키 오타 차단 — 가장 중요한 방어선
        frozen=True,  # 로드 후 변경 불가
        strict=False,
        validate_default=True,
    )


# --------------------------------------------------------------------- 열거형


class SizingMode(StrEnum):
    RISK_BASED = "risk_based"
    """D9: 손절 위치에서 명목가를 역산한다."""
    FIXED_NOTIONAL = "fixed_notional"
    """D4: 명목가를 equity 의 고정 비율로 둔다. MVP 미사용."""


class LeverageLimitAction(StrEnum):
    REDUCE_POSITION = "REDUCE_POSITION"
    SKIP_TRADE = "SKIP_TRADE"


class TightStopAction(StrEnum):
    WIDEN = "WIDEN"
    SKIP = "SKIP"
    REDUCE_POSITION = "REDUCE_POSITION"


class StopMode(StrEnum):
    STRUCTURE_OR_ATR = "structure_or_atr"
    ATR_ONLY = "atr_only"


class StopPick(StrEnum):
    WIDER = "wider"
    TIGHTER = "tighter"


class TakeProfitMode(StrEnum):
    R_MULTIPLE = "r_multiple"
    PCT = "pct"


class KillswitchMode(StrEnum):
    BLOCK_ENTRIES = "block_entries"
    FLATTEN = "flatten"


class DisplayTimezone(StrEnum):
    UTC = "UTC"
    KST = "KST"


class EdgeAggregationMode(StrEnum):
    """다중 horizon edge를 하나의 진입 게이트 값으로 합치는 규칙."""

    ALL_HORIZONS_MIN = "all_horizons_min"


class EdgeBucketConfig(_Base):
    """TRAIN에서 고정한 단일 score×side×regime×horizon 관측치."""

    side: PositionSide
    regime: str = Field(min_length=1)
    horizon_bars: int = Field(gt=0)
    score_from: float = Field(ge=0.0, lt=100.0)
    score_to: float = Field(gt=0.0, le=100.0)
    sample_count: int = Field(ge=0)
    observed_mean_bps: Rate
    positive_rate: Rate = Field(ge=0, le=1)
    min_samples: int = Field(ge=30)
    available: bool
    edge_bps: Rate

    @model_validator(mode="after")
    def _check_derived_values(self) -> EdgeBucketConfig:
        if self.side is PositionSide.FLAT:
            raise ValueError("edge bucket 방향은 LONG 또는 SHORT 여야 한다")
        if self.score_from >= self.score_to:
            raise ValueError("score_from 은 score_to 보다 작아야 한다")
        available = self.sample_count >= self.min_samples
        expected_edge = self.observed_mean_bps if available else Decimal(0)
        if self.available is not available or self.edge_bps != expected_edge:
            raise ValueError("available/edge_bps 가 sample_count 기반 파생값과 다르다")
        return self

    def artifact_payload(self) -> dict[str, Any]:
        return {
            "side": self.side.value,
            "regime": self.regime,
            "horizon_bars": self.horizon_bars,
            "score_from": self.score_from,
            "score_to": self.score_to,
            "sample_count": self.sample_count,
            "observed_mean_bps": str(self.observed_mean_bps),
            "positive_rate": str(self.positive_rate),
            "min_samples": self.min_samples,
            "available": self.available,
            "edge_bps": str(self.edge_bps),
        }


class EdgeTableConfig(_Base):
    """재현 가능한 TRAIN edge 산출물과 보수적 조회 정책."""

    aggregation: EdgeAggregationMode = EdgeAggregationMode.ALL_HORIZONS_MIN
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    symbol: str = Field(min_length=3, max_length=20)
    source_config_hash: str = Field(pattern="^[0-9a-f]{64}$")
    train_from_ms: int = Field(ge=0)
    train_to_ms: int = Field(gt=0)
    decision_timeframe: Timeframe
    score_boundaries: tuple[float, ...]
    horizons_bars: tuple[int, ...]
    min_samples: int = Field(ge=30)
    observations: int = Field(ge=0)
    buckets: tuple[EdgeBucketConfig, ...]

    @model_validator(mode="after")
    def _check_table(self) -> EdgeTableConfig:
        if self.train_to_ms <= self.train_from_ms:
            raise ValueError("TRAIN 종료는 시작보다 뒤여야 한다")
        if (
            len(self.score_boundaries) < 2
            or tuple(sorted(set(self.score_boundaries))) != self.score_boundaries
            or self.score_boundaries[0] < 0.0
            or self.score_boundaries[-1] > 100.0
        ):
            raise ValueError("score_boundaries 는 [0,100] 안의 중복 없는 오름차순이어야 한다")
        if (
            not self.horizons_bars
            or tuple(sorted(set(self.horizons_bars))) != self.horizons_bars
            or any(value <= 0 for value in self.horizons_bars)
        ):
            raise ValueError("horizons_bars 는 중복 없는 양의 오름차순이어야 한다")

        identities: set[tuple[PositionSide, str, int, float, float]] = set()
        for bucket in self.buckets:
            identity = (
                bucket.side,
                bucket.regime,
                bucket.horizon_bars,
                bucket.score_from,
                bucket.score_to,
            )
            if identity in identities:
                raise ValueError(f"중복 edge bucket: {identity}")
            identities.add(identity)
            if bucket.horizon_bars not in self.horizons_bars:
                raise ValueError("bucket horizon 이 horizons_bars 에 없다")
            if bucket.min_samples != self.min_samples:
                raise ValueError("bucket min_samples 가 table min_samples 와 다르다")

        if sha256_hex(self.artifact_payload()) != self.digest:
            raise ValueError("edge table digest 가 내용과 일치하지 않는다")
        return self

    def artifact_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source_config_hash": self.source_config_hash,
            "train_from_ms": self.train_from_ms,
            "train_to_ms": self.train_to_ms,
            "decision_timeframe": self.decision_timeframe.value,
            "score_boundaries": list(self.score_boundaries),
            "horizons_bars": list(self.horizons_bars),
            "min_samples": self.min_samples,
            "observations": self.observations,
            "buckets": [bucket.artifact_payload() for bucket in self.buckets],
        }

    def resolve(
        self,
        *,
        side: PositionSide,
        score: float,
        regime: str,
    ) -> tuple[Decimal | None, int | None]:
        """모든 horizon이 가용할 때만 최악의 평균 edge를 반환한다."""
        bounds = next(
            (
                (lower, upper)
                for index, (lower, upper) in enumerate(
                    zip(self.score_boundaries, self.score_boundaries[1:], strict=False)
                )
                if lower <= score < upper
                or (index == len(self.score_boundaries) - 2 and score == upper)
            ),
            None,
        )
        if bounds is None or side is PositionSide.FLAT:
            return None, None
        lower, upper = bounds
        matched = {
            bucket.horizon_bars: bucket
            for bucket in self.buckets
            if bucket.side is side
            and bucket.regime == regime
            and bucket.score_from == lower
            and bucket.score_to == upper
        }
        if set(matched) != set(self.horizons_bars):
            return None, None
        selected = tuple(matched[horizon] for horizon in self.horizons_bars)
        if not all(bucket.available for bucket in selected):
            return None, min(bucket.sample_count for bucket in selected)
        return (
            min(bucket.observed_mean_bps for bucket in selected),
            min(bucket.sample_count for bucket in selected),
        )


# --------------------------------------------------------------------- 리스크


class CostConfig(_Base):
    """거래 비용 추정치. 사이징 공식의 입력이다 (05 §3.2).

    수수료율은 하드코딩하지 않고 거래소 API 로 갱신한다 (원 명세 §6).
    여기 값은 API 취득 실패 시의 보수적 기본값이다.
    """

    taker_fee_rate: Rate = Field(default=Decimal("0.00045"), gt=0, le=Decimal("0.01"))
    maker_fee_rate: Rate = Field(default=Decimal("0.00020"), ge=0, le=Decimal("0.01"))
    slippage_entry_rate: Rate = Field(default=Decimal("0.00020"), ge=0, le=Decimal("0.01"))
    slippage_stop_multiple: Rate = Field(default=Decimal("2.0"), ge=1, le=10)
    include_funding: bool = True
    expected_funding_rate: Rate = Field(default=Decimal("0"), ge=0, le=Decimal("0.01"))

    @property
    def cost_rate_in(self) -> Decimal:
        """진입 비용률 (수수료 + 슬리피지). 진입은 시장가이므로 taker."""
        return self.taker_fee_rate + self.slippage_entry_rate

    @property
    def cost_rate_out(self) -> Decimal:
        """청산 비용률. 스톱은 유동성 공백에서 체결되므로 슬리피지에 배수를 적용한다."""
        return self.taker_fee_rate + self.slippage_entry_rate * self.slippage_stop_multiple

    @property
    def fixed_cost_rate(self) -> Decimal:
        """손절폭과 무관한 고정 비용률. D9 정합성 계산에 쓰인다."""
        return self.cost_rate_in + self.cost_rate_out


class StopConfig(_Base):
    """손절 위치 규칙 (05 §3.1).

    손절 위치가 포지션 크기를 결정하므로 전략의 일이지만, MVP 규칙은 설정으로 고정한다.
    """

    mode: StopMode = StopMode.STRUCTURE_OR_ATR
    atr_timeframe: Timeframe = Timeframe.H1
    atr_period: int = Field(default=14, ge=2, le=200)
    atr_multiple: Rate = Field(default=Decimal("1.5"), gt=0, le=10)
    swing_threshold_pct: Rate = Field(default=Decimal("0.02"), gt=0, lt=1)
    """ZigZag 되돌림 임계. 클수록 스윙이 줄고 **확정이 늦어진다** (실측: 2%→평균 6.3h)."""
    swing_buffer_atr: Rate = Field(default=Decimal("0.2"), ge=0, le=5)
    pick: StopPick = StopPick.WIDER


class TakeProfitConfig(_Base):
    enabled: bool = True
    mode: TakeProfitMode = TakeProfitMode.R_MULTIPLE
    r_multiple: Rate = Field(default=Decimal("2.0"), gt=0, le=20)
    take_profit_pct: Rate = Field(default=Decimal("0.05"), gt=0, le=1)


class VolatilityGateConfig(_Base):
    """G5.1 / G5.2. 고정 사이징이 아니어도 변동성 게이트는 중요하다 (05 §3.4)."""

    enabled: bool = True
    timeframe: Timeframe = Timeframe.H1
    atr_period: int = Field(default=14, ge=2, le=200)
    lookback_days: int = Field(default=365, ge=30, le=2000)
    atr_pct_lower: float = Field(default=0.05, ge=0.0, le=1.0)
    atr_pct_upper: float = Field(default=0.98, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_bounds(self) -> VolatilityGateConfig:
        if self.atr_pct_lower >= self.atr_pct_upper:
            raise ValueError(
                f"atr_pct_lower({self.atr_pct_lower}) 가 atr_pct_upper({self.atr_pct_upper}) "
                "이상이다. 모든 거래가 차단된다."
            )
        return self


class BlackoutWindow(_Base):
    """경제지표 발표 전후 진입 금지 창 (G6)."""

    before_min: int = Field(ge=0, le=1440)
    after_min: int = Field(ge=0, le=1440)


class KillswitchConfig(_Base):
    mode: KillswitchMode = KillswitchMode.BLOCK_ENTRIES
    file_path: str = "data/KILL"


class RiskConfig(_Base):
    """Risk Engine 설정 (05-risk-engine.md §6).

    ``[확정]`` 주석이 붙은 값은 13-decisions.md D9 에서 확정된 것이며,
    **느슨하게 조정하려면 사용자 승인이 필요하다** (AGENTS.md §6).
    """

    # --- 사이징 (D9) ---
    sizing_mode: SizingMode = SizingMode.RISK_BASED
    risk_per_trade: Rate = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.05"))
    """거래당 최대 손실 = equity × 이 비율. 5% 초과는 스키마 단계에서 거부한다."""
    margin_allocation: Rate = Field(default=Decimal("0.10"), gt=0, le=1)
    """증거금 **상한** (목표 아님). 청산가 거리를 결정한다 (05 §3.3)."""
    max_leverage: int = Field(default=10, ge=1, le=20)
    """설계상 천장. Phase 8 은 3 으로 시작한다. 20 초과는 거부."""
    min_stop_distance: Rate = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.5"))
    max_rounding_error: Rate = Field(default=Decimal("0.05"), gt=0, le=Decimal("0.5"))
    liquidation_safety_buffer: Rate = Field(default=Decimal("2.0"), ge=1, le=20)

    leverage_limit_action: LeverageLimitAction = LeverageLimitAction.REDUCE_POSITION
    tight_stop_action: TightStopAction = TightStopAction.WIDEN

    cost: CostConfig = Field(default_factory=CostConfig)
    stop: StopConfig = Field(default_factory=StopConfig)
    take_profit: TakeProfitConfig = Field(default_factory=TakeProfitConfig)

    # --- 손실 한도 ---
    daily_loss_limit_pct: Rate = Field(default=Decimal("0.02"), gt=0, le=Decimal("0.2"))
    max_drawdown_pct: Rate = Field(default=Decimal("0.10"), gt=0, le=Decimal("0.5"))
    consecutive_loss_limit: int = Field(default=4, ge=1, le=50)
    cooldown_after_losses_min: int = Field(default=240, ge=0, le=10_080)

    # --- 거래 빈도 / 보유 ---
    daily_trade_limit: int | None = Field(default=None, ge=1, le=100)
    min_hold_minutes: int = Field(default=120, ge=0, le=10_080)
    reentry_cooldown_minutes: int = Field(default=60, ge=0, le=10_080)
    same_direction_limit: int | None = Field(default=None, ge=1, le=50)
    max_unprotected_ms: int = Field(default=5000, ge=100, le=60_000)

    # --- 하드 게이트 임계값 ---
    margin_availability_buffer: Rate = Field(default=Decimal("1.5"), ge=1, le=10)
    max_spread_bps: Rate = Field(default=Decimal("5"), gt=0, le=1000)
    max_price_divergence_bps: Rate = Field(default=Decimal("50"), gt=0, le=5000)
    recent_spike_window_min: int = Field(default=3, ge=1, le=1440)
    recent_spike_pct: Rate = Field(default=Decimal("0.03"), gt=0, le=1)
    extreme_funding_rate: Rate = Field(default=Decimal("0.001"), gt=0, le=Decimal("0.1"))
    funding_imminent_min: int = Field(default=5, ge=0, le=480)

    volatility_gate: VolatilityGateConfig = Field(default_factory=VolatilityGateConfig)
    blackout: dict[str, BlackoutWindow] = Field(default_factory=dict)
    killswitch: KillswitchConfig = Field(default_factory=KillswitchConfig)

    # ---------------------------------------------------------------- 파생값

    @property
    def max_notional_multiple(self) -> Decimal:
        """명목가 상한 (equity 배수) = 증거금 상한 × 최대 레버리지.

        **이것이 갭·슬리피지 꼬리위험의 실질적 상한이다** (05 §3.8).
        """
        return self.margin_allocation * self.max_leverage

    @property
    def implied_min_stop_distance(self) -> Decimal:
        """레버리지 캡이 걸리기 시작하는 손절폭 (D9-3).

        이보다 좁은 손절은 ``REDUCE_POSITION`` 을 상시 발동시켜
        체계적으로 예산보다 적게 위험을 지게 만든다.
        """
        return self.risk_per_trade / self.max_notional_multiple - self.cost.fixed_cost_rate

    # ---------------------------------------------------------------- 정합성

    @property
    def cost_share_of_budget(self) -> Decimal:
        """최소 손절폭에서 거래비용이 위험 예산에서 차지하는 비율 (05 §3.7)."""
        return self.cost.fixed_cost_rate / (self.min_stop_distance + self.cost.fixed_cost_rate)

    @model_validator(mode="after")
    def _check_consistency(self) -> RiskConfig:
        """파라미터 간 정합성을 **모두** 검사하고 한 번에 보고한다.

        첫 번째 문제에서 멈추면 사용자가 설정을 여러 번 고쳐야 한다.
        Risk Engine 이 모든 veto 사유를 수집하는 것과 같은 이유다 (05 §1.1).
        """
        problems: list[str] = []

        # D9-3: risk / margin / leverage / min_stop 는 독립적이지 않다.
        # 값 범위 검증만으로는 절대 잡히지 않는 종류의 설정 사고다.
        implied = self.implied_min_stop_distance
        if self.min_stop_distance < implied:
            needed = self.risk_per_trade / (self.margin_allocation * self.min_stop_distance)
            problems.append(
                f"min_stop_distance({self.min_stop_distance:.4f}) 가 레버리지 상한이 함의하는 "
                f"최소 손절폭({implied:.4f}) 보다 작다. "
                f"이 조합에서는 손절폭 {implied:.2%} 미만인 모든 거래가 레버리지 캡에 걸려 "
                f"체계적으로 예산보다 적게 위험을 지게 된다. "
                f"max_leverage 를 {needed:.1f} 이상으로 올리거나 "
                f"min_stop_distance 를 {implied:.4f} 이상으로 올려야 한다. "
                f"(13-decisions.md D9-3)"
            )

        # 손실 한도 사다리: 거래당 < 일일 < 누적 DD
        if self.daily_loss_limit_pct < self.risk_per_trade:
            problems.append(
                f"daily_loss_limit_pct({self.daily_loss_limit_pct}) 가 "
                f"risk_per_trade({self.risk_per_trade}) 보다 작다. "
                "단 한 번의 손절로 일일 한도에 도달해 거래가 시작되자마자 멈춘다."
            )
        if self.max_drawdown_pct <= self.daily_loss_limit_pct:
            problems.append(
                f"max_drawdown_pct({self.max_drawdown_pct}) 가 "
                f"daily_loss_limit_pct({self.daily_loss_limit_pct}) 이하다. "
                "하루 한도를 채우면 곧바로 HALTED 가 되어 일일 한도가 무의미해진다."
            )

        # 손절폭이 비용 대비 너무 좁으면 위험 예산을 비용이 잠식한다
        share = self.cost_share_of_budget
        if share > _MAX_COST_SHARE:
            problems.append(
                f"min_stop_distance({self.min_stop_distance:.4f}) 에서 거래비용이 "
                f"위험 예산의 {share:.1%} 를 차지한다 (상한 {_MAX_COST_SHARE:.0%}). "
                "손절폭을 넓히거나 비용 가정을 재검토해야 한다."
            )

        if problems:
            raise ValueError("\n".join(f"[{i}] {p}" for i, p in enumerate(problems, 1)))
        return self


# --------------------------------------------------------------------- 전략


class FeatureConfig(_Base):
    """개별 feature 공통 설정. 모든 요소는 개별적으로 끌 수 있다 (원 명세 §19-4)."""

    enabled: bool = True
    timeframe: Timeframe
    params: dict[str, float | int | str | bool | list[float]] = Field(default_factory=dict)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class FeaturesConfig(_Base):
    """MVP feature 5개 (13-decisions.md D1). 가중치는 등가중 고정."""

    b1_trend: FeatureConfig
    b2_volregime: FeatureConfig
    t1_pullback: FeatureConfig
    t2_momentum: FeatureConfig
    t3_volume: FeatureConfig

    @property
    def enabled_names(self) -> tuple[str, ...]:
        return tuple(name for name in type(self).model_fields if getattr(self, name).enabled)


class PolicyConfig(_Base):
    """진입·청산 정책 (06 §4).

    진입에는 높은 임계값을 쓰되, 보유 포지션은 반대 방향 점수가 현재 방향
    점수를 엄격히 추월하는 순간 신호 청산한다 (D12).
    """

    long_entry_threshold: float = Field(default=70.0, gt=50.0, le=100.0)
    short_entry_threshold: float = Field(default=70.0, gt=50.0, le=100.0)
    bias_threshold: float = Field(default=0.5, gt=0.0, le=1.0)
    allow_counter_bias_when_neutral: bool = False
    reversal_requires_flat: bool = True
    minimum_required_edge_bps: float = Field(default=5.0, ge=0.0, le=1000.0)
    confidence_full_at: float = Field(default=0.6, gt=0.0, le=1.0)


class DataConfig(_Base):
    timeframes: tuple[Timeframe, ...] = (
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    )
    db_path: str = "data/trading.db"
    candle_dir: str = "data/candles"
    staleness_multiple: float = Field(default=1.5, gt=1.0, le=10.0)
    warmup_safety_bars: int = Field(default=50, ge=0, le=1000)
    account_snapshot_interval_sec: int = Field(default=60, ge=1, le=3600)
    """자산 곡선 해상도 (D10). 기록하지 않은 데이터는 나중에 복구할 수 없다."""

    @model_validator(mode="after")
    def _require_base_timeframe(self) -> DataConfig:
        if Timeframe.M1 not in self.timeframes:
            raise ValueError("1m 은 필수다. 봉 내부 경로 해소에 쓰인다 (07 §2).")
        return self


class UiConfig(_Base):
    """대시보드 (D10). **읽기 전용, localhost 전용.**"""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1024, le=65535)
    display_timezone: DisplayTimezone = DisplayTimezone.UTC
    poll_seconds: int = Field(default=5, ge=1, le=300)

    @model_validator(mode="after")
    def _localhost_only(self) -> UiConfig:
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError(
                f"ui.host 는 로컬 주소여야 한다: {self.host!r}. "
                "대시보드를 외부에 노출하지 않는다. 원격 접속이 필요하면 SSH 터널을 쓴다 (D10)."
            )
        return self


class NotifyConfig(_Base):
    telegram_enabled: bool = False
    daily_report_utc_hour: int = Field(default=0, ge=0, le=23)
    min_severity: str = Field(default="info", pattern="^(info|warn|critical)$")


class ExecutionConfig(_Base):
    client_order_id_prefix: str = Field(default="a3f2", min_length=1, max_length=8)
    order_timeout_ms: int = Field(default=10_000, ge=1000, le=60_000)
    max_order_retries: int = Field(default=3, ge=0, le=10)
    reconcile_interval_sec: int = Field(default=60, ge=10, le=3600)
    clock_drift_warn_ms: int = Field(default=1000, ge=100, le=10_000)
    clock_drift_halt_ms: int = Field(default=5000, ge=1000, le=60_000)
    orphan_policy: str = Field(default="flatten", pattern="^(flatten|hold_with_stop)$")

    @model_validator(mode="after")
    def _check_drift(self) -> ExecutionConfig:
        if self.clock_drift_warn_ms >= self.clock_drift_halt_ms:
            raise ValueError("clock_drift_warn_ms 는 clock_drift_halt_ms 보다 작아야 한다.")
        return self


# --------------------------------------------------------------------- 루트


class A1Config(_Base):
    """A1/v1 사전등록 값. 결과를 보고 바꿀 수 없도록 정확한 값만 허용한다."""

    volatility_hours: Literal[24] = 24
    rank_days: Literal[30] = 30
    contraction_quantile_pct: Rate = Field(default=Decimal("0.30"), ge=0, le=1)
    trend_hours: Literal[200] = 200
    range_hours: Literal[6] = 6
    hold_minutes: Literal[120] = 120
    stop_sigma_multiple: Rate = Field(default=Decimal("2.0"), gt=0)
    trigger_lock_minutes: Literal[360] = 360
    control_without_contraction: bool = False

    @model_validator(mode="after")
    def _check_frozen_values(self) -> A1Config:
        if self.contraction_quantile_pct != Decimal("0.30"):
            raise ValueError("A1/v1 수축 분위는 0.30으로 사전등록됐다")
        if self.stop_sigma_multiple != Decimal("2.0"):
            raise ValueError("A1/v1 손절 배수는 2.0으로 사전등록됐다")
        return self


# --------------------------------------------------------------------- 루트


class WAConfig(_Base):
    """WA/v1 고정 규칙. 방향은 TRAIN 선택 또는 합성 검사용 명시 입력이다."""

    return_hours: Literal[24] = 24
    range_hours: Literal[168] = 168
    range_position_pct: Rate = Decimal("0.20")
    hold_minutes: Literal[360] = 360
    trigger_lock_minutes: Literal[720] = 720
    stop_sigma_multiple: Rate = Decimal("2.0")
    direction: Literal["long", "short"]
    control_without_state: bool = False

    @model_validator(mode="after")
    def _check_frozen_values(self) -> WAConfig:
        if self.range_position_pct != Decimal("0.20"):
            raise ValueError("WA/v1 범위 경계는 0.20이다")
        if self.stop_sigma_multiple != Decimal("2.0"):
            raise ValueError("WA/v1 손절 배수는 2.0이다")
        return self


class RootConfig(_Base):
    """전체 설정. 이 객체의 해시가 ``decisions.config_version_id`` 로 기록된다."""

    mode: Mode = Mode.BACKTEST
    symbol: str = Field(default="BTCUSDT", min_length=3, max_length=20)
    exchange: str = Field(default="binance_usdm", min_length=3, max_length=40)
    strategy_id: Literal["h1", "a1_v1", "wa_v1"] = "h1"
    a1: A1Config | None = None
    wa: WAConfig | None = None

    risk: RiskConfig = Field(default_factory=RiskConfig)
    features: FeaturesConfig
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    edge_table: EdgeTableConfig | None = None

    @model_validator(mode="after")
    def _check_required_timeframes(self) -> RootConfig:
        """feature 가 쓰는 타임프레임이 수집 대상에 포함되어 있는지 확인한다."""
        needed = {
            self.features.b1_trend.timeframe,
            self.features.b2_volregime.timeframe,
            self.features.t1_pullback.timeframe,
            self.features.t2_momentum.timeframe,
            self.features.t3_volume.timeframe,
            self.risk.stop.atr_timeframe,
            self.risk.volatility_gate.timeframe,
        }
        missing = sorted(tf.value for tf in needed - set(self.data.timeframes))
        if missing:
            raise ValueError(
                f"feature/risk 가 사용하는 타임프레임이 data.timeframes 에 없다: {missing}"
            )
        return self

    @model_validator(mode="after")
    def _check_edge_table_identity(self) -> RootConfig:
        if self.edge_table is None:
            return self
        if self.edge_table.symbol != self.symbol:
            raise ValueError("edge_table.symbol 이 전략 symbol 과 다르다")
        trigger_timeframe = min(
            (
                self.features.t1_pullback.timeframe,
                self.features.t2_momentum.timeframe,
                self.features.t3_volume.timeframe,
            ),
            key=lambda timeframe: timeframe.ms,
        )
        if self.edge_table.decision_timeframe is not trigger_timeframe:
            raise ValueError("edge_table.decision_timeframe 이 전략 판단 주기와 다르다")
        return self

    @model_validator(mode="after")
    def _check_live_guardrails(self) -> RootConfig:
        """실거래 모드에서만 적용되는 추가 제약."""
        if self.strategy_id == "wa_v1":
            if self.wa is None:
                raise ValueError("WA/v1 은 wa 설정이 필요하다")
            if self.mode is not Mode.BACKTEST:
                raise ValueError("미검증 WA/v1 은 backtest 전용이다")
            if self.risk.tight_stop_action is not TightStopAction.REDUCE_POSITION:
                raise ValueError("WA/v1 은 REDUCE_POSITION만 허용한다")
            if self.risk.take_profit.enabled:
                raise ValueError("WA/v1 은 목표가를 사용하지 않는다")
            if Timeframe.H1 not in self.data.timeframes:
                raise ValueError("WA/v1 은 1시간봉이 필요하다")
        if self.strategy_id == "a1_v1":
            if self.a1 is None:
                raise ValueError("A1/v1 은 a1 설정이 필요하다")
            if self.mode is not Mode.BACKTEST:
                raise ValueError("미검증 A1/v1 은 현재 backtest 모드에서만 허용된다")
            if self.risk.tight_stop_action is not TightStopAction.REDUCE_POSITION:
                raise ValueError("A1/v1 은 손절 유지·포지션 축소 정책만 허용한다")
            if self.risk.take_profit.enabled:
                raise ValueError("A1/v1 은 목표가 주문을 만들지 않는다")
        if self.mode is not Mode.LIVE:
            return self
        if self.risk.killswitch.mode is KillswitchMode.FLATTEN:
            raise ValueError(
                "live 모드에서 killswitch.mode=flatten 은 기본값으로 쓰지 않는다. "
                "일시적 데이터 이상으로 전 포지션이 강제 청산될 수 있다 (05 §5). "
                "명시적으로 필요하면 이 검증을 해제하고 진행할 것."
            )
        if self.risk.max_leverage > 5:
            raise ValueError(
                f"live 모드에서 max_leverage={self.risk.max_leverage} 는 너무 높다. "
                "Phase 8 은 3, Phase 9 초기는 5 로 시작한다 (D9 §2). "
                "실측 슬리피지를 확인하기 전에 명목가를 키우면 손실만 커진다."
            )
        return self

    def summary_lines(self) -> tuple[str, ...]:
        """기동 시 로그·Telegram 으로 보내는 요약 (F5 대응).

        설정이 의도와 다른 상태로 돌아가는 것을 사람이 눈으로 잡을 마지막 기회다.
        """
        r = self.risk
        daily_trade_limit = (
            f"{r.daily_trade_limit}회" if r.daily_trade_limit is not None else "없음"
        )
        return (
            f"mode={self.mode.value} symbol={self.symbol}",
            f"거래당 최대손실={r.risk_per_trade:.2%} "
            f"증거금상한={r.margin_allocation:.0%} "
            f"최대레버리지={r.max_leverage}x",
            f"최소손절폭={r.min_stop_distance:.2%} "
            f"(캡 발동선 {r.implied_min_stop_distance:.2%}) "
            f"명목가상한=equity×{r.max_notional_multiple:.2f}",
            f"일일손실한도={r.daily_loss_limit_pct:.1%} "
            f"누적DD한도={r.max_drawdown_pct:.1%} "
            f"일일거래한도={daily_trade_limit}",
            f"진입={self.policy.long_entry_threshold:.0f} "
            "청산=반대점수 역전 "
            f"활성feature={len(self.features.enabled_names)}개",
        )
