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
   (O/v2만) 선물 비상 스톱: 무기한 저가 ≤ 전날 종가 × (1 − d) 이면 스톱가 × (1 − 불리폭)에 닫는다.
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

1~5는 :func:`step_overlay_day` 하나에 있다. 연구 재생(:func:`simulate_overlay`)과
Paper 실행기(``execution/paper_overlay.py``)가 **같은 함수**를 부르므로 두 경로의 규칙이
갈라질 수 없다 (Phase 6 parity). 6(다음 목표)은 종가 이력이 필요해 호출하는 쪽에서 한다.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from auto_trading.backtest.trend import TrendConfig, target_weight

__all__ = [
    "O2_STOP",
    "O3_2X",
    "T2_ALONE",
    "OverlayBar",
    "OverlayConfig",
    "OverlayDayOutcome",
    "OverlayMarketDay",
    "OverlayResult",
    "OverlayState",
    "next_targets",
    "simulate_overlay",
    "split_weights",
    "step_overlay_day",
    "vol_spikes",
    "weak_trends",
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
    catastrophe_stop: float | None = None
    """선물 추가분 비상 스톱: 전날 무기한 종가 × (1 − 이 값). ``None``이면 없음(O/v1).
    O/v2 사전등록(docs/research/27)은 0.20 (2026-09-24 사용자 선택)."""
    stop_slippage: float = 0.01
    """비상 스톱 체결 불리폭 — 급락 중 시장가 체결을 보수적으로 1% 로 둔다."""
    vol_spike_ratio: float | None = None
    """변동성 급등 끄기 (docs/research/43): 종목별 현물 σ(최근 ``vol_spike_days``일)
    ≥ 이 값 × σ(T/v2 60일)이면 다음 체결에서 그 종목 노출 배수를 1.0 으로 둔다(선물 추가분 0).
    ``None``이면 없음(O/v2)."""
    vol_spike_days: int = 10
    vol_spike_adverse_only: bool = False
    """True 면 급등이면서 최근 ``vol_spike_days``일 가격이 내렸을 때만 끈다(43 §6 B′).
    변동성이 커도 방향(롱)이 맞으면 선물 추가분을 유지한다."""
    futures_full_trend_only: bool = False
    """선물부터 줄이기 (docs/research/64): T/v2 추세 신호(20·60·120일)가 **모두** 상승일 때만
    선물 추가분을 든다. 하나라도 아니면 다음 체결에서 그 종목 노출 배수를 1.0 으로 둔다
    (현물은 T/v2 그대로).
    ``False``면 없음(O/v2·O/v3)."""


T2_ALONE = OverlayConfig(exposure=1.0, daily_loss_limit=math.inf, halt_drawdown=1.0)
"""대조군 A: 등록된 T/v2 그대로 (현물 규칙에는 일 손실·계좌 정지가 없다)."""

O2_STOP = OverlayConfig(catastrophe_stop=0.20)
"""O/v2: O/v1 + 선물 추가분 비상 스톱 −20% (docs/research/27, 전향 관찰 28)."""

O3_2X = OverlayConfig(exposure=2.0, catastrophe_stop=0.20)
"""O/v3: O/v2 규칙에 노출만 2배 — 한 달 운영 관찰용 (docs/research/31). 판정된 전략이 아니다."""


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
    stop_hits: int = 0
    """비상 스톱이 발동한 (종목, 날) 수."""
    spike_days: int = 0
    """노출 배수를 1.0 으로 둔 (종목, 날) 수 — 변동성 급등(43) 또는 선물부터 줄이기(64)."""


def split_weights(
    weights: Mapping[str, float],
    exposure: float,
    max_leverage: float,
    exposures: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """T/v2 목표 비중을 현물 비중·선물 비중으로 나눈다.

    ``exposures``를 주면 종목별 배수(43 변동성 급등 끄기)를 쓴다. 없으면 모두 ``exposure``.
    """
    if exposures is None:
        total = sum(weights.values())
        k = 1.0
        if total > 0 and max_leverage > 1:
            k = min(1.0, (max_leverage - exposure * total) / ((max_leverage - 1) * total))
        k = max(k, 0.0)
        spot = {s: k * w for s, w in weights.items()}
        fut = {s: (exposure - k) * w for s, w in weights.items()}
        return spot, fut
    total = sum(weights.values())
    gross = sum(exposures[s] * w for s, w in weights.items())
    k = 1.0
    if total > 0 and max_leverage > 1:
        k = min(1.0, (max_leverage - gross) / ((max_leverage - 1) * total))
    k = max(k, 0.0)
    spot = {s: k * w for s, w in weights.items()}
    fut = {s: (exposures[s] - k) * w for s, w in weights.items()}
    return spot, fut


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


@dataclass(slots=True)
class OverlayState:
    """하루와 다음 하루 사이에 넘어가는 전부. Paper 원장은 이것을 그대로 저장한다."""

    spot_qty: dict[str, float]
    fut_qty: dict[str, float]
    wallet: float
    """현물 밖 자본 = 선물 지갑(교차 증거금). 현물 매매 대금도 여기서 오간다."""
    peak: float
    """종가 기준 자본 고점 (계좌 정지 판정용)."""
    exposure: float
    """현재 적용 중인 노출 배수. 계좌 정지 뒤에는 1.0."""
    halted_day: int | None
    target: dict[str, float] | None
    """전날 종가로 정한 T/v2 목표 비중. 오늘 체결한다. ``None``이면 체결하지 않는다."""

    @classmethod
    def initial(cls, symbols: Sequence[str], equity: float, cfg: OverlayConfig) -> OverlayState:
        return cls(
            spot_qty=dict.fromkeys(symbols, 0.0),
            fut_qty=dict.fromkeys(symbols, 0.0),
            wallet=equity,
            peak=equity,
            exposure=cfg.exposure,
            halted_day=None,
            target=None,
        )


@dataclass(frozen=True, slots=True)
class OverlayMarketDay:
    """하루 ``d``의 시장 입력. 전날 종가는 ``d-1 → d`` 손익에 쓴다."""

    spot_prev_close: Mapping[str, float]
    spot: Mapping[str, OverlayBar]
    perp_prev_close: Mapping[str, float]
    perp: Mapping[str, OverlayBar]
    funding_rates: Mapping[str, Sequence[float]]
    """그날(``[d, d + 1일)``) 정산된 funding 비율, 시각 순."""
    spiked: frozenset[str] = frozenset()
    """전날 종가까지로 판정한 변동성 급등 종목 (43). 이 종목은 오늘 체결에서 배수 1.0."""


@dataclass(frozen=True, slots=True)
class OverlayDayOutcome:
    start_equity: float
    equity: float
    exposure: float
    fills: tuple[dict[str, float | str], ...]
    """그날 바뀐 포지션: 종목·현물/선물 수량 전후·비용·사유."""


def step_overlay_day(
    state: OverlayState,
    day: int,
    market: OverlayMarketDay,
    cfg: OverlayConfig,
    tally: OverlayResult,
) -> OverlayDayOutcome:
    """하루 ``day``의 1~5단계를 적용하고 ``state``를 갱신한다. 합계는 ``tally``에 더한다.

    ``tally``는 연구 재생에선 전체 누적, Paper에선 그날 하루치다. 합계를 반환하지 않고
    호출자가 준 객체에 직접 더하는 이유는 누적 순서를 리팩터링 전과 같게 두기 위해서다
    (부동소수 결과가 비트 단위로 같다 — 테스트로 고정).
    """
    symbols = sorted(state.spot_qty)
    tc = cfg.trend
    sq = state.spot_qty
    fq = state.fut_qty
    wallet = state.wallet
    peak = state.peak
    exposure = state.exposure
    target = state.target
    fills: list[dict[str, float | str]] = []
    spot_px = {s: market.spot[s].close for s in symbols}
    perp_px = {s: market.perp[s].close for s in symbols}
    start_equity = sum(sq[s] * market.spot_prev_close[s] for s in symbols) + wallet
    start_wallet = wallet
    # 1. 손익·funding
    fund_paid = 0.0
    for s in symbols:
        spnl = sq[s] * (spot_px[s] - market.spot_prev_close[s])
        fpnl = fq[s] * (perp_px[s] - market.perp_prev_close[s])
        tally.spot_pnl += spnl
        tally.futures_pnl += fpnl
        wallet += fpnl
        if fq[s]:
            for rate in market.funding_rates.get(s, ()):
                # 롱은 양수 funding을 낸다. 정산 가격은 당일 무기한 종가로 근사한다.
                fund_paid += fq[s] * perp_px[s] * rate
    tally.funding -= fund_paid
    wallet -= fund_paid
    # 1b. 비상 스톱 (O/v2): 무기한 저가가 전날 종가 × (1 − d) 이하면 그 가격 × (1 − 불리폭)에
    #     선물을 닫는다. 종가 손익을 스톱 체결가 손익으로 바꾼다. 청산보다 먼저 닿는다.
    stopped_loss = 0.0
    if cfg.catastrophe_stop is not None:
        for s in symbols:
            if fq[s] <= 0:
                continue
            trigger = market.perp_prev_close[s] * (1 - cfg.catastrophe_stop)
            if market.perp[s].low > trigger:
                continue
            fill = trigger * (1 - cfg.stop_slippage)
            adjust = fq[s] * (fill - perp_px[s])
            cost = fq[s] * fill * tc.perp_cost
            tally.futures_pnl += adjust
            tally.costs += cost
            tally.stop_hits += 1
            wallet += adjust - cost
            stopped_loss += fq[s] * (fill - market.perp_prev_close[s]) - cost
            fills.append({"symbol": s, "reason": "stop", "fut_before": fq[s], "cost": cost})
            fq[s] = 0.0
    # 2. 청산 (선물 지갑, 무기한 저가)
    worst_wallet = start_wallet - fund_paid + stopped_loss
    worst_notional = 0.0
    for s in symbols:
        low = market.perp[s].low
        worst_wallet += fq[s] * (low - market.perp_prev_close[s])
        worst_notional += fq[s] * low
    if worst_notional > 0 and worst_wallet <= cfg.maintenance_margin * worst_notional:
        tally.liquidations += 1
        fills.extend(
            {"symbol": s, "reason": "liquidation", "fut_before": fq[s]} for s in symbols if fq[s]
        )
        wallet = 0.0
        fq = dict.fromkeys(symbols, 0.0)
        worst_wallet = 0.0
    # 3. 계좌 정지 (봉 안 최악 자본)
    worst_equity = sum(sq[s] * market.spot[s].low for s in symbols) + max(worst_wallet, 0.0)
    tally.worst_intraday_drawdown = max(tally.worst_intraday_drawdown, 1 - worst_equity / peak)
    halt_now = state.halted_day is None and worst_equity <= (1 - cfg.halt_drawdown) * peak
    if halt_now:
        state.halted_day = day
        tally.halted_day = day
        exposure = 1.0
        for s in symbols:
            if fq[s]:
                cost = abs(fq[s]) * perp_px[s] * tc.perp_cost
                tally.costs += cost
                wallet -= cost
                fills.append({"symbol": s, "reason": "halt", "fut_before": fq[s], "cost": cost})
                fq[s] = 0.0
    equity = sum(sq[s] * spot_px[s] for s in symbols) + wallet
    # 4. 일 손실
    no_increase = start_equity > 0 and equity / start_equity - 1 <= -cfg.daily_loss_limit
    tally.loss_limit_days += no_increase
    # 5. 체결
    if target is not None and equity > 0:
        per_symbol = None
        if market.spiked:
            per_symbol = {s: 1.0 if s in market.spiked else exposure for s in symbols}
            tally.spike_days += sum(1 for s in symbols if s in market.spiked)
        spot_w, fut_w = split_weights(target, exposure, cfg.max_futures_leverage, per_symbol)
        tally.capped_days += any(spot_w[s] < target[s] - 1e-12 for s in symbols)
        traded = False
        for s in symbols:
            current = (sq[s] * spot_px[s] + fq[s] * perp_px[s]) / equity
            want = spot_w[s] + fut_w[s]
            band = tc.band * (exposure if per_symbol is None else per_symbol[s])
            if abs(want - current) < band and _sign(want) == _sign(current):
                continue
            if no_increase and want > current:
                continue
            new_sq = spot_w[s] * equity / spot_px[s]
            new_fq = fut_w[s] * equity / perp_px[s]
            cost = abs(new_sq - sq[s]) * spot_px[s] * tc.spot_cost
            cost += abs(new_fq - fq[s]) * perp_px[s] * tc.perp_cost
            tally.costs += cost
            tally.trades += 1
            wallet -= cost
            equity -= cost
            # 현물 매매 대금은 선물 지갑(현금)과 오간다. 이체 비용 0.
            wallet -= (new_sq - sq[s]) * spot_px[s]
            fills.append(
                {
                    "symbol": s,
                    "reason": "rebalance",
                    "spot_before": sq[s],
                    "spot_after": new_sq,
                    "fut_before": fq[s],
                    "fut_after": new_fq,
                    "cost": cost,
                }
            )
            sq[s], fq[s] = new_sq, new_fq
            traded = True
        notional = sum(fq[s] * perp_px[s] for s in symbols)
        cap = cfg.max_futures_leverage * max(wallet, 0.0)
        if notional > cap + 1e-9:
            scale = cap / notional
            for s in symbols:
                cost = fq[s] * (1 - scale) * perp_px[s] * tc.perp_cost
                tally.costs += cost
                wallet -= cost
                fills.append(
                    {
                        "symbol": s,
                        "reason": "deleverage",
                        "fut_before": fq[s],
                        "fut_after": fq[s] * scale,
                        "cost": cost,
                    }
                )
                fq[s] *= scale
            tally.deleverage_days += 1
            traded = True
        if traded:
            equity = sum(sq[s] * spot_px[s] for s in symbols) + wallet
    notional = sum(fq[s] * perp_px[s] for s in symbols)
    if wallet > 0:
        tally.max_futures_leverage = max(tally.max_futures_leverage, notional / wallet)
    peak = max(peak, equity)
    exposure_now = (
        (sum(sq[s] * spot_px[s] + fq[s] * perp_px[s] for s in symbols) / equity)
        if equity > 0
        else 0.0
    )
    state.spot_qty = sq
    state.fut_qty = fq
    state.wallet = wallet
    state.peak = peak
    state.exposure = exposure
    return OverlayDayOutcome(
        start_equity=start_equity, equity=equity, exposure=exposure_now, fills=tuple(fills)
    )


def next_targets(closes: Mapping[str, Sequence[float]], cfg: OverlayConfig) -> dict[str, float]:
    """6단계: 종목별 마지막 종가까지로 다음 T/v2 목표 비중 (이력 부족이면 0)."""
    return {s: target_weight(closes[s], cfg.trend) or 0.0 for s in sorted(closes)}


def vol_spikes(closes: Mapping[str, Sequence[float]], cfg: OverlayConfig) -> frozenset[str]:
    """43: 마지막 종가까지 σ(최근 ``vol_spike_days``일) ≥ ``vol_spike_ratio`` × σ(60일)인 종목.

    σ는 일간 로그수익 표본 표준편차(T/v2 ``target_weight``와 같은 계산).
    이력이 부족하면 판정하지 않는다.
    """
    ratio = cfg.vol_spike_ratio
    if ratio is None:
        return frozenset()
    long_days = cfg.trend.vol_days
    out: set[str] = set()
    for s, series in closes.items():
        if len(series) < long_days + 1:
            continue
        steps = [math.log(series[-k] / series[-k - 1]) for k in range(1, long_days + 1)]
        long_vol = statistics.stdev(steps)
        short_vol = statistics.stdev(steps[: cfg.vol_spike_days])
        adverse = series[-1] < series[-1 - cfg.vol_spike_days]
        spike = long_vol > 0 and short_vol >= ratio * long_vol
        if spike and (adverse or not cfg.vol_spike_adverse_only):
            out.add(s)
    return frozenset(out)


def weak_trends(closes: Mapping[str, Sequence[float]], cfg: OverlayConfig) -> frozenset[str]:
    """64: 마지막 종가까지 T/v2 추세 신호가 모두 상승은 아닌 종목 (이력 부족이면 판정 안 함)."""
    if not cfg.futures_full_trend_only:
        return frozenset()
    need = max(max(cfg.trend.lookbacks), cfg.trend.vol_days) + 1
    return frozenset(
        s
        for s, series in closes.items()
        if len(series) >= need and not all(series[-1] > series[-1 - k] for k in cfg.trend.lookbacks)
    )


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
    result = OverlayResult()
    state = OverlayState.initial(symbols, initial_equity, cfg)
    fund_t = {s: [ms for ms, _ in funding.get(s, ())] for s in symbols}
    fund_r = {s: [r for _, r in funding.get(s, ())] for s in symbols}
    history = {
        s: [spot_bars[s][d].close for d in sorted(spot_bars[s]) if d < from_ms] for s in symbols
    }
    day = from_ms
    while day <= to_ms:
        prev_day = day - _DAY_MS
        for s in symbols:
            for src in (spot_bars, perp_bars):
                if day not in src[s] or prev_day not in src[s]:
                    raise ValueError(f"일봉 결측: {s} {day}")
        rates: dict[str, list[float]] = {}
        for s in symbols:
            lo = bisect.bisect_right(fund_t[s], day - 1)
            hi = bisect.bisect_right(fund_t[s], day + _DAY_MS - 1)
            rates[s] = fund_r[s][lo:hi]
        market = OverlayMarketDay(
            spot_prev_close={s: spot_bars[s][prev_day].close for s in symbols},
            spot={s: spot_bars[s][day] for s in symbols},
            perp_prev_close={s: perp_bars[s][prev_day].close for s in symbols},
            perp={s: perp_bars[s][day] for s in symbols},
            funding_rates=rates,
            spiked=vol_spikes(history, cfg) | weak_trends(history, cfg),
        )
        outcome = step_overlay_day(state, day, market, cfg, result)
        # 6. 다음 목표
        for s in symbols:
            history[s].append(market.spot[s].close)
        state.target = next_targets(history, cfg)
        result.days.append(day)
        result.equity.append(outcome.equity)
        result.returns.append(
            outcome.equity / outcome.start_equity - 1 if outcome.start_equity > 0 else 0.0
        )
        result.exposure.append(outcome.exposure)
        day += _DAY_MS
    return result
