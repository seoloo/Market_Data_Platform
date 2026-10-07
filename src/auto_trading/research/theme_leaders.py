"""문서 162 — 테마 주도주 갈아타기: 주도주 고르기 · 미국 시가총액 · 검정 · 보유 기간 비교 (순수).

* A (모멘텀): 테마 안 21 · 63 · 126 거래일 수익 순위의 합이 가장 큰 종목. 동점은 126 일 수익.
* B (시가총액): 그날 시가총액이 가장 큰 종목. 동점은 코드 순.
* 미국 시가총액: SEC 보고 주식 수(제출일 ≤ t 인 마지막 값) × 분할 조정 종가 × 그 주식 수 기준일 뒤의
  분할 배수. PIT 값이 아직 없으면 가장 이른 값을 쓰고 ``backfilled`` 로 표시한다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def leader_momentum(rets: Mapping[str, tuple[float, float, float]]) -> str:
    """종목 → (21 일, 63 일, 126 일 수익). 순위(작을수록 0) 합이 가장 큰 종목 (순수)."""
    codes = list(rets)
    score = dict.fromkeys(codes, 0.0)
    for k in range(3):
        for rank, c in enumerate(sorted(codes, key=lambda c: rets[c][k])):
            score[c] += rank
    return max(codes, key=lambda c: (score[c], rets[c][2]))


def leader_mcap(caps: Mapping[str, float]) -> str:
    """시가총액 1 등. 동점이면 코드가 앞선 종목 (순수)."""
    return min(caps, key=lambda c: (-caps[c], c))


@dataclass(frozen=True, slots=True)
class UsCap:
    value: float
    backfilled: bool


def us_mktcap(
    shares: Sequence[tuple[str, str, float]],
    splits: Sequence[tuple[str, float]],
    close_split_adj: float,
    t: str,
) -> UsCap | None:
    """``shares`` = (기준일 end, 제출일 filed, 주식 수), ``splits`` = (날짜, 배수). 날짜 YYYY-MM-DD.

    t 에 알 수 있던(제출일 ≤ t) 마지막 주식 수를 고른다. 분할 조정 종가는 지금 기준으로 조정돼
    있으므로, 그 주식 수 기준일 뒤의 분할을 모두 곱하면 시가총액이 된다 (순수)."""
    if not shares:
        return None
    known = [s for s in shares if s[1] <= t]
    backfilled = not known
    end, _filed, val = (
        max(known, key=lambda s: (s[1], s[0])) if known else min(shares, key=lambda s: (s[1], s[0]))
    )
    factor = math.prod(r for d, r in splits if d > end)
    return UsCap(val * factor * close_split_adj, backfilled)


def nw_se(xs: Sequence[float], lag: int = 3) -> float:
    """평균의 Newey-West(Bartlett) 표준오차 (순수)."""
    n = len(xs)
    m = statistics.fmean(xs)
    d = [x - m for x in xs]
    v = sum(x * x for x in d) / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        v += 2 * w * sum(d[i] * d[i - k] for i in range(k, n)) / n
    return math.sqrt(max(v, 0.0) / n)


def rolling_horizon(a: Sequence[float], b: Sequence[float], months: int) -> dict[str, float] | None:
    """월 수익 a · b 로, 매달 시작해 ``months`` 달 들었을 때 a 가 b 를 이긴 비율과 누적 차이 중앙값.

    창이 겹치므로 통계 검정이 아니라 기술 통계다 (순수)."""
    n = len(a) - months + 1
    if n <= 0:
        return None
    diffs = []
    for s in range(n):
        ca = math.prod(1 + x for x in a[s : s + months])
        cb = math.prod(1 + x for x in b[s : s + months])
        diffs.append(ca / cb - 1)
    return {
        "windows": n,
        "a_wins": round(sum(1 for x in diffs if x > 0) / n, 3),
        "median_rel": round(statistics.median(diffs), 4),
        "worst_rel": round(min(diffs), 4),
        "best_rel": round(max(diffs), 4),
    }
