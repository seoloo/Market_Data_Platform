"""유휴 현금 이자 (Paper 보고용 가정) — 순수 함수. docs/research/29 C1.

전략이 쉬는 동안의 현금을 바로 뺄 수 있는 예치 상품(예: Binance Simple Earn USDT 유동형)에 두었다면
자본이 어떻게 달라졌는지를 **원장 기록 위에 따로** 계산한다. 원장 자체와 전략 규칙은 바꾸지 않는다.

비중으로 움직이는 전략이라 자본이 이자만큼 커지면 포지션도 같은 비율로 커진다. 그래서 하루 수익률은
원장과 같고, 전날 남은 유휴 현금에만 하루치 이자가 더해진다::

    E'(d) = E'(d-1) × (1 + r(d)) + 이자(유휴 비율(d-1) × E'(d-1))

이자율은 구간별(예: 1,000 USDT 까지 7.08%, 초과분 3.08%)이고 날짜에 따라 바뀔 수 있다.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["RateSchedule", "Tier", "daily_interest", "with_cash_yield"]


@dataclass(frozen=True, slots=True)
class Tier:
    up_to: float | None
    """이 구간의 누적 상한 (USDT). None 이면 끝까지."""
    apr: float
    """연 이자율 (단리 기준, 하루 = apr / 365)."""


@dataclass(frozen=True, slots=True)
class RateSchedule:
    """날짜별 이자율. ``entries`` 는 (적용 시작 UTC ms, 구간들) 오름차순."""

    entries: tuple[tuple[int, tuple[Tier, ...]], ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("이자율 항목이 없다")
        starts = [s for s, _ in self.entries]
        if starts != sorted(starts):
            raise ValueError("이자율 항목은 날짜 오름차순이어야 한다")

    def tiers_on(self, day_ms: int) -> tuple[Tier, ...]:
        """그날 적용할 구간. 첫 항목 이전 날짜에는 첫 항목을 쓴다(보고용 가정)."""
        starts = [s for s, _ in self.entries]
        i = max(bisect.bisect_right(starts, day_ms) - 1, 0)
        return self.entries[i][1]


def daily_interest(idle: float, tiers: Sequence[Tier]) -> float:
    """유휴 현금 ``idle`` USDT 에 붙는 하루 이자."""
    if idle <= 0:
        return 0.0
    total, floor = 0.0, 0.0
    for tier in tiers:
        top = idle if tier.up_to is None else min(idle, tier.up_to)
        if top > floor:
            total += (top - floor) * tier.apr / 365
            floor = top
        if tier.up_to is None or idle <= tier.up_to:
            break
    return total


def with_cash_yield(
    days: Sequence[int],
    equity: Sequence[float],
    idle_fraction: Sequence[float],
    schedule: RateSchedule,
) -> list[float]:
    """원장 자본 경로 위에 유휴 현금 이자를 더한 경로. 첫날은 원장과 같다.

    ``idle_fraction[i]`` 는 i 번째 날 **마감 뒤** 자본 대비 유휴 현금 비율이다(다음 날 이자의 근거).
    """
    if not (len(days) == len(equity) == len(idle_fraction)):
        raise ValueError("길이가 다르다")
    if not days:
        return []
    out = [equity[0]]
    for i in range(1, len(days)):
        prev = out[-1]
        ret = equity[i] / equity[i - 1] - 1 if equity[i - 1] else 0.0
        idle = max(min(idle_fraction[i - 1], 1.0), 0.0) * prev
        out.append(prev * (1 + ret) + daily_interest(idle, schedule.tiers_on(days[i])))
    return out
