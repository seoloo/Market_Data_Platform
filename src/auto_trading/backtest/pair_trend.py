"""P1/v1 ETH/BTC 페어 추세(시장 중립) 연구 시뮬레이터 (docs/research/22 사전등록).

비율 R = ETH 종가 / BTC 종가의 20·60·120일 추세 방향으로 ETH 무기한 롱 + BTC 무기한 숏(또는 반대)을
**같은 명목**으로 든다. 크기 = 방향 × min(15% / R의 60일 연변동성, 0.5). 순수 함수, I/O 없음.

하루 ``d``의 순서:

1. 보유 수량으로 두 다리의 ``d-1 → d`` 종가 손익과 그날 정산 funding(롱 지급·숏 수취)을 반영한다.
2. 청산 판정(교차 증거금, 보수적): 두 다리가 동시에 최악 가격(롱 저가·숏 고가)에 닿았다고 보고
   자본 ≤ 명목 × 유지증거금이면 전부 청산한다.
3. 전날 종가에 정한 목표를 ``d`` 종가에 두 다리 모두 체결한다(1일 지연). ETH 다리 비중 기준으로
   |목표 − 현재| < 밴드이고 방향이 같으면 거래하지 않는다. 체결하면 두 다리를 같은 명목으로 맞춘다.
4. ``d`` 종가까지로 다음 목표를 정한다.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = ["PairBar", "PairConfig", "PairResult", "pair_weight", "simulate_pair_trend"]

_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class PairConfig:
    long_symbol: str = "ETHUSDT"
    """비율의 분자. 방향이 양수면 이 종목 롱·분모 종목 숏."""
    short_symbol: str = "BTCUSDT"
    lookbacks: tuple[int, ...] = (20, 60, 120)
    vol_days: int = 60
    target_vol: float = 0.15
    max_weight: float = 0.5
    band: float = 0.05
    cost_per_leg: float = 0.00095
    """다리별 편도: 선물 taker 0.045% + 슬리피지 0.05%."""
    maintenance_margin: float = 0.01


@dataclass(frozen=True, slots=True)
class PairBar:
    high: float
    low: float
    close: float


@dataclass(slots=True)
class PairResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    """그날 체결 뒤 분자 종목 다리의 비중 (양수 = 분자 롱)."""
    price_pnl: float = 0.0
    funding: float = 0.0
    costs: float = 0.0
    trades: int = 0
    liquidations: int = 0
    days_pos: int = 0
    days_neg: int = 0


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def pair_weight(ratios: Sequence[float], cfg: PairConfig) -> float | None:
    """마지막 비율까지로 분자 다리의 목표 비중. 이력 부족이면 None."""
    need = max(max(cfg.lookbacks), cfg.vol_days) + 1
    if len(ratios) < need:
        return None
    last = ratios[-1]
    direction = statistics.fmean(_sign(last / ratios[-1 - k] - 1) for k in cfg.lookbacks)
    steps = [math.log(ratios[-k] / ratios[-k - 1]) for k in range(1, cfg.vol_days + 1)]
    vol = statistics.stdev(steps) * math.sqrt(365)
    if vol <= 0:
        return 0.0
    return direction * min(cfg.target_vol / vol, cfg.max_weight)


def simulate_pair_trend(
    bars: Mapping[str, Mapping[int, PairBar]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: float,
    cfg: PairConfig,
) -> PairResult:
    a, b = cfg.long_symbol, cfg.short_symbol
    result = PairResult()
    qty = {a: 0.0, b: 0.0}
    fund_t = {s: [ms for ms, _ in funding.get(s, ())] for s in (a, b)}
    fund_r = {s: [r for _, r in funding.get(s, ())] for s in (a, b)}
    ratios = [
        bars[a][d].close / bars[b][d].close for d in sorted(bars[a]) if d < from_ms and d in bars[b]
    ]
    target: float | None = None
    equity = initial_equity
    day = from_ms
    while day <= to_ms:
        prev_day = day - _DAY_MS
        for s in (a, b):
            if day not in bars[s] or prev_day not in bars[s]:
                raise ValueError(f"일봉 결측: {s} {day}")
        start_equity = equity
        # 1. 손익·funding
        for s in (a, b):
            bar, prev = bars[s][day], bars[s][prev_day]
            pnl = qty[s] * (bar.close - prev.close)
            result.price_pnl += pnl
            equity += pnl
            if qty[s]:
                lo = bisect.bisect_right(fund_t[s], day - 1)
                hi = bisect.bisect_right(fund_t[s], day + _DAY_MS - 1)
                for k in range(lo, hi):
                    income = -qty[s] * bar.close * fund_r[s][k]
                    result.funding += income
                    equity += income
        # 2. 청산
        if qty[a] or qty[b]:
            worst = start_equity
            notional = 0.0
            for s in (a, b):
                bar, prev = bars[s][day], bars[s][prev_day]
                price = bar.low if qty[s] > 0 else bar.high
                worst += qty[s] * (price - prev.close)
                notional += abs(qty[s]) * price
            if worst <= cfg.maintenance_margin * notional:
                result.liquidations += 1
                equity = max(worst, 0.0)
                qty = {a: 0.0, b: 0.0}
        # 3. 체결
        pa, pb = bars[a][day].close, bars[b][day].close
        if target is not None and equity > 0:
            current = qty[a] * pa / equity
            if abs(target - current) >= cfg.band or _sign(target) != _sign(current):
                new_a = target * equity / pa
                new_b = -target * equity / pb
                cost = (abs(new_a - qty[a]) * pa + abs(new_b - qty[b]) * pb) * cfg.cost_per_leg
                equity -= cost
                result.costs += cost
                result.trades += 1
                qty = {a: new_a, b: new_b}
        weight_now = qty[a] * pa / equity if equity > 0 else 0.0
        result.days_pos += weight_now > 0
        result.days_neg += weight_now < 0
        # 4. 다음 목표
        ratios.append(pa / pb)
        target = pair_weight(ratios, cfg)
        result.days.append(day)
        result.equity.append(equity)
        result.returns.append(equity / start_equity - 1 if start_equity > 0 else 0.0)
        result.weights.append(weight_now)
        day += _DAY_MS
    return result
