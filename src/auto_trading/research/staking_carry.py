"""스테이킹 캐리 과거 평가 (docs/research/92) — 순수 계산, I/O 없음.

WBETH/BNSOL(또는 대조군: 스테이킹 없는 현물)을 들고 같은 코인 수량만큼 USD-M 무기한 숏.
시간(1h)마다: funding → mark 고가 스트레스(청산·긴급 조정 판정) → mark 종가 손익 → 평가 →
주간(월요일 00:00 UTC)·긴급 조정. 조정은 계좌를 현물 ``spot_share`` / 선물 지갑 나머지로 되돌리고
숏 수량 = 코인 환산 수량. 비용은 현물·무기한 편도 비율.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "Bar",
    "CarryConfig",
    "CarryResult",
    "Funding",
    "longest_negative_funding_days",
    "mdd",
    "simulate_carry",
    "value_at",
]

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
_MONDAY_OFFSET_MS = 4 * DAY_MS
"""1970-01-01 은 목요일 — (t − 4일) % 7일 == 0 이면 월요일 00:00 UTC."""


@dataclass(frozen=True, slots=True)
class Bar:
    open_ms: int
    spot_close: float
    mark_close: float
    mark_high: float


@dataclass(frozen=True, slots=True)
class Funding:
    time_ms: int
    rate: float
    mark: float


@dataclass(frozen=True, slots=True)
class CarryConfig:
    capital: float = 10_000.0
    spot_share: float = 0.5
    spot_cost: float = 0.0015
    perp_cost: float = 0.00095
    emergency_ratio: float = 0.25
    liquidation_ratio: float = 0.01


@dataclass(slots=True)
class CarryResult:
    final_equity: float = 0.0
    liquidated_ms: int | None = None
    funding: float = 0.0
    costs: float = 0.0
    weekly_rebalances: int = 0
    emergency_rebalances: int = 0
    min_buffer: float = float("inf")
    """mark 고가에서 선물 지갑 ÷ 숏 명목의 최저치."""
    min_buffer_ms: int | None = None
    equity: list[tuple[int, float]] = field(default_factory=list)
    """(시간 끝 ms, 계좌 평가) — 청산 비용 전."""


def simulate_carry(
    bars: Sequence[Bar],
    funding: Sequence[Funding],
    ratio_at: Callable[[int], float],
    *,
    start_price: tuple[float, float],
    cfg: CarryConfig,
) -> CarryResult:
    """``bars`` 는 연속 1h 봉(오래된 것 먼저). ``start_price`` = (현물, mark) 시작 시각 가격.

    ``ratio_at(ms)`` = 그 시각의 코인 환산비율(WBETH:ETH 등, 대조군은 항상 1).
    """
    res = CarryResult()
    if not bars:
        return res
    t0 = bars[0].open_ms
    wallet = cfg.capital
    units = 0.0  # WBETH/BNSOL 수량 (대조군은 코인 수량)
    short = 0.0
    spot0, mark0 = start_price

    def rebalance(t: int, spot: float, mark: float) -> None:
        nonlocal wallet, units, short
        ratio = ratio_at(t)
        coin = units * ratio
        equity = coin * spot + wallet
        target = cfg.spot_share * equity
        trade = target - coin * spot
        spot_fee = cfg.spot_cost * abs(trade)
        wallet -= trade + spot_fee
        new_coin = target / spot
        units = new_coin / ratio
        perp_fee = cfg.perp_cost * abs(new_coin - short) * mark
        wallet -= perp_fee
        short = new_coin
        res.costs += spot_fee + perp_fee

    rebalance(t0, spot0, mark0)
    prev_mark = mark0
    events = sorted(funding, key=lambda f: f.time_ms)
    k = 0
    while k < len(events) and events[k].time_ms <= t0:
        k += 1
    for bar in bars:
        end = bar.open_ms + HOUR_MS
        while k < len(events) and events[k].time_ms <= end:
            ev = events[k]
            pay = short * ev.mark * ev.rate  # 양수 funding 이면 숏이 받는다
            wallet += pay
            res.funding += pay
            k += 1
        if short > 0:
            worst = wallet + short * (prev_mark - bar.mark_high)
            buffer = worst / (short * bar.mark_high)
            if buffer < res.min_buffer:
                res.min_buffer, res.min_buffer_ms = buffer, end
            if buffer <= cfg.liquidation_ratio:
                res.liquidated_ms = end
                res.final_equity = max(wallet + short * (prev_mark - bar.mark_high), 0.0)
                res.final_equity += units * ratio_at(bar.open_ms) * bar.spot_close
                return res
        else:
            buffer = float("inf")
        wallet += short * (prev_mark - bar.mark_close)
        prev_mark = bar.mark_close
        res.equity.append((end, units * ratio_at(bar.open_ms) * bar.spot_close + wallet))
        if buffer <= cfg.emergency_ratio:
            rebalance(bar.open_ms, bar.spot_close, bar.mark_close)
            res.emergency_rebalances += 1
        elif (end - _MONDAY_OFFSET_MS) % WEEK_MS == 0:
            rebalance(end, bar.spot_close, bar.mark_close)
            res.weekly_rebalances += 1
    last = bars[-1]
    coin = units * ratio_at(last.open_ms)
    exit_fee = cfg.spot_cost * coin * last.spot_close + cfg.perp_cost * short * last.mark_close
    res.costs += exit_fee
    res.final_equity = coin * last.spot_close + wallet - exit_fee
    return res


def mdd(points: Sequence[tuple[int, float]]) -> tuple[float, int | None, int | None]:
    """(최대 낙폭, 고점 시각, 바닥 시각)."""
    peak, worst = -1.0, 0.0
    peak_t: int | None = None
    span: tuple[int | None, int | None] = (None, None)
    for t, v in points:
        if v > peak:
            peak, peak_t = v, t
        if peak > 0 and 1 - v / peak > worst:
            worst, span = 1 - v / peak, (peak_t, t)
    return worst, span[0], span[1]


def longest_negative_funding_days(
    funding: Sequence[Funding], *, window_days: int = 30
) -> tuple[int, int | None]:
    """직전 ``window_days`` 일 funding 비율 합이 음수였던 가장 긴 연속 일수와 끝 날(ms)."""
    daily: dict[int, float] = {}
    for f in funding:
        daily[f.time_ms // DAY_MS] = daily.get(f.time_ms // DAY_MS, 0.0) + f.rate
    if not daily:
        return 0, None
    days = range(min(daily), max(daily) + 1)
    run = best = 0
    best_end: int | None = None
    window: list[float] = []
    for d in days:
        window.append(daily.get(d, 0.0))
        if len(window) > window_days:
            window.pop(0)
        if len(window) == window_days and sum(window) < 0:
            run += 1
            if run > best:
                best, best_end = run, d * DAY_MS
        else:
            run = 0
    return best, best_end


def value_at(points: Sequence[tuple[int, float]], t: int) -> float | None:
    """``t`` 시각(또는 그 직전) 평가."""
    found = None
    for pt, v in points:
        if pt > t:
            break
        found = v
    return found
