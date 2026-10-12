"""문서 228 — 짧은 숏(1 · 3 일, 코인당 0.5 배) 과 롱 배율 계좌 합치기 (순수 계산).

docs/research/228-long-lev-short-1x-brief-pilot.md.
자료 · 롱 엔진 호출은 ``scripts/run_brief_short_228.py``.
"""

from __future__ import annotations

import bisect
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from auto_trading.research.laya_short_227 import DAY_MS, FEE, Bar, Features, claude_ok

__all__ = [
    "ShortTrade",
    "combine",
    "random_short_totals",
    "short_ok",
    "simulate_shorts",
]

SIZE = 0.5
STOP = 0.05
STOP_SLIP = 0.01
HALT = 0.70


def short_ok(f: Features) -> bool:
    """추세 점수 −1(20 · 60 · 120 모두 하락) + 227 판단 규칙(급락 직후 · 숏 과밀 제외)."""
    return f.trend <= -1 and claude_ok(f)


@dataclass(frozen=True, slots=True)
class ShortTrade:
    coin: str
    signal_day: int
    entry_day: int
    exit_day: int
    stopped: bool
    ret: float
    """자본 대비 (크기 0.5 반영) 순수익."""


def _one(
    coin: str,
    d: int,
    hold: int,
    perp: Mapping[int, Bar],
    fund_t: Sequence[int],
    fund_r: Sequence[float],
) -> ShortTrade | None:
    e_day, x_day = d + DAY_MS, d + (hold + 1) * DAY_MS
    if e_day not in perp or x_day not in perp:
        return None
    entry = perp[e_day].open
    stop_px = entry * (1 + STOP)
    exit_px, stopped, end = perp[x_day].open, False, x_day
    day = e_day
    while day < x_day:
        if perp[day].high >= stop_px:
            exit_px, stopped, end = stop_px * (1 + STOP_SLIP), True, day + DAY_MS
            break
        day += DAY_MS
    lo, hi = bisect.bisect_left(fund_t, e_day), bisect.bisect_left(fund_t, end)
    trade = (entry - exit_px) / entry + sum(fund_r[lo:hi]) - FEE - FEE * exit_px / entry
    return ShortTrade(coin, d, e_day, end, stopped, SIZE * trade)


def simulate_shorts(
    signals: Mapping[str, Sequence[int]],
    hold: int,
    perp: Mapping[str, Mapping[int, Bar]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
) -> list[ShortTrade]:
    """코인마다 신호일 순서대로, 보유 중 신호는 무시."""
    out: list[ShortTrade] = []
    for coin, days in signals.items():
        ft = [t for t, _ in funding[coin]]
        fr = [r for _, r in funding[coin]]
        free_from = -1
        for d in sorted(days):
            if d + DAY_MS < free_from:
                continue
            t = _one(coin, d, hold, perp[coin], ft, fr)
            if t is None:
                continue
            out.append(t)
            free_from = t.exit_day
    out.sort(key=lambda t: (t.exit_day, t.coin))
    return out


def total(trades: Sequence[ShortTrade]) -> float:
    eq = 1.0
    for t in sorted(trades, key=lambda t: t.exit_day):
        eq *= 1 + t.ret
    return eq - 1


def random_short_totals(
    days: Mapping[str, Sequence[int]],
    n: int,
    hold: int,
    perp: Mapping[str, Mapping[int, Bar]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    reps: int = 1000,
    seed: int = 228,
) -> list[float]:
    """같은 거래 수 n 을 (코인, 무작위 날)에 — 같은 코인 안에서 겹치지 않게."""
    rng = random.Random(seed)
    coins = sorted(days)
    ft = {c: [t for t, _ in funding[c]] for c in coins}
    fr = {c: [r for _, r in funding[c]] for c in coins}
    cache: dict[tuple[str, int], ShortTrade | None] = {}
    out = []
    for _ in range(reps):
        chosen: list[ShortTrade] = []
        busy: dict[str, list[tuple[int, int]]] = {c: [] for c in coins}
        tries = 0
        while len(chosen) < n and tries < n * 200:
            tries += 1
            c = rng.choice(coins)
            d = rng.choice(days[c])
            if (c, d) not in cache:
                cache[(c, d)] = _one(c, d, hold, perp[c], ft[c], fr[c])
            t = cache[(c, d)]
            if t is None or any(not (t.exit_day <= a or t.entry_day >= b) for a, b in busy[c]):
                continue
            busy[c].append((t.entry_day, t.exit_day))
            chosen.append(t)
        out.append(total(chosen))
    return out


def combine(
    days: Sequence[int], long_returns: Sequence[float], shorts: Sequence[ShortTrade]
) -> tuple[list[float], int | None]:
    """날마다 수익 = 롱 계좌 수익 + 그날 닫힌 숏 수익. 처음 돈의 30% 이하면 그날부터 0 (AG 정리선).

    숏 수익의 기준일(``exit_day``)은 그 숏을 닫는 일봉의 시작 시각이다 —
    ``days`` 의 날짜(일봉 시작 시각)와 같은 축이다. 정리한 날을 돌려준다.
    """
    by_day: dict[int, float] = {}
    for t in shorts:
        by_day[t.exit_day] = by_day.get(t.exit_day, 0.0) + t.ret
    out: list[float] = []
    eq = 1.0
    halted = None
    for d, r in zip(days, long_returns, strict=True):
        if halted is not None:
            out.append(0.0)
            continue
        x = r + by_day.get(d, 0.0)
        eq *= 1 + x
        out.append(x)
        if eq <= 1 - HALT:
            halted = d
    return out, halted
