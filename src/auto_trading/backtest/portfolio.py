"""백테스트 계좌·포지션 회계.

실거래에서는 거래소가 진실의 원천이다 (AGENTS.md §3.3). 백테스트에는 거래소가
없으므로 이 모듈이 그 역할을 대신한다 — 그래서 **거래소가 실제로 하는 일만** 한다.
전략에 유리한 임의 처리를 넣으면 그 시점부터 백테스트는 거래소를 흉내 내는 것이
아니라 희망을 계산하는 것이 된다.

들어가는 항목: 체결가·수수료·슬리피지·funding 정산·강제청산.
빠지는 항목은 없다 — 비용을 빼먹으면 전략이 아니라 거래소 후원이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from auto_trading.backtest.fills import Fill
from auto_trading.config.schema import RiskConfig
from auto_trading.core.enums import CloseReason, OrderPurpose, PositionSide, PositionState
from auto_trading.core.errors import InvariantViolationError
from auto_trading.core.money import ZERO, safe_div
from auto_trading.core.types import Candle, FundingRate, PortfolioState, PositionSnapshot
from auto_trading.risk.gates import RiskState

__all__ = ["BacktestPortfolio", "BacktestRiskTracker", "BacktestTrade", "OpenPosition"]

ONE = Decimal(1)
_DAY_MS = 86_400_000
_MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    """청산까지 끝난 거래 한 건. 성과 지표는 전부 이 기록에서 나온다."""

    symbol: str
    side: PositionSide
    entry_ms: int
    exit_ms: int
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    close_reason: CloseReason
    gross_pnl: Decimal
    fees: Decimal
    funding_paid: Decimal
    planned_risk: Decimal
    leverage: int
    mae: Decimal
    """최대 역행폭 (가격 단위, 양수). 손절폭이 적정한지 판단하는 근거."""
    mfe: Decimal
    """최대 순행폭 (가격 단위, 양수). 익절폭이 적정한지 판단하는 근거."""

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fees - self.funding_paid

    @property
    def r_multiple(self) -> Decimal:
        """계획한 리스크 대비 실현 손익. 사이징과 무관하게 전략을 평가하는 척도."""
        return safe_div(self.net_pnl, self.planned_risk)

    @property
    def holding_ms(self) -> int:
        return self.exit_ms - self.entry_ms


@dataclass(slots=True)
class OpenPosition:
    """보유 중인 포지션. 보유 동안의 역행·순행을 함께 추적한다."""

    side: PositionSide
    qty: Decimal
    entry_price: Decimal
    entry_ms: int
    stop_price: Decimal
    take_profit_price: Decimal | None
    planned_risk: Decimal
    leverage: int
    liquidation_price: Decimal
    fees: Decimal = ZERO
    funding_paid: Decimal = ZERO
    mae: Decimal = ZERO
    mfe: Decimal = ZERO

    @property
    def notional(self) -> Decimal:
        return self.qty * self.entry_price

    def unrealized(self, price: Decimal) -> Decimal:
        return (price - self.entry_price) * self.qty * self.side.sign

    def observe(self, bar: Candle) -> None:
        """봉 하나를 지나며 역행·순행 최대치를 갱신한다.

        거래량 0 인 봉은 직전가로 채워진 것이라 시장가가 아니므로 무시한다
        (backtest/fills.py 참조).
        """
        if bar.volume <= ZERO:
            return
        if self.side is PositionSide.LONG:
            self.mae = max(self.mae, self.entry_price - bar.low)
            self.mfe = max(self.mfe, bar.high - self.entry_price)
        else:
            self.mae = max(self.mae, bar.high - self.entry_price)
            self.mfe = max(self.mfe, self.entry_price - bar.low)

    def is_liquidated(self, bar: Candle) -> bool:
        if bar.volume <= ZERO:
            return False
        if self.side is PositionSide.LONG:
            return bar.low <= self.liquidation_price
        return bar.high >= self.liquidation_price


@dataclass(slots=True)
class BacktestRiskTracker:
    """재생 경로에 따라 누적되는 :class:`RiskState`.

    리스크 게이트 자체는 순수하므로 상태를 갱신하지 않는다. 실거래에서는 DB가 이 값을
    영속화하고, 백테스트에서는 이 추적기가 같은 역할을 한다.
    """

    day_index: int
    day_start_equity: Decimal
    day_realized_pnl: Decimal = ZERO
    consecutive_losses: int = 0
    cooldown_until_ms: int | None = None
    day_trade_count: int = 0
    last_exit_ms: int | None = None
    last_entry_side: PositionSide | None = None
    same_direction_entries: int = 0

    @classmethod
    def start(
        cls,
        as_of_ms: int,
        equity: Decimal,
        initial: RiskState | None = None,
    ) -> BacktestRiskTracker:
        state = initial or RiskState()
        return cls(
            day_index=as_of_ms // _DAY_MS,
            day_start_equity=(state.day_start_equity if state.day_start_equity > ZERO else equity),
            day_realized_pnl=state.day_realized_pnl,
            consecutive_losses=state.consecutive_losses,
            cooldown_until_ms=state.cooldown_until_ms,
            day_trade_count=state.day_trade_count,
            last_exit_ms=state.last_exit_ms,
            last_entry_side=state.last_entry_side,
            same_direction_entries=state.same_direction_entries,
        )

    def advance(self, as_of_ms: int, equity: Decimal) -> None:
        """UTC 일자 변경과 연속 손실 쿨다운 만료를 반영한다."""
        current_day = as_of_ms // _DAY_MS
        if current_day != self.day_index:
            self.day_index = current_day
            self.day_start_equity = equity
            self.day_realized_pnl = ZERO
            self.day_trade_count = 0
        if self.cooldown_until_ms is not None and as_of_ms >= self.cooldown_until_ms:
            self.cooldown_until_ms = None
            self.consecutive_losses = 0

    def record_entry(self, fill: Fill, side: PositionSide) -> None:
        """진입 횟수와 즉시 지불한 진입 수수료를 기록한다."""
        self.day_trade_count += 1
        self.day_realized_pnl -= fill.fee
        if self.last_entry_side is side:
            self.same_direction_entries += 1
        else:
            self.last_entry_side = side
            self.same_direction_entries = 1

    def record_funding(self, payment: Decimal) -> None:
        """양수 payment는 지급액이므로 당일 손익에는 음수로 반영한다."""
        self.day_realized_pnl -= payment

    def record_exit(self, trade: BacktestTrade, exit_fee: Decimal, cfg: RiskConfig) -> None:
        """청산 손익과 재진입·연속 손실 상태를 갱신한다."""
        self.day_realized_pnl += trade.gross_pnl - exit_fee
        self.last_exit_ms = trade.exit_ms
        if trade.net_pnl < ZERO:
            self.consecutive_losses += 1
            if self.consecutive_losses >= cfg.consecutive_loss_limit:
                self.cooldown_until_ms = trade.exit_ms + cfg.cooldown_after_losses_min * _MINUTE_MS
        else:
            self.consecutive_losses = 0
            self.cooldown_until_ms = None

    def to_state(self) -> RiskState:
        return RiskState(
            day_start_equity=self.day_start_equity,
            day_realized_pnl=self.day_realized_pnl,
            consecutive_losses=self.consecutive_losses,
            cooldown_until_ms=self.cooldown_until_ms,
            day_trade_count=self.day_trade_count,
            last_exit_ms=self.last_exit_ms,
            last_entry_side=self.last_entry_side,
            same_direction_entries=self.same_direction_entries,
        )


@dataclass(slots=True)
class BacktestPortfolio:
    """현금·포지션·거래 기록을 들고 있는 백테스트 계좌."""

    symbol: str
    initial_equity: Decimal
    wallet_balance: Decimal = field(init=False)
    equity_high_water: Decimal = field(init=False)
    position: OpenPosition | None = None
    trades: list[BacktestTrade] = field(default_factory=list)
    funding_settled_ms: int = 0
    """마지막으로 정산한 funding 시각. 같은 정산을 두 번 적용하지 않기 위한 방어."""

    def __post_init__(self) -> None:
        if self.initial_equity <= ZERO:
            raise ValueError(f"초기 자본이 0 이하: {self.initial_equity}")
        self.wallet_balance = self.initial_equity
        self.equity_high_water = self.initial_equity

    # ---------------------------------------------------------------- 조회

    @property
    def is_flat(self) -> bool:
        return self.position is None

    def equity(self, mark_price: Decimal) -> Decimal:
        if self.position is None:
            return self.wallet_balance
        return self.wallet_balance + self.position.unrealized(mark_price)

    def to_state(self, mark_price: Decimal, as_of_ms: int) -> PortfolioState:
        """``decide()`` 에 넘길 계좌 상태.

        실거래에서 거래소가 주는 것과 **같은 타입**이어야 한다. 백테스트 전용
        타입을 만들면 그 순간 두 경로가 갈라진다.
        """
        equity = self.equity(mark_price)
        snapshot: PositionSnapshot | None = None
        unrealized = ZERO
        available = self.wallet_balance
        if self.position is not None:
            unrealized = self.position.unrealized(mark_price)
            available = self.wallet_balance - self.position.notional / Decimal(
                self.position.leverage
            )
            snapshot = PositionSnapshot(
                symbol=self.symbol,
                side=self.position.side,
                state=PositionState.OPEN,
                qty=self.position.qty,
                entry_price=self.position.entry_price,
                opened_ms=self.position.entry_ms,
                stop_price=self.position.stop_price,
                take_profit_price=self.position.take_profit_price,
                leverage=Decimal(self.position.leverage),
                liquidation_price=self.position.liquidation_price,
                unrealized_pnl=unrealized,
                planned_risk=self.position.planned_risk,
            )
        return PortfolioState(
            equity=equity,
            wallet_balance=self.wallet_balance,
            available_balance=max(ZERO, available),
            equity_high_water=self.equity_high_water,
            unrealized_pnl=unrealized,
            position=snapshot,
        )

    # ---------------------------------------------------------------- 갱신

    def open(
        self,
        fill: Fill,
        *,
        side: PositionSide,
        stop_price: Decimal,
        take_profit_price: Decimal | None,
        planned_risk: Decimal,
        leverage: int,
        liquidation_price: Decimal,
    ) -> None:
        if self.position is not None:
            raise InvariantViolationError("이미 포지션이 있는데 진입하려 한다 (One-way 위반)")
        self.wallet_balance -= fill.fee
        self.position = OpenPosition(
            side=side,
            qty=fill.qty,
            entry_price=fill.price,
            entry_ms=fill.at_ms,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            planned_risk=planned_risk,
            leverage=leverage,
            liquidation_price=liquidation_price,
            fees=fill.fee,
        )

    def close(self, fill: Fill, reason: CloseReason) -> BacktestTrade:
        position = self.position
        if position is None:
            raise InvariantViolationError("포지션이 없는데 청산하려 한다")
        gross = (fill.price - position.entry_price) * fill.qty * position.side.sign
        fees = position.fees + fill.fee
        self.wallet_balance += gross - fill.fee
        trade = BacktestTrade(
            symbol=self.symbol,
            side=position.side,
            entry_ms=position.entry_ms,
            exit_ms=fill.at_ms,
            entry_price=position.entry_price,
            exit_price=fill.price,
            qty=fill.qty,
            close_reason=reason,
            gross_pnl=gross,
            fees=fees,
            funding_paid=position.funding_paid,
            planned_risk=position.planned_risk,
            leverage=position.leverage,
            mae=position.mae,
            mfe=position.mfe,
        )
        self.trades.append(trade)
        self.position = None
        self.equity_high_water = max(self.equity_high_water, self.wallet_balance)
        return trade

    def liquidate(self, bar: Candle) -> BacktestTrade:
        """강제청산. 격리 증거금 전액을 잃은 것으로 처리한다.

        07 §3.5: 백테스트에서 청산이 **1회라도** 나오면 그 파라미터 세트는 실격이다.
        여기서 관대하게 처리하면 그 신호를 놓친다.
        """
        position = self.position
        if position is None:
            raise InvariantViolationError("포지션이 없는데 청산당할 수 없다")
        margin = position.notional / Decimal(position.leverage)
        self.wallet_balance -= margin
        trade = BacktestTrade(
            symbol=self.symbol,
            side=position.side,
            entry_ms=position.entry_ms,
            exit_ms=bar.open_ms,
            entry_price=position.entry_price,
            exit_price=position.liquidation_price,
            qty=position.qty,
            close_reason=CloseReason.LIQUIDATION,
            gross_pnl=-margin,
            fees=position.fees,
            funding_paid=position.funding_paid,
            planned_risk=position.planned_risk,
            leverage=position.leverage,
            mae=position.mae,
            mfe=position.mfe,
        )
        self.trades.append(trade)
        self.position = None
        return trade

    def settle_funding(self, rate: FundingRate, *, mark_price: Decimal | None = None) -> Decimal:
        """정산 시각에 포지션을 들고 있으면 실제 현금이 오간다.

        같은 정산을 두 번 적용하지 않도록 마지막 정산 시각을 기억한다. 재생 중
        같은 시각을 여러 번 지나갈 수 있기 때문이다.
        """
        if self.position is None or rate.funding_ms <= self.funding_settled_ms:
            return ZERO
        settlement_price = rate.mark_price or mark_price or self.position.entry_price
        payment = (
            self.position.qty * settlement_price * rate.rate * Decimal(self.position.side.sign)
        )
        self.position.funding_paid += payment
        self.wallet_balance -= payment
        self.funding_settled_ms = rate.funding_ms
        return payment

    def mark(self, bar: Candle) -> None:
        """봉 하나를 지나며 자산 최고점과 역행·순행을 갱신한다."""
        if self.position is not None:
            self.position.observe(bar)
        self.equity_high_water = max(self.equity_high_water, self.equity(bar.close))

    def protective_qty(self) -> Decimal:
        return ZERO if self.position is None else self.position.qty

    def purpose_to_reason(self, purpose: OrderPurpose) -> CloseReason:
        if purpose is OrderPurpose.STOP:
            return CloseReason.STOP
        if purpose is OrderPurpose.TAKE_PROFIT:
            return CloseReason.TAKE_PROFIT
        return CloseReason.SIGNAL
