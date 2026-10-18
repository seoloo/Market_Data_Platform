"""Causal market-neutral features and path-outcome labels (no market I/O)."""

from __future__ import annotations

import math
from collections.abc import Sequence

H4 = 14_400_000
WINDOW = 96
FEATURES = (
    "open_relative",
    "high_relative",
    "low_relative",
    "return",
    "range",
    "relative_volume",
    "sma20_distance",
    "sma60_distance",
    "rsi14",
    "atr14_relative",
)


def calculate(rows: Sequence[Sequence[float]]) -> list[list[float]]:
    """Rows: UTC open_ms,O,H,L,C,V. Gaps restart Wilder and rolling history."""
    out = []
    start = 0
    gain = loss = atr = 0.0
    gains: list[float] = []
    losses: list[float] = []
    trs: list[float] = []
    for i, row in enumerate(rows):
        if i == 0 or row[0] != rows[i - 1][0] + H4:
            start = i
            gain = loss = atr = 0.0
            gains, losses, trs = [], [], []
        age = i - start
        if age:
            prev = rows[i - 1][4]
            delta = row[4] - prev
            g, down = max(delta, 0), max(-delta, 0)
            tr = max(row[2] - row[3], abs(row[2] - prev), abs(row[3] - prev))
            gains.append(g)
            losses.append(down)
            trs.append(tr)
            if age == 14:
                gain, loss, atr = sum(gains) / 14, sum(losses) / 14, sum(trs) / 14
            elif age > 14:
                gain, loss, atr = (
                    (gain * 13 + g) / 14,
                    (loss * 13 + down) / 14,
                    (atr * 13 + tr) / 14,
                )
        if age < 59:
            out.append([math.nan] * len(FEATURES))
            continue
        prev = rows[i - 1][4]
        volume = sum(r[5] for r in rows[i - 20 : i]) / 20
        if volume <= 0 or row[5] <= 0:
            out.append([math.nan] * len(FEATURES))
            continue
        sma20 = sum(r[4] for r in rows[i - 19 : i + 1]) / 20
        sma60 = sum(r[4] for r in rows[i - 59 : i + 1]) / 60
        rsi = 50.0 if gain + loss == 0 else 100 * gain / (gain + loss)
        out.append(
            [
                *[math.log(row[k] / prev) for k in (1, 2, 3, 4)],
                (row[2] - row[3]) / prev,
                math.log(row[5] / volume),
                math.log(row[4] / sma20),
                math.log(row[4] / sma60),
                (rsi - 50) / 50,
                atr / row[4],
            ]
        )
    return out


def classify(long_return: float | None, short_return: float | None) -> int:
    """None is an unfilled direction, not a future unknown (unknowns removed upstream)."""
    lo = -math.inf if long_return is None else long_return
    sh = -math.inf if short_return is None else short_return
    if max(lo, sh) <= 0.002 or lo == sh:
        return 0
    return 1 if lo > sh else 2


def split_id(at_ms: int) -> int:
    bounds = (
        (1593561600000, 1704067200000),
        (1704067200000, 1735689600000),
        (1735689600000, 1788220800000),
    )
    for k, (lo, hi) in enumerate(bounds):
        if lo <= at_ms and at_ms + 26 * 3_600_000 < hi:
            return k
    return -1


def rule(last: Sequence[float]) -> int:
    # ln(C/SMA20)>0 and ln(C/SMA60)>ln(C/SMA20) means C>SMA20>SMA60.
    if last[6] > 0 and last[7] > last[6] and last[8] > 0:
        return 1
    if last[6] < 0 and last[7] < last[6] and last[8] < 0:
        return 2
    return 0
