"""Frozen anonymous Laya inputs and minute execution diagnostics; never operating orders."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from auto_trading.research import indicators_229 as ind
from auto_trading.research.short_search_229 import H4, Coin

MINUTE = 60000
ZERO = Decimal(0)
ONE = Decimal(1)
QUESTION = {
    "edge": {
        "type": "choice",
        "instructions": "Using only this anonymous snapshot, which outcome is more likely for a "
        "short held for 48 hours unless a 3% adverse-price stop triggers? "
        "Allow 0.19% round-trip fees/slippage, funding, and extra 0.2% adverse stop slippage.",
        "criteria": {
            "positive": "The short has positive net profit after these costs.",
            "nonpositive": "The short has zero or negative net profit after these costs.",
        },
    }
}
MODEL = "english"
THRESHOLD = 0.60


def snapshot_fields(c: Coin, i: int) -> dict[str, float | bool]:
    if i < 180:
        raise ValueError("snapshot warmup")
    closes = c.c[: i + 1]
    e20 = ind.ema(closes, 20)[-1]
    a20 = ind.atr(c.h[: i + 1], c.lo[: i + 1], closes, 20)[-1]
    r7 = ind.rsi(closes, 7)
    at = c.t[i] + H4
    f = bisect.bisect_right(c.fund_t, at) - 1
    previous_volume = sum(c.v[i - 20 : i]) / 20
    if f < 0 or at - c.fund_t[f] > 9 * 3600000 or a20 <= 0 or previous_volume <= 0:
        raise ValueError("funding/ATR unknown")
    values: dict[str, float | bool] = {
        "return4h_pct": (c.c[i] / c.c[i - 1] - 1) * 100,
        "return1d_pct": (c.c[i] / c.c[i - 6] - 1) * 100,
        "return7d_pct": (c.c[i] / c.c[i - 42] - 1) * 100,
        "return30d_pct": (c.c[i] / c.c[i - 180] - 1) * 100,
        "rsi7_previous": r7[-2],
        "rsi7_current": r7[-1],
        "rsi14": c.ind["rsi"][i],
        "stoch14_k": c.ind["sk"][i],
        "atr14_pct": c.ind["atr"][i] / c.c[i] * 100,
        "ema20_distance_atr20": (c.c[i] - e20) / a20,
        "volume_vs_previous20": c.v[i] / previous_volume,
        "funding_last_pct": c.fund_r[f] * 100,
        "closed_daily_adx25_down": c.regime["R5"][i],
    }
    if not all(math.isfinite(float(v)) for v in values.values()):
        raise ValueError("snapshot nonfinite")
    return values


def state_text(values: Mapping[str, float | bool], *, reverse: bool = False) -> str:
    items = list(values.items())
    if reverse:
        items.reverse()
    return (
        "Anonymous liquid cryptocurrency perpetual, closed 4-hour snapshot. "
        + "; ".join(f"{k}: {v if isinstance(v, bool) else f'{v:.4f}'}" for k, v in items)
        + ". No date, identity, absolute price, news, or future outcome is supplied."
    )


def request_key(text: str) -> str:
    return hashlib.sha256(
        json.dumps({"state": text, "questions": QUESTION, "model": MODEL}, sort_keys=True).encode()
    ).hexdigest()


def probability(raw: Mapping[str, Any] | None) -> float | None:
    if raw is None:
        return None
    try:
        a = raw["answers"]["edge"]
        probs = a["probabilities"]
        p, q = float(probs["positive"]), float(probs["nonpositive"])
        if (
            a["choice"] not in ("positive", "nonpositive")
            or not all(math.isfinite(x) and 0 <= x <= 1 for x in (p, q))
            or abs(p + q - 1) > 0.001
        ):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return p


@dataclass(frozen=True)
class MinuteBar:
    at_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Funding:
    at_ms: int
    rate: Decimal
    mark: Decimal
    low: Decimal
    high: Decimal
    exact: bool


@dataclass(frozen=True)
class MinuteResult:
    signal_ms: int
    entry_ms: int | None
    exit_ms: int | None
    desired_qty: Decimal
    filled_qty: Decimal
    remaining_qty: Decimal
    entry_px: Decimal
    gross: Decimal
    fee: Decimal
    funding: Decimal
    funding_worst: Decimal
    funding_best: Decimal
    stopped: bool
    partial_entry: bool
    exit_pieces: int
    exit_delay_min: int
    status: str

    @property
    def net(self) -> Decimal:
        return self.gross - self.fee + self.funding


def simulate_minute(
    bars: Mapping[int, MinuteBar],
    funding: Sequence[Funding],
    signal_ms: int,
    *,
    bounded: bool,
    cost: int = 1,
    delay4h: int = 0,
) -> MinuteResult:
    """Fixed 2,500-USDT opportunity audit, not a compounded wallet / operating risk engine."""
    if cost not in (1, 2) or delay4h not in (0, 1):
        raise ValueError("unregistered sensitivity")
    fee_rate, slip, band, stop_extra = (
        Decimal("0.0005") * cost,
        Decimal("0.00045") * cost,
        Decimal("0.003"),
        Decimal("0.002") * cost,
    )
    step, participation = Decimal("0.001"), Decimal("0.001")
    entry_ms = signal_ms + MINUTE + delay4h * H4
    desired = qty = remaining = px = gross = fees = main = worst = best = ZERO
    stopped, partial = False, False
    pieces, delay_min = 0, 0
    exit_ms: int | None = None
    status = "UNFILLED"

    def result() -> MinuteResult:
        return MinuteResult(
            signal_ms,
            entry_ms if qty > 0 else None,
            exit_ms,
            desired,
            qty,
            remaining,
            px,
            gross,
            fees,
            main,
            worst,
            best,
            stopped,
            partial,
            pieces,
            delay_min,
            status,
        )

    prev, first = bars.get(entry_ms - MINUTE), bars.get(entry_ms)
    if prev is None or first is None:
        status = "DATA_INCOMPLETE"
        return result()
    px = first.open * (ONE - slip)
    if prev.volume <= 0 or (bounded and px < prev.close * (ONE - band)):
        return result()
    desired = (Decimal(2500) / px / step).to_integral_value(rounding=ROUND_FLOOR) * step
    cap = (prev.volume * participation / step).to_integral_value(rounding=ROUND_FLOOR) * step
    qty = remaining = min(desired, cap)
    if qty <= 0:
        return result()
    partial = qty < desired
    fees = qty * px * fee_rate
    stop = px * Decimal("1.03")
    times = [f.at_ms for f in funding]
    fi = bisect.bisect_right(times, entry_ms)  # exclude entry boundary
    deadline = signal_ms + 36 * H4
    for at in range(entry_ms, deadline, MINUTE):
        bar, previous = bars.get(at), bars.get(at - MINUTE)
        if bar is None or previous is None:
            status = "DATA_INCOMPLETE"
            return result()
        # Funding at this open belongs to the carried quantity, before an exit.
        while fi < len(funding) and funding[fi].at_ms <= at:
            f = funding[fi]
            main += remaining * f.rate * f.mark
            worst += remaining * f.rate * (f.low if f.rate >= 0 else f.high)
            best += remaining * f.rate * (f.high if f.rate >= 0 else f.low)
            fi += 1
        if bar.open >= stop:
            cover = bar.open * (ONE + slip + stop_extra)
            gross += remaining * (px - cover)
            fees += remaining * cover * fee_rate
            remaining = ZERO
            stopped, pieces, exit_ms, status = True, pieces + 1, at, "CLOSED"
            return result()
        if at >= entry_ms + 12 * H4:
            cover = bar.open * (ONE + slip)
            if previous.volume > 0 and (not bounded or cover <= previous.close * (ONE + band)):
                available = (previous.volume * participation / step).to_integral_value(
                    rounding=ROUND_FLOOR
                ) * step
                taken = min(remaining, available)
                if taken > 0:
                    gross += taken * (px - cover)
                    fees += taken * cover * fee_rate
                    remaining -= taken
                    pieces += 1
                    exit_ms = at
            if remaining == 0:
                status = "CLOSED"
                return result()
            delay_min += 1
        hit = bar.high >= stop
        # Unknown intraminute stop ordering: retain debits, omit positive credits.
        while fi < len(funding) and funding[fi].at_ms <= at + MINUTE:
            f = funding[fi]
            if not hit or f.rate < 0:
                main += remaining * f.rate * f.mark
                worst += remaining * f.rate * (f.low if f.rate >= 0 else f.high)
                best += remaining * f.rate * (f.high if f.rate >= 0 else f.low)
            fi += 1
        if hit:
            cover = max(bar.open, stop) * (ONE + slip + stop_extra)
            gross += remaining * (px - cover)
            fees += remaining * cover * fee_rate
            remaining = ZERO
            stopped, pieces, exit_ms, status = True, pieces + 1, at + MINUTE, "CLOSED"
            return result()
    # A reserve / valuation is not an executed market close.
    last = bars[deadline - MINUTE]
    gross += remaining * (px - last.close)
    fees += remaining * last.close * fee_rate
    exit_ms, status = deadline, "UNRESOLVED_MTM"
    return result()
