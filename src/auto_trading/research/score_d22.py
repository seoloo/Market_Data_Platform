"""문서 207 — D22 종합 점수(지표 · 엘리엇 · 삼각형 · 캔들) 낙관 탐색. 순수 함수, 표준 라이브러리만.

봉 t 의 특징은 t 종가까지(꼭짓점은 확정 봉 이후)만 쓴다. 점수 = 특징의 가중합.
포지션은 t 종가에 정하고 t+1 시가에 체결, t+1 시가 → t+2 시가 수익을 받는다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import cast

from auto_trading.backtest.indicator_signals import ema, rsi
from auto_trading.core.types import Candle
from auto_trading.research.elliott.geometry_ew6 import (
    DIAGONAL,
    EXPANDING_TRIANGLE,
    FLAT,
    IMPULSE,
    RUNNING_TRIANGLE,
    TRIANGLE,
    ZIGZAG,
    Pivot6,
    adaptive_pivots,
    classify_five,
    classify_three,
)

__all__ = [
    "FEATURES",
    "Bars",
    "Sim",
    "features",
    "fit_ridge",
    "fit_univariate",
    "forward_returns",
    "scores",
    "simulate",
]

EW_ACTIVE = 6
"""엘리엇 · 다이버전스 표는 꼭짓점 확정 뒤 6 봉 동안 켜져 있다."""


@dataclass(frozen=True, slots=True)
class Bars:
    t: Sequence[int]
    o: Sequence[float]
    h: Sequence[float]
    low: Sequence[float]
    c: Sequence[float]


@dataclass(frozen=True, slots=True)
class _Hlc:
    high: float
    low: float
    close: float


GROUPS = {
    "trend": ("ema_align", "above200", "slope50", "donchian"),
    "momentum": ("rsi", "rsi_extreme", "macd", "macd_cross"),
    "ichimoku": ("cloud", "tk"),
    "bollinger": ("pctb", "bb_break"),
    "fib": ("fib_zone",),
    "divergence": ("div",),
    "structure": ("hh_hl",),
    "elliott": (
        "ew_imp_m2",
        "ew_tri_m2",
        "ew_corr_m2",
        "ew_imp_m3",
        "ew_tri_m3",
        "ew_corr_m3",
    ),
    "candle": ("engulf", "hammer_star", "star3", "soldiers", "doji_rev", "marubozu"),
}
FEATURES: tuple[str, ...] = tuple(f for g in GROUPS.values() for f in g)


def _sign(x: float) -> float:
    return 1.0 if x > 0 else -1.0 if x < 0 else 0.0


def _pivots(b: Bars, m: float) -> list[Pivot6]:
    hlc = [_Hlc(h, lo, c) for h, lo, c in zip(b.h, b.low, b.c, strict=True)]
    return adaptive_pivots(cast(Sequence[Candle], hlc), m)


def _active(n: int, events: Sequence[tuple[int, float]], span: int) -> list[float]:
    """(확정 봉, 표) 사건을 확정 봉부터 ``span`` 봉 동안 켠다(나중 사건이 덮어씀)."""
    out = [0.0] * n
    for t, v in sorted(events):
        for k in range(t, min(n, t + span)):
            out[k] = v
    return out


def _elliott(b: Bars, m: float) -> dict[str, list[float]]:
    """꼭짓점 k 확정 때 (0..k) 로 판독. 표 = −꼭짓점 방향(고점에서 끝나면 숏):
    충격 · 대각선 끝 → 반전, 삼각형(e 파) 끝 → e 반대로 돌파, 지그재그 · 플랫(C 파) 끝 → C 반대."""
    piv = _pivots(b, m)
    imp: list[tuple[int, float]] = []
    tri: list[tuple[int, float]] = []
    cor: list[tuple[int, float]] = []
    for k, p in enumerate(piv):
        vote = -float(p.kind)
        if k >= 5:
            five = classify_five([x.price for x in piv[k - 5 : k + 1]])
            if five & {IMPULSE, DIAGONAL}:
                imp.append((p.confirmed, vote))
            if five & {TRIANGLE, RUNNING_TRIANGLE, EXPANDING_TRIANGLE}:
                tri.append((p.confirmed, vote))
        if k >= 3 and classify_three([x.price for x in piv[k - 3 : k + 1]]) & {ZIGZAG, FLAT}:
            cor.append((p.confirmed, vote))
    n = len(b.c)
    tag = f"m{int(m)}"
    return {
        f"ew_imp_{tag}": _active(n, imp, EW_ACTIVE),
        f"ew_tri_{tag}": _active(n, tri, EW_ACTIVE),
        f"ew_corr_{tag}": _active(n, cor, EW_ACTIVE),
    }


def _candles(b: Bars) -> dict[str, list[float]]:
    n = len(b.c)
    o, h, lo, c = b.o, b.h, b.low, b.c
    out = {k: [0.0] * n for k in GROUPS["candle"]}
    for t in range(5, n):
        body = abs(c[t] - o[t])
        rng = h[t] - lo[t]
        if rng <= 0:
            continue
        up, dn = h[t] - max(o[t], c[t]), min(o[t], c[t]) - lo[t]
        prior = c[t - 1] - c[t - 5]
        # 장악형
        if c[t] > o[t] and c[t - 1] < o[t - 1] and c[t] >= o[t - 1] and o[t] <= c[t - 1]:
            out["engulf"][t] = 1.0
        elif c[t] < o[t] and c[t - 1] > o[t - 1] and c[t] <= o[t - 1] and o[t] >= c[t - 1]:
            out["engulf"][t] = -1.0
        # 망치형(하락 뒤) · 유성형(상승 뒤)
        if dn >= 2 * body and up <= max(body, 0.1 * rng) and prior < 0:
            out["hammer_star"][t] = 1.0
        elif up >= 2 * body and dn <= max(body, 0.1 * rng) and prior > 0:
            out["hammer_star"][t] = -1.0
        # 샛별 · 석별형
        b2, b1 = abs(c[t - 2] - o[t - 2]), abs(c[t - 1] - o[t - 1])
        mid2 = (o[t - 2] + c[t - 2]) / 2
        if c[t - 2] < o[t - 2] and b1 < 0.5 * b2 and c[t] > o[t] and c[t] > mid2:
            out["star3"][t] = 1.0
        elif c[t - 2] > o[t - 2] and b1 < 0.5 * b2 and c[t] < o[t] and c[t] < mid2:
            out["star3"][t] = -1.0
        # 적삼병 · 흑삼병
        if all(c[k] > o[k] and c[k] > c[k - 1] for k in (t - 2, t - 1, t)):
            out["soldiers"][t] = 1.0
        elif all(c[k] < o[k] and c[k] < c[k - 1] for k in (t - 2, t - 1, t)):
            out["soldiers"][t] = -1.0
        # 도지: 앞 흐름 반대
        if body <= 0.1 * rng:
            out["doji_rev"][t] = -_sign(prior)
        # 장대(마루보즈)
        if body >= 0.9 * rng:
            out["marubozu"][t] = _sign(c[t] - o[t])
    return out


def features(b: Bars) -> dict[str, list[float | None]]:
    """특징 이름 → 봉마다 값 (워밍업은 None)."""
    n = len(b.c)
    c = b.c
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    r = rsi(c, 14)
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [
        a - z if a is not None and z is not None else None for a, z in zip(e12, e26, strict=True)
    ]
    sig = _ema_opt(macd, 9)
    hist = [
        a - z if a is not None and z is not None else None for a, z in zip(macd, sig, strict=True)
    ]
    out: dict[str, list[float | None]] = {f: [None] * n for f in FEATURES}

    def mid(t: int, k: int) -> float:
        return (max(b.h[t - k + 1 : t + 1]) + min(b.low[t - k + 1 : t + 1])) / 2

    for t in range(n):
        a20, a50, a200 = e20[t], e50[t], e200[t]
        if a20 is not None and a50 is not None and a200 is not None:
            out["ema_align"][t] = 1.0 if a20 > a50 > a200 else -1.0 if a20 < a50 < a200 else 0.0
            out["above200"][t] = _sign(c[t] - a200)
        p50 = e50[t - 10] if t >= 10 else None
        if a50 is not None and p50 is not None:
            out["slope50"][t] = _sign(a50 - p50)
        if t >= 20:
            hi, lo = max(b.h[t - 20 : t]), min(b.low[t - 20 : t])
            out["donchian"][t] = 1.0 if c[t] > hi else -1.0 if c[t] < lo else 0.0
            w = c[t - 19 : t + 1]
            m, sd = statistics.fmean(w), statistics.pstdev(w)
            if sd > 0:
                out["pctb"][t] = (c[t] - m) / (2 * sd)
                out["bb_break"][t] = (
                    1.0 if c[t] > m + 2 * sd else -1.0 if c[t] < m - 2 * sd else 0.0
                )
        rt = r[t]
        if rt is not None:
            out["rsi"][t] = (rt - 50) / 50
            out["rsi_extreme"][t] = 1.0 if rt < 30 else -1.0 if rt > 70 else 0.0
        ht = hist[t]
        hp = hist[t - 1] if t >= 1 else None
        if ht is not None:
            out["macd"][t] = _sign(ht)
            if hp is not None:
                out["macd_cross"][t] = 1.0 if ht > 0 >= hp else -1.0 if ht < 0 <= hp else 0.0
        if t >= 52 + 26:
            tenkan, kijun = mid(t, 9), mid(t, 26)
            out["tk"][t] = _sign(tenkan - kijun)
            s = t - 26  # 26 봉 앞에 계산돼 지금 자리에 그려진 구름
            span_a = (mid(s, 9) + mid(s, 26)) / 2
            span_b = mid(s, 52)
            top, bot = max(span_a, span_b), min(span_a, span_b)
            out["cloud"][t] = 1.0 if c[t] > top else -1.0 if c[t] < bot else 0.0
    _swing_features(b, r, out)
    for name, vals in {**_elliott(b, 2.0), **_elliott(b, 3.0), **_candles(b)}.items():
        out[name] = [float(v) for v in vals]
    return out


def _ema_opt(values: Sequence[float | None], n: int) -> list[float | None]:
    start = next((k for k, v in enumerate(values) if v is not None), len(values))
    tail = ema([v for v in values[start:] if v is not None], n)
    return [None] * start + tail


def _swing_features(b: Bars, r: Sequence[float | None], out: dict[str, list[float | None]]) -> None:
    """피보나치 되돌림 구간 · RSI 다이버전스 · 고점/저점 구조 (확정 꼭짓점만)."""
    n = len(b.c)
    piv3 = _pivots(b, 3.0)
    piv2 = _pivots(b, 2.0)
    # 피보나치: 마지막 확정 다리 A→B 의 0.382 ~ 0.786 되돌림 안이면 다리 방향
    k = 0
    for t in range(n):
        while k < len(piv3) and piv3[k].confirmed <= t:
            k += 1
        if k >= 2:
            a, z = piv3[k - 2], piv3[k - 1]
            leg = z.price - a.price
            if leg != 0:
                retr = (z.price - b.c[t]) / leg
                out["fib_zone"][t] = _sign(leg) if 0.382 <= retr <= 0.786 else 0.0
    # 다이버전스 · 구조 (m=2)
    div: list[tuple[int, float]] = []
    highs = [p for p in piv2 if p.kind > 0]
    lows = [p for p in piv2 if p.kind < 0]
    for seq, kind in ((lows, -1), (highs, +1)):
        for a, z in pairwise(seq):
            ra, rz = r[a.index], r[z.index]
            if ra is None or rz is None:
                continue
            if kind < 0 and z.price < a.price and rz > ra:
                div.append((z.confirmed, 1.0))
            if kind > 0 and z.price > a.price and rz < ra:
                div.append((z.confirmed, -1.0))
    for t, v in enumerate(_active(n, div, EW_ACTIVE)):
        out["div"][t] = v
    hi_i = lo_i = 0
    for t in range(n):
        while hi_i < len(highs) and highs[hi_i].confirmed <= t:
            hi_i += 1
        while lo_i < len(lows) and lows[lo_i].confirmed <= t:
            lo_i += 1
        if hi_i >= 2 and lo_i >= 2:
            hh = highs[hi_i - 1].price > highs[hi_i - 2].price
            hl = lows[lo_i - 1].price > lows[lo_i - 2].price
            out["hh_hl"][t] = 1.0 if hh and hl else -1.0 if not hh and not hl else 0.0


def forward_returns(b: Bars, horizon: int) -> list[float | None]:
    """t 종가 결정 → t+1 시가 ~ t+1+horizon 시가 수익."""
    n = len(b.o)
    return [
        b.o[t + 1 + horizon] / b.o[t + 1] - 1 if t + 1 + horizon < n else None for t in range(n)
    ]


# ------------------------------------------------------------------ 가중치


@dataclass(frozen=True, slots=True)
class Model:
    names: tuple[str, ...]
    mean: tuple[float, ...]
    sd: tuple[float, ...]
    weights: tuple[float, ...]


def _rows(
    feats: Mapping[str, Sequence[float | None]], names: Sequence[str], idx: Sequence[int]
) -> list[int]:
    return [t for t in idx if all(feats[f][t] is not None for f in names)]


def _standardize(
    feats: Mapping[str, Sequence[float | None]], names: Sequence[str], rows: Sequence[int]
) -> tuple[list[float], list[float]]:
    mean, sd = [], []
    for f in names:
        v = [cast(float, feats[f][t]) for t in rows]
        mean.append(statistics.fmean(v))
        s = statistics.pstdev(v)
        sd.append(s if s > 0 else 1.0)
    return mean, sd


def fit_univariate(
    feats: Mapping[str, Sequence[float | None]],
    names: Sequence[str],
    target: Sequence[float | None],
    idx: Sequence[int],
) -> Model:
    """가중치 = 표준화 특징과 앞 수익의 상관 (TRAIN)."""
    rows = [t for t in _rows(feats, names, idx) if target[t] is not None]
    mean, sd = _standardize(feats, names, rows)
    y = [cast(float, target[t]) for t in rows]
    w = []
    for j, f in enumerate(names):
        x = [(cast(float, feats[f][t]) - mean[j]) / sd[j] for t in rows]
        w.append(statistics.correlation(x, y) if statistics.pstdev(x) > 0 else 0.0)
    return Model(tuple(names), tuple(mean), tuple(sd), tuple(w))


def fit_ridge(
    feats: Mapping[str, Sequence[float | None]],
    names: Sequence[str],
    target: Sequence[float | None],
    idx: Sequence[int],
    alpha: float,
) -> Model:
    """표준화 특징 · 릿지 (X'X + αnI) β = X'y — 가우스 소거."""
    rows = [t for t in _rows(feats, names, idx) if target[t] is not None]
    mean, sd = _standardize(feats, names, rows)
    k = len(names)
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    ym = statistics.fmean(cast(float, target[t]) for t in rows)
    for t in rows:
        x = [(cast(float, feats[f][t]) - mean[j]) / sd[j] for j, f in enumerate(names)]
        y = cast(float, target[t]) - ym
        for i in range(k):
            xty[i] += x[i] * y
            xi = x[i]
            row = xtx[i]
            for j in range(k):
                row[j] += xi * x[j]
    for i in range(k):
        xtx[i][i] += alpha * len(rows)
    return Model(tuple(names), tuple(mean), tuple(sd), tuple(_solve(xtx, xty)))


def _solve(a: list[list[float]], y: list[float]) -> list[float]:
    n = len(y)
    m = [[*row, y[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        if abs(p) < 1e-15:
            continue
        for r in range(n):
            if r != col:
                f = m[r][col] / p
                if f:
                    for c in range(col, n + 1):
                        m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-15 else 0.0 for i in range(n)]


def scores(feats: Mapping[str, Sequence[float | None]], model: Model, n: int) -> list[float | None]:
    out: list[float | None] = [None] * n
    for t in range(n):
        s = 0.0
        for j, f in enumerate(model.names):
            v = feats[f][t]
            if v is None:
                break
            s += model.weights[j] * (v - model.mean[j]) / model.sd[j]
        else:
            out[t] = s
    return out


# ------------------------------------------------------------------ 매매


@dataclass(frozen=True, slots=True)
class Sim:
    returns: list[float]
    """봉마다 순수익 (t: t 종가 결정 → t+1 시가 ~ t+2 시가)."""
    positions: list[float]
    trades: int
    long_pnl: float
    short_pnl: float
    cost: float
    funding: float


def positions_from(score: Sequence[float | None], lo: float, hi: float, mode: str) -> list[float]:
    """점수 > hi 롱 · < lo 숏 · 사이 현금. ``mode``: both · long · short."""
    out = []
    for s in score:
        p = 0.0
        if s is not None:
            if s > hi and mode != "short":
                p = 1.0
            elif s < lo and mode != "long":
                p = -1.0
        out.append(p)
    return out


def simulate(
    b: Bars,
    pos: Sequence[float],
    idx: Sequence[int],
    per_side: float,
    funding: Callable[[int], float] | None = None,
) -> Sim:
    """``idx`` 의 봉마다 결정. 비용 = 편도 × |비중 변화|.

    funding(t) = t+1 ~ t+2 시가 사이 rate 합 (롱은 내고 숏은 받는다)."""
    rets: list[float] = []
    poss: list[float] = []
    prev = 0.0
    trades = 0
    lp = sp = cost = fund = 0.0
    n = len(b.o)
    for t in idx:
        if t + 2 >= n:
            break
        p = pos[t]
        r = b.o[t + 2] / b.o[t + 1] - 1
        c = per_side * abs(p - prev)
        f = -p * funding(t) if funding is not None and p != 0 else 0.0
        if p != prev:
            trades += 1
        g = p * r
        if p > 0:
            lp += g
        elif p < 0:
            sp += g
        rets.append(g - c + f)
        poss.append(p)
        cost += c
        fund += f
        prev = p
    return Sim(rets, poss, trades, lp, sp, cost, fund)


def stats(rets: Sequence[float], bars_per_year: float) -> dict[str, float]:
    if not rets:
        return {"cagr": 0.0, "sharpe": 0.0, "mdd": 0.0, "total": 0.0}
    eq = peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    years = len(rets) / bars_per_year
    sd = statistics.pstdev(rets)
    return {
        "cagr": eq ** (1 / years) - 1 if eq > 0 else -1.0,
        "sharpe": statistics.fmean(rets) / sd * math.sqrt(bars_per_year) if sd > 0 else 0.0,
        "mdd": mdd,
        "total": eq - 1,
    }
