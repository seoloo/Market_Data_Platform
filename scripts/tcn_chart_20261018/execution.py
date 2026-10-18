"""Research-only bounded minute fills and Decimal cash flows; no exchange client."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

D = Decimal
ZERO = D(0)
DEFAULT_NOTIONAL = D(1000)
DEFAULT_PARTICIPATION = D("0.001")


def floor_qty(qty: Decimal) -> Decimal:
    return qty.quantize(D("0.001"), rounding=ROUND_FLOOR)


def limit_fill(previous: Any, bar: Any, side: int, symbol: str, cost: int) -> Decimal | None:
    """Price cap set before bar; adverse execution must lie in observed range."""
    if previous[5] <= 0 or bar[5] <= 0:
        return None
    tick = D("0.1") if symbol == "BTCUSDT" else D("0.01")
    cap = D(str(previous[4])) * (1 + D(side) * D("0.003"))
    px = D(str(bar[1])) * (1 + D(side) * D("0.00045") * cost)
    px = (px / tick).to_integral_value(rounding=ROUND_CEILING if side == 1 else ROUND_FLOOR) * tick
    if (side == 1 and px > cap) or (side == -1 and px < cap):
        return None
    if not D(str(bar[3])) <= px <= D(str(bar[2])):
        return None
    return px


@dataclass
class Episode:
    status: str = "UNFILLED"
    start: int = -1
    end: int = -1
    qty: Decimal = ZERO
    remaining: Decimal = ZERO
    entry: Decimal = ZERO
    gross: Decimal = ZERO
    fees: Decimal = ZERO
    funding: Decimal = ZERO
    turnover: Decimal = ZERO
    price_impact: Decimal = ZERO
    partial: bool = False
    stopped: bool = False
    pieces: int = 0
    exit_delay: int = 0
    was_unresolved: bool = False
    flows: list[tuple[int, Decimal, Decimal]] = field(default_factory=list)

    @property
    def net(self) -> Decimal:
        return self.gross - self.fees + self.funding


def simulate(
    a: Any,
    funds: Any,
    signal_ms: int,
    side: int,
    symbol: str,
    notional: Decimal = DEFAULT_NOTIONAL,
    *,
    cost: int = 1,
    delay: int = 1,
    participation: Decimal = DEFAULT_PARTICIPATION,
    worst_mark: bool = False,
) -> Episode:
    import numpy as np  # type: ignore[import-not-found]

    if side not in (-1, 1) or cost not in (1, 2) or delay not in (1, 6):
        raise ValueError("unregistered execution setting")
    e = Episode()
    first_ms = int(a[0, 0])
    ix = (signal_ms + delay * 60000 - first_ms) // 60000
    if ix <= 0 or ix + 1500 >= len(a) or int(a[ix, 0]) != signal_ms + delay * 60000:
        e.status = "DATA_INCOMPLETE"
        return e
    px = limit_fill(a[ix - 1], a[ix], side, symbol, cost)
    if px is None:
        return e
    desired = floor_qty(notional / px)
    qty = min(desired, floor_qty(D(str(a[ix - 1, 5])) * participation))
    if qty < D("0.001") or qty * px < 100 or desired == 0:
        e.status = "MINIMUM"
        return e
    # Existing max rounding discrepancy; partial liquidity is logged separately.
    if (notional - desired * px) / notional > D("0.05"):
        e.status = "ROUNDING"
        return e
    fee_rate = D("0.0005") * cost
    e.qty, e.remaining, e.entry = qty, qty, px
    e.start, e.partial = ix, qty < desired
    e.fees, e.turnover = qty * px * fee_rate, qty * px
    e.price_impact = qty * abs(px - D(str(a[ix, 1])))
    e.flows.append((ix, -e.fees, qty))
    stop = px * (1 - D(side) * D("0.03"))
    # Search stop path vectorially, then process sparse fund events and exits chronologically.
    max_ix = ix + 1500
    path = a[ix : max_ix + 1]
    hits = np.flatnonzero(path[:, 3] <= float(stop) if side == 1 else path[:, 2] >= float(stop))
    stop_ix = ix + int(hits[0]) if len(hits) else max_ix + 1
    t0, t1 = a[ix, 0], a[max_ix, 0]
    fl = int(np.searchsorted(funds[:, 0], t0, side="right"))
    fh = int(np.searchsorted(funds[:, 0], t1 + 60000, side="left"))
    fi = fl

    def accrue(until_ms: int) -> None:
        nonlocal fi
        while fi < fh and funds[fi, 0] <= until_ms:
            f = funds[fi]
            at = int((f[0] - first_ms) // 60000)
            rate = D(str(f[1]))
            mark = D(str(f[2]))
            if worst_mark:
                mark = D(str(f[4] if side * float(rate) > 0 else f[3]))
            flow = -D(side) * e.remaining * rate * mark
            if at == stop_ix and flow > 0:
                flow = ZERO
            e.funding += flow
            e.flows.append((at, flow, e.remaining))
            fi += 1

    def close(j: int, take: Decimal, price: Decimal) -> None:
        gross = take * D(side) * (price - px)
        fee = take * price * fee_rate
        e.gross += gross
        e.fees += fee
        e.turnover += take * price
        reference = D(str(a[j, 1]))
        if j >= stop_ix:
            reference = min(reference, stop) if side == 1 else max(reference, stop)
        e.price_impact += take * abs(price - reference)
        e.remaining -= take
        e.pieces += 1
        e.flows.append((j, gross - fee, e.remaining))
        e.end = j

    for j in range(min(stop_ix, ix + 1440), max_ix + 1):
        # Emergency intraminute stop ordering is unknown: include that minute's debits,
        # omit credits. Ordinary limit exits occur at the modeled open, before later funding.
        accrue(int(a[j, 0]) + (59999 if j == stop_ix else 0))
        if j >= stop_ix:
            opening = D(str(a[j, 1]))
            worst = min(opening, stop) if side == 1 else max(opening, stop)
            stop_px = worst * (1 - D(side) * (D("0.00045") + D("0.002")) * cost)
            close(j, e.remaining, stop_px)
            e.stopped = True
        elif j >= ix + 1440:
            exit_px = limit_fill(a[j - 1], a[j], -side, symbol, cost)
            if exit_px is not None:
                take = min(e.remaining, floor_qty(D(str(a[j - 1, 5])) * participation))
                # reduce-only final dust permitted; no invented full fill.
                if take > 0:
                    close(j, take, exit_px)
        if e.remaining == 0:
            e.status = "CLOSED"
            e.exit_delay = max(0, j - ix - 1440)
            return e
    accrue(int(a[max_ix, 0]))
    e.end, e.status, e.exit_delay = max_ix, "UNRESOLVED", 60
    e.was_unresolved = True
    return e


def carry_unresolved(
    e: Episode,
    a: Any,
    funds: Any,
    side: int,
    end_ms: int,
    *,
    cost: int = 1,
    worst_mark: bool = False,
) -> None:
    """No new entry or ordinary retry. Value retained inventory and existing emergency stop.

    This does not turn an expired execution into a successful 24h trade. The account stays
    halted and the original failure flag remains even if its emergency stop eventually fills.
    """
    import numpy as np

    if e.status != "UNRESOLVED" or e.remaining <= 0:
        raise ValueError("only unresolved inventory can be carried")
    first_ms = int(a[0, 0])
    start = e.end + 1
    terminal = min(len(a) - 1, (end_ms - first_ms) // 60000 - 1)
    if terminal < start:
        raise ValueError("unresolved valuation boundary")
    stop = e.entry * (1 - D(side) * D("0.03"))
    path = a[start : terminal + 1]
    hits = np.flatnonzero(path[:, 3] <= float(stop) if side == 1 else path[:, 2] >= float(stop))
    stop_ix = start + int(hits[0]) if len(hits) else None
    finish = terminal if stop_ix is None else stop_ix
    funding_end = int(a[finish, 0]) + 59999
    keep = funds[(funds[:, 0] > a[e.end, 0]) & (funds[:, 0] <= funding_end)]
    for f in keep:
        at = int((f[0] - first_ms) // 60000)
        rate = D(str(f[1]))
        mark = D(str(f[4] if side * float(rate) > 0 else f[3])) if worst_mark else D(str(f[2]))
        flow = -D(side) * e.remaining * rate * mark
        if at == stop_ix and flow > 0:
            flow = ZERO
        e.funding += flow
        e.flows.append((at, flow, e.remaining))
    if stop_ix is not None:
        opening = D(str(a[stop_ix, 1]))
        reference = min(opening, stop) if side == 1 else max(opening, stop)
        price = reference * (1 - D(side) * D("0.00245") * cost)
        gross = e.remaining * D(side) * (price - e.entry)
        fee = e.remaining * price * D("0.0005") * cost
        e.price_impact += e.remaining * abs(price - reference)
        e.turnover += e.remaining * price
        e.gross += gross
        e.fees += fee
        e.remaining = ZERO
        e.flows.append((stop_ix, gross - fee, ZERO))
        e.stopped = True
        e.pieces += 1
        e.status = "LATE_EMERGENCY_CLOSED"
    else:
        e.status = "UNRESOLVED_MTM"
    e.end = finish


def equity_path(e: Episode, a: Any, side: int) -> tuple[Any, Any]:
    """Minute MTM cash flow path, partial quantity changes and fees included."""
    import numpy as np

    n = e.end - e.start + 1
    cash = np.zeros(n)
    qty = np.full(n, float(e.qty))
    for at, delta, remaining in sorted(e.flows, key=lambda x: x[0]):
        i = max(0, at - e.start)
        cash[i:] += float(delta)
        qty[i:] = float(remaining)
    pnl = cash + qty * side * (a[e.start : e.end + 1, 4] - float(e.entry))
    return pnl, qty * a[e.start : e.end + 1, 4]
