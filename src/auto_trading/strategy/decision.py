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
from auto_trading.features.base import FeatureResult
from auto_trading.features.indicators import atr_series
from auto_trading.features.registry import build_feature_set, int_param
from auto_trading.features.structure import SwingKind, detect_swings
from auto_trading.risk.gates import GateContext, RiskGateEngine, RiskState
from auto_trading.risk.sizing import PositionSizingEngine, SizingRequest, SizingResult
from auto_trading.strategy.bias import compute_bias
from auto_trading.strategy.cost import estimate_cost, estimate_edge
from auto_trading.strategy.policy import resolve_action
from auto_trading.strategy.scoring import compose_scores
from auto_trading.strategy.trigger import TriggerResult, compute_trigger

__all__ = ["DecisionContext", "decide"]


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

    if not snapshot.data_quality.ok or not snapshot.data_quality.warmup_ok:
        return _veto_decision(snapshot, VetoReason.DATA_QUALITY)

    features = _compute_features(snapshot, cfg)
    unavailable = tuple(result for result in features if not result.available)
    if unavailable:
        quality = DataQuality(
            ok=False,
            warmup_ok=False,
            stale_timeframes=snapshot.data_quality.stale_timeframes,
            missing_timeframes=snapshot.data_quality.missing_timeframes,
            gapped_timeframes=snapshot.data_quality.gapped_timeframes,
            notes=tuple(result.name for result in unavailable),
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
    components = _components(features, trigger, cfg)
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
    edge = estimate_edge(runtime.expected_edge_bps, runtime.edge_sample_count)
    required = cost.total_bps + Decimal(str(cfg.policy.minimum_required_edge_bps))
    sizing_result = _size_entry(snapshot, portfolio, cfg, runtime, side, bias.risk_scale)
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
            volatility_percentile=volatility.normalized,
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
