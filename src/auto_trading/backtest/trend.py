"""T/v1 시계열 추세 추종 연구 시뮬레이터 (docs/research/15 사전등록).

종목마다 일봉 종가로 20·60·120일 수익률 부호의 평균을 방향으로, 60일 실현변동성으로 크기를 정한다.
롱은 현물 보유(funding 없음), 숏은 선물 1x(funding 반영)로 계산한다. 순수 함수이며 I/O가 없다.

하루 ``d``의 순서:

1. 보유 수량에 ``d-1 → d`` 가격 변화를 반영한다. 숏은 ``(d-1, d]``에 정산된 funding을 받거나 낸다.
2. 전날 종가에 정한 목표 비중을 ``d`` 종가에 체결한다 (1일 지연).
   변화가 밴드보다 작고 방향이 같으면 건너뛴다.
3. 선물 숏은 ``d`` 고가로 청산을 판정한다 (1x: 기준가의 약 2배).
4. ``d`` 종가까지의 정보로 다음 목표 비중을 계산한다.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = ["DailyBar", "TrendConfig", "TrendResult", "simulate_trend", "target_weight"]

_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class TrendConfig:
    lookbacks: tuple[int, ...] = (20, 60, 120)
    vol_days: int = 60
    target_vol: float = 0.15
    """종목별 연 변동성 목표."""
    max_weight: float = 0.5
    """종목별 |비중| 상한 (자본 대비 명목). 두 종목 합이 1을 넘지 않아 레버리지가 없다."""
    band: float = 0.05
    spot_cost: float = 0.0015
    """현물 수수료 0.10% + 슬리피지 0.05% (편도, 거래 명목 대비)."""
    perp_cost: float = 0.00095
    """선물 수수료 0.045% + 슬리피지 0.05%."""
    maintenance_margin: float = 0.004
    long_only: bool = False
    """True면 보고용 변형: 숏 신호를 현금(0)으로 바꾼다."""


@dataclass(frozen=True, slots=True)
class DailyBar:
    high: float
    close: float


@dataclass(slots=True)
class TrendResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    funding: float = 0.0
    costs: float = 0.0
    traded_notional: float = 0.0
    trades: int = 0
    liquidations: int = 0
    days_long: dict[str, int] = field(default_factory=dict)
    days_short: dict[str, int] = field(default_factory=dict)


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def target_weight(closes: Sequence[float], cfg: TrendConfig) -> float | None:
    """마지막 값까지의 종가로 목표 비중. 이력이 부족하면 None."""
    need = max(max(cfg.lookbacks), cfg.vol_days) + 1
    if len(closes) < need:
        return None
    last = closes[-1]
    direction = statistics.fmean(_sign(last / closes[-1 - k] - 1) for k in cfg.lookbacks)
    if cfg.long_only:
        direction = max(direction, 0.0)
    steps = [math.log(closes[-k] / closes[-k - 1]) for k in range(1, cfg.vol_days + 1)]
    vol = statistics.stdev(steps) * math.sqrt(365)
    if vol <= 0:
        return 0.0
    return direction * min(cfg.target_vol / vol, cfg.max_weight)


def simulate_trend(
    bars: Mapping[str, Mapping[int, DailyBar]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: float,
    cfg: TrendConfig,
) -> TrendResult:
    """``[from_ms, to_ms]``의 일봉(open_ms 기준)을 재생한다. 이전 이력은 신호에만 쓴다."""
    symbols = sorted(bars)
    result = TrendResult()
    qty = dict.fromkeys(symbols, 0.0)
    entry = dict.fromkeys(symbols, 0.0)
    target: dict[str, float] = {}
    fund_times = {s: [ms for ms, _ in funding.get(s, ())] for s in symbols}
    fund_rates = {s: [r for _, r in funding.get(s, ())] for s in symbols}
    history: dict[str, list[float]] = {}
    for s in symbols:
        history[s] = [bars[s][d].close for d in sorted(bars[s]) if d < from_ms]
        result.days_long[s] = result.days_short[s] = 0
    equity = initial_equity
    day = from_ms
    while day <= to_ms:
        if any(day not in bars[s] or day - _DAY_MS not in bars[s] for s in symbols):
            raise ValueError(f"일봉 결측: {day}")
        start_equity = equity
        for s in symbols:
            prev, bar = bars[s][day - _DAY_MS], bars[s][day]
            pnl = qty[s] * (bar.close - prev.close)
            if qty[s] > 0:
                result.long_pnl += pnl
            elif qty[s] < 0:
                result.short_pnl += pnl
                # 전날 종가(day − 1ms) 뒤부터 이날 종가까지 정산된 funding
                lo = bisect.bisect_right(fund_times[s], day - 1)
                hi = bisect.bisect_right(fund_times[s], day + _DAY_MS - 1)
                for k in range(lo, hi):
                    # 숏은 양수 funding을 받는다. 정산 가격은 당일 종가로 근사한다.
                    income = -qty[s] * bar.close * fund_rates[s][k]
                    result.funding += income
                    equity += income
            equity += pnl
        for s in symbols:
            bar = bars[s][day]
            want = target.get(s)
            if want is not None:
                current = qty[s] * bar.close / equity if equity > 0 else 0.0
                if abs(want - current) >= cfg.band or _sign(want) != _sign(current):
                    new_qty = want * equity / bar.close
                    cost = _trade_cost(qty[s], new_qty, bar.close, cfg)
                    result.traded_notional += abs(new_qty - qty[s]) * bar.close
                    result.costs += cost
                    result.trades += 1
                    equity -= cost
                    if new_qty < 0 and (qty[s] >= 0 or new_qty < qty[s]):
                        entry[s] = bar.close
                    qty[s] = new_qty
            if qty[s] < 0:
                result.days_short[s] += 1
                margin = -qty[s] * entry[s]
                loss = -qty[s] * (bar.high - entry[s])
                if margin - loss <= cfg.maintenance_margin * -qty[s] * bar.high:
                    # 증거금 전액 손실. 진입 뒤 종가까지의 손실은 이미 반영돼 있다.
                    result.liquidations += 1
                    booked = -qty[s] * (bar.close - entry[s])
                    equity -= max(margin - booked, 0.0)
                    qty[s] = 0.0
            elif qty[s] > 0:
                result.days_long[s] += 1
            history[s].append(bar.close)
            target[s] = target_weight(history[s], cfg) or 0.0
        result.days.append(day)
        result.equity.append(equity)
        result.returns.append(equity / start_equity - 1 if start_equity > 0 else 0.0)
        day += _DAY_MS
    return result


def _trade_cost(old: float, new: float, price: float, cfg: TrendConfig) -> float:
    """롱 쪽 변화는 현물 비용, 숏 쪽 변화는 선물 비용. 0을 가로지르면 둘로 나눈다."""
    long_change = abs(max(new, 0.0) - max(old, 0.0))
    short_change = abs(min(new, 0.0) - min(old, 0.0))
    return price * (long_change * cfg.spot_cost + short_change * cfg.perp_cost)
