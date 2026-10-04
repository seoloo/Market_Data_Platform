"""GR — 추세 켜짐 롱 그리드 + 따라 올라가는 손절 (docs/research/137). 순수 함수, I/O 없음.

1분봉(시가·고가·저가·종가)으로 그리드 체결을 재생한다. 하루 단위 일은 날이 바뀌는 첫 분에 한다.

* 추세(T/v2 식, 20·60·120 일 부호 평균 > 0)는 전날 종가까지로 정하고 그날 첫 분부터 쓴다.
* 켜짐: 그 분 시가를 중심 c 로 아래 10 칸 ``c(1 − k·s)``, 칸 간격 ``s = max(0.3%, 0.5σ)``.
* 칸에서 산 수량은 한 칸 위 ``L_k(1 + s)`` 에 판다. 가격이 **넘어가야** 체결(같으면 미체결).
* 같은 분에 산 수량은 그 분에 팔지 않고, 그 분에 판 칸은 그 분에 다시 사지 않는다.
* 손절선 ``c(1 − 11·s)``: 저가가 닿으면 지나간 칸을 먼저 사고(최악),
  전부 ``min(손절선, 시가)`` 에 판다.
* 들고 있는 것이 없고 종가가 ``c(1 + s)`` 이상이면 c 를 그 종가로 올린다(내리지 않음).
* 꺼짐: 그 분 시가에 전부 판다. 손절 뒤 7 일은 다시 깔지 않는다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "GridConfig",
    "GridResult",
    "MinuteBars",
    "daily_closes",
    "hold_trend",
    "simulate_grid",
    "trend_and_sigma",
]

DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class MinuteBars:
    """같은 길이의 목록 — 1분봉 시작 시각(ms)·시가·고가·저가·종가, 시각 오름차순."""

    t: Sequence[int]
    o: Sequence[float]
    h: Sequence[float]
    low: Sequence[float]
    c: Sequence[float]


@dataclass(frozen=True, slots=True)
class GridConfig:
    levels: int = 10
    vol_mult: float = 0.5
    min_step: float = 0.003
    vol_days: int = 20
    lookbacks: tuple[int, ...] = (20, 60, 120)
    grid_fee: float = 0.0005
    stop_fee: float = 0.00139
    exit_fee: float = 0.0005
    use_trend: bool = True
    use_stop: bool = True
    cooldown_days: int = 7


@dataclass(slots=True)
class GridResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)
    activations: int = 0
    recenters: int = 0
    stops: int = 0
    trend_exits: int = 0
    round_trips: int = 0
    buys: int = 0
    sells: int = 0
    fees: float = 0.0
    realized_grid: float = 0.0
    """끝난 왕복(칸 매수 → 한 칸 위 매도)의 차익 합, 비용 뺌 — 영상식 '수익' 표시."""
    stop_pnl: list[float] = field(default_factory=list)
    """손절마다 정리한 재고의 손익(비용 뺌)."""
    exit_pnl: list[float] = field(default_factory=list)
    """추세 꺼짐 정리마다 재고의 손익(비용 뺌)."""


def daily_closes(bars: MinuteBars) -> dict[int, float]:
    """UTC 날마다 마지막 1분봉 종가."""
    out: dict[int, float] = {}
    for t, c in zip(bars.t, bars.c, strict=True):
        out[t - t % DAY_MS] = c
    return out


def trend_and_sigma(
    closes: Mapping[int, float], cfg: GridConfig
) -> tuple[dict[int, bool], dict[int, float]]:
    """{날 D: D 에 쓸 추세 켜짐}, {날 D: D 에 쓸 σ} — 둘 다 D−1 종가까지로 계산한다."""
    days = sorted(closes)
    values = [closes[d] for d in days]
    need = max(cfg.lookbacks) + 1
    trend: dict[int, bool] = {}
    sigma: dict[int, float] = {}
    for i in range(1, len(days) + 1):
        upto = values[:i]  # 날 days[i-1] 종가까지
        apply_day = days[i - 1] + DAY_MS
        if len(upto) >= need:
            last = upto[-1]
            direction = statistics.fmean(
                (last / upto[-1 - k] > 1) - (last / upto[-1 - k] < 1) for k in cfg.lookbacks
            )
            trend[apply_day] = direction > 0
        if len(upto) >= cfg.vol_days + 1:
            rets = [math.log(upto[-j] / upto[-j - 1]) for j in range(1, cfg.vol_days + 1)]
            sigma[apply_day] = statistics.stdev(rets)
    return trend, sigma


def _mark(result: GridResult, day: int, cash: float, qty: float, price: float) -> None:
    value = qty * price
    equity = cash + value
    result.days.append(day)
    result.equity.append(equity)
    result.exposure.append(value / equity if equity > 0 else 0.0)


def _fill_gaps(result: GridResult, from_day: int, to_day: int, start: float) -> GridResult:
    """1분봉이 하나도 없는 날(거래소 점검)은 전날 값으로 채워 역일 목록을 만든다."""
    by_day = dict(zip(result.days, zip(result.equity, result.exposure, strict=True), strict=True))
    days, equity, exposure = [], [], []
    last = (start, 0.0)
    for d in range(from_day, to_day + 1, DAY_MS):
        last = by_day.get(d, last)
        days.append(d)
        equity.append(last[0])
        exposure.append(last[1])
    result.days, result.equity, result.exposure = days, equity, exposure
    return result


def simulate_grid(
    bars: MinuteBars,
    trend: Mapping[int, bool],
    sigma: Mapping[int, float],
    *,
    from_ms: int,
    to_ms: int,
    initial: float,
    cfg: GridConfig,
) -> GridResult:
    """``[from_ms, to_ms]`` 의 UTC 날들을 재생한다. 날마다 종가로 평가한 자본을 남긴다."""
    n = cfg.levels
    res = GridResult()
    cash = initial
    units = [0.0] * n
    basis = [0.0] * n
    bought_at = [-1] * n
    sold_at = [-1] * n
    level = [0.0] * n
    active = False
    c = s = size = 0.0
    cooldown_until = -1
    day_cur: int | None = None
    last_price = 0.0
    end = to_ms + DAY_MS

    def held() -> float:
        return sum(units)

    def sell_all(price: float, fee: float) -> float:
        nonlocal cash
        proceeds = 0.0
        cost = 0.0
        for k in range(n):
            if units[k] > 0:
                gross = units[k] * price
                f = gross * fee
                res.fees += f
                proceeds += gross - f
                cost += basis[k]
                res.sells += 1
                units[k] = 0.0
                basis[k] = 0.0
        cash += proceeds
        return proceeds - cost

    def buy(k: int, i: int) -> None:
        nonlocal cash
        notional = min(size, cash / (1 + cfg.grid_fee))
        if notional <= 1e-9:
            return
        f = notional * cfg.grid_fee
        cash -= notional + f
        res.fees += f
        units[k] = notional / level[k]
        basis[k] = notional + f
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
                _mark(res, day_cur, cash, held(), last_price)
            day_cur = day
            on = trend.get(day, False) if cfg.use_trend else True
            if active and not on:
                if held() > 0:
                    res.exit_pnl.append(sell_all(op, cfg.exit_fee))
                active = False
                res.trend_exits += 1
            if not active and on and t >= cooldown_until and day in sigma:
                s = max(cfg.min_step, cfg.vol_mult * sigma[day])
                c = op
                level = [c * (1 - (k + 1) * s) for k in range(n)]
                size = cash / n
                bought_at = [-1] * n
                sold_at = [-1] * n
                active = True
                res.activations += 1
        if active:
            stop = c * (1 - (n + 1) * s)
            if cfg.use_stop and lo <= stop:
                for k in range(n):
                    if units[k] == 0 and sold_at[k] != i and lo < level[k]:
                        buy(k, i)
                res.stop_pnl.append(sell_all(min(stop, op), cfg.stop_fee))
                active = False
                res.stops += 1
                cooldown_until = t + cfg.cooldown_days * DAY_MS
            else:
                for k in range(n):
                    if units[k] > 0 and bought_at[k] != i:
                        target = level[k] * (1 + s)
                        if hi > target:
                            gross = units[k] * target
                            f = gross * cfg.grid_fee
                            res.fees += f
                            cash += gross - f
                            res.realized_grid += gross - f - basis[k]
                            res.round_trips += 1
                            res.sells += 1
                            units[k] = 0.0
                            basis[k] = 0.0
                            sold_at[k] = i
                for k in range(n):
                    if units[k] == 0 and sold_at[k] != i and lo < level[k]:
                        buy(k, i)
                if held() == 0 and cl >= c * (1 + s):
                    c = cl
                    level = [c * (1 - (k + 1) * s) for k in range(n)]
                    size = cash / n
                    res.recenters += 1
        last_price = cl
    if day_cur is not None:
        _mark(res, day_cur, cash, held(), last_price)
    return _fill_gaps(res, from_ms - from_ms % DAY_MS, to_ms - to_ms % DAY_MS, initial)


def hold_trend(
    bars: MinuteBars,
    trend: Mapping[int, bool] | None,
    *,
    from_ms: int,
    to_ms: int,
    initial: float,
    fee: float,
) -> GridResult:
    """TH(``trend`` 있음): 켜지는 날 첫 분 시가에 전부 사고 꺼지는 날 첫 분 시가에 전부 판다.
    BH(``trend`` 없음): 첫 분 시가에 사서 끝까지 든다."""
    res = GridResult()
    cash, qty = initial, 0.0
    day_cur: int | None = None
    last_price = 0.0
    end = to_ms + DAY_MS
    for i, t in enumerate(bars.t):
        if t < from_ms:
            continue
        if t >= end:
            break
        day = t - t % DAY_MS
        if day != day_cur:
            if day_cur is not None:
                _mark(res, day_cur, cash, qty, last_price)
            day_cur = day
            op = bars.o[i]
            on = True if trend is None else trend.get(day, False)
            if on and qty == 0 and cash > 0:
                f = cash * fee / (1 + fee)
                qty = (cash - f) / op
                res.fees += f
                cash = 0.0
                res.buys += 1
            elif not on and qty > 0:
                gross = qty * op
                f = gross * fee
                cash += gross - f
                res.fees += f
                qty = 0.0
                res.sells += 1
        last_price = bars.c[i]
    if day_cur is not None:
        _mark(res, day_cur, cash, qty, last_price)
    return _fill_gaps(res, from_ms - from_ms % DAY_MS, to_ms - to_ms % DAY_MS, initial)
