"""문서 235 — AG 숏 배율 2 배 · 3 배 (232 엔진 · OWN 차단 그대로, 설명용 · I/O 없음).

엔진의 Case 는 숏 배율을 1 배(SHORT_1X)와 최대(SHORT_MAX = AG 배율)만 등록한다.
엔진 파일은 고치지 않고,
SHORT_MAX 계좌의 ``case.short_leverage`` 만 다른 값으로 보이게 하는 계좌 하위 클래스를 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_own_gate_232 import OwnGateAccount

__all__ = ["LevCase", "account_with_leverage"]


@dataclass(frozen=True)
class LevCase:
    """원래 Case 를 감싸 숏 배율만 바꾼다(나머지는 그대로 넘김)."""

    base: p.Case
    leverage: Decimal

    @property
    def short_leverage(self) -> Decimal:
        return self.leverage

    @property
    def exposure(self) -> int:
        return self.base.exposure

    @property
    def mode(self) -> str:
        return self.base.mode

    @property
    def scenario(self) -> Any:
        return self.base.scenario

    @property
    def key(self) -> str:
        return self.base.key


def account_with_leverage(leverage: Decimal) -> type[p.Account]:
    """SHORT_MAX 계좌에만 숏 배율 ``leverage`` 를 적용한다(BASE · SHORT_1X 는 그대로)."""

    class LevAccount(OwnGateAccount):
        @property
        def case(self) -> Any:
            c = self.__dict__["_case"]
            return LevCase(c, leverage) if c.mode == "SHORT_MAX" else c

        @case.setter
        def case(self, value: p.Case) -> None:
            self.__dict__["_case"] = value

    LevAccount.__name__ = f"LevAccount_{leverage}"
    return LevAccount
