"""전략의 단일 순수 진입점인 :func:`decide`.

시장·계좌·설정 외의 런타임 사실은 ``DecisionContext``로 값만 주입한다. 이 모듈은
네트워크, DB, 파일, 시스템 시각에 접근하지 않으므로 백테스트와 실거래가 같은 경로를 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import cast

from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import (
    Action,
    OrderPurpose,
    OrderType,
    PositionSide,
    Side,
    SystemState,
    Timeframe,
    VetoReason,
)
from auto_trading.core.hashing import sha256_hex
from auto_trading.core.money import ZERO
from auto_trading.core.types import (
    Component,
    DataQuality,
    Decision,
    Intent,
    MarketSnapshot,
    PortfolioState,
    ScoreSet,
    SizingPlan,
)
from auto_trading.features.a1 import A1Observation, latest_a1_observation
from auto_trading.features.base import FeatureResult
from auto_trading.features.indicators import atr_series
from auto_trading.features.registry import build_feature_set, int_param
from auto_trading.features.structure import SwingKind, detect_swings
from auto_trading.features.volatility import VolatilityRegimeFeature
from auto_trading.features.wa import WAState, evaluate_wa
from auto_trading.risk.gates import GateContext, RiskGateEngine, RiskState
from auto_trading.risk.sizing import PositionSizingEngine, SizingRequest, SizingResult
from auto_trading.strategy.bias import compute_bias
from auto_trading.strategy.cost import estimate_cost, estimate_edge
from auto_trading.strategy.policy import resolve_action
from auto_trading.strategy.scoring import compose_scores
from auto_trading.strategy.trigger import TriggerResult, compute_trigger

__all__ = ["DecisionContext", "ScoreEvaluation", "decide", "evaluate_scores"]


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Pure Core 밖에서 관측해 한 평가에 고정하는 런타임 사실.

    기본값만으로도 ``decide(snapshot, portfolio, cfg)``를 호출할 수 있지만, 진입에 필요한
    edge·거래소 필터가 없으므로 fail-closed 된다. 청산 판단은 언제나 가능하다.
    """

    system_state: SystemState = SystemState.RUNNING
    risk_state: RiskState = field(default_factory=RiskState)
    expected_edge_bps: Decimal | None = None
    edge_sample_count: int | None = None
    qty_step: Decimal | None = None
    price_tick: Decimal | None = None
    min_qty: Decimal | None = None
    min_notional: Decimal | None = None
    maintenance_margin_rate: Decimal | None = None
    confirmed_swing_price: Decimal | None = None
    """구조 손절 가격 **수동 지정**. 기본값 ``None`` 이면 스냅샷에서 직접 검출한다.

    자동 검출이 정상 경로다. 이 값을 주입하면 백테스트와 실거래가 서로 다른 스윙을
    볼 수 있으므로 테스트·재현 목적에만 쓴다.
    """
    killswitch_active: bool = False
    code_dirty: bool = False
    clock_drift_ms: int = 0
    reconcile_ok: bool = True
    bad_price: bool = False
    event_blackout_active: bool = False
    event_calendar_available: bool = False
    event_proximity: float = 0.0
    regime_stability: float = 1.0
    a1_observation: A1Observation | None = None
    a1_precomputed_at_ms: int | None = None
    research_unvalidated: bool = False
    research_edge_mode: bool = False
    wa_state: WAState | None = None


@dataclass(frozen=True, slots=True)
class ScoreEvaluation:
    """한 스냅샷의 순수 점수 계산 결과.

    edge 추정은 주문 의도·사이징·리스크 게이트가 아니라 이 결과만 필요하다.
    ``decide()``도 같은 함수를 호출하므로 연구용 점수 경로가 따로 생기지 않는다.
    """

    scores: ScoreSet
    components: tuple[Component, ...]
    risk_scale: Decimal
    volatility_percentile: float | None
    unavailable_names: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return not self.unavailable_names


def decide(
    snapshot: MarketSnapshot,
    portfolio: PortfolioState,
    cfg: RootConfig,
    context: DecisionContext | None = None,
) -> Decision:
    """동일 입력에 항상 동일한 :class:`Decision`을 돌려주는 전략 전체 경로."""
    runtime = context if context is not None else DecisionContext()
    if snapshot.symbol != cfg.symbol:
        raise ValueError(f"snapshot.symbol({snapshot.symbol}) != cfg.symbol({cfg.symbol})")

    if cfg.strategy_id == "a1_v1":
        return _decide_a1(snapshot, portfolio, cfg, runtime)
    if cfg.strategy_id == "wa_v1":
        return _decide_wa(snapshot, portfolio, cfg, runtime)

    if not snapshot.data_quality.ok or not snapshot.data_quality.warmup_ok:
        return _veto_decision(snapshot, VetoReason.DATA_QUALITY)

    evaluation = evaluate_scores(snapshot, cfg, runtime)
    if not evaluation.available:
        quality = DataQuality(
            ok=False,
            warmup_ok=False,
            stale_timeframes=snapshot.data_quality.stale_timeframes,
            missing_timeframes=snapshot.data_quality.missing_timeframes,
            gapped_timeframes=snapshot.data_quality.gapped_timeframes,
            notes=evaluation.unavailable_names,
        )
        return Decision(
            as_of_ms=snapshot.as_of_ms,
            symbol=snapshot.symbol,
            action=Action.NONE,
            scores=ScoreSet.neutral(),
            data_quality=quality,
            snapshot_digest=snapshot.digest(),
            veto_reasons=(VetoReason.WARMUP,),
            notes=("feature warm-up 미충족",),
        )

    scores = evaluation.scores
    components = evaluation.components
    action = resolve_action(scores, portfolio, cfg.policy)

    if action is Action.NONE:
        return _decision(snapshot, action, scores, components)
    if action is Action.CLOSE:
        return _decision(
            snapshot,
            action,
            scores,
            components,
            intents=_exit_intents(snapshot, portfolio),
            notes=("반대 점수가 현재 포지션 점수를 엄격히 역전",),
        )

    side = PositionSide.LONG if action is Action.OPEN_LONG else PositionSide.SHORT
    cost = estimate_cost(cfg.risk.cost, side=side, funding_rate=snapshot.funding_rate)
    edge_bps, edge_sample_count = _resolve_edge_input(runtime, cfg, side, scores)
    edge = estimate_edge(edge_bps, edge_sample_count)
    required = cost.total_bps + Decimal(str(cfg.policy.minimum_required_edge_bps))
    sizing_result = _size_entry(snapshot, portfolio, cfg, runtime, side, evaluation.risk_scale)
    recent_change = _recent_price_change(snapshot, cfg)
    gate = RiskGateEngine(cfg.risk).evaluate(
        GateContext(
            action=action,
            system_state=runtime.system_state,
            portfolio=portfolio,
            snapshot=snapshot,
            risk_state=runtime.risk_state,
            sizing=sizing_result.plan,
            sizing_veto_reasons=sizing_result.verdict.reasons,
            killswitch_active=runtime.killswitch_active,
            code_dirty=runtime.code_dirty,
            clock_drift_ms=runtime.clock_drift_ms,
            reconcile_ok=runtime.reconcile_ok,
            bad_price=runtime.bad_price,
            volatility_percentile=evaluation.volatility_percentile,
            recent_price_change_pct=recent_change,
            event_blackout_active=runtime.event_blackout_active,
            event_calendar_available=runtime.event_calendar_available,
            expected_edge_bps=edge.edge_bps if edge.available else None,
            required_edge_bps=required,
        )
    )
    if not gate.approved or sizing_result.plan is None:
        reasons = gate.reasons or sizing_result.verdict.reasons
        return _decision(
            snapshot,
            action,
            scores,
            components,
            veto_reasons=reasons,
            expected_edge_bps=float(edge.edge_bps),
            expected_cost_bps=float(cost.total_bps),
            required_edge_bps=float(required),
        )

    plan = sizing_result.plan
    return _decision(
        snapshot,
        action,
        scores,
        components,
        intents=_entry_intents(snapshot, side, plan, cfg),
        sizing=plan,
        expected_edge_bps=float(edge.edge_bps),
        expected_cost_bps=float(cost.total_bps),
        required_edge_bps=float(required),
    )


def _decide_a1(
    snapshot: MarketSnapshot,
    portfolio: PortfolioState,
    cfg: RootConfig,
    runtime: DecisionContext,
) -> Decision:
    """사전등록 A1. 같은 함수가 연구 재생과 이후 실행에서 호출된다."""
    a1 = cfg.a1
    if a1 is None:  # pragma: no cover - schema 가 보장한다
        raise ValueError("A1 설정이 없다")
    neutral = ScoreSet.neutral()
    position = portfolio.position
    if position is not None and not position.is_flat:
        if position.opened_ms is not None and position.holding_ms(snapshot.as_of_ms) >= (
            a1.hold_minutes * 60_000
        ):
            return _decision(
                snapshot,
                Action.CLOSE,
                neutral,
                (),
                intents=_exit_intents(snapshot, portfolio),
                notes=("A1 2시간 보유 종료",),
            )
        return _decision(snapshot, Action.NONE, neutral, ())

    if not snapshot.data_quality.ok or not snapshot.data_quality.warmup_ok:
        return _veto_decision(snapshot, VetoReason.DATA_QUALITY)
    m5 = snapshot.get(Timeframe.M5)
    if m5 is None or m5.last is None:
        return _veto_decision(snapshot, VetoReason.WARMUP)
    if runtime.a1_precomputed_at_ms is not None:
        # 사전 계산값은 정확히 이 봉을 평가할 때만 유효하다. 미래 신호 주입을 차단한다.
        if runtime.a1_precomputed_at_ms != m5.last.close_ms:
            return _veto_decision(snapshot, VetoReason.DATA_QUALITY)
        observation = runtime.a1_observation
    else:
        observation = latest_a1_observation(
            m5.candles, control_without_contraction=a1.control_without_contraction
        )
    if observation is None:
        return _decision(snapshot, Action.NONE, neutral, ())
    if observation.as_of_ms != m5.last.close_ms or observation.close != m5.last.close:
        return _veto_decision(snapshot, VetoReason.DATA_QUALITY)
    if observation.sigma_5m <= 0:
        return _veto_decision(snapshot, VetoReason.WARMUP)

    side = PositionSide.LONG if observation.direction == 1 else PositionSide.SHORT
    stop_distance = Decimal(str(observation.sigma_5m)) * Decimal(24).sqrt() * a1.stop_sigma_multiple
    return _fixed_horizon_entry(
        snapshot,
        portfolio,
        cfg,
        runtime,
        side,
        stop_distance,
        regime="a1_contraction_breakout",
        stop_source="a1_2sigma_2h",
    )


def _decide_wa(
    snapshot: MarketSnapshot,
    portfolio: PortfolioState,
    cfg: RootConfig,
    runtime: DecisionContext,
) -> Decision:
    """WA 코어. 어댑터는 evaluate_wa의 반환 상태를 매 평가 후 저장해야 한다."""
    wa = cfg.wa
    if wa is None:  # pragma: no cover - schema 보장
        raise ValueError("WA 설정이 없다")
    neutral = ScoreSet.neutral()
    position = portfolio.position
    if position is not None and not position.is_flat:
        if position.opened_ms is not None and position.holding_ms(snapshot.as_of_ms) >= (
            wa.hold_minutes * Timeframe.M1.ms
        ):
            return _decision(
                snapshot,
                Action.CLOSE,
                neutral,
                (),
                intents=_exit_intents(snapshot, portfolio),
                notes=("WA 6시간 보유 종료",),
            )
        return _decision(snapshot, Action.NONE, neutral, ())
    if not snapshot.data_quality.ok or not snapshot.data_quality.warmup_ok:
        return _veto_decision(snapshot, VetoReason.DATA_QUALITY)
    if runtime.wa_state is None:
        return _veto_decision(snapshot, VetoReason.DATA_QUALITY)
    h1 = snapshot.get(Timeframe.H1)
    if h1 is None:
        return _veto_decision(snapshot, VetoReason.WARMUP)
    observation = evaluate_wa(
        h1.candles,
        as_of_ms=snapshot.as_of_ms,
        cfg=wa,
        state=runtime.wa_state,
    )
    if not observation.available:
        return _veto_decision(snapshot, VetoReason.DATA_QUALITY)
    if not observation.triggered:
        return _decision(snapshot, Action.NONE, neutral, ())
    if observation.sigma_1h is None or observation.sigma_1h <= ZERO:
        return _veto_decision(snapshot, VetoReason.WARMUP)
    side = PositionSide.LONG if wa.direction == "long" else PositionSide.SHORT
    distance = observation.sigma_1h * Decimal(wa.hold_minutes // 60).sqrt() * wa.stop_sigma_multiple
    return _fixed_horizon_entry(
        snapshot,
        portfolio,
        cfg,
        runtime,
        side,
        distance,
        regime="wa_control" if wa.control_without_state else "wa_decline_near_low",
        stop_source="wa_2sigma_6h",
    )


def _fixed_horizon_entry(
    snapshot: MarketSnapshot,
    portfolio: PortfolioState,
    cfg: RootConfig,
    runtime: DecisionContext,
    side: PositionSide,
    stop_distance: Decimal,
    *,
    regime: str,
    stop_source: str,
) -> Decision:
    """A1/WA 공통 비용·사이징·위험·보호주문 경로."""
    direction = 1 if side is PositionSide.LONG else -1
    action = Action.OPEN_LONG if side is PositionSide.LONG else Action.OPEN_SHORT
    scores = ScoreSet(
        direction=float(direction),
        confidence_score=100.0,
        risk_score=0.0,
        bias_direction=direction,
        bias_multiplier=1.0,
        agreement=1.0,
        regime=regime,
    )
    if side is PositionSide.LONG and stop_distance >= Decimal(1):
        return _decision(snapshot, action, scores, (), veto_reasons=(VetoReason.INVALID_STOP,))
    stop = (
        snapshot.mark_price * (Decimal(1) - stop_distance)
        if side is PositionSide.LONG
        else snapshot.mark_price * (Decimal(1) + stop_distance)
    )
    required_filters = (
        runtime.qty_step,
        runtime.price_tick,
        runtime.min_qty,
        runtime.min_notional,
        runtime.maintenance_margin_rate,
    )
    sizing = (
        SizingResult.veto(VetoReason.DATA_QUALITY)
        if any(value is None for value in required_filters)
        else PositionSizingEngine(cfg.risk).calculate(
            SizingRequest(
                side=side,
                equity=portfolio.equity,
                entry_price=snapshot.mark_price,
                stop_price=stop,
                stop_source=stop_source,
                qty_step=cast(Decimal, runtime.qty_step),
                price_tick=cast(Decimal, runtime.price_tick),
                min_qty=cast(Decimal, runtime.min_qty),
                min_notional=cast(Decimal, runtime.min_notional),
                maintenance_margin_rate=cast(Decimal, runtime.maintenance_margin_rate),
                expected_funding_rate=snapshot.funding_rate,
            )
        )
    )
    h1 = snapshot.get(cfg.risk.volatility_gate.timeframe)
    volatility = (
        VolatilityRegimeFeature(
            timeframe=cfg.risk.volatility_gate.timeframe,
            atr_period=cfg.risk.volatility_gate.atr_period,
            lookback_days=cfg.risk.volatility_gate.lookback_days,
        ).compute(h1)
        if h1 is not None and cfg.risk.volatility_gate.enabled
        else None
    )
    cost = estimate_cost(cfg.risk.cost, side=side, funding_rate=snapshot.funding_rate)
    gate = RiskGateEngine(cfg.risk).evaluate(
        GateContext(
            action=action,
            system_state=runtime.system_state,
            portfolio=portfolio,
            snapshot=snapshot,
            risk_state=runtime.risk_state,
            sizing=sizing.plan,
            sizing_veto_reasons=sizing.verdict.reasons,
            killswitch_active=runtime.killswitch_active,
            code_dirty=runtime.code_dirty,
            clock_drift_ms=runtime.clock_drift_ms,
            reconcile_ok=runtime.reconcile_ok,
            bad_price=runtime.bad_price,
            volatility_percentile=volatility.normalized if volatility is not None else None,
            recent_price_change_pct=_recent_price_change(snapshot, cfg),
            event_blackout_active=runtime.event_blackout_active,
            event_calendar_available=runtime.event_calendar_available,
            expected_edge_bps=runtime.expected_edge_bps,
            required_edge_bps=cost.total_bps,
            research_unvalidated=runtime.research_unvalidated,
            research_edge_mode=runtime.research_edge_mode,
        )
    )
    if not gate.approved or sizing.plan is None:
        return _decision(
            snapshot,
            action,
            scores,
            (),
            veto_reasons=gate.reasons or sizing.verdict.reasons,
            expected_cost_bps=float(cost.total_bps),
        )
    return _decision(
        snapshot,
        action,
        scores,
        (),
        sizing=sizing.plan,
        intents=_entry_intents(snapshot, side, sizing.plan, cfg),
        expected_cost_bps=float(cost.total_bps),
    )


def _resolve_edge_input(
    runtime: DecisionContext,
    cfg: RootConfig,
    side: PositionSide,
    scores: ScoreSet,
) -> tuple[Decimal | None, int | None]:
    """수동 재현값이 없을 때만 설정에 고정된 다중 horizon table을 조회한다."""
    if runtime.expected_edge_bps is not None or runtime.edge_sample_count is not None:
        return runtime.expected_edge_bps, runtime.edge_sample_count
    if cfg.edge_table is None:
        return None, None
    score = scores.long_score if side is PositionSide.LONG else scores.short_score
    return cfg.edge_table.resolve(
        side=side,
        score=score,
        regime=scores.regime or "unknown",
    )


def evaluate_scores(
    snapshot: MarketSnapshot,
    cfg: RootConfig,
    context: DecisionContext | None = None,
) -> ScoreEvaluation:
    """라이브 ``decide()``와 동일한 경로로 점수까지만 계산한다.

    I/O·시계·난수를 사용하지 않는 순수 함수다. 데이터 품질 자체의 승인 여부는
    호출자가 확인하며, feature warm-up 부족은 ``available=False`` 로 반환한다.
    """
    runtime = context if context is not None else DecisionContext()
    if snapshot.symbol != cfg.symbol:
        raise ValueError(f"snapshot.symbol({snapshot.symbol}) != cfg.symbol({cfg.symbol})")
    if not snapshot.data_quality.ok or not snapshot.data_quality.warmup_ok:
        return ScoreEvaluation(
            scores=ScoreSet.neutral(),
            components=(),
            risk_scale=ZERO,
            volatility_percentile=None,
            unavailable_names=("data_quality",),
        )
    features = _compute_features(snapshot, cfg)
    unavailable = tuple(result.name for result in features if not result.available)
    if unavailable:
        return ScoreEvaluation(
            scores=ScoreSet.neutral(),
            components=(),
            risk_scale=ZERO,
            volatility_percentile=None,
            unavailable_names=unavailable,
        )

    trend, volatility, pullback, momentum, volume = features
    bias = compute_bias(
        trend, volatility, cfg.policy, cfg.risk.volatility_gate, cfg.features.b2_volregime
    )
    trigger = compute_trigger(
        pullback,
        momentum,
        volume,
        bias_direction=bias.direction,
        pullback_cfg=cfg.features.t1_pullback,
        momentum_cfg=cfg.features.t2_momentum,
    )
    scores = compose_scores(
        bias,
        trigger,
        cfg.policy,
        setup_lookback_bars=int_param(cfg.features.t1_pullback, "setup_lookback_bars", 12),
        data_quality=1.0,
        regime_stability=runtime.regime_stability,
        spread_risk=_spread_risk(snapshot, cfg),
        event_proximity=runtime.event_proximity,
        funding_extremity=_funding_extremity(snapshot, cfg),
        volatility_percentile=volatility.normalized or 0.0,
    )
    return ScoreEvaluation(
        scores=scores,
        components=_components(features, trigger, cfg),
        risk_scale=bias.risk_scale,
        volatility_percentile=volatility.normalized,
    )


def _compute_features(
    snapshot: MarketSnapshot, cfg: RootConfig
) -> tuple[FeatureResult, FeatureResult, FeatureResult, FeatureResult, FeatureResult]:
    features = build_feature_set(cfg.features).as_tuple()
    return cast(
        tuple[FeatureResult, FeatureResult, FeatureResult, FeatureResult, FeatureResult],
        tuple(feature.compute(snapshot.require(feature.timeframe)) for feature in features),
    )


def _size_entry(
    snapshot: MarketSnapshot,
    portfolio: PortfolioState,
    cfg: RootConfig,
    context: DecisionContext,
    side: PositionSide,
    risk_scale: Decimal,
) -> SizingResult:
    required = (
        context.qty_step,
        context.price_tick,
        context.min_qty,
        context.min_notional,
        context.maintenance_margin_rate,
    )
    if any(value is None for value in required):
        return SizingResult.veto(VetoReason.DATA_QUALITY)
    atrs = atr_series(
        snapshot.require(cfg.risk.stop.atr_timeframe).candles,
        cfg.risk.stop.atr_period,
    )
    atr = atrs[-1] if atrs else None
    if atr is None or atr <= ZERO:
        return SizingResult.veto(VetoReason.WARMUP)
    price_tick = cast(Decimal, context.price_tick)
    swing_price = (
        context.confirmed_swing_price
        if context.confirmed_swing_price is not None
        else _confirmed_swing_price(snapshot, cfg, side)
    )
    engine = PositionSizingEngine(cfg.risk)
    stop = engine.select_stop(
        side=side,
        entry_price=snapshot.mark_price,
        atr=atr,
        price_tick=price_tick,
        confirmed_swing_price=swing_price,
    )
    return engine.calculate(
        SizingRequest(
            side=side,
            equity=portfolio.equity,
            entry_price=snapshot.mark_price,
            stop_price=stop.price,
            stop_source=stop.source,
            qty_step=cast(Decimal, context.qty_step),
            price_tick=price_tick,
            min_qty=cast(Decimal, context.min_qty),
            min_notional=cast(Decimal, context.min_notional),
            maintenance_margin_rate=cast(Decimal, context.maintenance_margin_rate),
            expected_funding_rate=snapshot.funding_rate,
            risk_scale=risk_scale,
        )
    )


def _confirmed_swing_price(
    snapshot: MarketSnapshot, cfg: RootConfig, side: PositionSide
) -> Decimal | None:
    """구조 손절의 기준이 되는 **확정** 스윙 가격.

    ``detect_swings`` 는 확정된 스윙만 내보내므로 repainting 이 구조적으로 불가능하다.
    롱은 직전 확정 스윙 저점, 숏은 직전 확정 스윙 고점을 쓴다. 손절이 진입가의
    반대편에 오는 경우(이미 스윙을 뚫고 지나간 경우)는 후보에서 제외한다 —
    그대로 쓰면 즉시 체결되는 손절이 된다.
    """
    series = snapshot.get(cfg.risk.stop.atr_timeframe)
    if series is None or not series.candles:
        return None
    swings = detect_swings(series.candles, cfg.risk.stop.swing_threshold_pct)
    wanted = SwingKind.LOW if side is PositionSide.LONG else SwingKind.HIGH
    for swing in reversed(swings.swings):
        if swing.kind is not wanted:
            continue
        if side is PositionSide.LONG and swing.price >= snapshot.mark_price:
            return None
        if side is PositionSide.SHORT and swing.price <= snapshot.mark_price:
            return None
        return swing.price
    return None


def _components(
    features: tuple[FeatureResult, FeatureResult, FeatureResult, FeatureResult, FeatureResult],
    trigger: TriggerResult,
    cfg: RootConfig,
) -> tuple[Component, ...]:
    """``decisions.components_json`` 에 남길 기여도 내역.

    ``signal`` 은 **실제 합성식에 들어간 값 그대로**여야 한다. 기록용으로 한 번 더
    가공하면 나중에 "왜 이 점수가 나왔나" 를 재현할 수 없다 (D13 단일 squash).

    D14 이후 T1 은 가중합에 들어가지 않고 **setup 조건**(0/1)으로 작동하므로,
    signal 에는 setup 성립 여부를 1/0 으로 남기고 경과 봉·깊이는 raw 로 남긴다.
    """
    trend, volatility, pullback, momentum, volume = features
    setup = trigger.setup
    return (
        _component(trend, "bias", float(trend.normalized or 0.0), 1.0),
        _component(volatility, "bias", 0.0, 1.0),
        _component(pullback, "trigger", 1.0 if setup.armed else 0.0, 0.0),
        _component(momentum, "trigger", trigger.momentum_value, 1.0),
        _component(volume, "trigger", trigger.volume_confirm, 0.0),
    )


def _component(result: FeatureResult, layer: str, signal: float, weight: float) -> Component:
    return Component(
        name=result.name,
        layer=layer,
        raw=float(result.raw or ZERO),
        normalized=result.normalized or 0.0,
        signal=signal,
        weight=weight,
    )


def _entry_intents(
    snapshot: MarketSnapshot,
    side: PositionSide,
    plan: SizingPlan,
    cfg: RootConfig,
) -> tuple[Intent, ...]:
    entry_side = Side.BUY if side is PositionSide.LONG else Side.SELL
    exit_side = entry_side.opposite
    risk_distance = abs(plan.entry_price - plan.stop_price)
    multiple = cfg.risk.take_profit.r_multiple
    take_profit = (
        plan.entry_price + risk_distance * multiple
        if side is PositionSide.LONG
        else plan.entry_price - risk_distance * multiple
    )
    entry_action = Action.OPEN_LONG if side is PositionSide.LONG else Action.OPEN_SHORT
    base = _order_key(snapshot, entry_action)
    intents = [
        Intent(OrderPurpose.ENTRY, entry_side, OrderType.MARKET, plan.qty, f"{base}-E"),
        Intent(
            OrderPurpose.STOP,
            exit_side,
            OrderType.STOP_MARKET,
            ZERO,
            f"{base}-S",
            stop_price=plan.stop_price,
            close_position=True,
        ),
    ]
    if cfg.risk.take_profit.enabled:
        intents.append(
            Intent(
                OrderPurpose.TAKE_PROFIT,
                exit_side,
                OrderType.TAKE_PROFIT_MARKET,
                ZERO,
                f"{base}-T",
                stop_price=take_profit,
                close_position=True,
            )
        )
    return tuple(intents)


def _exit_intents(snapshot: MarketSnapshot, portfolio: PortfolioState) -> tuple[Intent, ...]:
    position = portfolio.position
    if position is None or position.is_flat:
        return ()
    return (
        Intent(
            purpose=OrderPurpose.EXIT,
            side=position.side.closing_side,
            order_type=OrderType.MARKET,
            qty=position.qty,
            client_order_id=f"{_order_key(snapshot, Action.CLOSE)}-X",
            reduce_only=True,
        ),
    )


def _order_key(snapshot: MarketSnapshot, action: Action) -> str:
    digest = sha256_hex(
        {"snapshot": snapshot.digest(), "symbol": snapshot.symbol, "action": action.value}
    )
    return f"at-{digest[:24]}"


def _recent_price_change(snapshot: MarketSnapshot, cfg: RootConfig) -> Decimal | None:
    series = snapshot.get(Timeframe.M1)
    if series is None or len(series) < 2:
        return None
    window = max(1, cfg.risk.recent_spike_window_min // series.timeframe.minutes)
    if len(series) <= window:
        return None
    base = series.closes[-1 - window]
    return None if base <= ZERO else (series.closes[-1] - base) / base


def _spread_risk(snapshot: MarketSnapshot, cfg: RootConfig) -> float:
    if snapshot.spread_bps is None:
        return 0.0
    return float(snapshot.spread_bps / cfg.risk.max_spread_bps)


def _funding_extremity(snapshot: MarketSnapshot, cfg: RootConfig) -> float:
    if snapshot.funding_rate is None:
        return 0.0
    return float(abs(snapshot.funding_rate) / cfg.risk.extreme_funding_rate)


def _decision(
    snapshot: MarketSnapshot,
    action: Action,
    scores: ScoreSet,
    components: tuple[Component, ...],
    *,
    veto_reasons: tuple[VetoReason, ...] = (),
    intents: tuple[Intent, ...] = (),
    sizing: SizingPlan | None = None,
    expected_edge_bps: float | None = None,
    expected_cost_bps: float | None = None,
    required_edge_bps: float | None = None,
    notes: tuple[str, ...] = (),
) -> Decision:
    return Decision(
        as_of_ms=snapshot.as_of_ms,
        symbol=snapshot.symbol,
        action=action,
        scores=scores,
        data_quality=snapshot.data_quality,
        snapshot_digest=snapshot.digest(),
        components=components,
        veto_reasons=veto_reasons,
        intents=intents,
        sizing=sizing,
        expected_edge_bps=expected_edge_bps,
        expected_cost_bps=expected_cost_bps,
        required_edge_bps=required_edge_bps,
        notes=notes,
    )


def _veto_decision(snapshot: MarketSnapshot, reason: VetoReason) -> Decision:
    return Decision(
        as_of_ms=snapshot.as_of_ms,
        symbol=snapshot.symbol,
        action=Action.NONE,
        scores=ScoreSet.neutral(),
        data_quality=snapshot.data_quality,
        snapshot_digest=snapshot.digest(),
        veto_reasons=(reason,),
    )
