"""문서 217 — 큰 하락 뒤 '추가 하락 실패 + 사전 가격대 회복' 코인 USD-M 롱.

순수 계산, 표준 라이브러리만. 완성 UTC 일봉만 쓴다.
체결은 일봉 근사(장중 순서를 모름 — 보수적으로 처리)이며 실제 체결 검증이 아니다.

- 사건: 종가 ≤ 0.70 × 90 일 최고 종가 → 기준가 P = 그날 종가. 끝 = 종가 ≥ H 또는 180 일.
- 비교군 A(5 일 새 저점 없음 + 종가 > P) · I(사건 시작일) · D(5 일 새 저점 없음)
  · T(20 일 수익 > 0) · R(무작위 시점).
- 진입 다음 날 지정가(종가 × 1.003), 보호 = 신호일 사건 저점(장중, 비상 1%),
  3ATR 추적 · 120 일 (종가 기준, 다음 날 지정가).
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "Bar",
    "Config",
    "Episode",
    "Signal",
    "Trade",
    "arm_signals",
    "atr",
    "cluster_bootstrap",
    "find_episodes",
    "random_signal",
    "replay",
    "simulate_trade",
]

DAY = 86_400_000
HALF_DAY = DAY // 2


@dataclass(frozen=True, slots=True)
class Bar:
    t: int  # UTC 일 시작 ms
    o: float
    h: float
    lo: float
    c: float
    qv: float


@dataclass(frozen=True, slots=True)
class Config:
    drop: float = 0.30
    look: int = 90
    quiet: int = 5
    life: int = 180
    trend: int = 20
    random_max: int = 30
    risk: float = 0.0025
    min_dist: float = 0.02
    pos_cap: float = 0.25
    max_pos: int = 4
    gross_cap: float = 1.0
    entry_bp: float = 0.003
    exit_bp: float = 0.003
    exit_tries: int = 3
    atr_n: int = 20
    trail_atr: float = 3.0
    max_hold: int = 120
    participation: float = 0.01
    cost: float = 0.00095
    emergency: float = 0.01
    worst_fill: bool = False
    use_funding: bool = True
    min_notional: float = 5.0
    equity0: float = 10_000.0
    daily_loss: float = 0.02
    halt_dd: float = 0.30


@dataclass(frozen=True, slots=True)
class Episode:
    symbol: str
    i0: int  # 사건 시작 봉
    end: int  # 사건 마지막 봉 (포함)
    p: float  # 기준가 = 사건 시작일 종가
    h: float  # 그때 90 일 최고 종가


@dataclass(frozen=True, slots=True)
class Signal:
    arm: str
    symbol: str
    ep: Episode
    i: int  # 신호 봉 (그날 종가로 판단, 다음 날 진입)
    stop: float  # 신호일까지의 사건 저점


@dataclass
class Trade:
    arm: str
    symbol: str
    trigger_t: int
    signal_t: int
    entry_t: int
    exit_t: int
    qty: float
    entry_px: float
    exit_px: float
    entry_kind: str
    exit_reason: str
    dist: float
    atr: float
    notional: float
    fees: float = 0.0
    funding: float = 0.0
    emergency: float = 0.0
    gross: float = 0.0
    net: float = 0.0
    r: float = 0.0
    open_at_end: bool = False
    path_t: list[int] = field(default_factory=list)  # 일 시작 ms
    path_cum: list[float] = field(default_factory=list)  # 그날 종가 기준 누적 순손익
    path_notional: list[float] = field(default_factory=list)  # 그날 종가 기준 보유 명목 (청산일 0)

    def cum_at(self, day: int) -> float:
        k = bisect.bisect_right(self.path_t, day) - 1
        return self.path_cum[k] if k >= 0 else 0.0

    def notional_at(self, day: int) -> float:
        k = bisect.bisect_right(self.path_t, day) - 1
        return self.path_notional[k] if k >= 0 else 0.0


# ------------------------------------------------------------------ 사건 · 신호


def find_episodes(
    symbol: str, bars: Sequence[Bar], member: Callable[[int], bool], cfg: Config
) -> list[Episode]:
    """사건 시작은 그 달 종목군일 때만. 사건이 진행 중이면 새 사건 없음."""
    out: list[Episode] = []
    i = cfg.look - 1
    n = len(bars)
    while i < n:
        b = bars[i]
        hmax = max(x.c for x in bars[i - cfg.look + 1 : i + 1])
        if member(b.t) and b.c <= (1 - cfg.drop) * hmax:
            end = min(i + cfg.life, n - 1)
            for j in range(i + 1, end + 1):
                if bars[j].c >= hmax:
                    end = j
                    break
            out.append(Episode(symbol, i, end, b.c, hmax))
            i = end + 1
            continue
        i += 1
    return out


def arm_signals(bars: Sequence[Bar], ep: Episode, cfg: Config) -> dict[str, Signal]:
    """I · D · A · T 의 첫 신호 (사건마다 비교군당 최대 1 개). 신호 봉까지의 자료만 쓴다."""
    low, low_i = bars[ep.i0].lo, ep.i0
    out = {"I": Signal("I", ep.symbol, ep, ep.i0, low)}
    for j in range(ep.i0 + 1, ep.end + 1):
        b = bars[j]
        if b.lo < low:
            low, low_i = b.lo, j
        quiet = j - low_i >= cfg.quiet
        if "D" not in out and quiet:
            out["D"] = Signal("D", ep.symbol, ep, j, low)
        if "A" not in out and quiet and b.c > ep.p:
            out["A"] = Signal("A", ep.symbol, ep, j, low)
        if "T" not in out and j >= cfg.trend and b.c > bars[j - cfg.trend].c:
            out["T"] = Signal("T", ep.symbol, ep, j, low)
        if len(out) == 4:
            break
    return out


def random_signal(bars: Sequence[Bar], ep: Episode, k: int) -> Signal:
    j = min(ep.i0 + k, ep.end)
    low = min(b.lo for b in bars[ep.i0 : j + 1])
    return Signal("R", ep.symbol, ep, j, low)


def atr(bars: Sequence[Bar], i: int, n: int) -> float | None:
    """봉 i 까지 n 개 true range 평균 (봉 i 포함, 그 앞 종가 필요)."""
    if i < n:
        return None
    trs = []
    for k in range(i - n + 1, i + 1):
        b, pc = bars[k], bars[k - 1].c
        trs.append(max(b.h - b.lo, abs(b.h - pc), abs(b.lo - pc)))
    return statistics.fmean(trs)


# ------------------------------------------------------------------ 거래 한 건 (일봉 근사)


def _floor_step(q: float, step: float) -> float:
    return math.floor(q / step + 1e-9) * step if step > 0 else q


def simulate_trade(
    bars: Sequence[Bar],
    sig: Signal,
    equity: float,
    cfg: Config,
    *,
    step: float = 0.0,
    funding: Sequence[tuple[int, float]] = (),
    last_eval_day: int,
    bar_t: Sequence[int] | None = None,
    fund_t: Sequence[int] | None = None,
) -> Trade | str:
    """신호 하나를 거래로. 못 들어가면 이유 문자열.

    funding = (정산 ms, rate) 시간순. ``last_eval_day`` = 평가 마지막 일 시작 ms —
    자료가 그보다 먼저 끝나면 폐지로 보고 마지막 종가 + 비상 비용으로 정리한다.
    """
    n = len(bars)
    e = sig.i + 1
    if e >= n:
        return "no_next_bar"
    a = atr(bars, sig.i, cfg.atr_n)
    if a is None:
        return "warmup"
    ref = bars[sig.i].c
    dist = max((ref - sig.stop) / ref, cfg.min_dist)
    qty = min(cfg.risk * equity / (ref * dist), cfg.pos_cap * equity / ref)
    b = bars[e]
    if b.o <= sig.stop:
        return "gap_invalid"
    lim = ref * (1 + cfg.entry_bp)
    if b.o <= lim:
        px, kind = (lim if cfg.worst_fill else b.o), "open"
    elif b.lo < lim:
        px, kind = lim, "limit"
    else:
        return "unfilled"
    qty = min(qty, cfg.participation * b.qv / px)
    qty = _floor_step(qty, step)
    if qty <= 0 or qty * px < cfg.min_notional:
        return "min_notional"
    tr = Trade(
        sig.arm,
        sig.symbol,
        bars[sig.ep.i0].t,
        bars[sig.i].t,
        b.t,
        b.t,
        qty,
        px,
        px,
        kind,
        "",
        dist,
        a,
        qty * px,
    )
    tr.fees = qty * px * cfg.cost
    entry_time = b.t if kind == "open" else b.t + HALF_DAY
    stop = sig.stop
    ft_all: Sequence[int] = fund_t if fund_t is not None else [f[0] for f in funding]
    bt: Sequence[int] = bar_t if bar_t is not None else [x.t for x in bars]
    fptr = bisect.bisect_right(ft_all, entry_time)
    paid = 0.0

    def pay_until(t1: int) -> float:
        """정산 (진입, t1] 을 차례로 더한다.

        수량 × 그 정산일 종가(마크 대신) × rate — 롱이 양수 rate 를 낸다.
        """
        nonlocal fptr, paid
        while fptr < len(funding) and funding[fptr][0] <= t1:
            ft, rate = funding[fptr]
            fptr += 1
            if cfg.use_funding:
                k = max(0, bisect.bisect_right(bt, ft) - 1)
                paid += qty * bars[k].c * rate
        return paid

    def close_out(px_out: float, t_out: int, day_t: int, reason: str, extra: float) -> Trade:
        tr.exit_px, tr.exit_t, tr.exit_reason = px_out, day_t, reason
        tr.fees += qty * px_out * cfg.cost
        tr.emergency = qty * px_out * extra
        tr.funding = pay_until(t_out)
        tr.gross = qty * (px_out - px)
        tr.net = tr.gross - tr.fees - tr.funding - tr.emergency
        tr.r = tr.net / (cfg.risk * equity)
        tr.path_t.append(day_t)
        tr.path_cum.append(tr.net)
        tr.path_notional.append(0.0)
        return tr

    def mark(j: int) -> None:
        c = bars[j].c
        cum = qty * (c - px) - tr.fees - pay_until(bars[j].t + DAY - 1)
        tr.path_t.append(bars[j].t)
        tr.path_cum.append(cum)
        tr.path_notional.append(qty * c)

    # 진입일: 지정가 체결 뒤 보호선에 닿으면 체결 뒤 손절로 본다 (보수)
    if b.lo <= stop:
        return close_out(min(stop, b.o), b.t + HALF_DAY, b.t, "stop", cfg.emergency)
    maxc = b.c
    pending: list[float] | None = None  # [한도, 시도 일수]
    reason = ""

    def end_of_day(j: int) -> None:
        nonlocal maxc, pending, reason
        maxc = max(maxc, bars[j].c)
        trail = max(stop, maxc - cfg.trail_atr * a)
        if pending is None:
            if bars[j].c < trail:
                pending, reason = [bars[j].c * (1 - cfg.exit_bp), 0.0], "trail"
            elif j - e >= cfg.max_hold:
                pending, reason = [bars[j].c * (1 - cfg.exit_bp), 0.0], "time"

    mark(e)
    end_of_day(e)
    for j in range(e + 1, n):
        bj = bars[j]
        if pending is not None:
            lim_x = pending[0]
            if pending[1] >= cfg.exit_tries:  # 3 일 안 되면 넷째 날 시가 + 비상
                return close_out(bj.o, bj.t, bj.t, reason + "_forced", cfg.emergency)
            if bj.o >= lim_x:
                return close_out(lim_x if cfg.worst_fill else bj.o, bj.t, bj.t, reason, 0.0)
        if bj.lo <= stop:
            t_out = bj.t if bj.o <= stop else bj.t + HALF_DAY
            return close_out(min(stop, bj.o), t_out, bj.t, "stop", cfg.emergency)
        if pending is not None:
            if bj.h > pending[0]:
                return close_out(pending[0], bj.t + HALF_DAY, bj.t, reason, 0.0)
            pending[1] += 1
        mark(j)
        end_of_day(j)
    last = bars[-1]
    if last.t < last_eval_day:  # 자료 끝 = 폐지 · 거래 중단
        tr.path_t.pop()
        tr.path_cum.pop()
        tr.path_notional.pop()
        return close_out(last.c, last.t + DAY - 1, last.t, "delisted", cfg.emergency)
    # 평가 끝: 열린 채 종가 평가 (청산 비용 미차감)
    tr.open_at_end = True
    tr.exit_t, tr.exit_px, tr.exit_reason = last.t, last.c, "open_at_end"
    tr.funding = pay_until(last.t + DAY - 1)
    tr.gross = qty * (last.c - px)
    tr.net = tr.path_cum[-1]
    tr.r = tr.net / (cfg.risk * equity)
    return tr


# ------------------------------------------------------------------ 계좌 (한도 · 정지)


@dataclass(frozen=True, slots=True)
class Candidate:
    sig: Signal
    entry_t: int
    priority: float  # 직전 30 일 거래대금 (큰 순)


def replay(
    cands: Sequence[Candidate],
    simulate: Callable[[Signal, float], Trade | str],
    days: Sequence[int],
    cfg: Config,
) -> dict[str, object]:
    """진입일 · 거래대금 순서로 받아들인다.

    진입일 e 앞의 날은 e 이전에 들어간 거래만으로 정해지므로, 그 자산 · 낙폭으로 한도를 검사한다.
    """
    order = sorted(cands, key=lambda c: (c.entry_t, -c.priority, c.sig.symbol))
    taken: list[Trade] = []
    skips: dict[str, int] = {}
    final: dict[int, float] = {}  # 확정된 날의 종가 자산
    day_list = list(days)
    ptr = 0
    peak = cfg.equity0
    halted_at: int | None = None

    def eq(day: int) -> float:
        return cfg.equity0 + sum(t.cum_at(day) for t in taken if t.entry_t <= day)

    def finalize(before: int) -> None:
        nonlocal ptr, peak, halted_at
        while ptr < len(day_list) and day_list[ptr] < before:
            d = day_list[ptr]
            v = eq(d)
            final[d] = v
            peak = max(peak, v)
            if halted_at is None and v <= peak * (1 - cfg.halt_dd):
                halted_at = d
            ptr += 1

    def skip(why: str) -> None:
        skips[why] = skips.get(why, 0) + 1

    for c in order:
        e = c.entry_t
        finalize(e)
        if halted_at is not None:
            skip("halted")
            continue
        prev = final.get(e - DAY, cfg.equity0)
        prev2 = final.get(e - 2 * DAY, cfg.equity0)
        if prev2 > 0 and prev / prev2 - 1 <= -cfg.daily_loss:
            skip("daily_loss")
            continue
        open_now = [t for t in taken if t.exit_t >= e]
        if any(t.symbol == c.sig.symbol for t in open_now):
            skip("same_symbol")
            continue
        if len(open_now) >= cfg.max_pos:
            skip("capacity")
            continue
        gross = sum(t.notional if t.entry_t == e else t.notional_at(e - DAY) for t in open_now)
        res = simulate(c.sig, prev)
        if isinstance(res, str):
            skip(res)
            continue
        if gross + res.notional > cfg.gross_cap * prev:
            skip("gross_cap")
            continue
        taken.append(res)
    finalize(day_list[-1] + 1 if day_list else 0)
    daily = []
    for d in day_list:
        v = final[d]
        expo = sum(t.notional_at(d) for t in taken if t.entry_t <= d <= t.exit_t)
        daily.append((d, v, expo / v if v > 0 else 0.0))
    return {"trades": taken, "skips": skips, "daily": daily, "halted_at": halted_at}


# ------------------------------------------------------------------ 통계


def cluster_bootstrap(
    items: Sequence[tuple[int, float]], reps: int = 2000, seed: int = 217
) -> tuple[float, float, float]:
    """(군집 키, 값) — 군집을 복원 추출한 평균의 (평균, 5%, 95%)."""
    groups: dict[int, list[float]] = {}
    for k, v in items:
        groups.setdefault(k, []).append(v)
    keys = sorted(groups)
    if not keys:
        return (math.nan, math.nan, math.nan)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        tot, cnt = 0.0, 0
        for _k in range(len(keys)):
            g = groups[keys[rng.randrange(len(keys))]]
            tot += sum(g)
            cnt += len(g)
        means.append(tot / cnt)
    means.sort()
    m = statistics.fmean(v for _, v in items)
    return (m, means[int(0.05 * reps)], means[int(0.95 * reps) - 1])
