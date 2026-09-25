"""Elliott + 피보나치 2·4파 되돌림 **데모 셋업** (docs/research/56). 순수 함수, I/O 없음.

시나리오 묶음(내보내기 형식, :mod:`scenario_export`)과 기준 순위 사이드카를 받아:

1. 기준 순위 상위 ``top_n``(10) 시나리오 중 **같은 방향 충격파 5파 종료 후보**
   (``WAVE_5_END_CANDIDATE``)가 ``need``(6) 개 이상이면 셋업이다.
   한 해석으로 확정하지 않고 다수 합의를 요구한다.
2. 그 충격파를 더 큰 파동의 1파 또는 3파로 보고, 이어질 **2파·4파 되돌림**에서 진입한다.
   A = 합의한 시나리오 중 순위가 가장 높은 것의 시작점(0), B = 끝점(5, 방금 확정된 꼭짓점).
3. 진입가 = B − 0.5 × (B − A) (피보나치 0.5 되돌림). 손절 = A — 되돌림이 충격파 시작을 넘으면
   2파 규칙(2파는 1파 시작을 넘지 않는다)으로 해석이 무효다.
   목표 = 진입가 + 1.618 × |B − A| (3파 목표 관례).

이 모듈은 **연구 전용**이다. 주문은 데모 스크립트(scripts/demo_ew_btc.py)만 낸다(실계좌 경로 없음).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = ["ENTRY_RETRACE", "TARGET_EXTENSION", "EwSetup", "agreement", "setup_from_set"]

ENTRY_RETRACE = Decimal("0.5")
TARGET_EXTENSION = Decimal("1.618")
IMPULSE_END = "WAVE_5_END_CANDIDATE"


@dataclass(frozen=True, slots=True)
class EwSetup:
    as_of_ms: int
    direction: int
    """+1 롱(상승 충격파 뒤 되돌림 매수), −1 숏."""
    a: Decimal
    b: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal
    agree: int
    top_n: int
    b_extreme_ms: int
    scenario_ids: tuple[str, ...]

    @property
    def impulse_pct(self) -> Decimal:
        return abs(self.b / self.a - 1)


def setup_from_set(
    scenario_set: Mapping[str, Any],
    baseline_rank: Mapping[str, int],
    *,
    top_n: int = 10,
    need: int = 6,
) -> EwSetup | None:
    scenarios: Sequence[Mapping[str, Any]] = scenario_set.get("scenarios") or []
    if not scenarios:
        return None
    ranked = sorted(scenarios, key=lambda s: baseline_rank[s["scenario_id"]])[:top_n]
    for direction in (1, -1):
        agree = [
            s for s in ranked if s["current_state"] == IMPULSE_END and s["direction"] == direction
        ]
        if len(agree) < need:
            continue
        best = agree[0]
        a = Decimal(str(best["anchors"][0]["price"]))
        b = Decimal(str(best["anchors"][-1]["price"]))
        if (b - a) * direction <= 0:
            continue
        move = b - a
        entry = b - ENTRY_RETRACE * move
        return EwSetup(
            as_of_ms=int(scenario_set["as_of_ms"]),
            direction=direction,
            a=a,
            b=b,
            entry=entry,
            stop=a,
            target=entry + TARGET_EXTENSION * move,
            agree=len(agree),
            top_n=len(ranked),
            b_extreme_ms=int(best["anchors"][-1]["extreme_at_ms"]),
            scenario_ids=tuple(s["scenario_id"] for s in agree),
        )
    return None


def agreement(
    scenario_set: Mapping[str, Any], baseline_rank: Mapping[str, int], *, top_n: int = 10
) -> tuple[int, int, int]:
    """(상위 ``top_n`` 수, 상승 충격파 5파 종료 수, 하락 충격파 5파 종료 수) — 알림용."""
    scenarios: Sequence[Mapping[str, Any]] = scenario_set.get("scenarios") or []
    ranked = sorted(scenarios, key=lambda s: baseline_rank[s["scenario_id"]])[:top_n]
    ends = [s["direction"] for s in ranked if s["current_state"] == IMPULSE_END]
    return len(ranked), ends.count(1), ends.count(-1)
