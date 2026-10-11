"""문서 208 — S&P 500 편입 · 편출 강제 매매 사건 (순수 계산, 표준 라이브러리만).

가격은 날짜 순 (date, open, close). 사건 창은 거래일 기준.

- 편입(B): 발표 다음 거래일 **시가** 매수 → 효력일 전 마지막 거래일 **종가** 매도
  (지수 펀드가 그 종가에 산다).
- 편출(A): 효력일 전 마지막 거래일 **종가** 매수(펀드가 파는 종가, LOC 지정가)
  → ``hold`` 거래일 뒤 종가 매도.
- 초과 수익 = 종목 수익 − SPY 같은 창 수익.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

__all__ = ["Px", "Window", "addition_window", "by_date_mean", "deletion_window", "t_stat"]


@dataclass(frozen=True, slots=True)
class Px:
    days: Sequence[date]
    open: Sequence[float]
    close: Sequence[float]

    def first_on_or_after(self, d: date) -> int | None:
        k = bisect.bisect_left(self.days, d)
        return k if k < len(self.days) else None

    def last_before(self, d: date) -> int | None:
        k = bisect.bisect_left(self.days, d) - 1
        return k if k >= 0 else None


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date
    ret: float
    bench: float

    @property
    def excess(self) -> float:
        return self.ret - self.bench


def _bench(spy: Px, start: date, end: date, *, from_open: bool) -> float | None:
    a = spy.first_on_or_after(start)
    b = spy.first_on_or_after(end)
    if a is None or b is None or spy.days[a] != start or spy.days[b] != end:
        return None
    base = spy.open[a] if from_open else spy.close[a]
    return spy.close[b] / base - 1


def addition_window(px: Px, spy: Px, announced: date, effective: date) -> Window | None:
    """발표 다음 거래일 시가 → 효력일 전 마지막 거래일 종가. 그 사이 거래일이 없으면 None."""
    a = px.first_on_or_after(_next_day(announced))
    b = px.last_before(effective)
    if a is None or b is None or b < a:
        return None
    start, end = px.days[a], px.days[b]
    bench = _bench(spy, start, end, from_open=True)
    if bench is None:
        return None
    return Window(start, end, px.close[b] / px.open[a] - 1, bench)


def deletion_window(px: Px, spy: Px, effective: date, hold: int) -> Window | None:
    """효력일 전 마지막 거래일 종가 → ``hold`` 거래일 뒤 종가."""
    a = px.last_before(effective)
    if a is None or a + hold >= len(px.days):
        return None
    start, end = px.days[a], px.days[a + hold]
    if (end - start).days > hold * 3:  # 거래 정지 · 자료 끊김
        return None
    bench = _bench(spy, start, end, from_open=False)
    if bench is None:
        return None
    return Window(start, end, px.close[a + hold] / px.close[a] - 1, bench)


def _next_day(d: date) -> date:
    return date.fromordinal(d.toordinal() + 1)


def by_date_mean(items: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """같은 날 시작한 사건은 한 묶음(동일 비중)으로 — 독립 단위."""
    groups: dict[date, list[float]] = {}
    for d, x in items:
        groups.setdefault(d, []).append(x)
    return [(d, statistics.fmean(v)) for d, v in sorted(groups.items())]


def t_stat(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0


def load_px(rows: Sequence[Mapping[str, str]]) -> Px:
    days: list[date] = []
    o: list[float] = []
    c: list[float] = []
    for r in rows:
        try:
            d = date.fromisoformat(r["date"][:10])
            op, cl = float(r["open"]), float(r["close"])
        except (KeyError, ValueError):
            continue
        if op > 0 and cl > 0 and (not days or d > days[-1]):
            days.append(d)
            o.append(op)
            c.append(cl)
    return Px(days, o, c)
