"""Conditional, frozen execution comparison; no exchange or operating integration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.core.money import floor_to_step

D = Decimal
ZERO = D(0)
MINUTE, DAY = 60_000, 86_400_000
INITIAL, CAP = D(10000), D("0.003")
SPOT_STEP, FUT_STEP, MIN_NOTIONAL = D("0.000001"), D("0.001"), D(5)
PARTICIPATION = D("0.001")


@dataclass(frozen=True)
class Bar:
    o: Decimal
    h: Decimal
    low: Decimal
    c: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Funding:
    at_ms: int
    rate: Decimal
    mark: Decimal
    exact_mark: bool


@dataclass(frozen=True)
class Frame:
    at_ms: int
    spot: Bar
    perp: Bar
    previous_spot: Bar
    previous_perp: Bar
    long_goal: Decimal
    goal_as_of_ms: int
    funding: tuple[Funding, ...] = ()


@dataclass(frozen=True)
class Event:
    symbol: str
    start_ms: int
    end_ms: int
    short_qty: Decimal
    short_reference: Decimal
    stop_price: Decimal
    long_qty: Decimal
    spot_reference: Decimal
    already_flat: bool = False


@dataclass(frozen=True)
class Scenario:
    name: str = "BASE"
    cost_multiple: int = 1
    participation: Decimal = PARTICIPATION
    ack_min: int = 1
    stopped_ack_extra_min: int = 0


SCENARIOS = (
    Scenario(),
    Scenario("COST2", cost_multiple=2),
    Scenario("THIN", participation=D("0.00025")),
    Scenario("ACK5", ack_min=5),
    Scenario("STOP_LATE", stopped_ack_extra_min=240),
)


def _valid_bar(bar: Bar) -> None:
    values = (bar.o, bar.h, bar.low, bar.c, bar.volume)
    if (
        not all(v.is_finite() for v in values)
        or not ZERO < bar.low <= min(bar.o, bar.c) <= max(bar.o, bar.c) <= bar.h
        or bar.volume < ZERO
    ):
        raise DataIntegrityError("invalid execution OHLCV")


def simulate(
    event: Event, frames: Sequence[Frame], *, wait_min: int, scenario: Scenario
) -> dict[str, Any]:
    """Both waits share close path; zero never bypasses a confirmed flat position."""
    if not all(
        v.is_finite()
        for v in (
            event.short_qty,
            event.short_reference,
            event.stop_price,
            event.long_qty,
            event.spot_reference,
        )
    ):
        raise DataIntegrityError("nonfinite event quantity/reference")
    if (
        wait_min not in (0, 60)
        or scenario not in SCENARIOS
        or not frames
        or frames[0].at_ms != event.start_ms
        or frames[-1].at_ms + MINUTE != event.end_ms
        or event.end_ms <= event.start_ms
        or event.long_qty < ZERO
        or event.short_qty < ZERO
        or event.spot_reference <= ZERO
        or event.short_reference <= ZERO
        or (event.already_flat and event.short_qty != ZERO)
        or (event.short_qty > ZERO and event.stop_price <= ZERO)
    ):
        raise DataIntegrityError("unregistered event/wait/scenario")
    cash = peak = INITIAL
    short_qty, spot_qty, bought = event.short_qty, ZERO, ZERO
    short_market = long_buy_raw = long_sell_raw = ZERO
    sf = ss = ff = fs = funding = turnover = ZERO
    spot_fee_rate, spot_slip_rate = (
        D("0.001") * scenario.cost_multiple,
        D("0.0005") * (scenario.cost_multiple),
    )
    fut_fee_rate, fut_slip_rate = (
        D("0.0005") * scenario.cost_multiple,
        D("0.00045") * (scenario.cost_multiple),
    )
    stop_slip_rate = D("0.002") * scenario.cost_multiple
    flat_ms: int | None = None
    ack_ms: int | None = None
    if event.already_flat:
        flat_ms = event.start_ms
        ack_ms = event.start_ms + (scenario.ack_min + scenario.stopped_ack_extra_min) * MINUTE
    first_buy_ms: int | None = None
    full_buy_ms: int | None = None
    off_ms: int | None = None
    sell_floor = ZERO
    mdd = exposure_sum = max_exposure = ZERO
    worst_equity = INITIAL
    counts: Counter[str] = Counter()
    fills: list[dict[str, Any]] = []
    checkpoints: dict[str, Decimal] = {}
    previous_ms = event.start_ms - MINUTE
    fee_mark_proxy_count = 0
    long_fill_notional = ZERO

    for frame in frames:
        at = frame.at_ms
        if at != previous_ms + MINUTE:
            raise DataIntegrityError("execution gap/order")
        previous_ms = at
        if frame.goal_as_of_ms > at:
            raise CausalityError("future long target")
        if not frame.long_goal.is_finite() or not ZERO <= frame.long_goal <= D("0.5"):
            raise DataIntegrityError("invalid original long target")
        for bar in (frame.spot, frame.perp, frame.previous_spot, frame.previous_perp):
            _valid_bar(bar)
        if at == event.start_ms and (
            frame.previous_spot.c != event.spot_reference
            or frame.previous_perp.c != event.short_reference
        ):
            raise DataIntegrityError("decision reference mismatch")
        carried_short_qty = short_qty
        for payment in frame.funding:
            if (
                not at <= payment.at_ms < at + MINUTE
                or not payment.rate.is_finite()
                or not payment.mark.is_finite()
                or payment.mark <= ZERO
            ):
                raise DataIntegrityError("funding boundary/schema")
            if payment.at_ms == at:
                amount = short_qty * payment.rate * payment.mark
                cash += amount
                funding += amount
                if short_qty > ZERO and not payment.exact_mark:
                    fee_mark_proxy_count += 1

        # Existing emergency-stop exception, conservative before ordinary fills.
        emergency = short_qty > ZERO and frame.perp.h >= event.stop_price
        if short_qty > ZERO and (emergency or at < event.start_ms + 240 * MINUTE):
            raw = max(event.stop_price, frame.perp.o) if emergency else frame.perp.o
            price = raw * (1 + fut_slip_rate + (stop_slip_rate if emergency else ZERO))
            qty = (
                short_qty
                if emergency
                else min(
                    short_qty,
                    floor_to_step(frame.previous_perp.volume * scenario.participation, FUT_STEP),
                )
            )
            if not emergency and price > event.short_reference * (1 + CAP):
                counts["short_limit_miss"] += 1
            elif qty <= ZERO:
                counts["short_zero_liquidity"] += 1
            else:
                fee, slip = qty * price * fut_fee_rate, qty * (price - raw)
                cash += qty * (event.short_reference - price) - fee
                short_market += qty * (event.short_reference - raw)
                ff, fs, turnover = ff + fee, fs + slip, turnover + qty * price
                counts["short_partial"] += int(qty < short_qty)
                short_qty -= qty
                fills.append(
                    {
                        "at_ms": at,
                        "leg": "STOP" if emergency else "CLOSE_SHORT",
                        "qty": qty,
                        "price": price,
                        "fee": fee,
                    }
                )
                if short_qty == ZERO:
                    flat_ms = at + (MINUTE if emergency else 0)
                    ack_ms = flat_ms + scenario.ack_min * MINUTE

        # Sub-minute payments belong only to remaining positions after open-time fills.
        for payment in frame.funding:
            if payment.at_ms > at:
                amount = short_qty * payment.rate * payment.mark
                # Stop time within the minute is unknown: do not grant positive carry.
                if emergency:
                    amount = min(ZERO, carried_short_qty * payment.rate * payment.mark)
                cash += amount
                funding += amount
                if amount != ZERO and not payment.exact_mark:
                    fee_mark_proxy_count += 1

        if frame.long_goal == ZERO and off_ms is None:
            off_ms, sell_floor = at, frame.previous_spot.c * (1 - CAP)
            counts["buy_canceled_on_goal_zero"] += int(bought < event.long_qty)

        eligible_ms = ack_ms + wait_min * MINUTE if ack_ms is not None else None
        if (
            off_ms is None
            and short_qty == ZERO
            and eligible_ms is not None
            and eligible_ms <= at < eligible_ms + 240 * MINUTE
            and bought < event.long_qty
        ):
            price = frame.spot.o * (1 + spot_slip_rate)
            if price > event.spot_reference * (1 + CAP):
                counts["buy_limit_miss"] += 1
            else:
                qty = min(
                    event.long_qty - bought,
                    floor_to_step(frame.previous_spot.volume * scenario.participation, SPOT_STEP),
                    floor_to_step(max(ZERO, cash) / (price * (1 + spot_fee_rate)), SPOT_STEP),
                )
                if qty * price < MIN_NOTIONAL:
                    counts["buy_minimum_or_liquidity"] += 1
                else:
                    fee, slip = qty * price * spot_fee_rate, qty * (price - frame.spot.o)
                    cash -= qty * price + fee
                    long_buy_raw += qty * frame.spot.o
                    sf, ss, turnover = sf + fee, ss + slip, turnover + qty * price
                    counts["buy_partial"] += int(qty < event.long_qty - bought)
                    spot_qty, bought = spot_qty + qty, bought + qty
                    long_fill_notional += qty * price
                    first_buy_ms = at if first_buy_ms is None else first_buy_ms
                    full_buy_ms = at if bought == event.long_qty else full_buy_ms
                    fills.append(
                        {"at_ms": at, "leg": "BUY_SPOT", "qty": qty, "price": price, "fee": fee}
                    )
        if off_ms is not None and at < off_ms + DAY and spot_qty > ZERO:
            price = frame.spot.o * (1 - spot_slip_rate)
            qty = min(
                spot_qty,
                floor_to_step(frame.previous_spot.volume * scenario.participation, SPOT_STEP),
            )
            if price < sell_floor:
                counts["sell_limit_miss"] += 1
            elif qty * price < MIN_NOTIONAL:
                counts["sell_minimum_or_liquidity"] += 1
            else:
                fee, slip = qty * price * spot_fee_rate, qty * (frame.spot.o - price)
                cash += qty * price - fee
                long_sell_raw += qty * frame.spot.o
                sf, ss, turnover = sf + fee, ss + slip, turnover + qty * price
                counts["sell_partial"] += int(qty < spot_qty)
                spot_qty -= qty
                fills.append(
                    {"at_ms": at, "leg": "SELL_SPOT", "qty": qty, "price": price, "fee": fee}
                )

        equity = cash + spot_qty * frame.spot.c + short_qty * (event.short_reference - frame.perp.c)
        if cash < ZERO or (spot_qty > ZERO and short_qty > ZERO):
            raise DataIntegrityError("cash/long-short overlap invariant")
        peak = max(peak, equity)
        mdd = max(mdd, 1 - equity / peak)
        worst_equity = min(worst_equity, equity)
        if equity <= ZERO:
            raise DataIntegrityError("conditional capital exhausted")
        exposure = (spot_qty * frame.spot.c + short_qty * frame.perp.c) / equity
        exposure_sum += exposure
        max_exposure = max(max_exposure, exposure)
        if at + MINUTE - event.start_ms in (DAY, 2 * DAY):
            checkpoints[str((at + MINUTE - event.start_ms) // DAY)] = equity - INITIAL

    last = frames[-1]
    long_market = long_sell_raw + spot_qty * last.spot.c - long_buy_raw
    short_market += short_qty * (event.short_reference - last.perp.c)
    long_net = long_market - sf - ss
    short_net = short_market - ff - fs + funding
    net = equity - INITIAL
    residual = net - long_net - short_net
    if abs(residual) > D("1e-18"):
        raise DataIntegrityError("conditional ledger residual")
    return {
        "symbol": event.symbol,
        "start_ms": event.start_ms,
        "wait_min": wait_min,
        "scenario": scenario.name,
        "net": net,
        "long_net": long_net,
        "short_net": short_net,
        "spot_fee": sf,
        "spot_slip": ss,
        "short_fee": ff,
        "short_slip": fs,
        "funding": funding,
        "funding_mark_proxy_count": fee_mark_proxy_count,
        "turnover": turnover,
        "flat_ms": flat_ms,
        "ack_ms": ack_ms,
        "first_buy_ms": first_buy_ms,
        "full_buy_ms": full_buy_ms,
        "off_ms": off_ms,
        "bought_qty": bought,
        "wanted_qty": event.long_qty,
        "average_buy_price": long_fill_notional / bought if bought else None,
        "spot_residual": spot_qty,
        "short_residual": short_qty,
        "mdd": mdd,
        "worst_net": worst_equity - INITIAL,
        "average_exposure": exposure_sum / len(frames),
        "max_exposure": max_exposure,
        "checkpoints": checkpoints,
        "counts": dict(counts),
        "fills": fills,
        "residual": residual,
    }
