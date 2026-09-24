"""사용자 스트림 이벤트를 주문 기록에 반영한다 — 이벤트는 **신호**, 거래소 조회가 **확정**.

TRADING_CORE §재시작·정합성 복구: "중복·역순 WS 이벤트를 멱등 반영하고 정기 REST 대조를 수행한다."

이벤트 내용을 상태로 직접 쓰지 않는다. 테스트넷 실측에서 순서가 뒤바뀌었다(발동 스톱의 체결이
ALGO_UPDATE TRIGGERING 보다 먼저). 대신 이벤트가 가리키는 주문 하나를
:meth:`FuturesOrderManager.refresh` 로 다시 조회한다. 그래서 같은 이벤트가 두 번 와도, 순서가
바뀌어도 결과는 거래소 상태와 같다.

| 이벤트 | 처리 |
|---|---|
| 우리 주문(ID 가 기록에 있음) | 그 주문을 조회로 다시 맞춘다 (``REFRESHED``) |
| 이벤트는 종결인데 조회가 아직 따라오지 않음 | ``LAGGING`` — :meth:`retry_lagging` 이 다시 본다 |
| 모르는 주문 | ``UNTRACKED`` — 다른 주체가 있다. 자동 조치 없이 감시자 복구로 넘긴다 |
| 계좌 변화 | ``ACCOUNT`` — 포지션 수량을 돌려준다. 비교·조치는 감시자가 한다 |
| 같은 내용 반복 | ``DUPLICATE`` — 조회하지 않는다 |

발동한 보호 스톱의 실제 시장가 주문은 스톱과 같은 ID(``clientAlgoId``)로 온다(실측).
그래서 스톱 기록 하나로 두 이벤트가 모두 이어진다. 스톱 기록은 Algo 조회(``actualOrderId``)로
체결 수량·가격을 얻는다.

연결될 때마다 :meth:`on_connect` 로 열린 주문 전체를 다시 맞춘다 — 끊긴 동안의 이벤트는 오지 않는다.
DB 연결은 스레드에 묶이므로 이 클래스는 **한 스레드**(주 루프)에서만 부른다.
스트림 스레드는 큐에 넣기만 한다.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from auto_trading.exchange.user_stream import (
    ALGO_TERMINAL,
    AccountUpdate,
    AlgoUpdate,
    OrderUpdate,
    UserEvent,
)
from auto_trading.execution.futures_orders import FuturesOrderManager
from auto_trading.storage.repositories.futures_orders import (
    TERMINAL_STATES,
    FuturesOrderRecord,
    FuturesOrderRepository,
)

__all__ = ["StreamSync", "SyncResult"]

_ORDER_TERMINAL = frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"})
_SEEN_LIMIT = 10_000

Kind = Literal["REFRESHED", "LAGGING", "UNTRACKED", "ACCOUNT", "DUPLICATE", "IGNORED"]


@dataclass(frozen=True, slots=True)
class SyncResult:
    kind: Kind
    client_order_id: str | None = None
    record: FuturesOrderRecord | None = None
    positions: dict[str, Decimal] = field(default_factory=dict)


class StreamSync:
    def __init__(self, manager: FuturesOrderManager, store: FuturesOrderRepository) -> None:
        self._manager = manager
        self._store = store
        self._seen: OrderedDict[tuple[str, ...], None] = OrderedDict()
        self._lagging: set[str] = set()

    @property
    def lagging(self) -> frozenset[str]:
        return frozenset(self._lagging)

    def on_connect(self) -> list[FuturesOrderRecord]:
        """(재)연결 직후: 끊긴 동안 놓친 변화를 조회로 메운다."""
        return self._manager.recover()

    def handle(self, event: UserEvent) -> SyncResult:
        if isinstance(event, AccountUpdate):
            return SyncResult("ACCOUNT", positions=dict(event.positions))
        if isinstance(event, OrderUpdate):
            cid, status = event.client_order_id, event.status
            final = status in _ORDER_TERMINAL
            key = ("O", cid, status, str(event.filled_qty))
        elif isinstance(event, AlgoUpdate):
            cid, status = event.client_algo_id, event.status
            final = status in ALGO_TERMINAL
            key = ("A", cid, status, str(event.filled_qty))
        else:
            return SyncResult("IGNORED")
        if key in self._seen:
            return SyncResult("DUPLICATE", cid)
        self._remember(key)
        if self._store.get(cid) is None:
            return SyncResult("UNTRACKED", cid)
        record = self._manager.refresh(cid)
        if final and record.state not in TERMINAL_STATES:
            # 이벤트는 끝났다는데 조회가 아직 따라오지 않았다 (발동 직후 Algo 가 TRIGGERING 등)
            self._lagging.add(cid)
            return SyncResult("LAGGING", cid, record)
        self._lagging.discard(cid)
        return SyncResult("REFRESHED", cid, record)

    def retry_lagging(self) -> list[FuturesOrderRecord]:
        """조회가 이벤트를 따라오지 못한 주문을 다시 본다. 종결되면 목록에서 뺀다."""
        done: list[FuturesOrderRecord] = []
        for cid in sorted(self._lagging):
            record = self._manager.refresh(cid)
            if record.state in TERMINAL_STATES:
                self._lagging.discard(cid)
                done.append(record)
        return done

    def _remember(self, key: tuple[str, ...]) -> None:
        self._seen[key] = None
        if len(self._seen) > _SEEN_LIMIT:
            self._seen.popitem(last=False)
