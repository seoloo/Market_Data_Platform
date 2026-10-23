"""체결 원문을 Kafka 토픽에 보내기 — Bronze 재생(키 없음 · 언제나) 또는 바이낸스 실시간(키 없음).

언제 쓰나: Spark 이미지 안에서 (Kafka 를 켠 뒤, ``docker compose --profile stream up -d``)::

    docker compose run --rm spark python -m mdp.stream.produce --source replay --symbol BTCUSDT \\
        --start 2024-03-01T00:00 --end 2024-03-02T00:05 --fresh-topic
    docker compose run --rm spark python -m mdp.stream.produce --source live --symbol BTCUSDT \\
        --duration-s 300

읽고 쓰는 것:
- 읽기: 재생이면 체크섬 목록 · Bronze 체결 zip (읽기 직전 SHA-256 재대조), 실시간이면 WebSocket
- 쓰기: Kafka 토픽 :data:`~mdp.stream.messages.RAW_TOPIC` (키 = 종목, 메시지 시각 = 받은 시각,
  머리 ``source`` = replay · live) · 실행 기록(작업 ``stream.produce``)

[이유] 원문 토픽은 '받은 그대로' 다 — 스트림 처리 규칙을 바꾸면 오프셋을 처음으로 돌려 같은 원문으로
       다시 만들 수 있다(Bronze 와 같은 생각). 그래서 보내는 쪽은 아무것도 고치거나 거르지 않는다.
       재생의 결함 섞기(:class:`~mdp.stream.messages.Faults`)도 '원문에 그런 일이 있었다' 를 흉내
       낼 뿐, 걸러 내는 것은 처리하는 쪽의 일이다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import time
import zipfile
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

from mdp.bronze.archive import Dataset
from mdp.bronze.fetch import (
    DEFAULT_MANIFEST,
    default_bronze_root,
    log_json,
    read_manifest,
    setup_cli_logging,
)
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run
from mdp.silver.sources import plan_month_sources, verify_bronze_file
from mdp.stream.messages import RAW_TOPIC, Delivery, Faults, apply_faults, replay_message

__all__ = ["ensure_topic", "main", "replay_rows"]

RETENTION_MS: Final = 7 * 24 * 3600 * 1000
"""원문 토픽 보관 7 일 — 하루치 BTCUSDT 체결 원문이 약 0.3 GB(재생 기준 추정, 압축 전)."""
LIVE_URL: Final = "wss://fstream.binance.com/market/ws/{symbol}@aggTrade"
"""바이낸스 USDⓈ-M 선물 실시간 체결 주소 (공개 · 키 없음). 2026-03 공지로 주소가 용도별
(``/public`` · ``/market`` · ``/private``)로 나뉘었고 aggTrade 는 ``/market`` 이다 — 예전 주소
(``/ws/…``)는 연결은 되지만 체결을 보내지 않았다(2026-10-24 확인). 또 바뀔 수 있어 ``--url`` 로
바꿀 수 있다."""


def _producer(bootstrap: str) -> Any:
    """[쓰임] Kafka 보내는 쪽 — 모든 복제본 확인(acks=all) · 같은 메시지 두 번 쓰기 방지
              (idempotence).
    [이유] 보내다 재시도해도 토픽에 두 번 들어가지 않게(재생 결함의 '중복' 과 헷갈리지 않게) 한다.
           20 ms 씩 모아 lz4 로 압축해 보낸다 — 하루 200 만 건을 빠르게."""
    from confluent_kafka import Producer

    return Producer(
        {
            "bootstrap.servers": bootstrap,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 20,
            "compression.type": "lz4",
            "queue.buffering.max.messages": 500_000,
        }
    )


def ensure_topic(bootstrap: str, topic: str, *, fresh: bool) -> None:
    """[쓰임] 원문 토픽이 없으면 만든다(파티션 1 · 보관 7 일). ``fresh`` 면 지우고 새로 만든다.
    [이유] 파티션을 하나로 둬 한 종목 안의 순서를 Kafka 가 지킨다(단일 PC · 종목 하나라 충분).
           시연을 처음부터 다시 할 때는 이전 재생이 섞이지 않게 비운다."""
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap})
    if fresh and topic in admin.list_topics(timeout=10).topics:
        for future in admin.delete_topics([topic]).values():
            future.result()
        for _ in range(50):  # 지우기는 비동기 — 사라질 때까지 기다린다
            if topic not in admin.list_topics(timeout=10).topics:
                break
            time.sleep(0.2)
    if topic in admin.list_topics(timeout=10).topics:
        return
    new = NewTopic(
        topic, num_partitions=1, replication_factor=1, config={"retention.ms": str(RETENTION_MS)}
    )
    for future in admin.create_topics([new]).values():
        future.result()
    log_json(event="topic_created", topic=topic, retention_ms=RETENTION_MS)


def _months(start: datetime, end: datetime) -> list[date]:
    """[쓰임] [start, end) 가 걸친 달들의 1 일."""
    months, current = [], date(start.year, start.month, 1)
    while datetime(current.year, current.month, 1, tzinfo=UTC) < end:
        months.append(current)
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)
    return months


def replay_rows(
    manifest_path: Path, bronze_root: Path, symbol: str, start: datetime, end: datetime
) -> Iterator[list[str]]:
    """[쓰임] Bronze 체결에서 [start, end) 의 줄을 체결 시각 순서대로 (CSV 글자 그대로).
    [이유] Silver 와 같은 출처 규칙(:func:`~mdp.silver.sources.plan_month_sources`)으로 고르고
           읽기 직전 SHA-256 을 다시 대조한다 — 재생 결과를 배치 봉과 맞댈 때 입력이 같다고 말할 수
           있게. zip 을 풀지 않고 흘려 읽어 메모리는 작게 쓴다. 파일은 체결 번호 순(= 시각 순)이라
           끝 시각을 넘으면 그 파일 읽기를 멈춘다."""
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    manifest = read_manifest(manifest_path)
    for month in _months(start, end):
        plan = plan_month_sources(manifest, symbol, month, Dataset.AGG_TRADES)
        for source in sorted(plan.sources, key=lambda s: s.days[0]):
            wanted = {d for d in source.days if start.date() <= d <= end.date()}
            if not wanted:
                continue
            zip_path = bronze_root.joinpath(*source.file.relative_path.split("/"))
            verify_bronze_file(zip_path, source.sha256)
            with zipfile.ZipFile(zip_path) as archive:
                (name,) = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                with archive.open(name) as raw:
                    for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline="")):
                        if not row or not row[0].isdigit():  # 머리줄
                            continue
                        time_ms = int(row[5])
                        if time_ms < start_ms:
                            continue
                        if time_ms >= end_ms:
                            break
                        if datetime.fromtimestamp(time_ms / 1000, UTC).date() in wanted:
                            yield row


class _Sender:
    """[쓰임] Kafka 로 한 건씩 보내고 성공 · 실패를 센다."""

    def __init__(self, bootstrap: str, topic: str, source: str) -> None:
        self.producer = _producer(bootstrap)
        self.topic = topic
        self.headers = [("source", source.encode())]
        self.counts: Counter[str] = Counter()
        self.failed = 0

    def _on_delivery(self, err: Any, _msg: Any) -> None:
        if err is not None:
            self.failed += 1

    def send(self, key: str, payload: bytes, received_ms: int, kind: str) -> None:
        """[쓰임] 한 건 보내기 — 보낼 줄이 꽉 차면 비워질 때까지 기다렸다 다시."""
        while True:
            try:
                self.producer.produce(
                    self.topic,
                    value=payload,
                    key=key,
                    timestamp=received_ms,
                    headers=self.headers,
                    on_delivery=self._on_delivery,
                )
                break
            except BufferError:
                self.producer.poll(0.1)
        self.producer.poll(0)
        self.counts[kind] += 1

    def close(self) -> None:
        """[쓰임] 남은 것을 모두 보내고, 하나라도 실패했으면 멈춘다."""
        remaining = self.producer.flush(60)
        if remaining or self.failed:
            raise RuntimeError(f"Kafka 로 못 보낸 메시지: 남음 {remaining} · 실패 {self.failed}")


def _run_replay(args: argparse.Namespace, sender: _Sender) -> None:
    """[쓰임] Bronze 재생 — ``--speed`` 배속(0 이면 최대 속도), 결함 섞기."""
    faults = Faults(args.duplicate_every, args.delay_every, args.delay_ms)
    rows = replay_rows(args.manifest, default_bronze_root(), args.symbol, args.start, args.end)
    messages = (replay_message(args.symbol, row) for row in rows)
    wall_start, first_event = time.monotonic(), None
    for event_ms, payload, kind in apply_faults(messages, faults):
        if args.speed > 0:
            first_event = event_ms if first_event is None else first_event
            ahead = (event_ms - first_event) / 1000 / args.speed - (time.monotonic() - wall_start)
            if ahead > 0:
                time.sleep(ahead)
        sender.send(args.symbol, payload, int(time.time() * 1000), kind)
        if sum(sender.counts.values()) % 500_000 == 0:
            log_json(event="progress", sent=dict(sender.counts))


async def _run_live(args: argparse.Namespace, sender: _Sender) -> None:
    """[쓰임] 실시간 — 받은 글자를 그대로 보낸다. 끊기면 1 · 2 · 4 … 최대 30 초 쉬고 다시 붙는다.
    [이유] 다시 붙는 동안 놓친 체결은 처리 쪽이 체결 번호가 끊긴 것으로 알아챈다(5-2 대사).
           여기서 메우려 하지 않는다 — 원문 토픽에는 실제로 받은 것만 있어야 한다."""
    import websockets

    url = args.url or LIVE_URL.format(symbol=args.symbol.lower())
    stop_at = time.monotonic() + args.duration_s
    backoff = 1.0
    while time.monotonic() < stop_at:
        try:
            async with websockets.connect(url, open_timeout=15) as socket:
                log_json(event="connected", url=url)
                backoff = 1.0
                while time.monotonic() < stop_at:
                    try:
                        text = await asyncio.wait_for(socket.recv(), timeout=5)
                    except TimeoutError:
                        continue
                    data = text.encode("utf-8") if isinstance(text, str) else text
                    sender.send(args.symbol, data, int(time.time() * 1000), Delivery.NORMAL)
        except (OSError, websockets.ConnectionClosed, websockets.InvalidHandshake) as exc:
            sender.counts["reconnects"] += 1
            log_json(event="disconnected", error=f"{type(exc).__name__}: {exc}", retry_s=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _parse_time(text: str) -> datetime:
    """[쓰임] ``2024-03-01T00:00`` (UTC) → 시각."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 (Spark 이미지 안에서)."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.stream.produce")
    parser.add_argument("--source", choices=["replay", "live"], required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", type=_parse_time, help="재생 시작 (UTC, 포함)")
    parser.add_argument("--end", type=_parse_time, help="재생 끝 (UTC, 제외)")
    parser.add_argument("--speed", type=float, default=0.0, help="재생 배속 (0 = 최대 속도)")
    parser.add_argument("--duplicate-every", type=int, default=0)
    parser.add_argument("--delay-every", type=int, default=0)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--duration-s", type=float, default=300.0, help="실시간 받는 시간")
    parser.add_argument("--url", help="실시간 주소 (기본: 바이낸스 선물 aggTrade)")
    parser.add_argument("--topic", default=RAW_TOPIC)
    parser.add_argument("--fresh-topic", action="store_true", help="토픽을 비우고 시작")
    parser.add_argument("--bootstrap", default=os.environ.get("MDP_KAFKA_BOOTSTRAP", "kafka:9092"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if args.source == "replay" and not (args.start and args.end and args.start < args.end):
        parser.error("재생은 --start < --end 가 필요하다")

    params: dict[str, object] = {
        "source": args.source,
        "symbol": args.symbol,
        "topic": args.topic,
        "start": args.start.isoformat() if args.start else None,
        "end": args.end.isoformat() if args.end else None,
        "speed": args.speed,
        "duplicate_every": args.duplicate_every,
        "delay_every": args.delay_every,
        "delay_ms": args.delay_ms,
        "duration_s": args.duration_s if args.source == "live" else None,
    }

    def run(recorder: RunRecorder) -> None:
        with record_run(recorder, "stream.produce", params) as handle:
            started = time.monotonic()
            ensure_topic(args.bootstrap, args.topic, fresh=args.fresh_topic)
            sender = _Sender(args.bootstrap, args.topic, args.source)
            if args.source == "replay":
                _run_replay(args, sender)
            else:
                asyncio.run(_run_live(args, sender))
            sender.close()
            sent = sum(v for k, v in sender.counts.items() if k != "reconnects")
            elapsed = time.monotonic() - started
            handle.add(
                Artifact(
                    f"kafka://{args.topic}",
                    "stream",
                    args.source,
                    row_count=sent,
                    elapsed_s=round(elapsed, 3),
                )
            )
            log_json(
                event="summary",
                run_id=str(handle.run_id),
                sent=sent,
                counts=dict(sender.counts),
                elapsed_s=round(elapsed, 1),
                per_s=round(sent / elapsed) if elapsed else None,
            )

    if args.pg_dsn:
        with PostgresRecorder(args.pg_dsn) as recorder:
            run(recorder)
    else:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이")
        run(NullRecorder())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
