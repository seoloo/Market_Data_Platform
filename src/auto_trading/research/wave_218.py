"""문서 218 — '파동에너지 이론' 5 장 공식 간단 확인 (순수).

docs/research/218-wave-energy-ma-stoch-quick-check.md. I/O 는 ``scripts/run_wave_218.py`` 가 한다.

* 이평선 5 · 10 · 20 · 60 · 120 · 240, 소파동 = 슬로 스토캐스틱 5-3-3 의 slow %K.
* 바닥은 다음 날 종가에서 확인(미래 값 없음), 쌍바닥 = 바로 앞 바닥과 3 ~ 30 일 · 사이 반등 15 이상.
* 결과는 다음 날 시가 진입 기준, 기준선 = 같은 상태의 모든 날.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

__all__ = [
    "MAS",
    "Bottom",
    "MonthSums",
    "bootstrap_excess",
    "find_bottoms",
    "slow_k",
    "sma",
    "states",
]

NAN = float("nan")
MAS = (5, 10, 20, 60, 120, 240)
TROUGH_MAX = 30.0
GAP_MIN, GAP_MAX = 3, 30
BOUNCE = 15.0


def sma(x: Sequence[float], n: int) -> list[float]:
    """단순 이동평균 (앞 n-1 칸은 nan)."""
    out = [NAN] * len(x)
    run = 0.0
    for t, v in enumerate(x):
        run += v
        if t >= n:
            run -= x[t - n]
        if t >= n - 1:
            out[t] = run / n
    return out


def _sma_nan(x: Sequence[float], n: int) -> list[float]:
    out = [NAN] * len(x)
    for t in range(n - 1, len(x)):
        w = x[t - n + 1 : t + 1]
        if not any(math.isnan(v) for v in w):
            out[t] = sum(w) / n
    return out


def slow_k(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 5, s: int = 3
) -> list[float]:
    """slow %K = fast %K 의 s 일 평균. 범위가 0 인 날은 50."""
    fast = [NAN] * len(close)
    for t in range(n - 1, len(close)):
        hh, ll = max(high[t - n + 1 : t + 1]), min(low[t - n + 1 : t + 1])
        fast[t] = 50.0 if hh == ll else 100.0 * (close[t] - ll) / (hh - ll)
    return _sma_nan(fast, s)


@dataclass(frozen=True, slots=True)
class Bottom:
    t: int
    """신호일 (두 번째 바닥 다음 날 — 이날 종가에서 확인)."""
    higher: bool
    """저점상승(두 번째 %K 바닥이 더 높음)."""
    triple: bool


def _troughs(k: Sequence[float]) -> list[int]:
    return [
        j
        for j in range(1, len(k) - 1)
        if not any(math.isnan(v) for v in k[j - 1 : j + 2])
        and k[j - 1] > k[j] < k[j + 1]
        and k[j] <= TROUGH_MAX
    ]


def _pair(k: Sequence[float], j1: int, j2: int) -> bool:
    if not GAP_MIN <= j2 - j1 <= GAP_MAX:
        return False
    return max(k[j1 : j2 + 1]) >= max(k[j1], k[j2]) + BOUNCE


def find_bottoms(k: Sequence[float]) -> list[Bottom]:
    """쌍바닥(쓰리바닥 포함) 신호 — j2 는 바로 앞 바닥 j1 과 짝."""
    tr = _troughs(k)
    out: list[Bottom] = []
    prev_ok = False
    for i in range(1, len(tr)):
        ok = _pair(k, tr[i - 1], tr[i])
        if ok:
            out.append(Bottom(tr[i] + 1, k[tr[i]] > k[tr[i - 1]], prev_ok))
        prev_ok = ok
    return out


def states(close: Sequence[float]) -> list[str]:
    """날마다 'A'(정배열 · 20 일선 위) · 'B'(정배열 · 60 < 종가 < 20) · 'C'(역배열) · ''."""
    ms = [sma(close, n) for n in MAS]
    out = []
    for t, c in enumerate(close):
        col = [m[t] for m in ms]
        if any(math.isnan(v) for v in col):
            out.append("")
            continue
        bull = all(a > b for a, b in pairwise(col))
        bear = all(a < b for a, b in pairwise(col))
        m20, m60 = col[2], col[3]
        if bull and c > m20:
            out.append("A")
        elif bull and m60 < c < m20:
            out.append("B")
        elif bear:
            out.append("C")
        else:
            out.append("")
    return out


class MonthSums:
    """이름(예: 'A_LL') → 달 → [합, 개수]. 묶음 부트스트랩용."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, list[float]]] = {}

    def add(self, name: str, month: str, value: float) -> None:
        cell = self.data.setdefault(name, {}).setdefault(month, [0.0, 0.0])
        cell[0] += value
        cell[1] += 1

    def mean(self, name: str) -> float:
        rows = self.data.get(name, {}).values()
        n = sum(r[1] for r in rows)
        return sum(r[0] for r in rows) / n if n else NAN

    def count(self, name: str) -> int:
        return int(sum(r[1] for r in self.data.get(name, {}).values()))

    def months(self) -> list[str]:
        return sorted({m for d in self.data.values() for m in d})


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def bootstrap_excess(
    sums: MonthSums,
    sig: str,
    base: str,
    reps: int = 2000,
    seed: int = 218,
    months: Sequence[str] | None = None,
) -> tuple[float, float, float]:
    """(점추정, 2.5%, 97.5%) — 평균(sig) − 평균(base), 달 단위로 다시 뽑기."""
    ms = list(months) if months is not None else sums.months()
    zero = [0.0, 0.0]
    sd, bd = sums.data.get(sig, {}), sums.data.get(base, {})
    rows = [(*sd.get(m, zero), *bd.get(m, zero)) for m in ms]
    tot = [sum(r[i] for r in rows) for i in range(4)]
    if not rows or tot[1] == 0 or tot[3] == 0:
        return NAN, NAN, NAN
    point = tot[0] / tot[1] - tot[2] / tot[3]
    rng = random.Random(seed)
    boot = []
    for _ in range(reps):
        a = b = c = d = 0.0
        for r in rng.choices(rows, k=len(rows)):
            a += r[0]
            b += r[1]
            c += r[2]
            d += r[3]
        if b and d:
            boot.append(a / b - c / d)
    return point, _quantile(boot, 0.025), _quantile(boot, 0.975)
