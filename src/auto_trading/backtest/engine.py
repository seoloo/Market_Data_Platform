"""이벤트 기반 봉 재생 엔진 (07-backtest.md §1~3).

**라이브와 같은 `decide()` 를 호출한다.** 벡터화하지 않는 이유가 이것이다 —
포지션·쿨다운·리스크 한도는 경로 의존적이라 벡터화하면 라이브와 로직이 갈라지고,
그 순간 백테스트 성적은 라이브에 대해 아무것도 말해주지 않는다.

한 사이클:

    결정 TF 봉 마감 (as_of = close_ms)
      → 스냅샷 구성 (as_of 이하 마감봉만)
      → decide()
      → Intent 를 **다음 1분봉부터** 체결 (R-1)

보유 중에는 1분봉을 하나씩 지나며 보호주문 발동·강제청산·funding 을 확인한다.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from decimal import Decimal

from auto_trading.backtest.calendar import MacroCalendar
from auto_trading.backtest.feed import CandleFeed, FeedWindow
from auto_trading.backtest.fills import FillSimulator, SlippageModel
from auto_trading.backtest.portfolio import BacktestPortfolio, BacktestRiskTracker, BacktestTrade
from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import (
    Action,
    CloseReason,
    Mode,
    OrderPurpose,
    PositionSide,
    Timeframe,
)
from auto_trading.core.money import ZERO
from auto_trading.core.types import (
    Candle,
    DataQuality,
    Decision,
    FundingRate,
    Intent,
    MarketSnapshot,
)
from auto_trading.features.a1 import A1Observation, scan_a1
from auto_trading.marketdata.snapshot import build_requirements
from auto_trading.strategy.decision import DecisionContext, decide

__all__ = ["BacktestEngine", "BacktestResult", "ExchangeFilters"]

_FUNDING_JITTER_MS = 60_000


@dataclass(frozen=True, slots=True)
class ExchangeFilters:
    """거래소 수량·가격 필터. 실거래에서는 exchangeInfo 에서 온다."""

    qty_step: Decimal = Decimal("0.001")
    price_tick: Decimal = Decimal("0.1")
    min_qty: Decimal = Decimal("0.001")
    min_notional: Decimal = Decimal("5")
    maintenance_margin_rate: Decimal = Decimal("0.004")


@dataclass(slots=True)
class BacktestResult:
    """한 번의 재생 결과. 지표 계산은 이 원자료에서만 한다."""

    symbol: str
    from_ms: int
    to_ms: int
    initial_equity: Decimal
    final_equity: Decimal
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[int, Decimal]] = field(default_factory=list)
    decisions_evaluated: int = 0
    entries_signalled: int = 0
    veto_counts: dict[str, int] = field(default_factory=dict)
    close_reasons: dict[str, int] = field(default_factory=dict)
    bars_in_position: int = 0
    bars_total: int = 0

    @property
    def liquidations(self) -> int:
        return self.close_reasons.get(CloseReason.LIQUIDATION.value, 0)

    @property
    def exposure(self) -> float:
        """포지션 보유 시간 비율. 낮으면 Sharpe 가 부풀려진다 (07 §4.2)."""
        return self.bars_in_position / self.bars_total if self.bars_total else 0.0

    def summary(self) -> str:
        net = self.final_equity - self.initial_equity
        pct = net / self.initial_equity * 100 if self.initial_equity else ZERO
        warning = "  [경고] n<100, 통계적 근거 부족" if len(self.trades) < 100 else ""
        liquidation = "  [경고] 강제청산 발생 = 실격" if self.liquidations else ""
        return (
            f"거래 {len(self.trades)}건{warning}\n"
            f"자본 {self.initial_equity} → {self.final_equity} ({pct:+.2f}%){liquidation}\n"
            f"평가 {self.decisions_evaluated}회 · 진입신호 {self.entries_signalled}회 · "
            f"노출 {self.exposure * 100:.1f}%\n"
            f"청산 사유 {self.close_reasons}\n"
            f"거부 사유 {dict(sorted(self.veto_counts.items(), key=lambda kv: -kv[1])[:6])}"
        )


class BacktestEngine:
    """봉 재생 오케스트레이터."""

    __slots__ = (
        "_a1_events",
        "_cfg",
        "_context",
        "_decision_tf",
        "_feed",
        "_filters",
        "_funding",
        "_funding_times",
        "_macro_calendar",
        "_sim",
    )

    def __init__(
        self,
        cfg: RootConfig,
        feed: CandleFeed,
        *,
        funding: tuple[FundingRate, ...] = (),
        filters: ExchangeFilters | None = None,
        slippage: SlippageModel | None = None,
        context: DecisionContext | None = None,
        macro_calendar: MacroCalendar | None = None,
    ) -> None:
        self._cfg = cfg
        self._feed = feed
        self._filters = filters or ExchangeFilters()
        self._sim = FillSimulator(cfg.risk.cost, slippage)
        self._funding = tuple(sorted(funding, key=lambda r: r.funding_ms))
        self._funding_times = tuple(rate.funding_ms for rate in self._funding)
        self._decision_tf = _decision_timeframe(cfg)
        self._context = context
        self._macro_calendar = macro_calendar
        self._a1_events: dict[int, A1Observation] = (
            scan_a1(
                feed.all_candles(Timeframe.M5),
                control_without_contraction=cfg.a1.control_without_contraction,
            )
            if cfg.strategy_id == "a1_v1" and cfg.a1 is not None
            else {}
        )

    @staticmethod
    def feed_windows(cfg: RootConfig) -> tuple[FeedWindow, ...]:
        """스냅샷 요구치와 **같은** 창을 쓴다.

        여기서 따로 정하면 백테스트와 실거래가 다른 깊이를 보게 된다.
        """
        return tuple(
            FeedWindow(timeframe=r.timeframe, bars=r.bars) for r in build_requirements(cfg)
        )

    def run(self, *, from_ms: int, to_ms: int, initial_equity: Decimal) -> BacktestResult:
        """``[from_ms, to_ms]`` 를 재생한다."""
        portfolio = BacktestPortfolio(symbol=self._feed.symbol, initial_equity=initial_equity)
        base_risk_state = self._context.risk_state if self._context is not None else None
        risk_tracker = BacktestRiskTracker.start(from_ms, initial_equity, base_risk_state)
        result = BacktestResult(
            symbol=self._feed.symbol,
            from_ms=from_ms,
            to_ms=to_ms,
            initial_equity=initial_equity,
            final_equity=initial_equity,
        )
        minutes = self._feed.bars_between(Timeframe.M1, from_ms, to_ms)
        protective: tuple[Intent, ...] = ()
        pending: Decision | None = None
        funding_index = 0
        last_decision_close = -1

        for bar in minutes:
            result.bars_total += 1
            risk_tracker.advance(bar.open_ms, portfolio.equity(bar.open))
            funding_index = self._settle_funding(portfolio, risk_tracker, bar, funding_index)

            # 1) 대기 중인 Intent 를 이 봉에서 체결한다 (신호 봉 다음 봉 = R-1).
            if pending is not None:
                if self._execute(portfolio, risk_tracker, pending, bar, result):
                    protective = _protective_of(pending)
                    pending = None
                elif bar.volume > ZERO:
                    pending = None  # 유동성이 있었는데 못 채웠으면 기회는 사라진 것이다

            # 2) 보유 중이면 보호주문·강제청산을 확인한다.
            if portfolio.position is not None:
                if portfolio.position.is_liquidated(bar):
                    trade = portfolio.liquidate(bar)
                    risk_tracker.record_exit(trade, ZERO, self._cfg.risk)
                    _count(result.close_reasons, trade.close_reason.value)
                    protective = ()
                else:
                    fired = self._sim.resolve_protective(protective, bar)
                    if fired is not None:
                        fill = self._sim.fill_protective(fired, bar, portfolio.protective_qty())
                        if fill is not None:
                            trade = portfolio.close(
                                fill, portfolio.purpose_to_reason(fired.purpose)
                            )
                            risk_tracker.record_exit(trade, fill.fee, self._cfg.risk)
                            _count(result.close_reasons, trade.close_reason.value)
                            protective = ()
                portfolio.mark(bar)
                if portfolio.position is not None:
                    result.bars_in_position += 1

            # A1 은 진입 체결 시각부터 120분 뒤에 닫는다. 5분 결정봉을 기다리면
            # 보유 시간이 최대 4분 더 길어지므로 1분 재생에서도 같은 decide()를 호출한다.
            as_of = bar.open_ms + Timeframe.M1.ms - 1
            if (
                self._cfg.strategy_id == "a1_v1"
                and self._cfg.a1 is not None
                and portfolio.position is not None
                and as_of - portfolio.position.entry_ms
                >= self._cfg.a1.hold_minutes * Timeframe.M1.ms
                and pending is None
            ):
                timed = self._evaluate(portfolio, risk_tracker, as_of, result)
                if timed is not None and timed.intents:
                    pending = timed

            # 3) 결정 TF 가 마감됐으면 판단한다.
            close_ms = self._decision_close(as_of)
            if close_ms > last_decision_close and pending is None:
                last_decision_close = close_ms
                decision = self._evaluate(portfolio, risk_tracker, close_ms, result)
                if decision is not None and decision.intents:
                    pending = decision
            result.equity_curve.append((bar.open_ms, portfolio.equity(bar.close)))

        result.final_equity = portfolio.equity(minutes[-1].close) if minutes else initial_equity
        result.trades = portfolio.trades
        return result

    # ----------------------------------------------------------- 내부

    def _decision_close(self, as_of_ms: int) -> int:
        """``as_of_ms`` 시점에 마지막으로 마감된 결정 TF 봉의 마감 시각."""
        tf = self._decision_tf
        return tf.open_ms_of(as_of_ms - tf.ms + 1) + tf.ms - 1

    def _evaluate(
        self,
        portfolio: BacktestPortfolio,
        risk_tracker: BacktestRiskTracker,
        close_ms: int,
        result: BacktestResult,
    ) -> Decision | None:
        snapshot = self._snapshot(close_ms)
        if snapshot is None:
            return None
        result.decisions_evaluated += 1
        state = portfolio.to_state(snapshot.mark_price, close_ms)
        decision = decide(snapshot, state, self._cfg, self._runtime(risk_tracker, snapshot))
        for reason in decision.veto_reasons:
            _count(result.veto_counts, reason.value)
        if decision.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
            result.entries_signalled += 1
        return decision

    def _snapshot(self, close_ms: int) -> MarketSnapshot | None:
        series = []
        for tf in self._feed.timeframes:
            if not self._feed.has_warmup(tf, close_ms):
                return None
            series.append(self._feed.series(tf, close_ms))
        mark = series[0].last
        if mark is None:  # pragma: no cover - warm-up 검사로 도달 불가
            return None
        rate = self._funding_as_of(close_ms)
        return MarketSnapshot(
            symbol=self._feed.symbol,
            as_of_ms=close_ms,
            mode=Mode.BACKTEST,
            series=tuple(series),
            mark_price=mark.close,
            last_price=mark.close,
            funding_rate=None if rate is None else rate.rate,
            next_funding_ms=None if rate is None else rate.next_funding_ms,
            spread_bps=self._cfg.risk.max_spread_bps / Decimal(5),
            data_quality=DataQuality(ok=True),
        )

    def _runtime(
        self, risk_tracker: BacktestRiskTracker, snapshot: MarketSnapshot
    ) -> DecisionContext:
        base = self._context or DecisionContext()
        filters = self._filters
        m5 = snapshot.get(Timeframe.M5) if self._cfg.strategy_id == "a1_v1" else None
        a1_close_ms = m5.last.close_ms if m5 is not None and m5.last is not None else None
        calendar_available = base.event_calendar_available
        blackout_active = base.event_blackout_active
        if self._cfg.strategy_id == "a1_v1":
            calendar_available, blackout_active = (
                self._macro_calendar.status_at(snapshot.as_of_ms, self._cfg.risk.blackout)
                if self._macro_calendar is not None
                else (False, False)
            )
        return DecisionContext(
            system_state=base.system_state,
            risk_state=risk_tracker.to_state(),
            expected_edge_bps=base.expected_edge_bps,
            edge_sample_count=base.edge_sample_count,
            qty_step=filters.qty_step,
            price_tick=filters.price_tick,
            min_qty=filters.min_qty,
            min_notional=filters.min_notional,
            maintenance_margin_rate=filters.maintenance_margin_rate,
            confirmed_swing_price=base.confirmed_swing_price,
            killswitch_active=base.killswitch_active,
            code_dirty=base.code_dirty,
            clock_drift_ms=base.clock_drift_ms,
            reconcile_ok=base.reconcile_ok,
            bad_price=base.bad_price,
            event_blackout_active=blackout_active,
            event_calendar_available=calendar_available,
            event_proximity=base.event_proximity,
            regime_stability=base.regime_stability,
            a1_observation=self._a1_events.get(a1_close_ms) if a1_close_ms is not None else None,
            a1_precomputed_at_ms=a1_close_ms,
            research_unvalidated=(
                self._cfg.strategy_id == "a1_v1" and self._cfg.mode is Mode.BACKTEST
            ),
        )

    def _execute(
        self,
        portfolio: BacktestPortfolio,
        risk_tracker: BacktestRiskTracker,
        decision: Decision,
        bar: Candle,
        result: BacktestResult,
    ) -> bool:
        entry = next((i for i in decision.intents if i.purpose is OrderPurpose.ENTRY), None)
        if entry is not None:
            return self._open(portfolio, risk_tracker, decision, entry, bar)
        exit_intent = next((i for i in decision.intents if i.purpose is OrderPurpose.EXIT), None)
        if exit_intent is None or portfolio.position is None:
            return False
        fill = self._sim.fill_market(exit_intent, bar, qty=portfolio.position.qty)
        if fill is None:
            return False
        trade = portfolio.close(fill, CloseReason.SIGNAL)
        risk_tracker.record_exit(trade, fill.fee, self._cfg.risk)
        _count(result.close_reasons, trade.close_reason.value)
        return True

    def _open(
        self,
        portfolio: BacktestPortfolio,
        risk_tracker: BacktestRiskTracker,
        decision: Decision,
        entry: Intent,
        bar: Candle,
    ) -> bool:
        plan = decision.sizing
        if plan is None or portfolio.position is not None:
            return False
        fill = self._sim.fill_market(entry, bar)
        if fill is None:
            return False
        take_profit = next(
            (i.stop_price for i in decision.intents if i.purpose is OrderPurpose.TAKE_PROFIT),
            None,
        )
        side = PositionSide.LONG if decision.action is Action.OPEN_LONG else PositionSide.SHORT
        portfolio.open(
            fill,
            side=side,
            stop_price=plan.stop_price,
            take_profit_price=take_profit,
            planned_risk=plan.max_loss,
            leverage=plan.exchange_leverage,
            liquidation_price=plan.liquidation_price,
        )
        risk_tracker.record_entry(fill, side)
        return True

    def _settle_funding(
        self,
        portfolio: BacktestPortfolio,
        risk_tracker: BacktestRiskTracker,
        bar: Candle,
        index: int,
    ) -> int:
        """이 1분봉 안에 정산 시각이 들어오면 적용한다."""
        end = bar.open_ms + Timeframe.M1.ms - 1
        while index < len(self._funding) and self._funding[index].funding_ms <= end:
            payment = portfolio.settle_funding(self._funding[index], mark_price=bar.close)
            risk_tracker.record_funding(payment)
            index += 1
        return index

    def _funding_as_of(self, as_of_ms: int) -> FundingRate | None:
        """``as_of_ms`` 시점에 이미 정산된 가장 최근 funding (인과성)."""
        index = bisect.bisect_right(self._funding_times, as_of_ms) - 1
        if index < 0:
            return None
        latest = self._funding[index]
        if as_of_ms > latest.next_funding_ms + _FUNDING_JITTER_MS:
            return None
        return latest


def _decision_timeframe(cfg: RootConfig) -> Timeframe:
    """가장 빠른 Trigger TF 에서 판단한다. 그보다 자주 봐도 새 정보가 없다."""
    if cfg.strategy_id == "a1_v1":
        return Timeframe.M5
    return min(
        (
            cfg.features.t1_pullback.timeframe,
            cfg.features.t2_momentum.timeframe,
            cfg.features.t3_volume.timeframe,
        ),
        key=lambda tf: tf.ms,
    )


def _protective_of(decision: Decision) -> tuple[Intent, ...]:
    return tuple(i for i in decision.intents if i.purpose.is_protective)


def _count(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1
