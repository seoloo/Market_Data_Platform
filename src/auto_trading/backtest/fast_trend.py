"""F1 빠른 추세 롱숏 — BTC·ETH 무기한 1x 연구 시뮬레이터 (docs/research/45 사전등록).

T(15·16)와 같은 크기 규칙을 더 빠른 lookback(10·20·40일)으로 쓰고,
**롱·숏 모두 USD-M 무기한**으로 든다.
신호는 현물 일봉 종가(T 와 같은 계열), 손익은 무기한 종가. 순수 함수, I/O 없음.

하루 ``d``의 순서:

1. 무기한 ``d-1 → d`` 종가 손익. ``(d-1, d]``에 정산된 funding: 롱은 양수 funding 을 내고
   숏은 받는다 (정산 가격은 그날 종가로 근사).
2. 비상 스톱: 롱은 저가 ≤ 전날 종가 × (1 − s), 숏은 고가 ≥ 전날 종가 × (1 + s) 이면 스톱가에서
   불리폭만큼 더 나쁘게 닫는다. 발동 판정 가격은 최종 체결가(기본, 보수적) 또는 mark price.
   스톱이 난 종목은 그날 다시 들지 않는다.
3. 청산 판정: 봉 안 최악 자본(판정 가격 = mark price 기본)이 유지증거금 이하이면 전액 손실.
4. 계좌 정지: 봉 안 최악 자본(mark price — 실제 계좌 자본 평가와 같다)이 종가 기준 고점의
   ``1 − halt_drawdown`` 이하면
   그날 종가에 전부 닫고 이후 거래하지 않는다. 최종 체결가 기준 최악 낙폭은 따로 보고한다.
5. 전날 종가에 정한 목표 비중을 ``d`` 종가에 체결한다(1일 지연, 밴드 5%p, 방향이 바뀌면 항상 체결).
6. ``d`` 현물 종가까지로 다음 목표를 정한다.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from auto_trading.backtest.trend import TrendConfig, target_weight

__all__ = ["F1B", "FastBar", "FastTrendConfig", "FastTrendResult", "simulate_fast_trend"]

_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class FastTrendConfig:
    lookbacks: tuple[int, ...] = (10, 20, 40)
    vol_days: int = 60
    target_vol: float = 0.15
    max_weight: float = 0.5
    band: float = 0.05
    perp_cost: float = 0.00095
    """편도, 명목 대비. 판정 기준 비용(42 §8). 참고 줄은 0.00023."""
    stop: float | None = 0.20
    stop_slippage: float = 0.01
    stop_on_mark: bool = False
    """False(기본)면 스톱 발동을 최종 체결가 고저로 판정한다(보수적 — 실거래는 mark price)."""
    liquidate_on_mark: bool = True
    maintenance_margin: float = 0.005
    halt_drawdown: float = 0.30

    def signal(self) -> TrendConfig:
        return TrendConfig(
            lookbacks=self.lookbacks,
            vol_days=self.vol_days,
            target_vol=self.target_vol,
            max_weight=self.max_weight,
        )


F1B = FastTrendConfig()
"""F1b: 10·20·40일, 1x, ±20% 비상 스톱 (docs/research/45)."""


@dataclass(frozen=True, slots=True)
class FastBar:
    """무기한 일봉(최종 체결가 고·저·종) + 그날 mark price 고·저."""

    high: float
    low: float
    close: float
    mark_high: float
    mark_low: float


@dataclass(slots=True)
class FastTrendResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    long_funding: float = 0.0
    short_funding: float = 0.0
    long_costs: float = 0.0
    short_costs: float = 0.0
    trades: int = 0
    traded_notional: float = 0.0
    stop_hits_long: int = 0
    stop_hits_short: int = 0
    stop_days: list[tuple[int, str, str]] = field(default_factory=list)
    """(날, 종목, long|short)."""
    liquidations: int = 0
    halted_day: int | None = None
    worst_intraday_drawdown: float = 0.0
    """mark price 기준 (계좌 정지 판정과 같다)."""
    worst_intraday_drawdown_last: float = 0.0
    """보고용: 최종 체결가 고저 기준 (순간 윅 포함)."""
    days_long: int = 0
    days_short: int = 0

    @property
    def short_net(self) -> float:
        """숏 다리 순손익 = 가격 + funding − 비용 (45 F3)."""
        return self.short_pnl + self.short_funding - self.short_costs


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _book(result: FastTrendResult, qty: float, amount: float) -> None:
    if qty > 0:
        result.long_pnl += amount
    else:
        result.short_pnl += amount


def _cost(result: FastTrendResult, old: float, new: float, price: float, rate: float) -> float:
    long_change = abs(max(new, 0.0) - max(old, 0.0)) * price * rate
    short_change = abs(min(new, 0.0) - min(old, 0.0)) * price * rate
    result.long_costs += long_change
    result.short_costs += short_change
    return long_change + short_change


def _worst_equity(
    equity: float, qty: Mapping[str, float], today: Mapping[str, FastBar], *, on_mark: bool
) -> float:
    """봉 안 최악 자본: 남은 포지션을 불리한 극값으로 (스톱 난 것은 이미 체결가로 반영)."""
    value = equity
    for s, q in qty.items():
        if not q:
            continue
        bar = today[s]
        if q > 0:
            extreme = bar.mark_low if on_mark else bar.low
        else:
            extreme = bar.mark_high if on_mark else bar.high
        value += q * (extreme - bar.close)
    return value


def simulate_fast_trend(
    signal_closes: Mapping[str, Mapping[int, float]],
    bars: Mapping[str, Mapping[int, FastBar]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: float,
    cfg: FastTrendConfig,
) -> FastTrendResult:
    symbols = sorted(bars)
    sig_cfg = cfg.signal()
    result = FastTrendResult()
    qty = dict.fromkeys(symbols, 0.0)
    target: dict[str, float] = {}
    fund_t = {s: [ms for ms, _ in funding.get(s, ())] for s in symbols}
    fund_r = {s: [r for _, r in funding.get(s, ())] for s in symbols}
    history = {
        s: [signal_closes[s][d] for d in sorted(signal_closes[s]) if d < from_ms] for s in symbols
    }
    equity = peak = initial_equity
    halted = False
    day = from_ms
    while day <= to_ms:
        prev_day = day - _DAY_MS
        for s in symbols:
            if day not in bars[s] or prev_day not in bars[s] or day not in signal_closes[s]:
                raise ValueError(f"일봉 결측: {s} {day}")
        start_equity = equity
        prev_close = {s: bars[s][prev_day].close for s in symbols}
        # 1. 손익·funding
        for s in symbols:
            q, bar = qty[s], bars[s][day]
            if not q:
                continue
            pnl = q * (bar.close - prev_close[s])
            _book(result, q, pnl)
            equity += pnl
            lo = bisect.bisect_right(fund_t[s], day - 1)
            hi = bisect.bisect_right(fund_t[s], day + _DAY_MS - 1)
            for rate in fund_r[s][lo:hi]:
                paid = q * bar.close * rate  # 롱은 양수 funding 을 낸다, 숏은 받는다
                equity -= paid
                if q > 0:
                    result.long_funding -= paid
                else:
                    result.short_funding -= paid
        # 2. 비상 스톱
        stopped: set[str] = set()
        if cfg.stop is not None:
            for s in symbols:
                q, bar = qty[s], bars[s][day]
                if not q:
                    continue
                if q > 0:
                    trig = prev_close[s] * (1 - cfg.stop)
                    hit = (bar.mark_low if cfg.stop_on_mark else bar.low) <= trig
                    fill = trig * (1 - cfg.stop_slippage)
                else:
                    trig = prev_close[s] * (1 + cfg.stop)
                    hit = (bar.mark_high if cfg.stop_on_mark else bar.high) >= trig
                    fill = trig * (1 + cfg.stop_slippage)
                if not hit:
                    continue
                adjust = q * (fill - bar.close)  # 종가 손익을 스톱 체결가 손익으로 바꾼다
                _book(result, q, adjust)
                equity += adjust
                equity -= _cost(result, q, 0.0, fill, cfg.perp_cost)
                if q > 0:
                    result.stop_hits_long += 1
                else:
                    result.stop_hits_short += 1
                result.stop_days.append((day, s, "long" if q > 0 else "short"))
                qty[s] = 0.0
                stopped.add(s)
        # 3. 청산
        today = {s: bars[s][day] for s in symbols}
        liq_equity = _worst_equity(equity, qty, today, on_mark=cfg.liquidate_on_mark)
        notional = sum(abs(qty[s]) * bars[s][day].close for s in symbols)
        if notional > 0 and liq_equity <= cfg.maintenance_margin * notional:
            result.liquidations += 1
            equity = 0.0
            qty = dict.fromkeys(symbols, 0.0)
            halted = True
        # 4. 계좌 정지
        worst = _worst_equity(equity, qty, today, on_mark=True) if not halted else 0.0
        if not halted:  # 정지 뒤에는 포지션이 없다 — 최악 낙폭을 더 세지 않는다
            worst_last = _worst_equity(equity, qty, today, on_mark=False)
            result.worst_intraday_drawdown = max(result.worst_intraday_drawdown, 1 - worst / peak)
            result.worst_intraday_drawdown_last = max(
                result.worst_intraday_drawdown_last, 1 - worst_last / peak
            )
        if not halted and worst <= (1 - cfg.halt_drawdown) * peak:
            result.halted_day = day
            halted = True
            for s in symbols:
                if qty[s]:
                    equity -= _cost(result, qty[s], 0.0, bars[s][day].close, cfg.perp_cost)
                    qty[s] = 0.0
        # 5. 체결
        if not halted and equity > 0:
            for s in symbols:
                want = target.get(s)
                if want is None or s in stopped:
                    continue
                price = bars[s][day].close
                current = qty[s] * price / equity
                if abs(want - current) < cfg.band and _sign(want) == _sign(current):
                    continue
                new_q = want * equity / price
                equity -= _cost(result, qty[s], new_q, price, cfg.perp_cost)
                result.traded_notional += abs(new_q - qty[s]) * price
                result.trades += 1
                qty[s] = new_q
        for s in symbols:
            result.days_long += qty[s] > 0
            result.days_short += qty[s] < 0
        # 6. 다음 목표
        for s in symbols:
            history[s].append(signal_closes[s][day])
            target[s] = target_weight(history[s], sig_cfg) or 0.0
        peak = max(peak, equity)
        result.days.append(day)
        result.equity.append(equity)
        result.returns.append(equity / start_equity - 1 if start_equity > 0 else 0.0)
        day += _DAY_MS
    return result
