"""결정론적 가짜 지갑 이체 — 장애 주입 테스트용 (docs/research/53).

현물·선물 두 지갑의 USDT 잔고와 이체 이력만 흉내 낸다. 잔고가 모자라면 거부한다.

장애 주입(다른 가짜 거래소와 같은 이름):

* ``fail_before_accept`` — 다음 N번 이체를 받기 **전에** 네트워크 오류 (이체 없음, 결과 모름)
* ``lose_response_after_accept`` — 다음 N번 이체를 받은 **뒤** 응답 유실 (이체 있음, 결과 모름)
* ``fail_lookups`` — 다음 N번 이력 조회 실패
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from auto_trading.core.errors import ExchangeError, OrderRejectedError, OrderStateUnknownError
from auto_trading.exchange.wallet import TransferDirection, TransferRow

__all__ = ["FakeWallets"]


@dataclass
class FakeWallets:
    spot: Decimal
    futures: Decimal
    clock: Callable[[], int]
    fail_before_accept: int = 0
    lose_response_after_accept: int = 0
    fail_lookups: int = 0
    history: list[TransferRow] = field(default_factory=list)

    def transfer(self, direction: TransferDirection, asset: str, amount: Decimal) -> str:
        if self.fail_before_accept:
            self.fail_before_accept -= 1
            raise OrderStateUnknownError("이체 전송 실패 (네트워크)")
        if asset != "USDT" or amount <= 0:
            raise OrderRejectedError("-1102 잘못된 자산·금액")
        source = self.spot if direction is TransferDirection.SPOT_TO_FUTURES else self.futures
        if amount > source:
            raise OrderRejectedError("-5013 잔고 부족")
        if direction is TransferDirection.SPOT_TO_FUTURES:
            self.spot -= amount
            self.futures += amount
        else:
            self.futures -= amount
            self.spot += amount
        tran_id = str(1000 + len(self.history))
        self.history.append(
            TransferRow(tran_id, direction, asset, amount, "CONFIRMED", self.clock())
        )
        if self.lose_response_after_accept:
            self.lose_response_after_accept -= 1
            raise OrderStateUnknownError("이체 응답 유실")
        return tran_id

    def transfers(
        self, direction: TransferDirection, start_ms: int, end_ms: int
    ) -> list[TransferRow]:
        if self.fail_lookups:
            self.fail_lookups -= 1
            raise ExchangeError("이력 조회 실패")
        return [
            r
            for r in self.history
            if r.direction is direction and start_ms <= r.timestamp_ms <= end_ms
        ]
