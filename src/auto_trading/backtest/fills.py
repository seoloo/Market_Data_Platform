"""체결 시뮬레이션 (07-backtest.md §3).

**백테스트는 낙관적일수록 쓸모없다.** 애매한 상황은 전부 불리한 쪽으로 판정한다.

세 가지 규칙이 이 모듈의 존재 이유다.

1. **신호 봉 종가로 체결하지 않는다.** 봉이 마감된 뒤에야 결정할 수 있으므로,
   체결은 **다음 1분봉**부터다 (R-1). 이걸 어기면 성과가 통째로 거짓이 된다.
2. **거래량 0 인 봉에서는 체결이 일어나지 않는다.** Binance 1m 아카이브는 체결이
   없는 분을 직전가로 채운다. 그 가격은 그 시점의 시장가가 아니므로, 그대로 쓰면
   체결되지 않았을 손절이 체결된 것으로 처리된다 (07 §2 실측).
3. **손절과 익절이 같은 봉 안에 모두 들어오면 손절이 먼저다.** 1분봉만으로는
   순서를 알 수 없고, 알 수 없을 때는 불리한 쪽을 택한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import CostConfig
from auto_trading.core.enums import OrderPurpose, OrderType, Side
from auto_trading.core.money import ZERO, dec
from auto_trading.core.types import Candle, Intent

__all__ = ["Fill", "FillSimulator", "SlippageModel"]

_BPS = Decimal("10000")
ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class Fill:
    """체결 한 건."""

    intent: Intent
    at_ms: int
    price: Decimal
    qty: Decimal
    fee: Decimal
    slippage_bps: Decimal
    is_maker: bool = False

    def __post_init__(self) -> None:
        if self.price <= ZERO:
            raise ValueError(f"체결가가 0 이하: {self.price}")
        if self.qty <= ZERO:
            raise ValueError(f"체결 수량이 0 이하: {self.qty}")

    @property
    def notional(self) -> Decimal:
        return self.price * self.qty


@dataclass(frozen=True, slots=True)
class SlippageModel:
    """07 §3.2 의 슬리피지 모델.

    계수는 **보수적 추정치**이며 Phase 8 소액 실거래 실측으로 보정한다.
    보정 전후 백테스트 결과를 반드시 비교 기록한다.
    """

    base_bps: Decimal = Decimal("1.0")
    vol_coef_bps: Decimal = Decimal("4.0")
    size_coef_bps: Decimal = Decimal("50.0")
    cap_bps: Decimal = Decimal("30.0")
    stop_multiple: Decimal = Decimal("2.0")

    def estimate(
        self,
        *,
        is_stop: bool,
        atr_percentile: float | None = None,
        notional: Decimal | None = None,
        bar_quote_volume: Decimal | None = None,
    ) -> Decimal:
        """bps 단위 슬리피지. 입력이 없으면 **더 비싼 쪽**으로 가정한다."""
        percentile = 1.0 if atr_percentile is None else max(0.0, min(1.0, atr_percentile))
        bps = self.base_bps + self.vol_coef_bps * dec(str(percentile))
        if notional is not None and bar_quote_volume is not None and bar_quote_volume > ZERO:
            bps += self.size_coef_bps * (notional / bar_quote_volume)
        else:
            bps += self.size_coef_bps  # 시장 충격을 알 수 없으면 최대로 본다
        if is_stop:
            bps *= self.stop_multiple
        return min(bps, self.cap_bps * (self.stop_multiple if is_stop else ONE))


class FillSimulator:
    """1분봉으로 주문 체결을 해소한다."""

    __slots__ = ("_cost", "_slippage")

    def __init__(self, cost: CostConfig, slippage: SlippageModel | None = None) -> None:
        self._cost = cost
        self._slippage = slippage or SlippageModel()

    # ------------------------------------------------------------ 시장가

    def fill_market(
        self,
        intent: Intent,
        bar: Candle,
        *,
        qty: Decimal | None = None,
        atr_percentile: float | None = None,
    ) -> Fill | None:
        """다음 1분봉 **시가** 기준 체결 (R-1).

        거래량 0 인 봉에서는 체결하지 않는다 — 호출자가 다음 봉으로 넘어가야 한다.
        """
        if bar.volume <= ZERO:
            return None
        amount = intent.qty if qty is None else qty
        if amount <= ZERO:
            return None
        bps = self._slippage.estimate(
            is_stop=False,
            atr_percentile=atr_percentile,
            notional=bar.open * amount,
            bar_quote_volume=bar.quote_volume,
        )
        price = _apply_slippage(bar.open, intent.side, bps)
        return Fill(
            intent=intent,
            at_ms=bar.open_ms,
            price=price,
            qty=amount,
            fee=price * amount * self._cost.taker_fee_rate,
            slippage_bps=bps,
        )

    # ------------------------------------------------------- 보호 주문

    def triggered(self, intent: Intent, bar: Candle) -> bool:
        """이 1분봉에서 보호 주문이 발동하는가.

        거래량 0 인 봉은 **발동시키지 않는다.** 유동성이 없던 구간이라 그 가격에
        체결될 수 없기 때문이다 (07 §2).
        """
        stop = intent.stop_price
        if stop is None or bar.volume <= ZERO:
            return False
        if intent.order_type is OrderType.STOP_MARKET:
            # 손절은 불리한 방향으로 뚫릴 때 발동한다.
            return bar.low <= stop if intent.side is Side.SELL else bar.high >= stop
        if intent.order_type is OrderType.TAKE_PROFIT_MARKET:
            return bar.high >= stop if intent.side is Side.SELL else bar.low <= stop
        return False

    def fill_protective(
        self,
        intent: Intent,
        bar: Candle,
        qty: Decimal,
        *,
        atr_percentile: float | None = None,
    ) -> Fill | None:
        """발동가 기준 체결. 갭이 나면 **봉 시가**로 체결한다.

        지정 발동가보다 이미 지나쳐서 열린 봉(갭)에서 발동가에 체결됐다고 하면
        손실을 체계적으로 과소평가한다.
        """
        stop = intent.stop_price
        if stop is None or qty <= ZERO or bar.volume <= ZERO:
            return None
        is_stop = intent.order_type is OrderType.STOP_MARKET
        reference = _gap_adjusted(stop, bar, intent.side, is_stop=is_stop)
        bps = self._slippage.estimate(
            is_stop=is_stop,
            atr_percentile=atr_percentile,
            notional=reference * qty,
            bar_quote_volume=bar.quote_volume,
        )
        price = _apply_slippage(reference, intent.side, bps)
        return Fill(
            intent=intent,
            at_ms=bar.open_ms,
            price=price,
            qty=qty,
            fee=price * qty * self._cost.taker_fee_rate,
            slippage_bps=bps,
        )

    def resolve_protective(
        self,
        protective: tuple[Intent, ...],
        bar: Candle,
    ) -> Intent | None:
        """한 봉 안에서 여러 보호 주문이 발동하면 **손절을 먼저** 택한다.

        1분봉만으로는 도달 순서를 알 수 없다. 알 수 없을 때 유리한 쪽(익절)을
        고르면 백테스트가 낙관적으로 편향된다.
        """
        fired = [intent for intent in protective if self.triggered(intent, bar)]
        if not fired:
            return None
        for intent in fired:
            if intent.purpose is OrderPurpose.STOP:
                return intent
        return fired[0]


def _apply_slippage(price: Decimal, side: Side, bps: Decimal) -> Decimal:
    """항상 **불리한 방향**으로 민다. 매수는 비싸게, 매도는 싸게."""
    drift = price * bps / _BPS
    return price + drift if side is Side.BUY else max(price - drift, Decimal("0.00000001"))


def _gap_adjusted(stop: Decimal, bar: Candle, side: Side, *, is_stop: bool) -> Decimal:
    """봉이 발동가를 건너뛰고 열렸으면 시가를 기준가로 삼는다."""
    if is_stop:
        # SELL 손절: 시가가 이미 손절가 아래면 그 시가에 체결된다.
        if side is Side.SELL:
            return min(stop, bar.open)
        return max(stop, bar.open)
    # 익절은 반대. 갭이 유리하게 나더라도 발동가보다 좋게 주지 않는다.
    return stop
