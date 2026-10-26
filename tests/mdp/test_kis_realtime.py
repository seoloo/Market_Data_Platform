"""한국투자증권 실시간 시험 — 체결 글자 읽기 · 구독 메시지 · 받기.

가짜 웹소켓 · 가짜 Kafka 만 쓴다(키 없음).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from mdp.errors import DataIntegrityError, ForbiddenRequestError, SourceUnavailableError
from mdp.kis.realtime import (
    FIELD,
    TRADE_COLUMNS,
    FrameKind,
    parse_frame,
    subscribe_message,
    trade_records,
)
from mdp.stream.kis_produce import capture


def record(
    symbol: str = "005930",
    hour: str = "093001",
    price: str = "71900",
    volume: str = "10",
    cumulative: str = "1000",
) -> list[str]:
    values = ["0"] * len(TRADE_COLUMNS)
    values[FIELD["MKSC_SHRN_ISCD"]] = symbol
    values[FIELD["STCK_CNTG_HOUR"]] = hour
    values[FIELD["STCK_PRPR"]] = price
    values[FIELD["CNTG_VOL"]] = volume
    values[FIELD["ACML_VOL"]] = cumulative
    values[FIELD["BSOP_DATE"]] = "20261027"
    return values


def frame(*records: list[str]) -> str:
    return f"0|H0STCNT0|{len(records):03d}|" + "^".join(v for r in records for v in r)


PING = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20261027093000"}})


def ack(ok: bool = True, symbol: str = "005930") -> str:
    return json.dumps(
        {
            "header": {"tr_id": "H0STCNT0", "tr_key": symbol, "encrypt": "N"},
            "body": {
                "rt_cd": "0" if ok else "1",
                "msg1": "SUBSCRIBE SUCCESS" if ok else "MAX SUBSCRIBE OVER",
            },
        }
    )


# ------------------------------------------------------------------ 글자 읽기 · 구독 메시지


def test_subscribe_message_shape() -> None:
    message = json.loads(subscribe_message("APPROVAL", "005930"))
    assert message["header"] == {
        "approval_key": "APPROVAL",
        "custtype": "P",
        "tr_type": "1",
        "content-type": "utf-8",
    }
    assert message["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}
    assert json.loads(subscribe_message("A", "005930", subscribe=False))["header"]["tr_type"] == "2"


@pytest.mark.parametrize("tr_id", ["H0STCNI0", "H0STCNI9", "H0UNCNT0", "H0STASP0"])
def test_other_realtime_trs_are_refused(tr_id: str) -> None:
    with pytest.raises(ForbiddenRequestError):
        subscribe_message("A", "005930", tr_id=tr_id)  # 체결통보 · 통합 · 호가는 아직 안 씀


def test_bad_symbol_is_refused() -> None:
    with pytest.raises(ValueError):
        subscribe_message("A", "5930")


def test_frames_are_told_apart() -> None:
    trades = parse_frame(frame(record()))
    assert (trades.kind, trades.tr_id, trades.symbol) == (FrameKind.TRADES, "H0STCNT0", "005930")
    assert parse_frame(PING).kind is FrameKind.PINGPONG
    good, bad = parse_frame(ack()), parse_frame(ack(ok=False))
    assert (good.kind, good.ok, good.symbol) == (FrameKind.CONTROL, True, "005930")
    assert (bad.ok, bad.message) == (False, "MAX SUBSCRIBE OVER")


def test_encrypted_frame_stops() -> None:
    with pytest.raises(DataIntegrityError):
        parse_frame("1|H0STCNI0|001|암호문")


def test_trade_records_split_every_47_fields() -> None:
    rows = trade_records(frame(record(cumulative="1000"), record(cumulative="1010")))
    assert [r["ACML_VOL"] for r in rows] == ["1000", "1010"]
    assert rows[0]["STCK_CNTG_HOUR"] == "093001" and len(rows[0]) == 47


def test_trade_records_check_the_count() -> None:
    text = frame(record(), record()).replace("|002|", "|003|")
    with pytest.raises(DataIntegrityError):
        trade_records(text)
    with pytest.raises(DataIntegrityError):
        trade_records(frame(record()) + "^여분")


# ------------------------------------------------------------------ 받기


class FakeSender:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.sent: list[tuple[str, bytes]] = []

    def send(self, key: str, payload: bytes, received_ms: int, kind: str) -> None:
        self.sent.append((key, payload))
        self.counts[kind] += 1


class FakeSocket:
    def __init__(self, incoming: list[str], then_drop: bool) -> None:
        self.incoming = list(incoming)
        self.then_drop = then_drop
        self.outgoing: list[str] = []
        self.pongs: list[str | bytes] = []

    async def send(self, message: str) -> None:
        self.outgoing.append(message)

    async def recv(self) -> str:
        if self.incoming:
            return self.incoming.pop(0)
        if self.then_drop:
            raise OSError("연결 끊김")
        await asyncio.sleep(10)  # 시간 초과로 넘어가게
        return ""

    async def pong(self, data: str | bytes = b"") -> None:
        self.pongs.append(data)


class Clock:
    """부를 때마다 1 초씩 가는 시계."""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


def _run(
    sockets: list[FakeSocket],
    *,
    seconds: int = 40,
    symbols: tuple[str, ...] = ("005930", "000660"),
    wait_s: int = 0,
) -> tuple[FakeSender, list[float], list[str]]:
    start = datetime(2026, 10, 26, 23, 40, tzinfo=UTC)  # 서울 08:40
    clock = Clock(start - timedelta(seconds=wait_s))
    sleeps: list[float] = []
    urls: list[str] = []
    queue: Iterator[FakeSocket] = iter(sockets)

    @asynccontextmanager
    async def connect(url: str) -> Any:
        urls.append(url)
        yield next(queue)

    async def sleep(seconds_: float) -> None:
        sleeps.append(seconds_)
        clock.t += timedelta(seconds=seconds_)

    sender = FakeSender()
    asyncio.run(
        capture(
            connect,
            sender,
            "APPROVAL",
            symbols,
            url="ws://x/tryitout",
            start_at=start,
            stop_at=start + timedelta(seconds=seconds),
            now=clock,
            sleep=sleep,
            recv_timeout_s=0.01,
        )
    )
    return sender, sleeps, urls


def test_capture_forwards_frames_as_received_and_answers_pingpong() -> None:
    trades = frame(record(), record(cumulative="1010"))
    socket = FakeSocket([ack(), ack(symbol="000660"), trades, PING], then_drop=False)
    sender, sleeps, _ = _run([socket], wait_s=120)
    assert sender.sent == [("005930", trades.encode("utf-8"))]
    assert socket.pongs == [PING]
    assert sender.counts["control"] == 2 and sender.counts["pingpong"] == 1
    subscribed = [json.loads(m)["body"]["input"]["tr_key"] for m in socket.outgoing]
    assert subscribed == ["005930", "000660"]
    assert sleeps[0] >= 60  # 붙기 전에 기다렸다


def test_capture_reconnects_and_resubscribes() -> None:
    first = FakeSocket([frame(record())], then_drop=True)
    second = FakeSocket([frame(record(cumulative="1010"))], then_drop=False)
    sender, sleeps, urls = _run([first, second])
    assert sender.counts["reconnects"] == 1 and len(urls) == 2
    assert len(second.outgoing) == 2  # 다시 구독
    assert sender.counts["trades"] == 2 and 1.0 in sleeps


def test_subscription_failure_stops() -> None:
    with pytest.raises(SourceUnavailableError):
        _run([FakeSocket([ack(ok=False)], then_drop=False)])


def test_too_many_symbols_are_refused() -> None:
    with pytest.raises(ValueError):
        _run([], symbols=tuple(f"{i:06d}" for i in range(41)))
