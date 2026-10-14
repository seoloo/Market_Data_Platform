"""문서 232 — AG 숏 엔진의 숏 영구 차단을 숏 자기 손익 기준으로 바꾼 계좌 (설명용, I/O 없음).

엔진(`ag_short_leverage_20261013`) 파일은 고치지 않는다. ``account_class`` 로 엔진이 쓰는
``Account`` 를 잠시 바꿔 같은 ``simulate`` 를 돌린다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from auto_trading.research import ag_short_leverage_20261013 as p

__all__ = ["OWN_LIMIT", "NoGateAccount", "OwnGateAccount", "account_class"]

OWN_LIMIT = Decimal("0.025")
"""닫힌 숏 누적 순손익이 자기 고점에서 계좌 고점 equity × 2.5% 이상 빠지면 숏 영구 차단."""


class OwnGateAccount(p.Account):
    """숏 영구 차단을 계좌 전체 낙폭 대신 숏 자기 손익으로 판단한다.

    엔진이 계좌 전체 기준으로 ``short_halted = True`` 를 써도 무시하고(setter),
    읽을 때(getter)는 닫힌 숏 거래만 보고 정한다.
    그 밖의 규칙(연패 쉼 · 하루 손실 · 30% 정지)은 그대로.
    """

    GATE = "OWN"

    @property
    def short_halted(self) -> bool:
        if self.GATE == "NONE":
            return False
        state = self.__dict__.setdefault("_own", {"seen": 0, "cum": Decimal(0), "top": Decimal(0)})
        if state.get("halted"):
            return True
        for t in self.trades[state["seen"] :]:
            if t["side"] != "SHORT":
                continue
            state["cum"] += t["net"]
            state["top"] = max(state["top"], state["cum"])
            if state["top"] - state["cum"] >= OWN_LIMIT * self.peak:
                state["halted"] = True
                state["halted_at_ms"] = t["exit_ms"]
                break
        state["seen"] = len(self.trades)
        return bool(state.get("halted"))

    @short_halted.setter
    def short_halted(self, _value: bool) -> None:
        pass  # 계좌 전체 낙폭 차단은 쓰지 않는다


class NoGateAccount(OwnGateAccount):
    GATE = "NONE"


@contextmanager
def account_class(cls: type[p.Account]) -> Iterator[None]:
    original = p.Account
    p.Account = cls  # type: ignore[misc]
    try:
        yield
    finally:
        p.Account = original  # type: ignore[misc]
