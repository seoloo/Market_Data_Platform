"""문서 233 — AG4 롱 손보기(추세 · 변동성 · 낙폭 브레이크) ± 232 숏 (설명용, I/O 없음).

Codex AG 엔진(`ag_short_leverage_20261013`)은 그대로 두고, 엔진이 읽는 ``Account.goals``(코인별
일일 목표 비중)만 하위 클래스에서 바꾼다. 숏 차단은 232 OWN(숏 자기 손익) 그대로.

* L0 — AG4 그대로.
* T  — 추세가 '완전히' 켜졌을 때(20 · 60 · 120 일 셋 다 +)만 4 배,
  아니면 그 코인 목표 × 0.25(배율 없이 1 배).
* V  — 변동성 목표에 60 일 대신 max(20 일, 60 일) 변동성을 쓴다(최근 변동성이 크면 비중을 줄임).
* D  — 계좌가 기준 고점에서 20% 빠지면 30 일 동안 목표 × 0.5,
  30 일 뒤 그때 평가액을 새 기준 고점으로.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_own_gate_232 import OwnGateAccount

__all__ = ["BRAKE_EVENTS", "VARIANTS", "DayInfo", "Variant", "account_for", "day_info"]

D = Decimal
DAY = 86_400_000
Variant = Literal["L0", "T", "V", "D"]
VARIANTS: tuple[Variant, ...] = ("L0", "T", "V", "D")
DD_TRIGGER = D("0.20")
DD_FACTOR = D("0.5")
DD_HOLD_MS = 30 * DAY
T_FACTOR = D("0.25")
BRAKE_EVENTS: dict[str, list[dict[str, Any]]] = {}
"""D 의 브레이크 켜짐 · 꺼짐 기록 (계좌 키 → 사건). 보고용."""


@dataclass(frozen=True)
class DayInfo:
    """한 코인의 일일 목표 계산 재료 (그날 목표와 같은 종가로)."""

    direction: float
    vol20: float
    vol60: float
    target: float


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def day_info(closes: Sequence[float]) -> DayInfo | None:
    """T/v2 목표 식(20 · 60 · 120 부호 평균, 60 일 변동성, 15% · 상한 0.5)을 그대로 다시 계산."""
    if len(closes) < 121:
        return None
    last = closes[-1]
    direction = max(0.0, statistics.fmean(_sign(last / closes[-1 - k] - 1) for k in (20, 60, 120)))
    steps = [math.log(closes[-k] / closes[-k - 1]) for k in range(1, 61)]
    vol60 = statistics.stdev(steps) * math.sqrt(365)
    vol20 = statistics.stdev(steps[:20]) * math.sqrt(365)
    return DayInfo(direction, vol20, vol60, direction * min(0.15 / vol60, 0.5))


def _scaled(variant: Variant, raw: Decimal, info: DayInfo) -> Decimal:
    if raw <= 0 or variant in ("L0", "D"):
        return raw
    if variant == "T":
        return raw if info.direction >= 1 else raw * T_FACTOR
    return D(repr(info.direction * min(0.15 / max(info.vol20, info.vol60), 0.5)))


def account_for(
    variant: Variant, infos: Mapping[int, tuple[DayInfo, DayInfo]], *, short_gate: str = "OWN"
) -> type[p.Account]:
    """``infos`` = 목표 갱신 블록 시각(at_ms) → (BTC, ETH) 재료."""

    class Variant233(OwnGateAccount):
        GATE = short_gate
        VARIANT = variant

        @property
        def goals(self) -> tuple[Decimal, Decimal]:
            raw: tuple[Decimal, Decimal] = self.__dict__.get("_raw_goals", (D(0), D(0)))
            if self.__dict__.pop("_raw_pending", False):
                # 엔진은 목표를 넣은 바로 다음 줄에서 targets_updated_ms 를 그 블록 시각으로 쓴다.
                self.__dict__["_raw_at"] = self.targets_updated_ms
            at = self.__dict__.get("_raw_at")
            if at is None:
                return raw
            info = infos.get(at)
            if info is None:
                raise DataIntegrityError(f"233 day info missing {at}")
            for i in range(2):
                if abs(float(raw[i]) - info[i].target) > 1e-9:
                    raise DataIntegrityError(f"233 target rebuild mismatch {at}")
            goals = tuple(_scaled(variant, raw[i], info[i]) for i in range(2))
            if variant == "D" and self._brake():
                goals = tuple(g * DD_FACTOR for g in goals)
            return goals[0], goals[1]

        @goals.setter
        def goals(self, value: tuple[Decimal, Decimal]) -> None:
            self.__dict__["_raw_goals"] = value
            self.__dict__["_raw_pending"] = True

        def _brake(self) -> bool:
            st: dict[str, Any] = self.__dict__.setdefault(
                "_dd", {"seen": 0, "ref": p.INITIAL, "until": None}
            )
            if len(self.trace) != st["seen"]:
                st["seen"] = len(self.trace)
                point = self.trace[-1]
                eq, at = D(point["equity"]), int(point["at_ms"])
                if st["until"] is not None and at >= st["until"]:
                    st["until"], st["ref"] = None, eq
                    BRAKE_EVENTS.setdefault(self.case.key, []).append({"off_ms": at, "equity": eq})
                if st["until"] is None:
                    st["ref"] = max(st["ref"], eq)
                    if eq <= st["ref"] * (1 - DD_TRIGGER):
                        st["until"] = at + DD_HOLD_MS
                        BRAKE_EVENTS.setdefault(self.case.key, []).append(
                            {"on_ms": at, "equity": eq, "ref": st["ref"]}
                        )
            return st["until"] is not None

    Variant233.__name__ = f"Variant233_{variant}"
    return Variant233
