"""문서 229 — 지표 전부로 숏 진입 조합 탐색 (순수 계산).

docs/research/229-all-indicator-short-search.md.
자료 · Laya · 실행은 ``scripts/run_short_search_229.py``.

* :class:`Coin` — 4 시간봉 · 지표 · 국면(직전 완성 일봉) · 파생 지표를 한 번에 계산해 둔다.
* 거래 결과는 (코인, 진입 신호 봉, 청산, 손절)에만 달려 있어 한 번만 계산해 재사용한다.
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from auto_trading.research import indicators_229 as ind

__all__ = [
    "EXITS",
    "REGIMES",
    "STOPS",
    "TRIGGERS",
    "Coin",
    "Trade",
    "build_coin",
    "laya_text",
    "run_config",
    "tstat",
]

H4 = 4 * 3_600_000
DAY = 86_400_000
FEE = 0.00095
SIZE = 0.5
STOP_SLIP = 0.002
NAN = float("nan")

REGIMES = ("R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")
TRIGGERS = tuple(f"T{i}" for i in range(1, 26))
EXITS = ("E1", "E2", "E3", "E4", "E5")
STOPS = ("S1", "S2", "S3")


def _isnan(*xs: float) -> bool:
    return any(math.isnan(x) for x in xs)


@dataclass
class Coin:
    name: str
    t: list[int]
    o: list[float]
    h: list[float]
    lo: list[float]
    c: list[float]
    v: list[float]
    ind: dict[str, list[float]] = field(default_factory=dict)
    regime: dict[str, list[bool]] = field(default_factory=dict)
    trig: dict[str, list[bool]] = field(default_factory=dict)
    fund_t: list[int] = field(default_factory=list)
    fund_r: list[float] = field(default_factory=list)
    trend_score: list[float] = field(default_factory=list)


def _daily(
    t: Sequence[int],
    o: Sequence[float],
    h: Sequence[float],
    lo: Sequence[float],
    c: Sequence[float],
) -> tuple[list[int], list[list[float]]]:
    days: dict[int, list[float]] = {}
    order: list[int] = []
    for i in range(len(t)):
        d = t[i] // DAY
        if d not in days:
            days[d] = [o[i], h[i], lo[i], c[i]]
            order.append(d)
        else:
            row = days[d]
            row[1] = max(row[1], h[i])
            row[2] = min(row[2], lo[i])
            row[3] = c[i]
    return order, [days[d] for d in order]


def build_coin(
    name: str,
    bars: Sequence[tuple[int, float, float, float, float, float]],
    funding: Sequence[tuple[int, float]],
    metrics: Mapping[int, tuple[float, float]] | None = None,
) -> Coin:
    """``bars`` = 4 시간봉 (시작 ms, 시가, 고가, 저가, 종가, 거래량).

    ``metrics`` = 봉 마감 ms → (OI, 상위 롱숏 계정 비율)."""
    t = [b[0] for b in bars]
    o = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    c = [b[4] for b in bars]
    v = [b[5] for b in bars]
    k = Coin(name, t, o, h, lo, c, v)
    k.fund_t = [x for x, _ in funding]
    k.fund_r = [r for _, r in funding]
    n = len(c)
    # ---- 4 시간 지표
    ix = k.ind
    ix["rsi"] = ind.rsi(c)
    ix["atr"] = ind.atr(h, lo, c)
    ix["sk"], ix["sd"] = ind.stoch(h, lo, c)
    ix["srsi"] = ind.stoch_rsi(c)
    ix["cci"] = ind.cci(h, lo, c)
    ix["wr"] = ind.williams_r(h, lo, c)
    ix["bbm"], ix["bbu"], ix["bbl"] = ind.bollinger(c)
    e20 = ind.ema(c, 20)
    a20 = ind.atr(h, lo, c, 20)
    ix["kcu"] = [a + 2 * b for a, b in zip(e20, a20, strict=True)]
    ix["e20"], ix["e50"] = e20, ind.ema(c, 50)
    ix["don"] = [NAN, *ind.lowest(lo, 20)[:-1]]  # 직전 20 봉 저점
    ix["macd"], ix["macds"] = ind.macd(c)
    psar = ind.psar(h, lo)
    st = ind.supertrend(h, lo, c)
    ix["mfi"] = ind.mfi(h, lo, c, v)
    ob = ind.obv(c, v)
    vw = ind.vwap_daily(t, h, lo, c, v)
    _, kij, _, _ = ind.ichimoku(h, lo)
    vavg = ind.sma(v, 20)
    ix["hh10"] = ind.highest(h, 10)
    # ---- 일봉 국면 (봉 i 마감 때 끝난 마지막 일봉)
    dorder, drows = _daily(t, o, h, lo, c)
    dc = [r[3] for r in drows]
    dh = [r[1] for r in drows]
    dl = [r[2] for r in drows]
    sma200 = ind.sma(dc, 200)
    e50d, e200d = ind.ema(dc, 50), ind.ema(dc, 200)
    adx_, pdi, mdi = ind.adx(dh, dl, dc)
    _, _, spa, spb = ind.ichimoku(dh, dl)
    std = ind.supertrend(dh, dl, dc)
    md, mds = ind.macd(dc)
    dts: list[float] = []
    for j in range(len(dc)):
        if j < 120:
            dts.append(NAN)
            continue
        s = [(dc[j] > dc[j - q]) - (dc[j] < dc[j - q]) for q in (20, 60, 120)]
        dts.append(sum(s) / 3)
    dpos = {d: j for j, d in enumerate(dorder)}
    reg = {r: [False] * n for r in REGIMES}
    k.trend_score = [NAN] * n
    for i in range(n):
        close_ms = t[i] + H4
        jd = dpos.get(close_ms // DAY - 1)  # 이 봉 마감 시각에 끝나 있는 마지막 일봉
        if jd is None:
            continue
        j = jd
        reg["R0"][i] = True
        ts = dts[j]
        k.trend_score[i] = ts
        reg["R1"][i] = not math.isnan(ts) and ts <= -1
        reg["R2"][i] = not math.isnan(ts) and ts <= 0
        reg["R3"][i] = not math.isnan(sma200[j]) and dc[j] < sma200[j]
        reg["R4"][i] = not _isnan(e50d[j], e200d[j]) and e50d[j] < e200d[j]
        reg["R5"][i] = not _isnan(adx_[j], pdi[j], mdi[j]) and adx_[j] > 25 and mdi[j] > pdi[j]
        reg["R6"][i] = not _isnan(spa[j], spb[j]) and dc[j] < min(spa[j], spb[j])
        reg["R7"][i] = std[j] == -1
        reg["R8"][i] = not _isnan(md[j], mds[j]) and md[j] < mds[j] and md[j] < 0
    k.regime = reg
    # ---- 4 시간 신호
    r_, sk, sd = ix["rsi"], ix["sk"], ix["sd"]

    def cross_dn(x: Sequence[float], lvl: float, i: int) -> bool:
        return not _isnan(x[i], x[i - 1]) and x[i - 1] >= lvl > x[i]

    def below_from_above(x: Sequence[float], ref: Sequence[float], i: int) -> bool:
        return not _isnan(ref[i], ref[i - 1]) and c[i - 1] >= ref[i - 1] and c[i] < ref[i]

    oi = [NAN] * n
    lsr = [NAN] * n
    if metrics:
        for i in range(n):
            m = metrics.get(t[i] + H4)
            if m:
                oi[i], lsr[i] = m
    fund_last = [NAN] * n
    for i in range(n):
        p = bisect.bisect_right(k.fund_t, t[i] + H4) - 1
        if p >= 0:
            fund_last[i] = k.fund_r[p]
    tr: dict[str, list[bool]] = {x: [False] * n for x in TRIGGERS}
    for i in range(25, n):
        tr["T1"][i] = cross_dn(r_, 70, i)
        tr["T2"][i] = cross_dn(r_, 60, i)
        tr["T3"][i] = cross_dn(r_, 50, i)
        tr["T4"][i] = (
            not _isnan(sk[i], sd[i], sk[i - 1], sd[i - 1])
            and sk[i - 1] >= 80
            and sk[i - 1] >= sd[i - 1]
            and sk[i] < sd[i]
        )
        tr["T5"][i] = cross_dn(ix["srsi"], 0.8, i)
        tr["T6"][i] = cross_dn(ix["cci"], 100, i)
        tr["T7"][i] = cross_dn(ix["wr"], -20, i)
        tr["T8"][i] = (
            not _isnan(ix["bbu"][i], ix["bbu"][i - 1])
            and c[i - 1] > ix["bbu"][i - 1]
            and c[i] < ix["bbu"][i]
        )
        tr["T9"][i] = (
            not _isnan(ix["kcu"][i], ix["kcu"][i - 1])
            and c[i - 1] > ix["kcu"][i - 1]
            and c[i] < ix["kcu"][i]
        )
        tr["T10"][i] = below_from_above(c, e20, i)
        tr["T11"][i] = below_from_above(c, ix["e50"], i)
        tr["T12"][i] = (
            not math.isnan(ix["don"][i]) and c[i] < ix["don"][i] and c[i - 1] >= ix["don"][i - 1]
        )
        tr["T13"][i] = (
            not _isnan(ix["macd"][i], ix["macds"][i], ix["macd"][i - 1], ix["macds"][i - 1])
            and ix["macd"][i - 1] >= ix["macds"][i - 1]
            and ix["macd"][i] < ix["macds"][i]
        )
        tr["T14"][i] = psar[i - 1] == 1 and psar[i] == -1
        tr["T15"][i] = st[i - 1] == 1 and st[i] == -1
        tr["T16"][i] = c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]
        tr["T17"][i] = not math.isnan(vavg[i - 1]) and v[i] >= 2 * vavg[i - 1] and c[i] < o[i]
        tr["T18"][i] = cross_dn(ix["mfi"], 80, i)
        tr["T19"][i] = c[i - 1] >= vw[i - 1] and c[i] < vw[i]
        if i >= 21 and not _isnan(r_[i], r_[i - 1]):
            # 직전 봉(i − 1)이 그 앞 19 봉의 고점보다 높은데 RSI 는 낮음 → 이번 봉 RSI 꺾임
            ph = max(range(i - 20, i - 1), key=lambda q: h[q])
            tr["T20"][i] = (
                h[i - 1] > h[ph]
                and not math.isnan(r_[ph])
                and r_[i - 1] < r_[ph]
                and r_[i] < r_[i - 1]
            )
        tr["T21"][i] = i >= 10 and c[i] > c[i - 10] and ob[i] < ob[i - 10] and c[i] < o[i]
        tr["T22"][i] = not math.isnan(fund_last[i]) and fund_last[i] >= 0.0003
        tr["T23"][i] = (
            i >= 6
            and not _isnan(oi[i], oi[i - 6])
            and oi[i - 6] > 0
            and oi[i] / oi[i - 6] - 1 >= 0.05
            and c[i] < c[i - 6]
        )
        tr["T24"][i] = (
            not math.isnan(lsr[i])
            and lsr[i] >= 2.0
            and (math.isnan(lsr[i - 1]) or lsr[i - 1] < 2.0)
        )
        tr["T25"][i] = below_from_above(c, kij, i)
    k.trig = tr
    ix["fund_last"] = fund_last
    ix["vratio"] = [
        NAN if math.isnan(vavg[i]) or vavg[i] == 0 else v[i] / vavg[i] for i in range(n)
    ]
    ix["adx4"], _, _ = ind.adx(h, lo, c)
    return k


@dataclass(frozen=True, slots=True)
class Trade:
    coin: str
    signal: int
    """신호 봉 번호."""
    entry_t: int
    exit_t: int
    exit_i: int
    ret: float
    """자본 대비 (0.5 배 반영) 순수익."""
    stopped: bool


def _trade(k: Coin, i: int, ex: str, sp: str) -> Trade | None:
    n = len(k.c)
    j = i + 1
    if j >= n or _isnan(k.ind["atr"][i]):
        return None
    entry = k.o[j]
    a = k.ind["atr"][i]
    if sp == "S1":
        stop = entry + 2 * a
    elif sp == "S2":
        hh = k.ind["hh10"][i]
        stop = max(hh + 0.5 * a, entry + 0.5 * a) if not math.isnan(hh) else entry + 2 * a
    else:
        stop = entry * 1.03
    max_bars = {"E1": 6, "E2": 12, "E3": 12, "E4": 12, "E5": 18}[ex]
    low_close = entry
    for q in range(j, min(n, j + max_bars + 1)):
        if k.h[q] >= stop:
            px = max(k.o[q], stop) * (1 + STOP_SLIP)
            return _finish(k, i, j, q, entry, px, k.t[q] + H4, True)
        held = q - j + 1
        done = held >= max_bars
        if ex == "E3":
            done = done or (not math.isnan(k.ind["rsi"][q]) and k.ind["rsi"][q] <= 30)
        elif ex == "E4":
            done = done or (not math.isnan(k.ind["bbm"][q]) and k.c[q] <= k.ind["bbm"][q])
        elif ex == "E5":
            low_close = min(low_close, k.c[q])
            if not math.isnan(k.ind["atr"][q]):
                stop = min(stop, low_close + 2 * k.ind["atr"][q])
        if done:
            if q + 1 >= n:
                return None
            return _finish(k, i, j, q + 1, entry, k.o[q + 1], k.t[q + 1], False)
    return None


def _finish(
    k: Coin, i: int, j: int, q: int, entry: float, px: float, exit_t: int, stopped: bool
) -> Trade:
    lo = bisect.bisect_left(k.fund_t, k.t[j])
    hi = bisect.bisect_left(k.fund_t, exit_t)
    fund = sum(k.fund_r[lo:hi])
    r = (entry - px) / entry + fund - FEE - FEE * px / entry
    return Trade(k.name, i, k.t[j], exit_t, q, SIZE * r, stopped)


class TradeCache:
    def __init__(self, coins: Sequence[Coin]) -> None:
        self.coins = {k.name: k for k in coins}
        self.cache: dict[tuple[str, int, str, str], Trade | None] = {}

    def get(self, coin: str, i: int, ex: str, sp: str) -> Trade | None:
        key = (coin, i, ex, sp)
        if key not in self.cache:
            self.cache[key] = _trade(self.coins[coin], i, ex, sp)
        return self.cache[key]


def run_config(
    cache: TradeCache,
    regime: str,
    trigger: str,
    ex: str,
    sp: str,
    t_from: int,
    t_to: int,
    keep: Callable[[str, int], bool] | None = None,
) -> list[Trade]:
    """코인마다 신호 순서대로, 보유 중 신호는 무시. 신호 봉 시각이 [t_from, t_to) 안인 것만."""
    out: list[Trade] = []
    for name, k in cache.coins.items():
        reg, trg = k.regime[regime], k.trig[trigger]
        lo = bisect.bisect_left(k.t, t_from)
        hi = bisect.bisect_left(k.t, t_to)
        busy_t = -1
        for i in range(lo, hi):
            if not (trg[i] and reg[i]) or i + 1 >= len(k.t) or k.t[i + 1] < busy_t:
                continue
            if keep is not None and not keep(name, i):
                continue
            tr = cache.get(name, i, ex, sp)
            if tr is None:
                continue
            out.append(tr)
            busy_t = tr.exit_t
    out.sort(key=lambda x: x.exit_t)
    return out


def tstat(trades: Sequence[Trade]) -> float:
    r = [x.ret for x in trades]
    if len(r) < 2:
        return NAN
    sd = statistics.stdev(r)
    return statistics.fmean(r) / sd * math.sqrt(len(r)) if sd > 0 else NAN


def total(trades: Sequence[Trade]) -> float:
    eq = 1.0
    for x in sorted(trades, key=lambda x: x.exit_t):
        eq *= 1 + x.ret
    return eq - 1


def random_totals(
    cache: TradeCache,
    n: int,
    ex: str,
    sp: str,
    t_from: int,
    t_to: int,
    reps: int = 1000,
    seed: int = 229,
) -> list[float]:
    rng = random.Random(seed)
    pool = []
    for name, k in cache.coins.items():
        lo, hi = bisect.bisect_left(k.t, t_from), bisect.bisect_left(k.t, t_to)
        pool += [(name, i) for i in range(lo, hi)]
    out = []
    for _ in range(reps):
        chosen: list[Trade] = []
        busy: dict[str, list[tuple[int, int]]] = {c: [] for c in cache.coins}
        tries = 0
        while len(chosen) < n and tries < n * 200:
            tries += 1
            name, i = rng.choice(pool)
            tr = cache.get(name, i, ex, sp)
            if tr is None or any(not (tr.exit_t <= a or tr.entry_t >= b) for a, b in busy[name]):
                continue
            busy[name].append((tr.entry_t, tr.exit_t))
            chosen.append(tr)
        out.append(total(chosen))
    return out


def laya_text(k: Coin, i: int) -> str:
    """진입 순간의 지표 문장 (날짜 · 가격 수준 없음)."""
    ix = k.ind
    c = k.c

    def pct(a: float) -> str:
        return "n/a" if math.isnan(a) else f"{a * 100:+.1f}%"

    def num(a: float, d: int = 1) -> str:
        return "n/a" if math.isnan(a) else f"{a:.{d}f}"

    r4 = c[i] / c[i - 1] - 1 if i >= 1 else NAN
    r1d = c[i] / c[i - 6] - 1 if i >= 6 else NAN
    r7d = c[i] / c[i - 42] - 1 if i >= 42 else NAN
    bbw = ix["bbu"][i] - ix["bbl"][i]
    pb = (c[i] - ix["bbl"][i]) / bbw if bbw and not math.isnan(bbw) else NAN
    coin = "Bitcoin" if k.name.startswith("BTC") else "Ethereum"
    e50 = ix["e50"][i]
    vs_e50 = c[i] / e50 - 1 if not math.isnan(e50) else NAN
    fund = ix["fund_last"][i] * 100
    return (
        f"{coin} perpetual futures, 4-hour chart snapshot. "
        f"Change last 4 hours: {pct(r4)}, last 1 day: {pct(r1d)}, last 7 days: {pct(r7d)}. "
        f"RSI(14): {num(ix['rsi'][i])}. Stochastic %K: {num(ix['sk'][i])}. "
        f"CCI(20): {num(ix['cci'][i], 0)}. "
        f"Bollinger %B: {num(pb, 2)}. "
        f"ATR as % of price: {num(ix['atr'][i] / c[i] * 100, 2)}%. "
        f"ADX(14): {num(ix['adx4'][i])}. Price vs EMA50: {pct(vs_e50)}. "
        f"Volume vs 20-bar average: {num(ix['vratio'][i], 2)}x. "
        f"Last funding rate: {num(fund, 4)}% per 8 hours. "
        f"Daily trend score from 20/60/120-day returns: {num(k.trend_score[i], 2)} "
        "(from -1 to 1)."
    )


def claude_veto(k: Coin, i: int) -> bool:
    """229 §4-1 — Laya 가 좋다고 해도 거부할 상황 (True 면 거부)."""
    ix = k.ind
    c = k.c
    r = ix["rsi"][i]
    e50 = ix["e50"][i]
    f = ix["fund_last"][i]
    ts = k.trend_score[i]
    return (
        (not math.isnan(r) and r <= 35)
        or (i >= 42 and c[i] / c[i - 42] - 1 <= -0.15)
        or (not math.isnan(f) and f < 0)
        or (not math.isnan(e50) and c[i] / e50 - 1 < -0.08)
        or (not math.isnan(ts) and ts > 0)
    )
