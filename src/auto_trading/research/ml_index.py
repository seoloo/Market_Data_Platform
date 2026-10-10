"""문서 204 — 나스닥100 · S&P 500 주간 ML 의 순수 계산 (표준 라이브러리만).

특징 · 정답표 · 주간 결정 · 실행 지연(1 일) · 전략 원장 · 통계. 학습은 scripts/run_index_ml_204.py.
모든 특징은 결정일 t 의 종가까지만 쓰고(금리는 전날까지), 워밍업이 모자라면 None.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import date

FEATURES = (
    "r1",
    "r5",
    "r20",
    "r60",
    "r120",
    "ma20",
    "ma60",
    "ma200",
    "dd252",
    "rv20",
    "rv60",
    "rvr",
    "rvp",
    "up20",
    "y20",
    "y60",
)
UP_BAR = 0.001
DOWN_BAR = -0.02
HOLD = 5
WARMUP = 252 + 20


def weekly_ends(dates: Sequence[str]) -> list[int]:
    """ISO 주마다 마지막 거래일의 위치."""
    out = []
    for i, d in enumerate(dates):
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        if (
            nxt is None
            or date.fromisoformat(nxt).isocalendar()[:2]
            != (date.fromisoformat(d).isocalendar()[:2])
        ):
            out.append(i)
    return out


def _rv(logret: Sequence[float], i: int, n: int) -> float | None:
    """i 일까지 n 개 일간 로그 수익의 연 변동성 (logret[i] = ln(P_i / P_{i−1}))."""
    if i - n + 1 < 1:
        return None
    return statistics.stdev(logret[i - n + 1 : i + 1]) * math.sqrt(252)


def features(
    dates: Sequence[str], px: Sequence[float], yield_by_date: Mapping[str, float]
) -> dict[int, list[float]]:
    """위치 i → 16 개 특징 (워밍업 · 금리 부족이면 빠짐)."""
    logret = [0.0] + [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    ydates = sorted(yield_by_date)
    yvals = [yield_by_date[d] for d in ydates]
    # 날짜마다 '전날까지' 의 마지막 금리 위치
    ypos = [bisect.bisect_left(ydates, d) - 1 for d in dates]
    rv20 = [_rv(logret, i, 20) for i in range(len(px))]
    out: dict[int, list[float]] = {}
    for i in range(WARMUP, len(px)):
        window = [v for v in rv20[i - 251 : i + 1] if v is not None]
        r20, r60 = rv20[i], _rv(logret, i, 60)
        if r20 is None or r60 is None or r60 <= 0 or len(window) < 252:
            continue
        k = ypos[i]
        # 금리: 전날까지 마지막 값과 그보다 20 · 60 거래일 앞의 값
        if k - 60 < 0:
            continue
        y_now, y20, y60 = yvals[k], yvals[k - 20], yvals[k - 60]
        p = px[i]
        out[i] = [
            math.log(p / px[i - 1]),
            math.log(p / px[i - 5]),
            math.log(p / px[i - 20]),
            math.log(p / px[i - 60]),
            math.log(p / px[i - 120]),
            math.log(p / statistics.fmean(px[i - 19 : i + 1])),
            math.log(p / statistics.fmean(px[i - 59 : i + 1])),
            math.log(p / statistics.fmean(px[i - 199 : i + 1])),
            math.log(p / max(px[i - 251 : i + 1])),
            r20,
            r60,
            r20 / r60,
            sum(1 for v in window if v <= r20) / len(window),
            sum(1 for j in range(i - 19, i + 1) if logret[j] > 0) / 20,
            y_now - y20,
            y_now - y60,
        ]
    return out


def label(px: Sequence[float], i: int) -> tuple[float, int, int] | None:
    """t+1 종가에 사서 t+6 종가까지: (R, y_up, y_dn). 끝이 자료 밖이면 None."""
    if i + 1 + HOLD >= len(px):
        return None
    r = px[i + 1 + HOLD] / px[i + 1] - 1
    return r, int(r > UP_BAR), int(r < DOWN_BAR)


def t_weight(px: Sequence[float], i: int) -> float | None:
    """T 규칙(롱만): max(20 · 60 · 120 일 부호 평균, 0) × min(15% / rv60, 1)."""
    if i < 120:
        return None
    signs = [
        (px[i] > px[i - k]) - (px[i] < px[i - k])
        for k in (20, 60, 120)  # 오늘 종가 기준
    ]
    steps = [math.log(px[j] / px[j - 1]) for j in range(i - 59, i + 1)]
    vol = statistics.stdev(steps) * math.sqrt(252)
    if vol <= 0:
        return 0.0
    return max(statistics.fmean(signs), 0.0) * min(0.15 / vol, 1.0)


def daily_exposure(n: int, decided: Mapping[int, float]) -> list[float]:
    """결정 위치 i 의 비중이 i+2 일 수익부터 적용 (i+1 종가 체결). 첫 결정 전은 0."""
    out = [0.0] * n
    keys = sorted(decided)
    for d in range(n):
        j = bisect.bisect_right(keys, d - 2) - 1
        if j >= 0:
            out[d] = decided[keys[j]]
    return out


def ledger(
    dates: Sequence[str], px: Sequence[float], exposure: Sequence[float], cost: float
) -> dict[str, float]:
    """일 수익 = 노출 × 지수 일 수익 − 비용 × |노출 변화| → 달마다 복리 (달 → 수익)."""
    by: dict[str, float] = {}
    prev = 0.0
    for d in range(1, len(px)):
        r = exposure[d] * (px[d] / px[d - 1] - 1) - cost * abs(exposure[d] - prev)
        prev = exposure[d]
        m = dates[d][:7]
        by[m] = (1 + by.get(m, 0.0)) * (1 + r) - 1
    return by


def lr_se(xs: Sequence[float], lag: int = 3) -> float:
    """평균의 표준오차 (Bartlett lag 3)."""
    n = len(xs)
    m = statistics.fmean(xs)
    d = [x - m for x in xs]
    s = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        s += 2 * (1 - k / (lag + 1)) * sum(d[i] * d[i - k] for i in range(k, n)) / n
    return math.sqrt(max(s, 0.0) / n)


def p_one_sided(t: float) -> float:
    return 0.5 * math.erfc(t / math.sqrt(2))


def holm(pvals: Mapping[str, float], alpha: float = 0.025) -> dict[str, bool]:
    """Holm 단계적 (한쪽 α). 작은 p 부터 α/(m−j) 와 비교, 처음 실패에서 멈춘다."""
    order = sorted(pvals, key=lambda k: pvals[k])
    m = len(order)
    out = dict.fromkeys(order, False)
    for j, k in enumerate(order):
        if pvals[k] <= alpha / (m - j):
            out[k] = True
        else:
            break
    return out


def sharpe(monthly: Sequence[float]) -> float:
    sd = statistics.pstdev(monthly)
    return statistics.fmean(monthly) / sd * math.sqrt(12) if sd > 0 else 0.0


def max_drawdown(monthly: Sequence[float]) -> float:
    v = peak = 1.0
    worst = 0.0
    for r in monthly:
        v *= 1 + r
        peak = max(peak, v)
        worst = max(worst, 1 - v / peak)
    return worst
