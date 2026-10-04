"""PG — 업비트 USDT/원화 프리미엄 그리드 시뮬레이터 (docs/research/138). 순수 함수, I/O 없음.

원화 USDT = 환율 × (1 + 김치 프리미엄).
중심을 **전날 공식 환율 × (1 + 최근 30 일 평균 프리미엄)** 에
매일 붙여 두고, 그 아래 8 칸에서 산 수량을 각자 매수가 × (1 + g) 에 판다.

* 칸 가격은 호가 단위로 내림, 매도 목표는 올림. 가격이 **넘어가야** 체결(같은 값 터치는 미체결).
* 같은 분에 산 수량은 그 분에 팔지 않고, 그 분에 비운 칸은 그 분에 다시 사지 않는다.
* 손절선 ``c(1 − 9g)``: 저가가 닿으면 지나간 빈 칸을 먼저 사고(최악)
  전부 ``min(손절선, 시가) − 1 호가`` 에 판다.
  다음 날까지 새로 사지 않는다.
* ``recenter_daily=False`` (PA, 참고): 중심을 첫 분 시가에 두고,
  보유가 없고 종가가 한 칸 위면 올리기만.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from auto_trading.backtest.grid import MinuteBars, daily_closes

__all__ = ["PremiumGridConfig", "PremiumGridResult", "centers", "simulate_premium_grid"]

DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class PremiumGridConfig:
    levels: int = 8
    step: float = 0.0025
    fee: float = 0.0005
    stop_fee: float = 0.0005
    tick: float = 1.0
    through: float = 0.0
    """체결에 필요한 초과폭(원). 0 = 칸을 넘어가기만 하면(엄격 부등호). 참고 줄은 1 (≥ 2 원)."""
    recenter_daily: bool = True
    use_stop: bool = True


@dataclass(slots=True)
class PremiumGridResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)
    round_trips: int = 0
    buys: int = 0
    sells: int = 0
    stops: int = 0
    recenters: int = 0
    fees: float = 0.0
    turnover: float = 0.0
    realized_grid: float = 0.0
    """끝난 왕복의 차익 합(비용 뺌)."""
    trip_pnl: list[float] = field(default_factory=list)
    stop_pnl: list[float] = field(default_factory=list)


def centers(
    bars: MinuteBars, fx: Mapping[str, float], avg_days: int = 30
) -> tuple[dict[int, float], dict[int, float]]:
    """({날 D: 중심}, {날 d: 프리미엄 P_d}). ``fx`` 는 {YYYY-MM-DD: 원/달러}.

    중심 c_D = (D−1 이하 마지막 환율) × (1 + D−1 까지 최근 ``avg_days`` 개 P_d 평균).
    """
    fx_days = sorted(fx)
    closes = daily_closes(bars)

    def fx_on_or_before(day_ms: int) -> float | None:
        key = _ymd(day_ms)
        i = bisect_right(fx_days, key)
        return fx[fx_days[i - 1]] if i else None

    premium: dict[int, float] = {}
    for d in sorted(closes):
        rate = fx_on_or_before(d)
        if rate:
            premium[d] = closes[d] / rate - 1
    pdays = sorted(premium)
    out: dict[int, float] = {}
    for j in range(avg_days, len(pdays) + 1):
        window = pdays[j - avg_days : j]
        day = window[-1] + DAY_MS  # 마지막 P 다음 날에 쓴다
        rate = fx_on_or_before(day - DAY_MS)
        if rate:
            out[day] = rate * (1 + statistics.fmean(premium[d] for d in window))
    return out, premium


def _ymd(day_ms: int) -> str:
    return datetime.fromtimestamp(day_ms / 1000, UTC).strftime("%Y-%m-%d")


def simulate_premium_grid(
    bars: MinuteBars,
    center_by_day: Mapping[int, float],
    *,
    from_ms: int,
    to_ms: int,
    initial: float,
    cfg: PremiumGridConfig,
) -> PremiumGridResult:
    n, g, tick = cfg.levels, cfg.step, cfg.tick
    res = PremiumGridResult()
    cash = initial
    qty = [0.0] * n
    basis = [0.0] * n
    target = [0.0] * n
    bought_at = [-1] * n
    emptied_at = [-1] * n
    c: float | None = None
    size = 0.0
    blocked = False
    day_cur: int | None = None
    last_price = 0.0
    end = to_ms + DAY_MS
    marks: dict[int, tuple[float, float]] = {}

    def floor_t(x: float) -> float:
        return math.floor(x / tick + 1e-9) * tick

    def ceil_t(x: float) -> float:
        return math.ceil(x / tick - 1e-9) * tick

    def levels() -> list[float]:
        assert c is not None
        return [floor_t(c * (1 - (k + 1) * g)) for k in range(n)]

    def held() -> float:
        return sum(qty)

    def buy(k: int, price: float, i: int) -> None:
        nonlocal cash
        notional = min(size, cash / (1 + cfg.fee))
        if notional <= 1e-6:
            return
        f = notional * cfg.fee
        cash -= notional + f
        res.fees += f
        res.turnover += notional
        qty[k] = notional / price
        basis[k] = notional + f
        target[k] = ceil_t(price * (1 + g))
        bought_at[k] = i
        res.buys += 1

    for i, t in enumerate(bars.t):
        if t < from_ms:
            continue
        if t >= end:
            break
        op, hi, lo, cl = bars.o[i], bars.h[i], bars.low[i], bars.c[i]
        day = t - t % DAY_MS
        if day != day_cur:
            if day_cur is not None:
                value = held() * last_price
                marks[day_cur] = (cash + value, value / (cash + value) if cash + value else 0.0)
            day_cur = day
            blocked = False
            equity_now = cash + held() * op
            if cfg.recenter_daily:
                c = center_by_day.get(day)
                size = equity_now / n
            elif c is None:
                c = op
                size = equity_now / n
        if c is not None:
            stop = c * (1 - (n + 1) * g)
            if cfg.use_stop and lo <= stop and (not blocked or held() > 0):
                for k, lvl in enumerate(levels()):
                    if (
                        not blocked
                        and qty[k] == 0
                        and emptied_at[k] != i
                        and lvl >= stop
                        and lo < lvl - cfg.through
                    ):
                        buy(k, lvl, i)
                fill = min(stop, op) - tick
                pnl = 0.0
                sold = False
                for k in range(n):
                    if qty[k] > 0:
                        gross = qty[k] * fill
                        f = gross * cfg.stop_fee
                        res.fees += f
                        res.turnover += gross
                        cash += gross - f
                        pnl += gross - f - basis[k]
                        qty[k] = basis[k] = 0.0
                        emptied_at[k] = i
                        res.sells += 1
                        sold = True
                if sold:
                    res.stop_pnl.append(pnl)
                res.stops += 1
                blocked = True
            else:
                for k in range(n):
                    if qty[k] > 0 and bought_at[k] != i and hi > target[k] + cfg.through:
                        gross = qty[k] * target[k]
                        f = gross * cfg.fee
                        res.fees += f
                        res.turnover += gross
                        cash += gross - f
                        trip = gross - f - basis[k]
                        res.realized_grid += trip
                        res.trip_pnl.append(trip)
                        res.round_trips += 1
                        res.sells += 1
                        qty[k] = basis[k] = 0.0
                        emptied_at[k] = i
                if not blocked:
                    for k, lvl in enumerate(levels()):
                        if qty[k] == 0 and emptied_at[k] != i and lo < lvl - cfg.through:
                            buy(k, lvl, i)
                if not cfg.recenter_daily and held() == 0 and cl >= c * (1 + g):
                    c = cl
                    size = cash / n
                    res.recenters += 1
        last_price = cl
    if day_cur is not None:
        value = held() * last_price
        marks[day_cur] = (cash + value, value / (cash + value) if cash + value else 0.0)
    last = (initial, 0.0)
    for d in range(from_ms - from_ms % DAY_MS, to_ms - to_ms % DAY_MS + 1, DAY_MS):
        last = marks.get(d, last)
        res.days.append(d)
        res.equity.append(last[0])
        res.exposure.append(last[1])
    return res
