"""문서 229 — 차트 지표 (순수 · 목록 입력 · 앞부분은 nan).

모두 봉 i 의 값은 봉 i 까지의 자료만 쓴다(미래 없음). 일목 구름만 26 봉 앞으로 민 값을 쓰므로
봉 i 의 구름 = 봉 i − 26 까지로 계산한 선행스팬이다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

NAN = float("nan")
L = list[float]


def _nan(n: int) -> L:
    return [NAN] * n


def sma(x: Sequence[float], n: int) -> L:
    out = _nan(len(x))
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x: Sequence[float], n: int) -> L:
    out = _nan(len(x))
    k = 2 / (n + 1)
    prev = NAN
    for i, v in enumerate(x):
        if math.isnan(v):
            continue
        if math.isnan(prev):
            if i >= n - 1 and not any(math.isnan(t) for t in x[i - n + 1 : i + 1]):
                prev = sum(x[i - n + 1 : i + 1]) / n
                out[i] = prev
            continue
        prev = prev + k * (v - prev)
        out[i] = prev
    return out


def wilder(x: Sequence[float], n: int) -> L:
    """와일더 평균 (RSI · ATR · ADX)."""
    out = _nan(len(x))
    prev = NAN
    vals: list[float] = []
    for i, v in enumerate(x):
        if math.isnan(v):
            continue
        if math.isnan(prev):
            vals.append(v)
            if len(vals) == n:
                prev = sum(vals) / n
                out[i] = prev
            continue
        prev = (prev * (n - 1) + v) / n
        out[i] = prev
    return out


def rsi(c: Sequence[float], n: int = 14) -> L:
    up = [NAN] + [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    dn = [NAN] + [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    au, ad = wilder(up, n), wilder(dn, n)
    out = _nan(len(c))
    for i in range(len(c)):
        if math.isnan(au[i]):
            continue
        out[i] = 100.0 if ad[i] == 0 else 100 - 100 / (1 + au[i] / ad[i])
    return out


def true_range(h: Sequence[float], lo: Sequence[float], c: Sequence[float]) -> L:
    return [h[0] - lo[0]] + [
        max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))
    ]


def atr(h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int = 14) -> L:
    return wilder(true_range(h, lo, c), n)


def _roll(x: Sequence[float], n: int, f: object) -> L:
    out = _nan(len(x))
    for i in range(n - 1, len(x)):
        w = x[i - n + 1 : i + 1]
        if not any(math.isnan(v) for v in w):
            out[i] = f(w)  # type: ignore[operator]
    return out


def highest(x: Sequence[float], n: int) -> L:
    return _roll(x, n, max)


def lowest(x: Sequence[float], n: int) -> L:
    return _roll(x, n, min)


def stoch(
    h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int = 14, k: int = 3, d: int = 3
) -> tuple[L, L]:
    hh, ll = highest(h, n), lowest(lo, n)
    fast = [
        NAN
        if math.isnan(hh[i])
        else (50.0 if hh[i] == ll[i] else 100 * (c[i] - ll[i]) / (hh[i] - ll[i]))
        for i in range(len(c))
    ]
    slow_k = _roll(fast, k, lambda w: sum(w) / len(w))
    return slow_k, _roll(slow_k, d, lambda w: sum(w) / len(w))


def stoch_rsi(c: Sequence[float], n: int = 14, k: int = 3) -> L:
    r = rsi(c, n)
    hh, ll = highest(r, n), lowest(r, n)
    raw = [
        NAN if math.isnan(hh[i]) else (0.5 if hh[i] == ll[i] else (r[i] - ll[i]) / (hh[i] - ll[i]))
        for i in range(len(c))
    ]
    return _roll(raw, k, lambda w: sum(w) / len(w))


def cci(h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int = 20) -> L:
    tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(len(c))]
    out = _nan(len(c))
    for i in range(n - 1, len(c)):
        w = tp[i - n + 1 : i + 1]
        m = sum(w) / n
        md = sum(abs(v - m) for v in w) / n
        out[i] = 0.0 if md == 0 else (tp[i] - m) / (0.015 * md)
    return out


def williams_r(h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int = 14) -> L:
    hh, ll = highest(h, n), lowest(lo, n)
    return [
        NAN
        if math.isnan(hh[i])
        else (-50.0 if hh[i] == ll[i] else -100 * (hh[i] - c[i]) / (hh[i] - ll[i]))
        for i in range(len(c))
    ]


def bollinger(c: Sequence[float], n: int = 20, k: float = 2.0) -> tuple[L, L, L]:
    mid = sma(c, n)
    sd = _roll(c, n, lambda w: math.sqrt(sum((v - sum(w) / len(w)) ** 2 for v in w) / len(w)))
    up = [m + k * s for m, s in zip(mid, sd, strict=True)]
    dn = [m - k * s for m, s in zip(mid, sd, strict=True)]
    return mid, up, dn


def macd(c: Sequence[float], f: int = 12, s: int = 26, sig: int = 9) -> tuple[L, L]:
    ef, es = ema(c, f), ema(c, s)
    line = [a - b for a, b in zip(ef, es, strict=True)]
    return line, ema(line, sig)


def psar(h: Sequence[float], lo: Sequence[float], step: float = 0.02, mx: float = 0.2) -> list[int]:
    """추세 방향 (+1 상승 · −1 하락)."""
    n = len(h)
    out = [0] * n
    if n < 2:
        return out
    up = h[1] >= h[0]
    sar = lo[0] if up else h[0]
    ep = h[1] if up else lo[1]
    af = step
    out[1] = 1 if up else -1
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, lo[i - 1], lo[i - 2])
            if lo[i] < sar:
                up, sar, ep, af = False, ep, lo[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, mx)
        else:
            sar = max(sar, h[i - 1], h[i - 2])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], step
            elif lo[i] < ep:
                ep, af = lo[i], min(af + step, mx)
        out[i] = 1 if up else -1
    return out


def supertrend(
    h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int = 10, m: float = 3.0
) -> list[int]:
    a = atr(h, lo, c, n)
    out = [0] * len(c)
    fu = fl = NAN
    d = 1
    for i in range(len(c)):
        if math.isnan(a[i]):
            continue
        mid = (h[i] + lo[i]) / 2
        bu, bl = mid + m * a[i], mid - m * a[i]
        fu = bu if math.isnan(fu) or bu < fu or c[i - 1] > fu else fu
        fl = bl if math.isnan(fl) or bl > fl or c[i - 1] < fl else fl
        if d == 1 and c[i] < fl:
            d = -1
        elif d == -1 and c[i] > fu:
            d = 1
        out[i] = d
    return out


def adx(h: Sequence[float], lo: Sequence[float], c: Sequence[float], n: int = 14) -> tuple[L, L, L]:
    pdm = [NAN] + [
        (h[i] - h[i - 1])
        if (h[i] - h[i - 1]) > (lo[i - 1] - lo[i]) and (h[i] - h[i - 1]) > 0
        else 0.0
        for i in range(1, len(c))
    ]
    mdm = [NAN] + [
        (lo[i - 1] - lo[i])
        if (lo[i - 1] - lo[i]) > (h[i] - h[i - 1]) and (lo[i - 1] - lo[i]) > 0
        else 0.0
        for i in range(1, len(c))
    ]
    tr = [NAN, *true_range(h, lo, c)[1:]]
    str_, sp, sm = wilder(tr, n), wilder(pdm, n), wilder(mdm, n)
    pdi = [NAN if math.isnan(t) or t == 0 else 100 * p / t for p, t in zip(sp, str_, strict=True)]
    mdi = [NAN if math.isnan(t) or t == 0 else 100 * m / t for m, t in zip(sm, str_, strict=True)]
    dx = [
        NAN if math.isnan(p) or math.isnan(m) or p + m == 0 else 100 * abs(p - m) / (p + m)
        for p, m in zip(pdi, mdi, strict=True)
    ]
    return wilder(dx, n), pdi, mdi


def ichimoku(h: Sequence[float], lo: Sequence[float], shift: int = 26) -> tuple[L, L, L, L]:
    """(전환선, 기준선, 봉 i 의 구름 위 · 아래 — 26 봉 앞에서 계산한 선행스팬 A · B)."""
    t = [(a + b) / 2 for a, b in zip(highest(h, 9), lowest(lo, 9), strict=True)]
    k = [(a + b) / 2 for a, b in zip(highest(h, 26), lowest(lo, 26), strict=True)]
    sa = [(a + b) / 2 for a, b in zip(t, k, strict=True)]
    sb = [(a + b) / 2 for a, b in zip(highest(h, 52), lowest(lo, 52), strict=True)]
    span_a = _nan(shift) + sa[: len(sa) - shift]
    span_b = _nan(shift) + sb[: len(sb) - shift]
    return t, k, span_a, span_b


def mfi(
    h: Sequence[float], lo: Sequence[float], c: Sequence[float], v: Sequence[float], n: int = 14
) -> L:
    tp = [(h[i] + lo[i] + c[i]) / 3 for i in range(len(c))]
    out = _nan(len(c))
    for i in range(n, len(c)):
        pos = sum(tp[j] * v[j] for j in range(i - n + 1, i + 1) if tp[j] > tp[j - 1])
        neg = sum(tp[j] * v[j] for j in range(i - n + 1, i + 1) if tp[j] < tp[j - 1])
        out[i] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
    return out


def obv(c: Sequence[float], v: Sequence[float]) -> L:
    out = [0.0]
    for i in range(1, len(c)):
        out.append(out[-1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0.0))
    return out


def vwap_daily(
    t: Sequence[int],
    h: Sequence[float],
    lo: Sequence[float],
    c: Sequence[float],
    v: Sequence[float],
) -> L:
    """UTC 하루마다 처음부터 누적한 VWAP (봉 i 까지)."""
    out: L = []
    day = -1
    pv = vv = 0.0
    for i in range(len(c)):
        d = t[i] // 86_400_000
        if d != day:
            day, pv, vv = d, 0.0, 0.0
        tp = (h[i] + lo[i] + c[i]) / 3
        pv += tp * v[i]
        vv += v[i]
        out.append(pv / vv if vv > 0 else c[i])
    return out
