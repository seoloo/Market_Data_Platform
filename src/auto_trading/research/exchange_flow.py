"""문서 206 — BTC 거래소 흐름 충격 뒤 숏 (순수 계산, 표준 라이브러리만).

날짜 T 는 UTC 하루. 흐름 값 x_T 는 T 가 끝난 뒤(공개는 보통 T+1 01 ~ 02 시 UTC) 알 수 있다.
충격 점수 z_T 는 x_T 를 **T 앞** ``window`` 일(T 제외)의 평균 · 표본 SD 로 표준화한다.
진입은 T+1 06:00 UTC, 청산은 진입 + ``hold`` 일 같은 시각 (시간봉 시가).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "Trade",
    "circular_shift_p",
    "event_days",
    "forward_short",
    "holm",
    "non_overlapping",
    "series",
    "zscores",
]


def series(rows: Sequence[Mapping[str, float]], kind: str) -> list[float | None]:
    """원 지표 → 변수. ``I`` 입금 · ``O`` 출금 · ``N`` 순유입(I − O) · ``R`` 보유량 1 일 변화."""
    out: list[float | None] = []
    for k, r in enumerate(rows):
        i, o, s = r.get("in"), r.get("out"), r.get("sply")
        if kind == "I":
            out.append(i)
        elif kind == "O":
            out.append(o)
        elif kind == "N":
            out.append(i - o if i is not None and o is not None else None)
        elif kind == "R":
            prev = rows[k - 1].get("sply") if k > 0 else None
            out.append(s - prev if s is not None and prev is not None else None)
        else:
            raise ValueError(kind)
    return out


def zscores(x: Sequence[float | None], window: int) -> list[float | None]:
    """z_T = (x_T − 평균(x_{T−w..T−1})) / SD(같은 창). 창에 빈 값이 있거나 SD 0 이면 None."""
    out: list[float | None] = [None] * len(x)
    for t in range(window, len(x)):
        cur = x[t]
        win = x[t - window : t]
        if cur is None or any(v is None for v in win):
            continue
        vals = [v for v in win if v is not None]
        sd = statistics.stdev(vals)
        if sd > 0:
            out[t] = (cur - statistics.fmean(vals)) / sd
    return out


def event_days(z: Sequence[float | None], threshold: float) -> list[int]:
    return [t for t, v in enumerate(z) if v is not None and v > threshold]


def forward_short(entry: Sequence[float | None], t: int, hold: int) -> float | None:
    """T 의 신호 → ``entry[t]``(T+1 06:00 가격)에 숏, ``entry[t + hold]`` 에 청산. 숏 수익."""
    if t + hold >= len(entry):
        return None
    a, b = entry[t], entry[t + hold]
    if a is None or b is None:
        return None
    return -(b / a - 1)


@dataclass(frozen=True, slots=True)
class Trade:
    signal: int
    exit_signal: int
    """청산 가격이 ``entry[exit_signal]`` (= 날짜 exit_signal 다음 날 06:00)."""
    gross: float
    cost: float
    funding: float
    """받은 funding (+ 가 숏에 이익), 진입 명목 대비."""

    @property
    def net(self) -> float:
        return self.gross - self.cost + self.funding


def non_overlapping(
    events: Sequence[int],
    entry: Sequence[float | None],
    hold: int,
    per_side: float,
    funding: Sequence[float] | None = None,
) -> list[Trade]:
    """포지션이 열려 있는 동안 나온 신호는 버린다(추가 · 연장 없음). 비용 = 편도 × 2.

    ``funding[t]`` 은 날짜 t 의 06:00 이후 24 시간 동안 받은 funding 합(숏 기준, 명목 대비)."""
    out: list[Trade] = []
    free_from = -1
    for t in events:
        if t < free_from:
            continue
        g = forward_short(entry, t, hold)
        if g is None:
            continue
        f = sum(funding[t : t + hold]) if funding is not None else 0.0
        out.append(Trade(t, t + hold, g, 2 * per_side, f))
        free_from = t + hold
    return out


def circular_shift_p(
    values: Sequence[float | None], flags: Sequence[bool], *, step: int = 1
) -> tuple[float, float, float]:
    """사건 표시를 원형으로 밀어(군집 구조 유지) 같은 수의 무작위 날짜 평균과 비교.

    → (사건 평균 − 전체 평균, 한쪽 p = 밀린 평균 ≥ 사건 평균인 비율, 전체 평균)."""
    idx = [k for k, v in enumerate(values) if v is not None]
    vals = [values[k] for k in idx]
    fl = [flags[k] for k in idx]
    n = len(vals)
    v2 = [v for v in vals if v is not None]
    base = statistics.fmean(v2)
    obs_pts = [v for v, f in zip(v2, fl, strict=True) if f]
    if not obs_pts:
        return (math.nan, math.nan, base)
    obs = statistics.fmean(obs_pts)
    hits = total = 0
    for s in range(step, n, step):
        pts = [v2[(k + s) % n] for k in range(n) if fl[k]]
        total += 1
        if statistics.fmean(pts) >= obs:
            hits += 1
    return (obs - base, (hits + 1) / (total + 1), base)


def holm(pvals: Mapping[str, float], alpha: float = 0.025) -> dict[str, bool]:
    order = sorted(pvals, key=lambda k: pvals[k])
    m = len(order)
    out = dict.fromkeys(order, False)
    for j, k in enumerate(order):
        if pvals[k] <= alpha / (m - j):
            out[k] = True
        else:
            break
    return out
