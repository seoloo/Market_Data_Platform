"""K/v1 델타 중립 펀딩 캐리 연구 시뮬레이터 (docs/research/10-k-carry-preregistration.md).

현물 BTC 롱 + USD-M 무기한 숏(같은 수량)을 1시간 단위로 재생한다. 순수 함수이며 I/O가 없다.
돈 계산은 Decimal, 통계는 float로 한다.

한 1h봉 ``t`` 안의 순서 (사전등록 §2):

1. ``t``에 정산된 funding을 **현재 보유**에 적용한다 (체결보다 먼저 — 정산 뒤 체결).
2. 이전 판단으로 예약된 체결(진입·해제·재조정)을 ``t`` 시가에 실행한다. 현물 봉이 없으면 미룬다.
3. 선물 고가로 청산을 판정한다.
4. 종가로 자본을 평가한다.
5. ``t``에 정산이 있었으면 신호로 진입·해제를 판단하고, 보유 중이면 재조정 조건을 본다.
   판단은 다음 봉에 체결한다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any

__all__ = [
    "CarryConfig",
    "CarryHour",
    "CarryResult",
    "CarryVerdict",
    "FundingEvent",
    "judge_carry",
    "simulate_carry",
]

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HOUR_MS = 3_600_000


@dataclass(frozen=True, slots=True)
class CarryConfig:
    leverage: Decimal = Decimal(2)
    """선물 숏 레버리지. 현물 비중 = L/(L+1), 증거금 비중 = 1/(L+1)."""
    enter_rate: Decimal = Decimal("0.00005")
    exit_rate: Decimal = _ZERO
    signal_window: int = 21
    rebalance_move: Decimal = Decimal("0.20")
    spot_fee: Decimal = Decimal("0.001")
    perp_fee: Decimal = Decimal("0.00045")
    slippage: Decimal = Decimal("0.0002")
    maintenance_margin: Decimal = Decimal("0.004")
    use_filter: bool = True
    """False면 대조군: 첫 봉에 진입해 끝까지 보유."""

    @property
    def spot_share(self) -> Decimal:
        return self.leverage / (self.leverage + _ONE)


@dataclass(frozen=True, slots=True)
class CarryHour:
    open_ms: int
    perp_open: Decimal
    perp_high: Decimal
    perp_close: Decimal
    spot_open: Decimal | None
    spot_close: Decimal | None


@dataclass(frozen=True, slots=True)
class FundingEvent:
    funding_ms: int
    rate: Decimal
    mark_price: Decimal


@dataclass(slots=True)
class CarryResult:
    equity: list[tuple[int, Decimal]] = field(default_factory=list)
    """(1h봉 open_ms, 종가 평가 자본). 마지막 값은 청산 비용 반영 후."""
    initial_equity: Decimal = _ZERO
    final_equity: Decimal = _ZERO
    funding_income: Decimal = _ZERO
    costs: Decimal = _ZERO
    entries: int = 0
    exits: int = 0
    rebalances: int = 0
    hours_in_market: int = 0
    liquidations: int = 0
    deferred_fills: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "initial_equity": str(self.initial_equity),
            "final_equity": str(self.final_equity),
            "funding_income": str(self.funding_income),
            "costs": str(self.costs),
            "entries": self.entries,
            "exits": self.exits,
            "rebalances": self.rebalances,
            "hours": len(self.equity),
            "hours_in_market": self.hours_in_market,
            "liquidations": self.liquidations,
            "deferred_fills": self.deferred_fills,
        }


@dataclass(slots=True)
class _Book:
    cash: Decimal
    qty: Decimal = _ZERO
    margin: Decimal = _ZERO
    perp_entry: Decimal = _ZERO
    reference: Decimal = _ZERO
    last_spot: Decimal | None = None

    def value(self, spot: Decimal, perp: Decimal) -> Decimal:
        return self.cash + self.qty * spot + self.margin + self.qty * (self.perp_entry - perp)


def _signal(rates: Sequence[Decimal], window: int) -> Decimal | None:
    if len(rates) < window:
        return None
    return sum(rates[-window:], _ZERO) / window


def simulate_carry(
    hours: Sequence[CarryHour],
    funding: Sequence[FundingEvent],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: Decimal,
    cfg: CarryConfig,
) -> CarryResult:
    """``[from_ms, to_ms]`` 구간을 재생한다. ``funding``은 구간 전 이력을 포함해도 된다(신호용)."""
    result = CarryResult(initial_equity=initial_equity)
    book = _Book(cash=initial_equity)
    # 정산 시각은 정시에서 수 ms 어긋나 기록된다 → 그 시각이 속한 1h봉에 붙인다.
    by_time = {f.funding_ms - f.funding_ms % _HOUR_MS: f for f in funding}
    history = [
        f.rate for f in sorted(funding, key=lambda f: f.funding_ms) if f.funding_ms < from_ms
    ]
    pending: str | None = None if cfg.use_filter else "open"
    slip = cfg.slippage
    window = [h for h in hours if from_ms <= h.open_ms <= to_ms]

    def fee_spot(qty: Decimal, price: Decimal) -> Decimal:
        return qty * price * cfg.spot_fee

    def fee_perp(qty: Decimal, price: Decimal) -> Decimal:
        return qty * price * cfg.perp_fee

    def open_position(h: CarryHour, spot: Decimal) -> None:
        equity = book.cash
        spot_fill = spot * (_ONE + slip)
        perp_fill = h.perp_open * (_ONE - slip)
        qty = equity * cfg.spot_share / spot
        cost = fee_spot(qty, spot_fill) + fee_perp(qty, perp_fill) + qty * spot * slip
        book.qty = qty
        book.margin = equity - qty * spot_fill - fee_spot(qty, spot_fill) - fee_perp(qty, perp_fill)
        book.cash = _ZERO
        book.perp_entry = perp_fill
        book.reference = h.perp_open
        result.costs += cost + qty * h.perp_open * slip
        result.entries += 1

    def close_position(spot: Decimal, perp: Decimal) -> None:
        qty = book.qty
        spot_fill = spot * (_ONE - slip)
        perp_fill = perp * (_ONE + slip)
        proceeds = qty * spot_fill - fee_spot(qty, spot_fill)
        pnl = qty * (book.perp_entry - perp_fill) - fee_perp(qty, perp_fill)
        book.cash += proceeds + book.margin + pnl
        result.costs += (
            fee_spot(qty, spot_fill) + fee_perp(qty, perp_fill) + qty * (spot + perp) * slip
        )
        book.qty = book.margin = book.perp_entry = _ZERO
        result.exits += 1

    def rebalance(h: CarryHour, spot: Decimal) -> None:
        perp = h.perp_open
        book.margin += book.qty * (book.perp_entry - perp)
        book.perp_entry = perp
        equity = book.qty * spot + book.margin
        target = equity * cfg.spot_share / spot
        delta = abs(target - book.qty)
        spot_fill = spot * (_ONE + slip) if target > book.qty else spot * (_ONE - slip)
        perp_fill = perp * (_ONE - slip) if target > book.qty else perp * (_ONE + slip)
        cost = (
            fee_spot(delta, spot_fill) + fee_perp(delta, perp_fill) + delta * (spot + perp) * slip
        )
        book.qty = target
        book.margin = equity - target * spot - cost
        book.reference = perp
        result.costs += cost
        result.rebalances += 1

    for h in window:
        # 1. 정산 funding (체결 전 보유에 적용)
        event = by_time.get(h.open_ms)
        if event is not None and book.qty > 0:
            income = book.qty * event.mark_price * event.rate
            book.margin += income
            result.funding_income += income
        # 2. 예약 체결
        if pending is not None:
            if h.spot_open is None:
                result.deferred_fills += 1
            else:
                if pending == "open" and book.qty == 0:
                    open_position(h, h.spot_open)
                elif pending == "close" and book.qty > 0:
                    close_position(h.spot_open, h.perp_open)
                elif pending == "rebalance" and book.qty > 0:
                    rebalance(h, h.spot_open)
                pending = None
        if h.spot_close is not None:
            book.last_spot = h.spot_close
        spot_mark = book.last_spot if book.last_spot is not None else h.perp_close
        # 3. 청산 판정 (선물 고가)
        if book.qty > 0:
            equity_at_high = book.margin + book.qty * (book.perp_entry - h.perp_high)
            if equity_at_high <= cfg.maintenance_margin * book.qty * h.perp_high:
                result.liquidations += 1
                book.cash += book.qty * spot_mark * (_ONE - cfg.spot_fee)
                book.qty = book.margin = book.perp_entry = _ZERO
                pending = None
        # 4. 평가
        if book.qty > 0:
            result.hours_in_market += 1
        result.equity.append((h.open_ms, book.value(spot_mark, h.perp_close)))
        # 5. 판단
        if event is not None:
            history.append(event.rate)
            signal = _signal(history, cfg.signal_window)
            if cfg.use_filter and signal is not None:
                if book.qty == 0 and signal >= cfg.enter_rate:
                    pending = "open"
                elif book.qty > 0 and signal < cfg.exit_rate:
                    pending = "close"
        if pending is None and book.qty > 0 and book.reference > 0:
            move = abs(h.perp_close / book.reference - _ONE)
            if move >= cfg.rebalance_move:
                pending = "rebalance"

    if window and book.qty > 0:
        last = window[-1]
        spot_last = book.last_spot if book.last_spot is not None else last.perp_close
        close_position(spot_last, last.perp_close)
        result.equity[-1] = (last.open_ms, book.cash)
    result.final_equity = result.equity[-1][1] if result.equity else initial_equity
    return result


# ------------------------------------------------------------------ 판정


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).year


def _month(ms: int) -> tuple[int, int]:
    moment = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return moment.year, moment.month


def monthly_returns(result: CarryResult) -> list[float]:
    """월 경계(각 달 첫 1h봉 평가 자본) 사이 수익률. 첫 값은 초기 자본 기준."""
    marks: list[Decimal] = [result.initial_equity]
    last_month: tuple[int, int] | None = None
    last_value = result.initial_equity
    for ms, value in result.equity:
        month = _month(ms)
        if last_month is not None and month != last_month:
            marks.append(last_value)
        last_month = month
        last_value = value
    marks.append(result.final_equity)
    return [float(b / a - 1) for a, b in pairwise(marks) if a > 0]


def yearly_returns(result: CarryResult) -> dict[int, float]:
    out: dict[int, float] = {}
    start = result.initial_equity
    current_year: int | None = None
    last_value = result.initial_equity
    for ms, value in result.equity:
        year = _year(ms)
        if current_year is not None and year != current_year:
            out[current_year] = float(last_value / start - 1)
            start = last_value
        current_year = year
        last_value = value
    if current_year is not None:
        out[current_year] = float(result.final_equity / start - 1)
    return out


def max_drawdown(result: CarryResult) -> float:
    peak = result.initial_equity
    worst = 0.0
    for _, value in result.equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, float(1 - value / peak))
    return worst


def annualized(result: CarryResult) -> float:
    if not result.equity or result.initial_equity <= 0 or result.final_equity <= 0:
        return math.nan
    years = len(result.equity) / (24 * 365.25)
    growth = float(result.final_equity / result.initial_equity)
    return float(growth ** (1 / years)) - 1


@dataclass(frozen=True, slots=True)
class CarryVerdict:
    status: str
    """``PASS`` | ``REJECT``."""
    criteria: tuple[tuple[str, bool, dict[str, Any]], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "criteria": [
                {"name": name, "passed": passed, "detail": detail}
                for name, passed, detail in self.criteria
            ],
        }


def judge_carry(
    result: CarryResult,
    *,
    years: Sequence[int],
    min_annual: float = 0.05,
    max_mdd: float = 0.05,
    require_return: bool = True,
) -> CarryVerdict:
    """사전등록 §4. ``require_return=False``는 STRESS (안전·MDD만)."""
    monthly = monthly_returns(result)
    yearly = yearly_returns(result)
    mdd = max_drawdown(result)
    criteria: list[tuple[str, bool, dict[str, Any]]] = []
    if require_return:
        ann = annualized(result)
        criteria.append(("K1", ann >= min_annual, {"annualized": ann, "threshold": min_annual}))
        mean = statistics.fmean(monthly) if monthly else math.nan
        se = statistics.stdev(monthly) / math.sqrt(len(monthly)) if len(monthly) > 1 else math.inf
        criteria.append(
            (
                "K2",
                mean - 2 * se > 0,
                {"months": len(monthly), "mean": mean, "se": se, "lower_2se": mean - 2 * se},
            )
        )
        by_year = {str(y): yearly.get(y) for y in years}
        criteria.append(
            ("K3", all(v is not None and v > 0 for v in by_year.values()), {"yearly": by_year})
        )
    criteria.append(("K4", mdd <= max_mdd, {"mdd": mdd, "threshold": max_mdd}))
    criteria.append(("SAFETY", result.liquidations == 0, {"liquidations": result.liquidations}))
    status = "PASS" if all(passed for _, passed, _ in criteria) else "REJECT"
    return CarryVerdict(status, tuple(criteria))
