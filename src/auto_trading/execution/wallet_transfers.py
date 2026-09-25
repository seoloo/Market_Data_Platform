"""현물 ↔ 선물 지갑 이체 — 멱등 의도·선기록·이력 조회 해소 (docs/research/53, O/v2 V3).

Binance 이체 API 에는 **클라이언트 멱등 ID 가 없다**(응답의 tranId 뿐). 그래서 주문보다 조심한다.

* **의도 ID:** 전략·결정 시각·방향(+재발행 번호). 같은 의도는 몇 번을 호출해도 이체 한 건.
* **선기록:** 거래소 호출 전에 PENDING 을 커밋한다.
* **결과를 모르면(UNKNOWN) 다시 보내지 않는다.** 이력 조회에서 같은 방향·자산·금액이고
  아직 다른 기록이 가져가지 않은 tranId 를 찾는다. 정확히 하나면 CONFIRMED,
  여럿이면 UNKNOWN 유지(사람 확인),
  하나도 없고 ``resolve_after_ms`` 가 지났으면 NOT_FOUND 로 닫는다.
  다시 보내려면 ``attempt`` 를 올린다.
* **해소되지 않은 이체가 있으면 새 이체를 받지 않는다.**
  돈이 어디 있는지 모르는 채 움직이지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal

from auto_trading.core.errors import ExchangeError, OrderRejectedError, ReconciliationError
from auto_trading.exchange.wallet import TransferDirection, WalletTransfers
from auto_trading.storage.repositories.wallet_transfers import (
    OPEN_TRANSFER_STATES,
    TransferRecord,
    WalletTransferRepository,
)

__all__ = ["WalletTransferManager", "transfer_intent_id"]

_CODE = {TransferDirection.SPOT_TO_FUTURES: "S2F", TransferDirection.FUTURES_TO_SPOT: "F2S"}
_CENT = Decimal("0.01")
ASSET = "USDT"


def transfer_intent_id(
    tag: str, decision_ms: int, direction: TransferDirection, attempt: int = 0
) -> str:
    if attempt < 0:
        raise ValueError(f"attempt 는 0 이상이어야 한다: {attempt}")
    iid = f"{tag}-{decision_ms}-{_CODE[direction]}"
    return f"{iid}-{attempt}" if attempt else iid


class WalletTransferManager:
    __slots__ = (
        "_clock",
        "_lookback_ms",
        "_mode",
        "_resolve_after_ms",
        "_store",
        "_strategy_id",
        "_tag",
        "_wallets",
    )

    def __init__(
        self,
        wallets: WalletTransfers,
        store: WalletTransferRepository,
        *,
        mode: str,
        strategy_id: str,
        strategy_tag: str,
        clock: Callable[[], int],
        resolve_after_ms: int = 600_000,
        lookback_ms: int = 120_000,
    ) -> None:
        self._wallets = wallets
        self._store = store
        self._mode = mode
        self._strategy_id = strategy_id
        self._tag = strategy_tag
        self._clock = clock
        self._resolve_after_ms = resolve_after_ms
        self._lookback_ms = lookback_ms

    def blocked(self) -> bool:
        """결과를 모르는 이체가 있으면 True — 새 이체와 그 돈에 기대는 주문을 막는다."""
        return bool(self._store.open())

    def transfer(
        self,
        *,
        decision_ms: int,
        direction: TransferDirection,
        amount: Decimal,
        attempt: int = 0,
    ) -> TransferRecord:
        amount = amount.quantize(_CENT, rounding=ROUND_DOWN)
        if amount <= 0:
            raise ValueError(f"이체 금액은 0.01 USDT 이상이어야 한다: {amount}")
        iid = transfer_intent_id(self._tag, decision_ms, direction, attempt)
        existing = self._store.get(iid)
        if existing is not None:
            return (
                existing
                if existing.state not in OPEN_TRANSFER_STATES
                else self._reconcile(existing)
            )
        if self.blocked():
            raise ReconciliationError("결과를 모르는 이체가 있어 새 이체 금지 — 먼저 recover()")
        now = self._clock()
        record = TransferRecord(
            intent_id=iid,
            mode=self._mode,
            strategy_id=self._strategy_id,
            decision_ms=decision_ms,
            direction=direction.value,
            asset=ASSET,
            amount=amount,
            state="PENDING",
            created_ms=now,
            updated_ms=now,
        )
        self._store.insert(record)  # 선기록 — 거래소 호출 전
        try:
            tran_id = self._wallets.transfer(direction, ASSET, amount)
        except OrderRejectedError as exc:
            done = record.with_state("REJECTED", self._clock(), error=str(exc))
        except ExchangeError as exc:
            done = record.with_state("UNKNOWN", self._clock(), error=str(exc))
        else:
            done = record.with_state("CONFIRMED", self._clock(), tran_id=tran_id)
        self._store.update(done)
        return done

    def recover(self) -> list[TransferRecord]:
        """결과를 모르는 이체를 이력 조회로 해소한다. 다시 보내지 않는다."""
        return [self._reconcile(r) for r in self._store.open()]

    def _reconcile(self, r: TransferRecord) -> TransferRecord:
        now = self._clock()
        direction = TransferDirection(r.direction)
        try:
            rows = self._wallets.transfers(direction, r.created_ms - self._lookback_ms, now)
        except ExchangeError as exc:
            kept = r.with_state(r.state, now, error=f"이력 조회 실패: {exc}")
            self._store.update(kept)
            return kept
        claimed = {x.tran_id for x in self._store.all() if x.tran_id and x.intent_id != r.intent_id}
        matches = [
            row
            for row in rows
            if row.asset == r.asset and row.amount == r.amount and row.tran_id not in claimed
        ]
        if len(matches) == 1:
            done = r.with_state("CONFIRMED", now, tran_id=matches[0].tran_id, error=None)
        elif len(matches) > 1:
            done = r.with_state(
                "UNKNOWN", now, error=f"이력에 같은 금액 이체가 {len(matches)}건 — 사람 확인"
            )
        elif now - r.created_ms >= self._resolve_after_ms:
            done = r.with_state("NOT_FOUND", now, error="이력에 없음 (조회로 확인)")
        else:
            done = r.with_state("UNKNOWN", now, error="이력에 아직 없음 — 다음 복구에서 다시 조회")
        self._store.update(done)
        return done
