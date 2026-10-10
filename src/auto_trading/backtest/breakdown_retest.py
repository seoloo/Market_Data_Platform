"""BRS — Breakdown-Retest Short v1 (docs/research/205). 순수 함수, I/O 없음.

15 분봉 하나로 1 시간 · 4 시간봉을 **완성된 봉만** 만들어 쓴다.

1. 4 시간 추세: 마지막으로 **마감된** 4 시간봉 종가 < EMA200 그리고 EMA50 < EMA200.
2. 1 시간 붕괴: 마감된 1 시간봉 k 가 종가 < 직전 20 봉 최저가(k 제외), 거래량 > 직전 20 봉
   평균(k 제외) × 1.2, RSI(14) > 30, 그리고 그 시각의 4 시간 추세 → 붕괴 사건. 직전 20 봉
   최저가가 ``retest_level``.
3. 되돌림: 사건이 나온 1 시간봉 마감 뒤 15 분봉 8 개 안에서, 봉의 고가 · 저가가
   ``level × (1 ± 0.3%)`` 띠에 닿고 종가 < level 이고 음봉이면 거절 → **다음 봉 시가**에 숏
   (지정가 IOC, 체결가 = 시가 × (1 − 미끄럼)).
4. 손절 = 체결가 + ATR(14, 신호 봉까지) × 1.5, 익절 = 체결가 − 2 × 위험. 봉 안에서 고가 ≥ 손절이면
   손절(익절과 같은 봉이면 손절 먼저, 시가가 이미 넘었으면 시가), 아니면 저가 < 익절이면 익절
   (시가가 이미 아래면 시가). 청산에도 미끄럼 · 수수료.
5. 크기: 잔고 × 0.5% ÷ (손절 − 체결가), 명목 ≤ 잔고 × 3.
6. 한 번에 한 포지션. 사건 하나는 한 번만 쓴다 — 포지션 · 진입 대기 중에 나온 사건은 버리고,
   기다리는 중에 새 사건이 오면 새 사건으로 바꾼다.
7. funding: 포지션을 들고 정산 시각(봉 시가 시각)을 지나면 숏은 ``수량 × 시가 × rate`` 를 받는다.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from auto_trading.backtest.ema_box import Bars, atr
from auto_trading.backtest.indicator_signals import ema, rsi

__all__ = [
    "DEFAULT",
    "MIN15_MS",
    "Breakdown",
    "Config",
    "Run",
    "Trade",
    "breakdowns",
    "daily_equity",
    "drawdowns",
    "max_streak",
    "resample",
    "simulate",
    "summary",
]

MIN15_MS = 15 * 60_000
HOUR_MS = 60 * 60_000
DAY_MS = 24 * HOUR_MS


@dataclass(frozen=True, slots=True)
class Config:
    ema_fast: int = 50
    ema_slow: int = 200
    lookback: int = 20
    vol_mult: float = 1.2
    rsi_len: int = 14
    rsi_min: float = 30.0
    tolerance: float = 0.003
    window: int = 8
    atr_len: int = 14
    atr_mult: float = 1.5
    rr: float = 2.0
    risk: float = 0.005
    max_leverage: float = 3.0
    fee: float = 0.0004
    slippage: float = 0.0002
    funding: bool = True


DEFAULT = Config()


def resample(b: Bars, minutes: int) -> Bars:
    """15 분봉 → ``minutes`` 분봉. 구성 15 분봉이 모두 있는 묶음만 (t = 묶음 시작 시각)."""
    span = minutes * 60_000
    need = span // MIN15_MS
    t: list[int] = []
    o: list[float] = []
    h: list[float] = []
    low: list[float] = []
    c: list[float] = []
    v: list[float] = []
    i, n = 0, len(b.t)
    while i < n:
        start = b.t[i] - b.t[i] % span
        j = i
        while j < n and b.t[j] < start + span:
            j += 1
        if j - i == need and b.t[i] == start:
            t.append(start)
            o.append(b.o[i])
            h.append(max(b.h[i:j]))
            low.append(min(b.low[i:j]))
            c.append(b.c[j - 1])
            v.append(sum(b.v[i:j]))
        i = j
    return Bars(t, o, h, low, c, v)


@dataclass(frozen=True, slots=True)
class Breakdown:
    at_ms: int
    """1 시간봉 마감 시각 = 이 사건을 쓸 수 있는 첫 15 분봉의 시가 시각."""
    level: float
    close: float
    volume_ratio: float
    rsi: float


def breakdowns(h1: Bars, h4: Bars, cfg: Config = DEFAULT) -> list[Breakdown]:
    """마감된 1 시간봉 · 4 시간봉만으로 붕괴 사건을 찾는다 (시간 순)."""
    e_fast = ema(h4.c, cfg.ema_fast)
    e_slow = ema(h4.c, cfg.ema_slow)
    h4_close = [t + 4 * HOUR_MS for t in h4.t]
    r = rsi(h1.c, cfg.rsi_len)
    out: list[Breakdown] = []
    n = cfg.lookback
    for k in range(n, len(h1.t)):
        if h1.t[k] - h1.t[k - n] != n * HOUR_MS:
            continue  # 빈 시간이 끼면 '직전 20 봉' 이 아니다
        at = h1.t[k] + HOUR_MS
        support = min(h1.low[k - n : k])
        avg_vol = sum(h1.v[k - n : k]) / n
        rk = r[k]
        if rk is None or avg_vol <= 0:
            continue
        if not (h1.c[k] < support and h1.v[k] > avg_vol * cfg.vol_mult and rk > cfg.rsi_min):
            continue
        j = bisect.bisect_right(h4_close, at) - 1  # 이 시각까지 마감된 마지막 4 시간봉
        if j < 0:
            continue
        ef, es = e_fast[j], e_slow[j]
        if ef is None or es is None or not (h4.c[j] < es and ef < es):
            continue
        out.append(Breakdown(at, support, h1.c[k], h1.v[k] / avg_vol, rk))
    return out


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    breakdown_ms: int
    signal_ms: int
    entry_ms: int
    exit_ms: int
    """청산이 일어난 15 분봉의 시가 시각."""
    entry_price: float
    exit_price: float
    stop_price: float
    take_profit: float
    quantity: float
    position_value: float
    risk_amount: float
    atr: float
    retest_level: float
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    funding_fee: float
    """받은 funding (+ 이 숏의 이익)."""
    net_pnl: float
    r_multiple: float
    exit_reason: str
    """``stop`` · ``take_profit`` · ``end``."""
    balance_after_trade: float
    capped: bool
    """명목 한도(3 배)에 걸려 위험이 0.5% 보다 작았나."""
    ambiguous: bool
    """같은 봉에서 손절 · 익절이 둘 다 닿았나 (손절로 처리)."""


@dataclass(slots=True)
class Run:
    trades: list[Trade] = field(default_factory=list)
    breakdowns: int = 0
    busy: int = 0
    """포지션 · 진입 대기 중이라 버린 사건."""
    replaced: int = 0
    expired: int = 0
    marks: list[tuple[int, float]] = field(default_factory=list)
    """(15 분봉 시가 시각, 그 봉 종가 기준 평가 자산) — 포지션이 있을 때만 의미."""


Resolver = Callable[[int, float, float], str | None]
"""(봉 시가 시각, 손절, 익절) → 더 짧은 봉으로 본 먼저 닿은 쪽 ``stop`` / ``take_profit`` / None."""


@dataclass(slots=True)
class _Pos:
    bd: Breakdown
    signal_ms: int
    entry_ms: int
    entry: float
    stop: float
    tp: float
    qty: float
    risk_amount: float
    atr: float
    entry_fee: float
    capped: bool
    funding: float = 0.0


def simulate(
    symbol: str,
    b: Bars,
    events: Sequence[Breakdown],
    cfg: Config = DEFAULT,
    *,
    from_ms: int,
    to_ms: int,
    balance: float = 10_000.0,
    funding: Mapping[int, float] | None = None,
    resolver: Resolver | None = None,
) -> Run:
    """``[from_ms, to_ms]`` 의 15 분봉으로 거래한다. ``events`` 는 시간 순."""
    run = Run()
    a = atr(b, cfg.atr_len)
    ev = [e for e in events if from_ms <= e.at_ms <= to_ms]
    run.breakdowns = len(ev)
    p_ev = 0
    setup: Breakdown | None = None
    setup_end = 0
    pending: tuple[Breakdown, int, float] | None = None  # (사건, 신호 봉 시각, 신호 봉 ATR)
    pos: _Pos | None = None
    fund = funding or {}
    last_i = -1

    def close(p: _Pos, i: int, price: float, reason: str, ambiguous: bool) -> None:
        nonlocal balance
        gross = (p.entry - price) * p.qty
        exit_fee = price * p.qty * cfg.fee
        net = gross - p.entry_fee - exit_fee + p.funding
        balance += net
        run.trades.append(
            Trade(
                symbol=symbol,
                breakdown_ms=p.bd.at_ms,
                signal_ms=p.signal_ms,
                entry_ms=p.entry_ms,
                exit_ms=b.t[i],
                entry_price=p.entry,
                exit_price=price,
                stop_price=p.stop,
                take_profit=p.tp,
                quantity=p.qty,
                position_value=p.entry * p.qty,
                risk_amount=p.risk_amount,
                atr=p.atr,
                retest_level=p.bd.level,
                gross_pnl=gross,
                entry_fee=p.entry_fee,
                exit_fee=exit_fee,
                funding_fee=p.funding,
                net_pnl=net,
                r_multiple=net / p.risk_amount,
                exit_reason=reason,
                balance_after_trade=balance,
                capped=p.capped,
                ambiguous=ambiguous,
            )
        )

    for i in range(len(b.t)):
        t = b.t[i]
        if t < from_ms:
            continue
        if t > to_ms:
            break
        last_i = i
        op, hi, lo, cl = b.o[i], b.h[i], b.low[i], b.c[i]
        # 1) 이 시각에 쓸 수 있게 된 붕괴 사건
        while p_ev < len(ev) and ev[p_ev].at_ms <= t:
            e = ev[p_ev]
            p_ev += 1
            if pos is not None or pending is not None:
                run.busy += 1
            else:
                if setup is not None:
                    run.replaced += 1
                setup, setup_end = e, e.at_ms + cfg.window * MIN15_MS
        # 2) 대기 중인 진입을 이 봉 시가에
        if pending is not None:
            bd, sig_ms, sig_atr = pending
            pending = None
            entry = op * (1 - cfg.slippage)
            stop = entry + sig_atr * cfg.atr_mult
            dist = stop - entry
            risk_amount = balance * cfg.risk
            qty = risk_amount / dist
            capped = qty * entry > balance * cfg.max_leverage
            if capped:
                qty = balance * cfg.max_leverage / entry
            pos = _Pos(
                bd,
                sig_ms,
                t,
                entry,
                stop,
                entry - cfg.rr * dist,
                qty,
                qty * dist,
                sig_atr,
                entry * qty * cfg.fee,
                capped,
            )
        # 3) 포지션: funding → 손절 · 익절
        if pos is not None:
            p = pos
            rate = fund.get(t)
            if cfg.funding and rate is not None and p.entry_ms < t:
                p.funding += p.qty * op * rate
            hit_stop = hi >= p.stop
            hit_tp = lo < p.tp
            reason: str | None = None
            ambiguous = hit_stop and hit_tp
            if ambiguous and resolver is not None:
                reason = resolver(t, p.stop, p.tp)
            if reason is None:
                reason = "stop" if hit_stop else "take_profit" if hit_tp else None
            if reason == "stop":
                close(p, i, max(p.stop, op) * (1 + cfg.slippage), "stop", ambiguous)
                pos = None
            elif reason == "take_profit":
                close(p, i, min(p.tp, op) * (1 + cfg.slippage), "take_profit", ambiguous)
                pos = None
            else:
                run.marks.append((t, balance + (p.entry - cl) * p.qty + p.funding - p.entry_fee))
            continue
        # 4) 포지션 없음: 되돌림 거절이면 다음 봉 시가 진입 예약
        if setup is not None:
            if t >= setup_end:
                run.expired += 1
                setup = None
            else:
                level = setup.level
                lower, upper = level * (1 - cfg.tolerance), level * (1 + cfg.tolerance)
                ai = a[i]
                touched = hi >= lower and lo <= upper
                if touched and cl < level and cl < op and ai is not None:
                    pending = (setup, t, ai)
                    setup = None
    if pos is not None and last_i >= 0:
        close(pos, last_i, b.c[last_i] * (1 + cfg.slippage), "end", False)
    return run


def with_costs(cfg: Config, per_side: float) -> Config:
    """수수료 + 미끄럼 = ``per_side`` 가 되게 수수료만 바꾼다(미끄럼 0.02% 유지)."""
    return replace(cfg, fee=per_side - cfg.slippage)


# ------------------------------------------------------------------ 성과


def max_streak(trades: Sequence[Trade], *, wins: bool) -> int:
    best = cur = 0
    for tr in trades:
        if (tr.net_pnl > 0) == wins:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def daily_equity(
    trades: Sequence[Trade],
    marks: Sequence[tuple[int, float]],
    start: float,
    from_ms: int,
    to_ms: int,
) -> list[tuple[int, float]]:
    """날마다(UTC) 마지막 평가 자산. 포지션이 없으면 잔고, 있으면 그 봉 종가 평가."""
    events: list[tuple[int, int, float]] = [(t, 0, v) for t, v in marks]
    events += [(tr.exit_ms, 1, tr.balance_after_trade) for tr in trades]
    events.sort()
    out: list[tuple[int, float]] = []
    eq = start
    k = 0
    day = from_ms - from_ms % DAY_MS
    while day <= to_ms:
        end = day + DAY_MS
        while k < len(events) and events[k][0] < end:
            eq = events[k][2]
            k += 1
        out.append((day, eq))
        day = end
    return out


@dataclass(frozen=True, slots=True)
class Drawdown:
    peak_ms: int
    trough_ms: int
    recovery_ms: int | None
    depth: float


def drawdowns(curve: Sequence[tuple[int, float]], top: int = 10) -> list[Drawdown]:
    """겹치지 않는 낙폭 구간, 깊은 순 ``top`` 개."""
    out: list[Drawdown] = []
    if not curve:
        return out
    peak_t, peak = curve[0]
    trough_t, trough = peak_t, peak
    for t, v in curve[1:]:
        if v >= peak:
            if trough < peak:
                out.append(Drawdown(peak_t, trough_t, t, 1 - trough / peak))
            peak_t, peak, trough_t, trough = t, v, t, v
        elif v < trough:
            trough_t, trough = t, v
    if trough < peak:
        out.append(Drawdown(peak_t, trough_t, None, 1 - trough / peak))
    return sorted(out, key=lambda d: -d.depth)[:top]


def _ratio(returns: Sequence[float], downside: bool) -> float:
    if len(returns) < 2:
        return 0.0
    m = statistics.fmean(returns)
    if downside:
        sd = math.sqrt(statistics.fmean(min(r, 0.0) ** 2 for r in returns))
    else:
        sd = statistics.pstdev(returns)
    return m / sd * math.sqrt(365) if sd > 0 else 0.0


def summary(
    trades: Sequence[Trade], curve: Sequence[tuple[int, float]], start: float
) -> dict[str, float | int | None]:
    """사용자 §14 지표. ``curve`` 는 ``daily_equity`` (샤프 · 소르티노 · CAGR · MDD)."""
    n = len(trades)
    net = [tr.net_pnl for tr in trades]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    rs = [tr.r_multiple for tr in trades]
    gp, gl = sum(wins), -sum(losses)
    end = curve[-1][1] if curve else start
    years = (curve[-1][0] - curve[0][0]) / (365.25 * DAY_MS) if len(curve) > 1 else 0.0
    daily = [curve[k][1] / curve[k - 1][1] - 1 for k in range(1, len(curve))]
    holds = [(tr.exit_ms + MIN15_MS - tr.entry_ms) / HOUR_MS for tr in trades]
    trade_ret = [tr.net_pnl / (tr.balance_after_trade - tr.net_pnl) for tr in trades]
    peak = start
    mdd_abs = mdd_pct = 0.0
    for _, v in curve:
        peak = max(peak, v)
        mdd_abs = max(mdd_abs, peak - v)
        mdd_pct = max(mdd_pct, 1 - v / peak)
    return {
        "total_trades": n,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": len(wins) / n if n else None,
        "gross_profit": gp,
        "gross_loss": gl,
        "net_profit": sum(net),
        "total_return_pct": end / start - 1,
        "cagr": (end / start) ** (1 / years) - 1 if years > 0 and end > 0 else None,
        "avg_trade_return": statistics.fmean(trade_ret) if n else None,
        "avg_win": statistics.fmean(wins) if wins else None,
        "avg_loss": statistics.fmean(losses) if losses else None,
        "avg_r": statistics.fmean(rs) if n else None,
        "r_sd": statistics.stdev(rs) if n > 1 else None,
        "profit_factor": gp / gl if gl > 0 else None,
        "expectancy": statistics.fmean(net) if n else None,
        "mdd": mdd_abs,
        "mdd_pct": mdd_pct,
        "sharpe": _ratio(daily, downside=False),
        "sortino": _ratio(daily, downside=True),
        "max_consecutive_wins": max_streak(trades, wins=True),
        "max_consecutive_losses": max_streak(trades, wins=False),
        "avg_hold_hours": statistics.fmean(holds) if n else None,
        "median_hold_hours": statistics.median(holds) if n else None,
        "funding_total": sum(tr.funding_fee for tr in trades),
        "fees_total": sum(tr.entry_fee + tr.exit_fee for tr in trades),
        "capped_trades": sum(tr.capped for tr in trades),
        "ambiguous_bars": sum(tr.ambiguous for tr in trades),
    }
