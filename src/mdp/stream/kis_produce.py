"""국내 주식 실시간 체결을 Kafka 원문 토픽에 받은 그대로 쌓는다(K-4) — 한국투자증권 웹소켓, 조회만.

언제 쓰나: 장 날 아침에 띄워 두면 정한 시각에 붙고 정한 시각에 멈춘다. Kafka 를 켠 뒤::

    docker compose --profile stream run -d --name mdp-kis-capture kis-capture \\
        python -m mdp.stream.kis_produce --symbols 005930,000660,069500 \\
        --start-at 2026-10-27T08:40 --stop-at 2026-10-27T15:45

읽고 쓰는 것:
- 읽기: 한국투자증권 웹소켓 접속키(:meth:`mdp.kis.client.KisClient.approval_key`), 실시간 체결
  ``H0STCNT0`` (KRX)
- 쓰기: Kafka 토픽 :data:`RAW_TOPIC` (값 = 받은 글자 그대로, 키 = 종목, 메시지 시각 = 받은 시각,
  머리 ``source=kis_live``) · 실행 기록(작업 ``stream.kis_produce``)

[이유] 원문 토픽은 '받은 그대로' 다(바이낸스 원문 토픽과 같은 생각, ADR 0009) — 스트림 처리 규칙을
       바꾸면 같은 원문으로 다시 만든다. 그래서 여기서는 거르지도 고치지도 않는다. 끊긴 동안 놓친
       체결은 처리 쪽이 누적 거래량이 끊긴 것으로 알아챈다. 제어 메시지(구독 응답 · PINGPONG)는
       토픽에 넣지 않고 세기만 한다. 구독 실패(예: 구독 수 초과)는 조용히 넘기지 않고 멈춘다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.errors import SourceUnavailableError
from mdp.kis.client import Credentials, KisClient
from mdp.kis.realtime import FrameKind, parse_frame, subscribe_message
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run

__all__ = ["MAX_SUBSCRIPTIONS", "RAW_TOPIC", "capture", "main"]

RAW_TOPIC: Final = "raw.kis.domestic.ccnl"
SEOUL: Final = ZoneInfo("Asia/Seoul")
MAX_SUBSCRIPTIONS: Final = 40
"""한 연결의 구독 수 한도 — 공식 예제가 40 을 넘으면 멈춘다(정확한 한도는 확인 필요)."""
WS_PATH: Final = "/tryitout"
"""공식 예제가 붙는 경로 (``KISWebSocket(api_url="/tryitout")``)."""


class Sender(Protocol):
    """[쓰임] Kafka 로 보내는 쪽의 모양 — :class:`mdp.stream.produce._Sender` 와 같다."""

    counts: Any

    def send(self, key: str, payload: bytes, received_ms: int, kind: str) -> None: ...


class Socket(Protocol):
    """[쓰임] 웹소켓의 모양 — 보내기 · 받기 · pong."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def pong(self, data: str | bytes = b"") -> Any: ...


Connect = Callable[[str], Any]
"""주소 → ``async with`` 로 여는 연결(:class:`Socket`)."""


async def _wait_until(
    moment: datetime, now: Callable[[], datetime], sleep: Callable[[float], Awaitable[None]]
) -> None:
    """[쓰임] 그 시각까지 1 분씩 기다린다(그 사이 깨어나 로그 한 줄)."""
    while (left := (moment - now()).total_seconds()) > 0:
        log_json(event="waiting", until=moment.isoformat(), seconds=round(left))
        await sleep(min(left, 60.0))


async def capture(
    connect: Connect,
    sender: Sender,
    approval_key: str,
    symbols: Sequence[str],
    *,
    url: str,
    start_at: datetime,
    stop_at: datetime,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    recv_timeout_s: float = 5.0,
) -> None:
    """[쓰임] ``start_at`` 까지 기다렸다가 붙어 종목마다 구독하고, ``stop_at`` 까지 받은 체결을
              보낸다. 끊기면 1 · 2 · 4 … 최대 30 초 쉬고 다시 붙어 다시 구독한다.
    [이유] 체결 글자는 받은 그대로 보낸다(종목은 키로만). PINGPONG 은 받은 그대로 pong 으로 돌려준다
           (공식 예제 방식) — 안 하면 서버가 연결을 끊는다(100 초, 공개 자료 · 확인 필요)."""
    import websockets

    if len(symbols) > MAX_SUBSCRIPTIONS:
        raise ValueError(f"구독은 {MAX_SUBSCRIPTIONS} 개까지: {len(symbols)}")
    await _wait_until(start_at, now, sleep)
    backoff = 1.0
    while now() < stop_at:
        try:
            async with connect(url) as socket:
                log_json(event="connected", url=url)
                for symbol in symbols:
                    await socket.send(subscribe_message(approval_key, symbol))
                    await sleep(0.05)
                backoff = 1.0
                while now() < stop_at:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=recv_timeout_s)
                    except TimeoutError:
                        continue
                    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    frame = parse_frame(text)
                    if frame.kind is FrameKind.TRADES:
                        sender.send(
                            str(frame.symbol),
                            text.encode("utf-8"),
                            int(now().timestamp() * 1000),
                            "trades",
                        )
                    elif frame.kind is FrameKind.PINGPONG:
                        await socket.pong(text)
                        sender.counts["pingpong"] += 1
                    else:
                        sender.counts["control"] += 1
                        log_json(
                            event="control",
                            tr_id=frame.tr_id,
                            symbol=frame.symbol,
                            ok=frame.ok,
                            message=frame.message,
                        )
                        if frame.ok is False:
                            raise SourceUnavailableError(
                                f"구독 실패: {frame.tr_id} {frame.symbol} — {frame.message}"
                            )
        except (OSError, websockets.ConnectionClosed, websockets.InvalidHandshake) as exc:
            sender.counts["reconnects"] += 1
            log_json(event="disconnected", error=f"{type(exc).__name__}: {exc}", retry_s=backoff)
            await sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _seoul_time(text: str) -> datetime:
    """[쓰임] ``2026-10-27T08:40`` (서울 시각) → UTC 시각."""
    return datetime.fromisoformat(text).replace(tzinfo=SEOUL).astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안 · Kafka 와 같은 네트워크에서)."""
    import websockets

    from mdp.stream.produce import _Sender, ensure_topic

    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.stream.kis_produce")
    parser.add_argument("--symbols", required=True, help="쉼표로, 예: 005930,000660,069500")
    parser.add_argument("--start-at", required=True, type=_seoul_time, help="서울 시각에 붙는다")
    parser.add_argument("--stop-at", required=True, type=_seoul_time, help="서울 시각에 멈춘다")
    parser.add_argument("--topic", default=RAW_TOPIC)
    parser.add_argument("--bootstrap", default=os.environ.get("MDP_KAFKA_BOOTSTRAP", "kafka:9092"))
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    symbols = sorted({s.strip() for s in args.symbols.split(",") if s.strip()})
    if args.start_at >= args.stop_at:
        parser.error("--start-at 이 --stop-at 보다 앞이어야 한다")
    credentials = Credentials.from_env()
    params: dict[str, object] = {
        "symbols": ",".join(symbols),
        "start_at": args.start_at.isoformat(),
        "stop_at": args.stop_at.isoformat(),
        "env": credentials.env.value,
        "topic": args.topic,
    }

    def run(recorder: RunRecorder) -> None:
        with record_run(recorder, "stream.kis_produce", params) as handle:
            started = time.monotonic()
            with KisClient(credentials) as client:
                approval_key = client.approval_key()
            ensure_topic(args.bootstrap, args.topic, fresh=False)
            sender = _Sender(args.bootstrap, args.topic, "kis_live")
            url = credentials.env.ws_url + WS_PATH
            asyncio.run(
                capture(
                    lambda address: websockets.connect(address, open_timeout=15),
                    sender,
                    approval_key,
                    symbols,
                    url=url,
                    start_at=args.start_at,
                    stop_at=args.stop_at,
                )
            )
            sender.close()
            sent = int(sender.counts["trades"])
            handle.add(
                Artifact(
                    f"kafka://{args.topic}",
                    "stream",
                    "kis_live",
                    row_count=sent,
                    elapsed_s=round(time.monotonic() - started, 3),
                )
            )
            log_json(event="summary", run_id=str(handle.run_id), counts=dict(sender.counts))

    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run(recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        run(NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
