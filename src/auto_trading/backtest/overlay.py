"""O/v1 T/v2 신호 1.5배 선물 추가 노출 연구 시뮬레이터 (docs/research/24 사전등록).

T/v2(현물 롱 전용 추세, ``TrendConfig(long_only=True)``)의 종목별 목표 비중 ``w_i``를 그대로 쓰고,
총 노출을 ``exposure × w_i``로 키운다. 늘어난 부분은 USD-M 무기한 롱으로 든다.
현물 밖의 자본은 전부 선물 지갑(교차 증거금)에 둔다. 순수 함수, I/O 없음.

현물·선물 구성 (목표 비중 합 ``w``, 노출 ``E``, 선물 레버리지 상한 ``L``):
현물 ``k·w_i``, 선물 ``(E − k)·w_i``, ``k = min(1, (L − E·w) / ((L − 1)·w))``.
선물 명목 ≤ L × 선물 지갑(= 1 − 현물)을 지키도록 현물을 줄이고 선물로 대신 든다(총 노출은 같다).

하루 ``d``의 순서:

1. 현물은 현물 종가, 선물은 무기한 종가로 ``d-1 → d`` 손익.
   롱 선물은 그날 정산된 funding을 낸다(양수일 때).
2. 청산 판정(보수적): 선물 지갑이 무기한 **저가**에서 유지증거금 이하면 선물 지갑 전액 손실.
3. 계좌 정지: 봉 안 최악 자본(현물·무기한 저가)이 종가 기준 고점의 ``1 − halt_drawdown`` 이하면
   선물을 그날 종가에 전부 닫고 이후 선물 추가를 끈다(현물은 T/v2 그대로).
4. 일 손실: 그날 손실이 시작 자본의 ``daily_loss_limit`` 이상이면
   그날은 노출을 늘리는 거래를 하지 않는다.
5. 전날 종가에 정한 목표를 ``d`` 종가에 체결한다(1일 지연).
   종목별 총 노출 변화가 ``band × E``보다 작고 방향이 같으면 건너뛴다.
   체결 뒤 선물 명목이 ``L × 선물 지갑``을 넘으면 선물을 줄인다.
6. ``d`` 현물 종가까지로 다음 목표를 정한다.

``exposure = 1``이고 일 손실·정지를 끄면(``T2_ALONE``) 선물이 없고
T/v2(`simulate_trend(long_only=True)`)와 같은 경로다. 비교 대조군 A로 쓴다.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from auto_trading.backtest.trend import TrendConfig, target_weight

__all__ = [
    "T2_ALONE",
    "OverlayBar",
    "OverlayConfig",
    "OverlayResult",
    "simulate_overlay",
    "split_weights",
]

_DAY_MS = 86_400_000


def _t2() -> TrendConfig:
    return TrendConfig(long_only=True)


@dataclass(frozen=True, slots=True)
class OverlayConfig:
    trend: TrendConfig = field(default_factory=_t2)
    """T/v2 신호·크기·밴드·비용. 변경 금지(16 등록)."""
    exposure: float = 1.5
    """총 노출 = exposure × T/v2 목표 비중. 2026-09-24 사용자 선택."""
    max_futures_leverage: float = 3.0
    """선물 명목 / 선물 지갑 상한. 2026-09-24 사용자 선택."""
    daily_loss_limit: float = 0.02
    halt_drawdown: float = 0.30
    maintenance_margin: float = 0.005


T2_ALONE = OverlayConfig(exposure=1.0, daily_loss_limit=math.inf, halt_drawdown=1.0)
"""대조군 A: 등록된 T/v2 그대로 (현물 규칙에는 일 손실·계좌 정지가 없다)."""


@dataclass(frozen=True, slots=True)
class OverlayBar:
    low: float
    close: float


@dataclass(slots=True)
class OverlayResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)
    """그날 체결 뒤 총 노출(현물 + 선물 명목) / 자본."""
    spot_pnl: float = 0.0
    futures_pnl: float = 0.0
    funding: float = 0.0
    costs: float = 0.0
    trades: int = 0
    liquidations: int = 0
    halted_day: int | None = None
    loss_limit_days: int = 0
    capped_days: int = 0
    """현물을 줄여 선물로 대신 든 날(k < 1)."""
    deleverage_days: int = 0
    """체결 뒤 선물 레버리지 상한 때문에 선물을 줄인 날."""
    max_futures_leverage: float = 0.0
    worst_intraday_drawdown: float = 0.0


def split_weights(
    weights: Mapping[str, float], exposure: float, max_leverage: float
) -> tuple[dict[str, float], dict[str, float]]:
    """T/v2 목표 비중을 현물 비중·선물 비중으로 나눈다."""
    total = sum(weights.values())
    k = 1.0
    if total > 0 and max_leverage > 1:
        k = min(1.0, (max_leverage - exposure * total) / ((max_leverage - 1) * total))
    k = max(k, 0.0)
    spot = {s: k * w for s, w in weights.items()}
    fut = {s: (exposure - k) * w for s, w in weights.items()}
    return spot, fut


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def simulate_overlay(
    spot_bars: Mapping[str, Mapping[int, OverlayBar]],
    perp_bars: Mapping[str, Mapping[int, OverlayBar]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: float,
    cfg: OverlayConfig,
) -> OverlayResult:
    symbols = sorted(spot_bars)
    tc = cfg.trend
    result = OverlayResult()
    sq = dict.fromkeys(symbols, 0.0)
    fq = dict.fromkeys(symbols, 0.0)
    wallet = initial_equity
    fund_t = {s: [ms for ms, _ in funding.get(s, ())] for s in symbols}
    fund_r = {s: [r for _, r in funding.get(s, ())] for s in symbols}
    history = {
        s: [spot_bars[s][d].close for d in sorted(spot_bars[s]) if d < from_ms] for s in symbols
    }
    target: dict[str, float] | None = None
    exposure = cfg.exposure
    peak = initial_equity
    day = from_ms
    while day <= to_ms:
        prev_day = day - _DAY_MS
        for s in symbols:
            for src in (spot_bars, perp_bars):
                if day not in src[s] or prev_day not in src[s]:
                    raise ValueError(f"일봉 결측: {s} {day}")
        spot_px = {s: spot_bars[s][day].close for s in symbols}
        perp_px = {s: perp_bars[s][day].close for s in symbols}
        start_equity = sum(sq[s] * spot_bars[s][prev_day].close for s in symbols) + wallet
        start_wallet = wallet
        # 1. 손익·funding
        fund_paid = 0.0
        for s in symbols:
            spnl = sq[s] * (spot_px[s] - spot_bars[s][prev_day].close)
            fpnl = fq[s] * (perp_px[s] - perp_bars[s][prev_day].close)
            result.spot_pnl += spnl
            result.futures_pnl += fpnl
            wallet += fpnl
            if fq[s]:
                lo = bisect.bisect_right(fund_t[s], day - 1)
                hi = bisect.bisect_right(fund_t[s], day + _DAY_MS - 1)
                for k in range(lo, hi):
                    # 롱은 양수 funding을 낸다. 정산 가격은 당일 무기한 종가로 근사한다.
                    fund_paid += fq[s] * perp_px[s] * fund_r[s][k]
        result.funding -= fund_paid
        wallet -= fund_paid
        # 2. 청산 (선물 지갑, 무기한 저가)
        worst_wallet = start_wallet - fund_paid
        worst_notional = 0.0
        for s in symbols:
            low = perp_bars[s][day].low
            worst_wallet += fq[s] * (low - perp_bars[s][prev_day].close)
            worst_notional += fq[s] * low
        if worst_notional > 0 and worst_wallet <= cfg.maintenance_margin * worst_notional:
            result.liquidations += 1
            wallet = 0.0
            fq = dict.fromkeys(symbols, 0.0)
            worst_wallet = 0.0
        # 3. 계좌 정지 (봉 안 최악 자본)
        worst_equity = sum(sq[s] * spot_bars[s][day].low for s in symbols) + max(worst_wallet, 0.0)
        result.worst_intraday_drawdown = max(
            result.worst_intraday_drawdown, 1 - worst_equity / peak
        )
        halt_now = result.halted_day is None and worst_equity <= (1 - cfg.halt_drawdown) * peak
        if halt_now:
            result.halted_day = day
            exposure = 1.0
            for s in symbols:
                if fq[s]:
                    cost = abs(fq[s]) * perp_px[s] * tc.perp_cost
                    result.costs += cost
                    wallet -= cost
                    fq[s] = 0.0
        equity = sum(sq[s] * spot_px[s] for s in symbols) + wallet
        # 4. 일 손실
        no_increase = start_equity > 0 and equity / start_equity - 1 <= -cfg.daily_loss_limit
        result.loss_limit_days += no_increase
        # 5. 체결
        if target is not None and equity > 0:
            spot_w, fut_w = split_weights(target, exposure, cfg.max_futures_leverage)
            result.capped_days += any(spot_w[s] < target[s] - 1e-12 for s in symbols)
            traded = False
            for s in symbols:
                current = (sq[s] * spot_px[s] + fq[s] * perp_px[s]) / equity
                want = spot_w[s] + fut_w[s]
                if abs(want - current) < tc.band * exposure and _sign(want) == _sign(current):
                    continue
                if no_increase and want > current:
                    continue
                new_sq = spot_w[s] * equity / spot_px[s]
                new_fq = fut_w[s] * equity / perp_px[s]
                cost = abs(new_sq - sq[s]) * spot_px[s] * tc.spot_cost
                cost += abs(new_fq - fq[s]) * perp_px[s] * tc.perp_cost
                result.costs += cost
                result.trades += 1
                wallet -= cost
                equity -= cost
                # 현물 매매 대금은 선물 지갑(현금)과 오간다. 이체 비용 0.
                wallet -= (new_sq - sq[s]) * spot_px[s]
                sq[s], fq[s] = new_sq, new_fq
                traded = True
            notional = sum(fq[s] * perp_px[s] for s in symbols)
            cap = cfg.max_futures_leverage * max(wallet, 0.0)
            if notional > cap + 1e-9:
                scale = cap / notional
                for s in symbols:
                    cost = fq[s] * (1 - scale) * perp_px[s] * tc.perp_cost
                    result.costs += cost
                    wallet -= cost
                    fq[s] *= scale
                result.deleverage_days += 1
                traded = True
            if traded:
                equity = sum(sq[s] * spot_px[s] for s in symbols) + wallet
        notional = sum(fq[s] * perp_px[s] for s in symbols)
        if wallet > 0:
            result.max_futures_leverage = max(result.max_futures_leverage, notional / wallet)
        peak = max(peak, equity)
        # 6. 다음 목표
        target = {}
        for s in symbols:
            history[s].append(spot_px[s])
            target[s] = target_weight(history[s], tc) or 0.0
        result.days.append(day)
        result.equity.append(equity)
        result.returns.append(equity / start_equity - 1 if start_equity > 0 else 0.0)
        result.exposure.append(
            (sum(sq[s] * spot_px[s] + fq[s] * perp_px[s] for s in symbols) / equity)
            if equity > 0
            else 0.0
        )
        day += _DAY_MS
    return result
