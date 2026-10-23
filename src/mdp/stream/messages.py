"""체결 원문 메시지 — 재생할 때 바이낸스 실시간과 같은 모양으로 만들고, 일부러 결함을 섞는다.

언제 쓰나: :mod:`mdp.stream.produce` 가 Bronze 체결을 Kafka 로 재생할 때. 호스트 시험에서도 부른다.
읽고 쓰는 것: 없음 (Kafka · 파일을 만지지 않는 순수 함수).

메시지 모양 — 바이낸스 USDⓈ-M 선물 WebSocket ``<symbol>@aggTrade`` 의 한 건과 같은 칸::

    {"e":"aggTrade","E":이벤트 시각,"a":묶음 번호,"s":종목,"p":"가격","q":"수량",
     "f":첫 체결 번호,"l":끝 체결 번호,"T":체결 시각,"m":매수자가 메이커인가}

- 재생에서 ``E`` 는 체결 시각(``T``)과 같게 둔다 — 아카이브에는 이벤트 시각이 없다.
- 실시간 메시지의 칸 뜻은 아카이브 칸(묶음 번호 · 가격 · 수량 · 첫/끝 번호 · 체결 시각 · 메이커)과
  같다고 보고 쓴다. 같은지는 같은 날 일 파일과 맞대어 확인한다(5-2, 그 전까지 '확인 필요').

[이유] 재생 메시지를 실시간과 같은 모양으로 만들어, 스트림 처리 코드가 원천이 무엇인지 몰라도 되게
       했다. 그래서 키 없이 · 장이 열려 있지 않아도 같은 코드를 끝까지 시험할 수 있다.
"""

from __future__ import annotations

import heapq
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "RAW_TOPIC",
    "Delivery",
    "Faults",
    "apply_faults",
    "replay_message",
]

RAW_TOPIC: Final = "raw.binance.um.aggtrade"
"""체결 원문 토픽 — 받은 그대로(재생이면 아카이브 한 줄을 실시간 모양으로) 보관한다."""


class Delivery(StrEnum):
    """[쓰임] 보낸 메시지가 어떤 것인가 (재생 결함 섞기 결과)."""

    NORMAL = "normal"
    DUPLICATE = "duplicate"
    """같은 메시지를 한 번 더 — 재연결 때 같은 체결을 두 번 받는 경우를 흉내 낸다."""
    DELAYED = "delayed"
    """체결 시각보다 늦게 — 네트워크 지연 · 순서 뒤바뀜을 흉내 낸다."""
    SKIPPED = "skipped"
    """보내지 않음 — PC 가 잠들었거나 연결이 끊긴 동안의 체결을 흉내 낸다."""


@dataclass(frozen=True, slots=True)
class Faults:
    """[쓰임] 재생에 일부러 섞을 결함 — 0 이면 끈다. 늘 같은 자리에 섞는다(다시 돌려도 같음).

    - ``duplicate_every``: n 번째 메시지마다 한 번 더 보낸다.
    - ``delay_every`` · ``delay_ms``: n 번째 메시지마다 체결 시각으로 ``delay_ms`` 만큼 뒤에 보낸다.
    - ``gap_from_ms`` · ``gap_to_ms``: 체결 시각이 [from, to) 인 메시지는 보내지 않는다(빈 곳).
    """

    duplicate_every: int = 0
    delay_every: int = 0
    delay_ms: int = 0
    gap_from_ms: int = 0
    gap_to_ms: int = 0

    def __post_init__(self) -> None:
        if min(self.duplicate_every, self.delay_every, self.delay_ms) < 0:
            raise ValueError("결함 설정은 0 이상")
        if self.delay_every and not self.delay_ms:
            raise ValueError("delay_every 를 쓰면 delay_ms 도 줘야 한다")
        if self.gap_from_ms > self.gap_to_ms:
            raise ValueError("빈 곳은 gap_from_ms <= gap_to_ms")

    def in_gap(self, time_ms: int) -> bool:
        """[쓰임] 이 체결 시각이 빈 곳(보내지 않을 구간)인가."""
        return self.gap_from_ms <= time_ms < self.gap_to_ms


def replay_message(symbol: str, row: Sequence[str]) -> tuple[int, bytes]:
    """[쓰임] 아카이브 체결 CSV 한 줄(:data:`mdp.spark.csv.AGG_TRADE_COLUMNS` 순서) →
              (체결 시각 ms, 실시간 모양 JSON 바이트).
    [이유] 가격 · 수량은 글자 그대로 둔다 — 숫자로 바꾸면 소수점 끝자리가 바뀔 수 있다."""
    agg_id, price, quantity, first_id, last_id, transact_time, is_buyer_maker = row
    time_ms = int(transact_time)
    payload = {
        "e": "aggTrade",
        "E": time_ms,
        "a": int(agg_id),
        "s": symbol,
        "p": price,
        "q": quantity,
        "f": int(first_id),
        "l": int(last_id),
        "T": time_ms,
        "m": is_buyer_maker.strip().lower() == "true",
    }
    return time_ms, json.dumps(payload, separators=(",", ":")).encode("utf-8")


def apply_faults(
    messages: Iterable[tuple[int, bytes]], faults: Faults
) -> Iterator[tuple[int, bytes, Delivery]]:
    """[쓰임] (체결 시각, 메시지) 흐름에 결함을 섞어 보낼 순서대로 내보낸다. 빈 곳의 메시지는
              :attr:`Delivery.SKIPPED` 로 내보낸다(보내는 쪽이 세기만 하고 보내지 않음).
    [이유] 늦게 보낼 메시지는 잡아 두었다가, 흐름의 체결 시각이 '원래 시각 + 지연' 을 지나면 그때
           내보낸다 — 실제 지연처럼 '더 나중 체결들 뒤에' 도착한다. 끝나면 남은 것을 모두
           내보낸다."""
    held: list[tuple[int, int, int, bytes]] = []  # (내보낼 시각, 순번, 체결 시각, 메시지)
    for index, (time_ms, payload) in enumerate(messages, start=1):
        if faults.in_gap(time_ms):
            yield time_ms, payload, Delivery.SKIPPED
            continue
        while held and held[0][0] <= time_ms:
            _, _, held_time, held_payload = heapq.heappop(held)
            yield held_time, held_payload, Delivery.DELAYED
        if faults.delay_every and index % faults.delay_every == 0:
            heapq.heappush(held, (time_ms + faults.delay_ms, index, time_ms, payload))
            continue
        yield time_ms, payload, Delivery.NORMAL
        if faults.duplicate_every and index % faults.duplicate_every == 0:
            yield time_ms, payload, Delivery.DUPLICATE
    while held:
        _, _, held_time, held_payload = heapq.heappop(held)
        yield held_time, held_payload, Delivery.DELAYED
