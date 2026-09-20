"""시계 추상화.

Pure Core 안에서는 ``datetime.now()`` / ``time.time()`` 을 호출하지 않는다 (AGENTS.md §3.1).
전략 판단에 필요한 시각은 항상 ``MarketSnapshot.as_of_ms`` 에서 온다.

실제 시스템 시계가 필요한 곳(스케줄러·하트비트)은 ``auto_trading.ops.clock.RealClock`` 을 쓴다.
``RealClock`` 이 여기 없는 이유는 core 를 순수하게 유지하기 위해서다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Clock", "SimClock"]


@runtime_checkable
class Clock(Protocol):
    """현재 시각을 UTC epoch 밀리초로 제공한다."""

    def now_ms(self) -> int: ...


class SimClock:
    """백테스트·테스트용 시뮬레이션 시계.

    시간이 저절로 흐르지 않는다. 명시적으로 전진시켜야 한다.
    """

    __slots__ = ("_now_ms",)

    def __init__(self, now_ms: int = 0) -> None:
        self._now_ms = now_ms

    def now_ms(self) -> int:
        return self._now_ms

    def set(self, now_ms: int) -> None:
        """시각을 특정 값으로 설정한다. 과거로 되돌리는 것도 허용한다 (재생 테스트용)."""
        self._now_ms = now_ms

    def advance(self, delta_ms: int) -> int:
        """시각을 전진시킨다. 음수는 거부한다."""
        if delta_ms < 0:
            raise ValueError(f"시계를 뒤로 돌릴 수 없다: {delta_ms}")
        self._now_ms += delta_ms
        return self._now_ms

    def __repr__(self) -> str:
        return f"SimClock(now_ms={self._now_ms})"
