"""Lo·Mamaysky·Wang (2000) 반전형 차트 패턴 탐지 — 역헤드앤숄더(IHS) · 이중 바닥(DBOT) (순수).

docs/research/158-kr-reversal-patterns-preregistration.md §3. 정의는 LMW §I·§II 문장 그대로이고,
계산상 바꾼 곳만 적는다.

* 창: 길이 ``L = l + d`` (35 + 3) 거래일 종가. **창 안 자료만** 쓴다(창 끝 = 탐지 시각).
* 평활: 가우스 커널 Nadaraya–Watson, **고정 대역폭 1.25 거래일**. 논문의 "교차 검증 h* × 0.3"
  은 재현되지 않았다 — 식 (13) 을 글자대로 쓰면 교과서 모양에서도 h* → ∞, 보통의 leave-one-out
  으로 읽으면 거의 원가격(패턴이 논문의 4 ~ 6 배). 0.3 자체가 분석가 눈으로 정한 값이라, 국장
  무작위 120 종목에서 **수익 없이 패턴 빈도만** 재어 논문 발표 빈도(IHS 0.95 · DBOT 1.19 /
  종목·연)에 가장 가까운 고정값을 골랐다(시도 9 가지, 158 §3). 사용자 확정 2026-10-07.
* 극점: 평활 곡선 기울기 부호가 바뀌는 칸 i → 실제 종가 i−1·i·i+1 가운데 최고(최저)값의 칸.
* 완성 시점: 패턴을 끝내는 마지막 극점이 **정확히 창 끝에서 d 칸 앞**(LMW "day t + l − 1").
  창을 하루씩 굴리므로 같은 패턴은 한 번만 잡힌다.
* IHS: 연속 극점 E1~E5, E1 최저점, E3 < E1 · E3 < E5, E1·E5 가 둘의 평균에서 1.5% 안,
  E2·E4 가 둘의 평균에서 1.5% 안.
* DBOT: E1 최저점, Ea = E1 뒤 극점 중 가장 낮은 값(최저점), E1·Ea 가 둘의 평균에서 1.5% 안,
  간격 > 22 칸. 마지막 극점 = Ea.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

__all__ = [
    "BANDWIDTH",
    "Pattern",
    "detect",
    "extrema",
    "find_dbot",
    "find_ihs",
    "smooth",
]

BANDWIDTH = 1.25
TOL = 0.015
DBOT_GAP = 22


@dataclass(frozen=True, slots=True)
class Pattern:
    kind: str  # "IHS" | "DBOT"
    points: tuple[int, ...]  # 창 안 극점 칸 (IHS 5 개 · DBOT 2 개: E1, Ea)
    last: int  # 마지막 극점 칸


@cache
def _weights(n: int, h: float) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(math.exp(-((i - j) ** 2) / (2 * h * h)) for j in range(n)) for i in range(n))


def smooth(prices: Sequence[float], h: float) -> list[float]:
    n = len(prices)
    out = []
    for w in _weights(n, round(h, 6)):
        out.append(sum(map(float.__mul__, w, prices)) / sum(w))
    return out


def extrema(prices: Sequence[float], m: Sequence[float]) -> list[tuple[int, float, int]]:
    """(칸, 실제 종가, +1 최고 / −1 최저) — 평활 곡선 기울기 부호가 바뀌는 칸마다."""
    out: list[tuple[int, float, int]] = []
    n = len(m)
    for i in range(1, n - 1):
        a, b = m[i] - m[i - 1], m[i + 1] - m[i]
        if a > 0 >= b:
            kind = 1
        elif a < 0 <= b:
            kind = -1
        else:
            continue
        lo, hi = i - 1, i + 1
        span = range(lo, hi + 1)
        j = max(span, key=lambda k: prices[k]) if kind == 1 else min(span, key=lambda k: prices[k])
        if out and out[-1][2] == kind:  # 평평한 구간에서 같은 종류가 이어지면 더 극단인 쪽만
            if (kind == 1 and prices[j] > out[-1][1]) or (kind == -1 and prices[j] < out[-1][1]):
                out[-1] = (j, prices[j], kind)
            continue
        out.append((j, prices[j], kind))
    return out


def _close(a: float, b: float) -> bool:
    avg = (a + b) / 2
    return abs(a - avg) <= TOL * avg and abs(b - avg) <= TOL * avg


def find_ihs(ext: Sequence[tuple[int, float, int]], end: int) -> list[Pattern]:
    """마지막 극점이 정확히 칸 ``end`` 인 IHS."""
    out = []
    for k in range(len(ext) - 4):
        e = ext[k : k + 5]
        if e[4][0] != end or e[0][2] != -1:
            continue
        p = [x[1] for x in e]
        if p[2] < p[0] and p[2] < p[4] and _close(p[0], p[4]) and _close(p[1], p[3]):
            out.append(Pattern("IHS", tuple(x[0] for x in e), e[4][0]))
    return out


def find_dbot(ext: Sequence[tuple[int, float, int]], end: int) -> list[Pattern]:
    """Ea(마지막 극점)가 정확히 칸 ``end`` 인 DBOT. Ea 는 칸 ``end`` 까지의 극점 중 최저."""
    out = []
    for k, (i1, p1, kind) in enumerate(ext):
        if kind != -1:
            continue
        later = [x for x in ext[k + 1 :] if x[0] <= end]
        if not later:
            continue
        ia, pa, ka = min(later, key=lambda x: (x[1], x[0]))
        if ka == -1 and ia == end and ia - i1 > DBOT_GAP and _close(p1, pa):
            out.append(Pattern("DBOT", (i1, ia), ia))
    return out


def detect(prices: Sequence[float], lag: int = 3) -> list[Pattern]:
    """창 하나(마지막 칸 = 탐지 시각)에서 칸 L−1−lag 에 완성된 IHS · DBOT. 창 밖 자료 없음."""
    xs = [float(p) for p in prices]
    if len(xs) < 10 or min(xs) <= 0:
        return []
    ext = extrema(xs, smooth(xs, BANDWIDTH))
    end = len(xs) - 1 - lag
    return find_ihs(ext, end) + find_dbot(ext, end)
