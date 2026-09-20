"""Feature 프로토콜.

**Feature 는 '측정' 만 한다. '해석' 은 strategy 가 한다.**

06-scoring-engine.md 초안은 feature 가 signal(-1~+1)까지 만들도록 했으나,
T1(되돌림)은 bias 방향을 알아야 신호 부호가 정해진다. feature 가 bias 를 알면
측정과 해석이 섞여 단위 테스트가 어려워지고 의존 방향이 꼬인다.

그래서 feature 는 방향과 무관한 ``raw`` / ``normalized`` 만 내고,
방향을 아는 ``strategy/signals.py`` 가 signal 로 변환한다.

모든 feature 는 순수하다 — I/O 없음, 입력이 같으면 출력이 같다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import TimeframeSeries

__all__ = ["Feature", "FeatureResult"]


@dataclass(frozen=True, slots=True)
class FeatureResult:
    """feature 계산 결과.

    ``available=False`` 면 warm-up 미충족이다. 이때 ``raw``/``normalized`` 는 ``None``
    이며, Risk Engine 이 진입을 거부한다 (게이트 G2.2). **0 으로 대체하지 않는다** —
    중립값은 "진입해도 된다" 는 신호로 오작동한다 (AGENTS.md §3.6).
    """

    name: str
    timeframe: Timeframe
    available: bool
    raw: Decimal | None = None
    """원본 단위 값 (가격, RSI, 비율 등). 사람이 읽고 검증하는 값."""
    normalized: float | None = None
    """비교 가능한 척도로 변환한 값 (z-score, 백분위 등)."""
    detail: dict[str, Any] | None = None
    """``decisions.components_json`` 에 남길 부가 정보."""

    @classmethod
    def unavailable(cls, name: str, timeframe: Timeframe, reason: str) -> FeatureResult:
        return cls(
            name=name,
            timeframe=timeframe,
            available=False,
            detail={"reason": reason},
        )

    def to_digest_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tf": self.timeframe.value,
            "ok": self.available,
            "raw": self.raw,
            "norm": self.normalized,
        }


class Feature(Protocol):
    """feature 계약.

    ``warmup_bars`` 만큼의 봉이 없으면 ``compute`` 는 ``available=False`` 를 낸다.
    호출자는 이 값을 보고 필요한 봉 수를 미리 확보한다 (SnapshotBuilder).
    """

    @property
    def name(self) -> str: ...

    @property
    def timeframe(self) -> Timeframe: ...

    @property
    def warmup_bars(self) -> int: ...

    def compute(self, series: TimeframeSeries) -> FeatureResult: ...
