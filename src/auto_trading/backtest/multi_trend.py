"""TA/v1 다종목 선물 시계열 추세 연구 시뮬레이터 (docs/research/20 사전등록).

월별 유니버스(상위 20 무기한)의 각 종목에 T 표준형(20·60·120일 부호 평균 × 변동성 목표)을 적용해
롱·숏을 무기한 선물로 든다. 교차 증거금(계좌 하나)으로 계산한다. 순수 함수, I/O 없음.

하루 ``d``(일봉 open_ms)의 순서:

1. 보유 수량으로 ``d-1 → d`` 종가 손익과 그날 정산 funding(롱 지급·숏 수취, 가격은 종가 근사)을
   반영한다. 봉이 없으면(상장폐지) 마지막 종가에 벌점 슬리피지로 강제 정리한다.
2. 청산 판정: 그날 보유 전체가 동시에 최악 가격(롱은 저가·숏은 고가)에 닿았다고 보고,
   자본이 유지증거금(명목 × 1%) 이하면 전부 청산한다 (보수적).
3. 전날 종가에 정한 목표 비중을 ``d`` 종가에 체결한다(1일 지연). 밴드 미만·같은 방향이면 건너뛴다.
4. ``d`` 종가까지의 정보로 다음 목표를 정한다. 유니버스는 **체결일(d+1)이 속한 달**의 것을 쓴다
   (그 달 유니버스는 d까지의 거래대금으로 정해지므로 미래 정보가 아니다). 빠진 종목의 목표는 0.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = ["DayOHLC", "MultiTrendConfig", "MultiTrendResult", "simulate_multi_trend", "weight"]

_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class MultiTrendConfig:
    lookbacks: tuple[int, ...] = (20, 60, 120)
    vol_days: int = 60
    risk_budget: float = 0.30
    """종목별 변동성 목표 = risk_budget / universe_size (T의 15% × 2종목과 같은 총 위험 예산)."""
    universe_size: int = 20
    max_weight: float = 0.5
    gross_cap: float = 2.0
    """총 명목 상한(자본 배수). 레버리지 1~10x 범위 안."""
    band: float = 0.005
    cost_major: float = 0.00095
    """BTC·ETH 편도: 선물 taker 0.045% + 슬리피지 0.05%."""
    cost_alt: float = 0.00145
    """그 밖의 종목: 0.045% + 슬리피지 0.10%."""
    forced_slippage: float = 0.01
    maintenance_margin: float = 0.01
    majors: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")

    @property
    def per_asset_vol(self) -> float:
        return self.risk_budget / self.universe_size


@dataclass(frozen=True, slots=True)
class DayOHLC:
    high: float
    low: float
    close: float


@dataclass(slots=True)
class MultiTrendResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    funding: float = 0.0
    costs: float = 0.0
    trades: int = 0
    liquidations: int = 0
    forced_closes: int = 0
    gross: list[float] = field(default_factory=list)
    symbols_traded: set[str] = field(default_factory=set)


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def weight(closes: Sequence[float], vol_target: float, cfg: MultiTrendConfig) -> float | None:
    """마지막 종가까지로 목표 비중. 이력 부족이면 None."""
    need = max(max(cfg.lookbacks), cfg.vol_days) + 1
    if len(closes) < need:
        return None
    last = closes[-1]
    direction = statistics.fmean(_sign(last / closes[-1 - k] - 1) for k in cfg.lookbacks)
    steps = [math.log(closes[-k] / closes[-k - 1]) for k in range(1, cfg.vol_days + 1)]
    vol = statistics.stdev(steps) * math.sqrt(365)
    if vol <= 0:
        return 0.0
    return direction * min(vol_target / vol, cfg.max_weight)


def _month_start(ms: int) -> int:
    moment = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return int(datetime(moment.year, moment.month, 1, tzinfo=UTC).timestamp() * 1000)


def _history(bars: Mapping[int, DayOHLC], day: int, need: int) -> list[float] | None:
    """``day``까지 연속된 종가 ``need``개. 중간에 빠지면 None."""
    out = []
    for k in range(need - 1, -1, -1):
        bar = bars.get(day - k * _DAY_MS)
        if bar is None:
            return None
        out.append(bar.close)
    return out


def simulate_multi_trend(
    bars: Mapping[str, Mapping[int, DayOHLC]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    universe: Mapping[int, Sequence[str]],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: float,
    cfg: MultiTrendConfig,
) -> MultiTrendResult:
    result = MultiTrendResult()
    qty: dict[str, float] = {}
    last_close: dict[str, float] = {}
    targets: dict[str, float] = {}
    fund_t = {s: [ms for ms, _ in v] for s, v in funding.items()}
    fund_r = {s: [r for _, r in v] for s, v in funding.items()}
    need = max(max(cfg.lookbacks), cfg.vol_days) + 1
    equity = initial_equity
    day = from_ms
    while day <= to_ms:
        start_equity = equity
        # 1. 손익·funding·상장폐지
        for s in list(qty):
            bar = bars.get(s, {}).get(day)
            if bar is None:
                penalty = abs(qty[s]) * last_close[s] * cfg.forced_slippage
                equity -= penalty
                result.costs += penalty
                result.forced_closes += 1
                del qty[s]
                continue
            pnl = qty[s] * (bar.close - last_close[s])
            if qty[s] > 0:
                result.long_pnl += pnl
            else:
                result.short_pnl += pnl
            equity += pnl
            times = fund_t.get(s, [])
            lo = bisect.bisect_right(times, day - 1)
            hi = bisect.bisect_right(times, day + _DAY_MS - 1)
            for k in range(lo, hi):
                income = -qty[s] * bar.close * fund_r[s][k]
                result.funding += income
                equity += income
        # 2. 청산 (교차 증거금, 모든 포지션이 동시에 최악 가격)
        if qty:
            worst = start_equity
            notional = 0.0
            for s, q in qty.items():
                bar = bars[s][day]
                price = bar.low if q > 0 else bar.high
                worst += q * (price - last_close[s])
                notional += abs(q) * price
            if worst <= cfg.maintenance_margin * notional:
                result.liquidations += 1
                equity = max(worst, 0.0)
                qty.clear()
        # 3. 체결 (전날 목표를 오늘 종가에)
        for s in sorted(set(qty) | set(targets)):
            bar = bars.get(s, {}).get(day)
            if bar is None:
                continue
            want = targets.get(s, 0.0)
            current = qty.get(s, 0.0) * bar.close / equity if equity > 0 else 0.0
            if abs(want - current) < cfg.band and _sign(want) == _sign(current):
                continue
            new_qty = want * equity / bar.close
            cost_rate = cfg.cost_major if s in cfg.majors else cfg.cost_alt
            cost = abs(new_qty - qty.get(s, 0.0)) * bar.close * cost_rate
            equity -= cost
            result.costs += cost
            result.trades += 1
            if new_qty == 0:
                qty.pop(s, None)
            else:
                qty[s] = new_qty
                result.symbols_traded.add(s)
        for s in qty:
            last_close[s] = bars[s][day].close
        # 4. 다음 목표
        members = universe.get(_month_start(day + _DAY_MS), ())
        raw: dict[str, float] = {}
        for s in members:
            closes = _history(bars.get(s, {}), day, need)
            w = None if closes is None else weight(closes, cfg.per_asset_vol, cfg)
            if w:
                raw[s] = w
        gross = sum(abs(w) for w in raw.values())
        scale = cfg.gross_cap / gross if gross > cfg.gross_cap else 1.0
        targets = {s: w * scale for s, w in raw.items()}
        result.days.append(day)
        result.equity.append(equity)
        result.returns.append(equity / start_equity - 1 if start_equity > 0 else 0.0)
        result.gross.append(
            sum(abs(q) * bars[s][day].close for s, q in qty.items()) / equity if equity > 0 else 0
        )
        day += _DAY_MS
    return result
