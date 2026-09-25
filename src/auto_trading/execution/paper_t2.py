"""T/v2-long Paper 실행기 — 하루 1회 (Phase 6, docs/research/16 §3·§5).

매 실행(00:00 UTC 이후):

1. 방금 마감된 일봉의 close_ms를 ``decision_ms``로 둔다. 이미 기록이 있으면 그대로 돌려준다(멱등).
2. 해소되지 않은 주문을 복구한다. 그래도 남으면 DEGRADED — 체결하지 않는다(fail-closed).
3. **전날** 기록의 목표를 지금 가격으로 체결한다 — 연구의 "종가 d 목표 → 종가 d+1 체결"과 같다.
   전날 기록이 없으면(PC 꺼짐 등) 오래된 목표를 체결하지 않고 NO_PRIOR_DECISION으로 남긴다.
4. 마감 일봉으로 새 목표를 정해 오늘 행에 저장한다(다음 실행이 체결).

가상 잔고는 저장하지 않고 ``spot_orders``(mode='paper')의 체결 기록에서 매번 계산한다(원장).
가상 체결은 :class:`FakeSpotExchange`가 현재가로 하고, 비용 0.15%(수수료 0.10% + 슬리피지 0.05%)를
수수료로 뗀다 — 연구 시뮬레이터의 현물 비용과 같다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal
from typing import Protocol

from auto_trading.config.schema import SpotTrendConfig
from auto_trading.core.enums import Timeframe
from auto_trading.core.money import floor_to_step
from auto_trading.core.types import Candle
from auto_trading.exchange.fake_spot import FakeSpotExchange
from auto_trading.exchange.spot import SpotFilters
from auto_trading.execution.spot_orders import SpotOrderManager
from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository
from auto_trading.storage.repositories.spot_orders import SpotOrderRecord, SpotOrderRepository
from auto_trading.strategy.spot_trend import (
    SpotSide,
    SpotTarget,
    plan_spot_orders,
    spot_trend_targets,
)

__all__ = ["PaperMarket", "PaperT2Runner", "ledger_balances"]

_DAY_MS = Timeframe.D1.ms
_QUOTE = "USDT"
_FILLED = frozenset({"FILLED", "EXPIRED", "PARTIALLY_FILLED"})


class PaperMarket(Protocol):
    def closed_daily(self, symbol: str, as_of_ms: int) -> list[Candle]: ...

    def last_price(self, symbol: str) -> Decimal: ...

    def filters(self, symbol: str) -> SpotFilters: ...


def ledger_balances(
    records: Iterable[SpotOrderRecord], *, initial_quote: Decimal
) -> dict[str, Decimal]:
    """체결 기록으로 가상 잔고를 계산한다. 수수료는 인용 자산에서 뗀다(FakeSpotExchange와 같다)."""
    balance = {_QUOTE: initial_quote}
    for r in records:
        if r.state not in _FILLED or r.filled_qty <= 0:
            continue
        base = r.symbol.removesuffix(_QUOTE)
        sign = 1 if r.side == "BUY" else -1
        balance[base] = balance.get(base, Decimal(0)) + sign * r.filled_qty
        balance[_QUOTE] -= sign * r.quote_qty + r.fee
    return balance


def _equity(balance: Mapping[str, Decimal], prices: Mapping[str, Decimal]) -> Decimal:
    total = balance.get(_QUOTE, Decimal(0))
    for symbol, price in prices.items():
        total += balance.get(symbol.removesuffix(_QUOTE), Decimal(0)) * price
    return total


class PaperT2Runner:
    def __init__(
        self,
        market: PaperMarket,
        orders: SpotOrderRepository,
        days: PaperDayRepository,
        *,
        clock: Callable[[], int],
        cfg: SpotTrendConfig | None = None,
        initial_quote: Decimal = Decimal(10_000),
        cost_rate: Decimal = Decimal("0.0015"),
        strategy_id: str = "t2_long",
        strategy_tag: str = "t2l",
        mode: str = "paper",
    ) -> None:
        self._market = market
        self._orders = orders
        self._days = days
        self._clock = clock
        self._cfg = cfg if cfg is not None else SpotTrendConfig()
        self._initial = initial_quote
        self._cost = cost_rate
        self._strategy_id = strategy_id
        self._tag = strategy_tag
        self._mode = mode

    def _records(self) -> list[SpotOrderRecord]:
        return [
            r
            for r in self._orders.all()
            if r.mode == self._mode and r.strategy_id == self._strategy_id
        ]

    def run_once(self, now_ms: int) -> PaperDay:
        decision_ms = now_ms - now_ms % _DAY_MS - 1
        done = self._days.get(self._strategy_id, decision_ms)
        if done is not None:
            return done
        symbols = self._cfg.symbols
        prices = {s: self._market.last_price(s) for s in symbols}
        filters = {s: self._market.filters(s) for s in symbols}
        balance = ledger_balances(self._records(), initial_quote=self._initial)
        fake = FakeSpotExchange(
            balance=dict(balance), prices=dict(prices), symbol_filters=filters, fee_rate=self._cost
        )
        manager = SpotOrderManager(
            fake,
            self._orders,
            mode=self._mode,
            strategy_id=self._strategy_id,
            strategy_tag=self._tag,
            clock=self._clock,
        )
        manager.recover()
        status, note = "OK", None
        executed: list[dict[str, str]] = []
        prev = self._days.get(self._strategy_id, decision_ms - _DAY_MS)
        if manager.blocked():
            status, note = "DEGRADED", "해소되지 않은 주문이 있어 체결하지 않음"
        elif prev is None:
            status, note = "NO_PRIOR_DECISION", "전날 결정 기록 없음 — 오래된 목표는 체결하지 않음"
        elif not any(r.decision_ms == prev.decision_ms for r in self._records()):
            executed = self._execute(prev, manager, fake, balance, prices, filters)
        balance = ledger_balances(self._records(), initial_quote=self._initial)
        daily = {s: self._market.closed_daily(s, decision_ms) for s in symbols}
        targets = spot_trend_targets(daily, as_of_ms=decision_ms, cfg=self._cfg)
        day = PaperDay(
            strategy_id=self._strategy_id,
            decision_ms=decision_ms,
            status=status,
            equity=_equity(balance, prices),
            holdings={a: str(q) for a, q in sorted(balance.items()) if q},
            prices={s: str(p) for s, p in prices.items()},
            targets={s: None if t.weight is None else str(t.weight) for s, t in targets.items()},
            executed=executed,
            note=note,
            created_ms=self._clock(),
        )
        self._days.insert(day)
        return day

    def _execute(
        self,
        prev: PaperDay,
        manager: SpotOrderManager,
        fake: FakeSpotExchange,
        balance: Mapping[str, Decimal],
        prices: Mapping[str, Decimal],
        filters: Mapping[str, SpotFilters],
    ) -> list[dict[str, str]]:
        targets = {
            s: SpotTarget(s, None if w is None else Decimal(w)) for s, w in prev.targets.items()
        }
        holdings = {s: balance.get(s.removesuffix(_QUOTE), Decimal(0)) for s in prices}
        intents = plan_spot_orders(
            targets,
            holdings=holdings,
            prices=prices,
            equity=_equity(balance, prices),
            cfg=self._cfg,
            step_size={s: f.step_size for s, f in filters.items()},
            min_notional={s: f.min_notional for s, f in filters.items()},
        )
        out = []
        for intent in intents:  # 매도가 먼저 온다
            qty = intent.quantity
            if intent.side is SpotSide.BUY:
                # 비용 때문에 현금이 모자라면 살 수 있는 만큼만 산다 (연구는 음수 현금 허용)
                price = prices[intent.symbol]
                affordable = fake.balance.get(_QUOTE, Decimal(0)) / (price * (1 + self._cost))
                qty = min(qty, floor_to_step(affordable, filters[intent.symbol].step_size))
                if qty * price < filters[intent.symbol].min_notional:
                    continue
            record = manager.submit(
                decision_ms=prev.decision_ms,
                symbol=intent.symbol,
                side=intent.side.value,
                quantity=qty,
                target_weight=intent.target_weight,
            )
            out.append(
                {
                    "symbol": record.symbol,
                    "side": record.side,
                    "qty": str(record.filled_qty),
                    "state": record.state,
                    "quote": str(record.quote_qty),
                    "fee": str(record.fee),
                }
            )
        return out
