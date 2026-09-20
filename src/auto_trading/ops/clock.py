"""실제 시스템 시계.

core 가 아니라 ops 에 있는 이유: ``time.time()`` 호출은 I/O 성격이므로
Pure Core 밖에 둔다. 전략 코드는 이 클래스를 import 하지 않는다.
"""

from __future__ import annotations

import time

__all__ = ["RealClock"]


class RealClock:
    """UTC epoch 밀리초를 시스템 시계에서 읽는다."""

    __slots__ = ()

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def __repr__(self) -> str:
        return "RealClock()"
