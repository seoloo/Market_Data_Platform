"""Frozen role masks for an explanatory retrospective SHORT study, not operating signals."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product

from auto_trading.research import indicators_229 as ind
from auto_trading.research.short_search_229 import Coin

TRIGGERS = ("RSI7_70", "RSI14_60", "ST14_80", "ST21_70", "MACD", "EMA20", "CCI", "MFI")
BACKDROPS = ("R0", "R5", "R4", "R6", "R1")
CONFIRMS = ("NONE", "EMA50", "MACD", "ICHI", "CMF", "STRUCTURE", "BB", "KELTNER")
PARTICIPATIONS = ("NONE", "RVOL", "OBV")
RISKS = ("NONE", "REGULAR", "BOTH", "BOTH_EXTREME")
NAN = float("nan")


@dataclass(frozen=True)
class RoleConfig:
    trigger: str
    backdrop: str
    confirm: str
    participation: str
    risk: str

    def __post_init__(self) -> None:
        for value, allowed in zip(
            (self.trigger, self.backdrop, self.confirm, self.participation, self.risk),
            (TRIGGERS, BACKDROPS, CONFIRMS, PARTICIPATIONS, RISKS),
            strict=True,
        ):
            if value not in allowed:
                raise ValueError(value)

    @property
    def key(self) -> str:
        return ":".join((self.trigger, self.backdrop, self.confirm, self.participation, self.risk))


def configs() -> list[RoleConfig]:
    return [
        RoleConfig(*values)
        for values in product(TRIGGERS, BACKDROPS, CONFIRMS, PARTICIPATIONS, RISKS)
    ]


def cmf(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], volume: Sequence[float]
) -> list[float]:
    if not len(high) == len(low) == len(close) == len(volume):
        raise ValueError("CMF length mismatch")
    money = [
        (2 * c - h - bottom) / (h - bottom) * v if h > bottom else 0.0
        for h, bottom, c, v in zip(high, low, close, volume, strict=True)
    ]
    out = [NAN] * len(close)
    for i in range(19, len(close)):
        vsum = sum(volume[i - 19 : i + 1])
        if vsum > 0:
            out[i] = sum(money[i - 19 : i + 1]) / vsum
    return out


@dataclass(frozen=True)
class PivotState:
    last_low: list[float]
    known: list[bool]
    regular_bull: list[bool]
    hidden_bull: list[bool]
    regular_bear: list[bool]
    hidden_bear: list[bool]
    events: tuple[tuple[int, int, str], ...]  # pivot index, available index, label


def confirmed_pivots(
    high: Sequence[float], low: Sequence[float], rsi: Sequence[float]
) -> PivotState:
    """Strict 2L/2R. Tied extrema / simultaneous extrema reset the evidence segment."""
    if not len(high) == len(low) == len(rsi):
        raise ValueError("pivot length mismatch")
    n = len(high)
    levels, known = [NAN] * n, [False] * n
    labels = {
        label: [False] * n
        for label in ("regular_bull", "hidden_bull", "regular_bear", "hidden_bear")
    }
    previous: dict[str, int] = {}
    latest: dict[str, int] = {}
    evidence_known = False
    last_low = NAN
    events: list[tuple[int, int, str]] = []
    for available in range(n):
        p = available - 2
        if p >= 2:
            hw, lw = list(high[p - 2 : p + 3]), list(low[p - 2 : p + 3])
            is_high, is_low = high[p] == max(hw), low[p] == min(lw)
            ambiguous = (
                (is_high and hw.count(high[p]) != 1)
                or (is_low and lw.count(low[p]) != 1)
                or (is_high and is_low)
            )
            if ambiguous:
                previous.clear()
                latest.clear()
                evidence_known = False
                last_low = NAN
                events.append((p, available, "UNKNOWN"))
            else:
                kind = "low" if is_low else "high" if is_high else None
                if kind is not None:
                    prev = previous.get(kind)
                    pair_known = (
                        prev is not None
                        and 4 <= p - prev <= 60
                        and math.isfinite(rsi[p])
                        and math.isfinite(rsi[prev])
                    )
                    if kind == "low":
                        last_low = low[p]
                        evidence_known = pair_known
                    if pair_known and prev is not None:
                        price1, price2 = (
                            (low[prev], low[p]) if kind == "low" else (high[prev], high[p])
                        )
                        label: str | None = None
                        if kind == "low" and price2 < price1 and rsi[p] > rsi[prev]:
                            label = "regular_bull"
                        elif kind == "low" and price2 > price1 and rsi[p] < rsi[prev]:
                            label = "hidden_bull"
                        elif kind == "high" and price2 > price1 and rsi[p] < rsi[prev]:
                            label = "regular_bear"
                        elif kind == "high" and price2 < price1 and rsi[p] > rsi[prev]:
                            label = "hidden_bear"
                        if label is not None:
                            latest[label] = available
                            events.append((p, available, label))
                    previous[kind] = p
        # A pair becomes stale if its most recent low is more than 60 bars old.
        low_at = previous.get("low")
        known[available] = evidence_known and low_at is not None and available - low_at <= 60
        levels[available] = last_low
        for label, values in labels.items():
            event_at = latest.get(label)
            values[available] = event_at is not None and 0 <= available - event_at < 12
    return PivotState(
        levels,
        known,
        labels["regular_bull"],
        labels["hidden_bull"],
        labels["regular_bear"],
        labels["hidden_bear"],
        tuple(events),
    )


@dataclass(frozen=True)
class RoleMasks:
    triggers: dict[str, list[int]]
    confirms: dict[str, list[bool]]
    participations: dict[str, list[bool]]
    risks: dict[str, list[bool]]
    pivots: PivotState


def masks(coin: Coin) -> RoleMasks:
    n = len(coin.t)
    c, h, lo, v = coin.c, coin.h, coin.lo, coin.v
    r7, r14 = ind.rsi(c, 7), ind.rsi(c, 14)
    k14, d14 = ind.stoch(h, lo, c, 14)
    k21, d21 = ind.stoch(h, lo, c, 21)
    md, ms = ind.macd(c)
    hist = [a - b for a, b in zip(md, ms, strict=True)]
    e20, e50 = ind.ema(c, 20), ind.ema(c, 50)
    a14, a20 = ind.atr(h, lo, c, 14), ind.atr(h, lo, c, 20)
    cc, mf = ind.cci(h, lo, c), ind.mfi(h, lo, c, v)
    _, bbu, bbl = ind.bollinger(c)
    tenkan, kijun, _, _ = ind.ichimoku(h, lo)
    cf, ob, va = cmf(h, lo, c, v), ind.obv(c, v), ind.sma(v, 20)
    pivots = confirmed_pivots(h, lo, r14)
    triggers: dict[str, list[int]] = {key: [] for key in TRIGGERS}
    confirms = {key: [False] * n for key in CONFIRMS}
    participations = {key: [False] * n for key in PARTICIPATIONS}
    risks = {key: [False] * n for key in RISKS}
    for i in range(n):
        confirms["NONE"][i] = participations["NONE"][i] = risks["NONE"][i] = True
        confirms["EMA50"][i] = c[i] < e50[i]
        confirms["MACD"][i] = i > 0 and hist[i] < 0 and hist[i] < hist[i - 1]
        confirms["ICHI"][i] = tenkan[i] < kijun[i]
        confirms["CMF"][i] = cf[i] < 0
        confirms["STRUCTURE"][i] = c[i] < pivots.last_low[i]
        width = bbu[i] - bbl[i]
        confirms["BB"][i] = (
            math.isfinite(width) and width > 0 and 0.5 <= (c[i] - bbl[i]) / width <= 1
        )
        confirms["KELTNER"][i] = (
            math.isfinite(a20[i]) and a20[i] > 0 and 0 <= c[i] - e20[i] <= 2 * a20[i]
        )
        participations["RVOL"][i] = i > 0 and va[i - 1] > 0 and v[i] >= 1.5 * va[i - 1]
        participations["OBV"][i] = i >= 5 and ob[i] < ob[i - 5]
        regular, hidden = pivots.regular_bull[i], pivots.hidden_bull[i]
        risks["REGULAR"][i] = pivots.known[i] and not regular
        risks["BOTH"][i] = pivots.known[i] and not (regular or hidden)
        risks["BOTH_EXTREME"][i] = (
            risks["BOTH"][i]
            and math.isfinite(r14[i])
            and math.isfinite(a14[i])
            and math.isfinite(e20[i])
            and not (r14[i] <= 30 and c[i] < e20[i] - 2 * a14[i])
        )
        if i == 0:
            continue
        fire = {
            "RSI7_70": r7[i - 1] >= 70 > r7[i],
            "RSI14_60": r14[i - 1] >= 60 > r14[i],
            "ST14_80": k14[i - 1] >= 80 and k14[i - 1] >= d14[i - 1] and k14[i] < d14[i],
            "ST21_70": k21[i - 1] >= 70 and k21[i - 1] >= d21[i - 1] and k21[i] < d21[i],
            "MACD": md[i - 1] >= ms[i - 1] and md[i] < ms[i],
            "EMA20": c[i - 1] >= e20[i - 1] and c[i] < e20[i],
            "CCI": cc[i - 1] >= 100 > cc[i],
            "MFI": mf[i - 1] >= 80 > mf[i],
        }
        for key, value in fire.items():
            if value:
                triggers[key].append(i)
    return RoleMasks(triggers, confirms, participations, risks, pivots)


def admitted(coin: Coin, data: RoleMasks, cfg: RoleConfig, i: int) -> bool:
    return (
        coin.regime[cfg.backdrop][i]
        and data.confirms[cfg.confirm][i]
        and data.participations[cfg.participation][i]
        and data.risks[cfg.risk][i]
    )


def signals(coin: Coin, data: RoleMasks, cfg: RoleConfig) -> list[int]:
    return [i for i in data.triggers[cfg.trigger] if admitted(coin, data, cfg, i)]
