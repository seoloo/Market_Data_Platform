"""문서 221 — O 선물 추가분 × funding 과열 끄기 (후향 파일럿, 순수).

docs/research/221-o-leg-funding-gate-pilot.md. I/O 는 ``scripts/run_funding_gate_221.py``.

운영 엔진(:func:`auto_trading.backtest.overlay.step_overlay_day`)을 그대로 부르고, 하루 순서만
:func:`~auto_trading.backtest.overlay.simulate_overlay` 와 같게 다시 감싸 '배수 1.0 종목' 에
funding 끄기를 더한다. 문턱이 무한대면 ``simulate_overlay`` 와 숫자가 같다(시험).
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Mapping, Sequence

from auto_trading.backtest.overlay import (
    OverlayBar,
    OverlayConfig,
    OverlayMarketDay,
    OverlayResult,
    OverlayState,
    next_targets,
    step_overlay_day,
    vol_spikes,
    weak_trends,
)

__all__ = [
    "GATE",
    "LOOKBACK_DAYS",
    "block_bootstrap_sharpe_diff",
    "funding_hot",
    "sharpe",
    "simulate_gated",
]

DAY_MS = 86_400_000
GATE = 0.0003
"""8 시간당 0.03% (기본 0.01% 의 3 배). 221 §2."""
LOOKBACK_DAYS = 7


def funding_hot(
    fund_t: Mapping[str, Sequence[int]],
    fund_r: Mapping[str, Sequence[float]],
    day: int,
    gate: float,
    lookback_days: int = LOOKBACK_DAYS,
) -> frozenset[str]:
    """[day − lookback, day) 에 정산된 funding 평균이 ``gate`` 이상인 종목 (정산이 없으면 아님)."""
    out = set()
    for s, times in fund_t.items():
        lo = bisect.bisect_left(times, day - lookback_days * DAY_MS)
        hi = bisect.bisect_left(times, day)
        rates = fund_r[s][lo:hi]
        if rates and statistics.fmean(rates) >= gate:
            out.add(s)
    return frozenset(out)


def simulate_gated(
    spot_bars: Mapping[str, Mapping[int, OverlayBar]],
    perp_bars: Mapping[str, Mapping[int, OverlayBar]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: float,
    cfg: OverlayConfig,
    gate: float = GATE,
) -> tuple[OverlayResult, int]:
    """(결과, funding 으로 끈 (종목, 날) 수). 순서는 ``simulate_overlay`` 와 같다."""
    symbols = sorted(spot_bars)
    result = OverlayResult()
    state = OverlayState.initial(symbols, initial_equity, cfg)
    fund_t = {s: [ms for ms, _ in funding.get(s, ())] for s in symbols}
    fund_r = {s: [r for _, r in funding.get(s, ())] for s in symbols}
    history = {
        s: [spot_bars[s][d].close for d in sorted(spot_bars[s]) if d < from_ms] for s in symbols
    }
    gated = 0
    day = from_ms
    while day <= to_ms:
        prev_day = day - DAY_MS
        for s in symbols:
            for src in (spot_bars, perp_bars):
                if day not in src[s] or prev_day not in src[s]:
                    raise ValueError(f"일봉 결측: {s} {day}")
        rates: dict[str, list[float]] = {}
        for s in symbols:
            lo = bisect.bisect_right(fund_t[s], day - 1)
            hi = bisect.bisect_right(fund_t[s], day + DAY_MS - 1)
            rates[s] = fund_r[s][lo:hi]
        hot = funding_hot(fund_t, fund_r, day, gate) if math.isfinite(gate) else frozenset()
        gated += len(hot)
        market = OverlayMarketDay(
            spot_prev_close={s: spot_bars[s][prev_day].close for s in symbols},
            spot={s: spot_bars[s][day] for s in symbols},
            perp_prev_close={s: perp_bars[s][prev_day].close for s in symbols},
            perp={s: perp_bars[s][day] for s in symbols},
            funding_rates=rates,
            spiked=vol_spikes(history, cfg) | weak_trends(history, cfg) | hot,
        )
        outcome = step_overlay_day(state, day, market, cfg, result)
        for s in symbols:
            history[s].append(market.spot[s].close)
        state.target = next_targets(history, cfg)
        result.days.append(day)
        result.equity.append(outcome.equity)
        result.returns.append(
            outcome.equity / outcome.start_equity - 1 if outcome.start_equity > 0 else 0.0
        )
        result.exposure.append(outcome.exposure)
        day += DAY_MS
    return result, gated


def sharpe(returns: Sequence[float]) -> float:
    """연 샤프 (365 일, 무위험 0)."""
    if len(returns) < 2:
        return math.nan
    sd = statistics.stdev(returns)
    return statistics.fmean(returns) / sd * math.sqrt(365) if sd > 0 else math.nan


def block_bootstrap_sharpe_diff(
    a: Sequence[float],
    b: Sequence[float],
    block: int = 14,
    reps: int = 1000,
    seed: int = 221,
    level: float = 0.90,
) -> tuple[float, float, float]:
    """(점추정, 하한, 상한) — sharpe(a) − sharpe(b), 같은 날 묶음을 짝으로 다시 뽑기."""
    if len(a) != len(b):
        raise ValueError("길이가 다름")
    n = len(a)
    point = sharpe(a) - sharpe(b)
    rng = random.Random(seed)
    starts = list(range(n - block + 1))
    diffs = []
    for _ in range(reps):
        idx: list[int] = []
        while len(idx) < n:
            s = rng.choice(starts)
            idx.extend(range(s, s + block))
        idx = idx[:n]
        d = sharpe([a[i] for i in idx]) - sharpe([b[i] for i in idx])
        if not math.isnan(d):
            diffs.append(d)
    diffs.sort()
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    return point, diffs[int(lo_q * (len(diffs) - 1))], diffs[int(hi_q * (len(diffs) - 1))]
