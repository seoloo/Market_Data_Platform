"""M/v1 다중 종목 델타 중립 펀딩 캐리 시뮬레이터 (docs/research/13 사전등록).

종목마다 현물 롱 + USD-M 선물 숏(같은 수량, 선물 1x)을 두고, 월별 유니버스 안에서
직전 7일 funding이 높은 종목을 최대 ``slots``개 보유한다. 순수 함수이며 I/O가 없다.
K/v1(`carry.py`)은 전향 관찰(F)을 위해 동결되어 있으므로 바꾸지 않고,
결과 자료형과 판정 함수만 재사용한다.

한 1h봉 ``t`` 안의 순서:

1. ``t``에 정산된 funding을 보유 포지션에 적용한다 (체결 전).
2. 예약 체결을 ``t`` 시가에 실행한다: 청산 → 재조정 → 진입 순. 현물·선물 봉이 없으면 미룬다.
3. 선물 고가로 청산을 판정한다.
4. 가격이 24시간 연속 없으면(상장폐지 등) 마지막 가격에 벌점 슬리피지로 강제 정리한다.
5. 종가로 자본을 평가한다.
6. 8시간 경계(00·08·16 UTC)면 해제·진입을 판단하고, 매시간 재조정 조건을 본다 → 다음 봉에 체결.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from auto_trading.backtest.carry import CarryResult

__all__ = ["MultiBar", "MultiCarryConfig", "MultiCarryResult", "simulate_multi_carry"]

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HOUR_MS = 3_600_000
_DECISION_MS = 8 * _HOUR_MS
_WEEK_MS = 7 * 24 * _HOUR_MS


@dataclass(frozen=True, slots=True)
class MultiCarryConfig:
    slots: int = 4
    enter_rate: Decimal = Decimal("0.0001")
    """7일 funding 합 / 21 (8시간 환산) 진입 문턱."""
    exit_rate: Decimal = Decimal("0.00005")
    min_history_ms: int = 6 * 24 * _HOUR_MS
    """신호에 필요한 funding 이력 (7일 창의 첫 정산이 이보다 앞서야 한다)."""
    rebalance_move: Decimal = Decimal("0.30")
    spot_fee: Decimal = Decimal("0.001")
    perp_fee: Decimal = Decimal("0.00045")
    slippage: Decimal = Decimal("0.0005")
    forced_slippage: Decimal = Decimal("0.01")
    maintenance_margin: Decimal = Decimal("0.01")
    stale_hours: int = 24
    rank_by_volume: bool = False
    """True면 대조군: funding 대신 유니버스 거래대금 순위로 고른다 (문턱은 같다)."""


@dataclass(frozen=True, slots=True)
class MultiBar:
    perp_open: Decimal
    perp_high: Decimal
    perp_close: Decimal
    spot_open: Decimal | None
    spot_close: Decimal | None


@dataclass(slots=True)
class MultiCarryResult(CarryResult):
    forced_closes: int = 0
    symbols_held: set[str] = field(default_factory=set)
    funding_by_symbol: dict[str, Decimal] = field(default_factory=dict)


@dataclass(slots=True)
class _Position:
    qty: Decimal
    margin: Decimal
    entry: Decimal
    reference: Decimal
    last_spot: Decimal
    last_perp: Decimal
    stale: int = 0


class _Funding:
    """종목별 정산 기록. 창 합을 prefix sum으로 구한다."""

    __slots__ = ("_prefix", "_times")

    def __init__(self, events: Sequence[tuple[int, Decimal]]) -> None:
        ordered = sorted(events)
        self._times = [ms for ms, _ in ordered]
        self._prefix = [_ZERO]
        for _, rate in ordered:
            self._prefix.append(self._prefix[-1] + rate)

    def signal(self, at_ms: int, min_history_ms: int) -> Decimal | None:
        """(at − 7일, at]에 정산된 rate 합 / 21. 이력이 부족하면 None."""
        lo = bisect.bisect_right(self._times, at_ms - _WEEK_MS)
        hi = bisect.bisect_right(self._times, at_ms)
        if hi == lo or self._times[lo] > at_ms - min_history_ms:
            return None
        return (self._prefix[hi] - self._prefix[lo]) / 21

    def settled_in(self, start_ms: int, end_ms: int) -> list[Decimal]:
        lo = bisect.bisect_left(self._times, start_ms)
        hi = bisect.bisect_left(self._times, end_ms)
        return [self._prefix[k + 1] - self._prefix[k] for k in range(lo, hi)]


def _month_start(ms: int) -> int:
    moment = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return int(datetime(moment.year, moment.month, 1, tzinfo=UTC).timestamp() * 1000)


def simulate_multi_carry(
    bars: Mapping[str, Mapping[int, MultiBar]],
    funding: Mapping[str, Sequence[tuple[int, Decimal]]],
    universe: Mapping[int, Sequence[str]],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: Decimal,
    cfg: MultiCarryConfig,
) -> MultiCarryResult:
    """``[from_ms, to_ms]``를 1시간 단위로 재생한다.

    ``universe``는 월 시작 ms → 거래대금 순 종목 목록이다.
    """
    result = MultiCarryResult(initial_equity=initial_equity)
    books = {symbol: _Funding(events) for symbol, events in funding.items()}
    positions: dict[str, _Position] = {}
    pending: dict[str, str] = {}
    cash = initial_equity
    slip = cfg.slippage

    def equity() -> Decimal:
        return cash + sum(
            (p.qty * p.last_spot + p.margin + p.qty * (p.entry - p.last_perp))
            for p in positions.values()
        )

    def close(symbol: str, spot: Decimal, perp: Decimal, slippage: Decimal) -> None:
        nonlocal cash
        p = positions.pop(symbol)
        spot_fill = spot * (_ONE - slippage)
        perp_fill = perp * (_ONE + slippage)
        spot_fee = p.qty * spot_fill * cfg.spot_fee
        perp_fee = p.qty * perp_fill * cfg.perp_fee
        cash += p.qty * spot_fill - spot_fee + p.margin + p.qty * (p.entry - perp_fill) - perp_fee
        result.costs += spot_fee + perp_fee + p.qty * (spot + perp) * slippage
        result.exits += 1

    def open_(symbol: str, bar: MultiBar, spot: Decimal) -> None:
        nonlocal cash
        slot = min(equity() / cfg.slots, cash)
        if slot <= 0:
            return
        spot_fill = spot * (_ONE + slip)
        perp_fill = bar.perp_open * (_ONE - slip)
        qty = slot / 2 / spot
        spot_fee = qty * spot_fill * cfg.spot_fee
        perp_fee = qty * perp_fill * cfg.perp_fee
        positions[symbol] = _Position(
            qty=qty,
            margin=slot - qty * spot_fill - spot_fee - perp_fee,
            entry=perp_fill,
            reference=bar.perp_open,
            last_spot=spot,
            last_perp=bar.perp_open,
        )
        cash -= slot
        result.costs += spot_fee + perp_fee + qty * (spot + bar.perp_open) * slip
        result.entries += 1
        result.symbols_held.add(symbol)

    def rebalance(symbol: str, bar: MultiBar, spot: Decimal) -> None:
        p = positions[symbol]
        perp = bar.perp_open
        p.margin += p.qty * (p.entry - perp)
        p.entry = perp
        value = p.qty * spot + p.margin
        target = value / 2 / spot
        delta = abs(target - p.qty)
        cost = delta * spot * (cfg.spot_fee + slip) + delta * perp * (cfg.perp_fee + slip)
        p.qty = target
        p.margin = value - target * spot - cost
        p.reference = perp
        result.costs += cost
        result.rebalances += 1

    hour = from_ms - from_ms % _HOUR_MS
    while hour <= to_ms:
        # 1. funding
        for symbol, p in positions.items():
            book = books.get(symbol)
            if book is None:
                continue
            bar = bars.get(symbol, {}).get(hour)
            price = bar.perp_open if bar is not None else p.last_perp
            for rate in book.settled_in(hour, hour + _HOUR_MS):
                income = p.qty * price * rate
                p.margin += income
                result.funding_income += income
                result.funding_by_symbol[symbol] = (
                    result.funding_by_symbol.get(symbol, _ZERO) + income
                )
        # 2. 예약 체결: 청산 → 재조정 → 진입
        for action in ("close", "rebalance", "open"):
            for symbol in sorted(s for s, a in pending.items() if a == action):
                bar = bars.get(symbol, {}).get(hour)
                if bar is None or bar.spot_open is None:
                    result.deferred_fills += 1
                    continue
                del pending[symbol]
                if action == "close" and symbol in positions:
                    close(symbol, bar.spot_open, bar.perp_open, slip)
                elif action == "rebalance" and symbol in positions:
                    rebalance(symbol, bar, bar.spot_open)
                elif action == "open" and symbol not in positions and len(positions) < cfg.slots:
                    open_(symbol, bar, bar.spot_open)
        # 3·4. 청산 판정, 가격 갱신, 장기 결측 강제 정리
        for symbol in list(positions):
            p = positions[symbol]
            bar = bars.get(symbol, {}).get(hour)
            if bar is None or bar.spot_close is None:
                p.stale += 1
                if p.stale >= cfg.stale_hours:
                    close(symbol, p.last_spot, p.last_perp, cfg.forced_slippage)
                    pending.pop(symbol, None)
                    result.forced_closes += 1
                continue
            p.stale = 0
            if p.margin + p.qty * (p.entry - bar.perp_high) <= (
                cfg.maintenance_margin * p.qty * bar.perp_high
            ):
                result.liquidations += 1
                cash += p.qty * bar.spot_close * (_ONE - cfg.spot_fee)
                del positions[symbol]
                pending.pop(symbol, None)
                continue
            p.last_spot = bar.spot_close
            p.last_perp = bar.perp_close
        # 5. 평가
        if positions:
            result.hours_in_market += 1
        result.equity.append((hour, equity()))
        # 6. 판단
        if hour % _DECISION_MS == 0:
            members = list(universe.get(_month_start(hour), ()))
            signals = {
                s: sig
                for s in members
                if (book := books.get(s)) is not None
                and (sig := book.signal(hour, cfg.min_history_ms)) is not None
            }
            for symbol in positions:
                sig = signals.get(symbol)
                if symbol not in members or sig is None or sig < cfg.exit_rate:
                    pending[symbol] = "close"
            staying = sum(1 for s in positions if pending.get(s) != "close")
            opening = sum(1 for a in pending.values() if a == "open")
            free = cfg.slots - staying - opening
            eligible = [
                s
                for s in members
                if s not in positions
                and s not in pending
                and signals.get(s, _ZERO) >= cfg.enter_rate
            ]
            if not cfg.rank_by_volume:
                eligible.sort(key=lambda s: signals[s], reverse=True)
            for symbol in eligible[: max(free, 0)]:
                pending[symbol] = "open"
        for symbol, p in positions.items():
            moved = p.reference > 0 and abs(p.last_perp / p.reference - _ONE) >= cfg.rebalance_move
            if symbol not in pending and moved:
                pending[symbol] = "rebalance"
        hour += _HOUR_MS

    for symbol in list(positions):
        p = positions[symbol]
        close(symbol, p.last_spot, p.last_perp, slip)
    if result.equity:
        result.equity[-1] = (result.equity[-1][0], cash)
    result.final_equity = result.equity[-1][1] if result.equity else initial_equity
    return result
