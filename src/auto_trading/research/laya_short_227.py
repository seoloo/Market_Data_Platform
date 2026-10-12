"""문서 227 — Laya 확률 + Claude 판단 규칙 BTC 숏 후향 파일럿 (순수 계산).

docs/research/227-laya-btc-short-pilot.md. 자료 · Laya 호출은 ``scripts/run_laya_short_227.py``.
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "DAY_MS",
    "QUESTION",
    "Bar",
    "Features",
    "Trade",
    "claude_ok",
    "features",
    "laya_ok",
    "random_totals",
    "simulate",
    "state_text",
]

DAY_MS = 86_400_000
HOLD_DAYS = 7
STOP = 0.15
STOP_SLIP = 0.01
FEE = 0.00095
P_MIN = 0.50
CRASH_7D = -0.15

QUESTION = {
    "next7": {
        "type": "choice",
        "instructions": "Over the next 7 days, what is most likely for Bitcoin's price?",
        "criteria": {
            "drop": "falls more than 5%",
            "rise": "rises more than 5%",
            "flat": "stays within plus or minus 5%",
        },
    }
}


@dataclass(frozen=True, slots=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class Features:
    r1: float
    r7: float
    r30: float
    r90: float
    ma200: float
    dd90: float
    vol30: float
    fund7: float
    trend: float


def features(closes: Sequence[float], fund_rates_7d: Sequence[float]) -> Features | None:
    """마지막 값이 신호일 d 의 종가인 현물 종가 목록 · d 마감 전 7 일 funding 비율."""
    if len(closes) < 201 or not fund_rates_7d:
        return None
    c = closes
    last = c[-1]
    logs = [math.log(c[-k] / c[-k - 1]) for k in range(1, 31)]
    signs = [(last > c[-1 - k]) - (last < c[-1 - k]) for k in (20, 60, 120)]
    return Features(
        r1=last / c[-2] - 1,
        r7=last / c[-8] - 1,
        r30=last / c[-31] - 1,
        r90=last / c[-91] - 1,
        ma200=last / statistics.fmean(c[-200:]) - 1,
        dd90=last / max(c[-90:]) - 1,
        vol30=statistics.stdev(logs) * math.sqrt(365),
        fund7=statistics.fmean(fund_rates_7d),
        trend=sum(signs) / 3,
    )


def state_text(f: Features) -> str:
    """Laya 에 줄 영어 문장 — 날짜 · 가격 수준 없음."""

    def pct(x: float) -> str:
        return f"{x * 100:+.1f}%"

    return (
        "Bitcoin daily snapshot. "
        f"Change last 1 day: {pct(f.r1)}. Last 7 days: {pct(f.r7)}. "
        f"Last 30 days: {pct(f.r30)}. Last 90 days: {pct(f.r90)}. "
        f"Price versus 200-day average: {pct(f.ma200)}. "
        f"Distance from 90-day high: {pct(f.dd90)}. "
        f"30-day volatility (annualized): {f.vol30 * 100:.0f}%. "
        f"Average funding rate last 7 days: {f.fund7 * 100:+.4f}% per 8 hours. "
        f"Trend score from 20/60/120-day returns: {f.trend:+.2f} (from -1 to 1)."
    )


def laya_ok(label: str | None, p_drop: float | None) -> bool:
    return label == "drop" and p_drop is not None and p_drop >= P_MIN


def claude_ok(f: Features) -> bool:
    """Claude 판단 규칙 (227 §2, 결과 전 고정): 급락 직후 · 숏 과밀이면 안 한다."""
    return f.r7 > CRASH_7D and f.fund7 >= 0


@dataclass(frozen=True, slots=True)
class Trade:
    signal_day: int
    entry_day: int
    exit_day: int
    entry: float
    exit: float
    stopped: bool
    funding: float
    """자본 대비 받은(+) · 낸(−) funding 비율."""
    ret: float
    """자본 대비 순수익 (비용 · funding 포함)."""


def _one(
    d: int, perp: Mapping[int, Bar], fund_t: Sequence[int], fund_r: Sequence[float]
) -> Trade | None:
    e_day = d + DAY_MS
    x_day = d + (HOLD_DAYS + 1) * DAY_MS
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
    lo = bisect.bisect_left(fund_t, e_day)
    hi = bisect.bisect_left(fund_t, end)
    fund = sum(fund_r[lo:hi])  # 숏: 양수 funding 을 받는다 (명목 ≈ 자본 1 배로 근사)
    price_ret = (entry - exit_px) / entry
    ret = price_ret + fund - FEE - FEE * (exit_px / entry)
    return Trade(d, e_day, end, entry, exit_px, stopped, fund, ret)


def simulate(
    signal_days: Sequence[int],
    perp: Mapping[int, Bar],
    funding: Sequence[tuple[int, float]],
) -> list[Trade]:
    """신호일 순서대로, 보유 중 신호는 무시하고 숏 1 배 7 일."""
    fund_t = [t for t, _ in funding]
    fund_r = [r for _, r in funding]
    out: list[Trade] = []
    free_from = -1
    for d in sorted(signal_days):
        if d + DAY_MS < free_from:
            continue
        t = _one(d, perp, fund_t, fund_r)
        if t is None:
            continue
        out.append(t)
        free_from = t.exit_day
    return out


def total(trades: Sequence[Trade]) -> float:
    eq = 1.0
    for t in trades:
        eq *= 1 + t.ret
    return eq - 1


def random_totals(
    days: Sequence[int],
    n: int,
    perp: Mapping[int, Bar],
    funding: Sequence[tuple[int, float]],
    reps: int = 1000,
    seed: int = 227,
) -> list[float]:
    """같은 거래 수 n 을 무작위 신호일(겹치지 않게)에 — 총 수익 분포."""
    rng = random.Random(seed)
    fund_t = [t for t, _ in funding]
    fund_r = [r for _, r in funding]
    cache: dict[int, Trade | None] = {}
    out = []
    for _ in range(reps):
        chosen: list[Trade] = []
        busy: list[tuple[int, int]] = []
        tries = 0
        while len(chosen) < n and tries < n * 200:
            tries += 1
            d = rng.choice(days)
            if d not in cache:
                cache[d] = _one(d, perp, fund_t, fund_r)
            t = cache[d]
            if t is None or any(not (t.exit_day <= a or t.entry_day >= b) for a, b in busy):
                continue
            busy.append((t.entry_day, t.exit_day))
            chosen.append(t)
        chosen.sort(key=lambda t: t.entry_day)
        out.append(total(chosen))
    return out


def max_drawdown(trades: Sequence[Trade], perp: Mapping[int, Bar]) -> float:
    """자본 MDD — 보유 중에는 그날 고가(숏의 최악)로 표시한다."""
    eq = peak = 1.0
    mdd = 0.0
    for t in trades:
        day = t.entry_day
        while day < t.exit_day:
            worst_px = min(perp[day].high, t.exit if t.stopped else math.inf)
            mark = eq * (1 + (t.entry - worst_px) / t.entry)
            mdd = max(mdd, 1 - mark / peak)
            day += DAY_MS
        eq *= 1 + t.ret
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return mdd
