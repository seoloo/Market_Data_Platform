"""marketdata 테스트 공용 픽스처.

네트워크를 쓰지 않는다. ``httpx.MockTransport`` 로 아카이브 서버를 흉내 낸다.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.marketdata.archive import ArchiveFile, BinanceVisionClient, Granularity
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.candles import CandleRepository

SYMBOL = "BTCUSDT"
NOW = 1_726_000_000_000


def make_csv_rows(
    start_ms: int,
    count: int,
    timeframe: Timeframe,
    *,
    with_header: bool = False,
    price: str = "60000.00",
) -> str:
    """Binance klines CSV 를 만든다 (12 컬럼)."""
    lines: list[str] = []
    if with_header:
        lines.append(
            "open_time,open,high,low,close,volume,close_time,quote_volume,"
            "count,taker_buy_volume,taker_buy_quote_volume,ignore"
        )
    for i in range(count):
        open_ms = start_ms + i * timeframe.ms
        close_ms = open_ms + timeframe.ms - 1
        lines.append(
            f"{open_ms},{price},60100.00,59900.00,{price},1.5,{close_ms},90000.0,42,0.7,42000.0,0"
        )
    return "\n".join(lines) + "\n"


def make_zip(stem: str, csv_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}.csv", csv_text)
    return buffer.getvalue()


@dataclass
class FakeArchiveServer:
    """URL -> 응답 매핑. 등록되지 않은 경로는 404 를 돌려준다."""

    files: dict[str, bytes] = field(default_factory=dict)
    request_log: list[str] = field(default_factory=list)
    corrupt_checksums: set[str] = field(default_factory=set)
    server_errors: dict[str, int] = field(default_factory=dict)

    def add(self, archive_file: ArchiveFile, csv_text: str) -> None:
        payload = make_zip(archive_file.stem, csv_text)
        self.files[archive_file.url()] = payload
        digest = hashlib.sha256(payload).hexdigest()
        self.files[archive_file.checksum_url()] = (f"{digest}  {archive_file.filename}\n").encode()

    def add_raw(self, archive_file: ArchiveFile, payload: bytes) -> None:
        self.files[archive_file.url()] = payload
        digest = hashlib.sha256(payload).hexdigest()
        self.files[archive_file.checksum_url()] = (f"{digest}  {archive_file.filename}\n").encode()

    def corrupt(self, archive_file: ArchiveFile) -> None:
        """체크섬은 그대로 두고 본문만 바꿔 불일치를 만든다."""
        self.files[archive_file.url()] = b"corrupted payload"

    def drop_checksum(self, archive_file: ArchiveFile) -> None:
        self.files.pop(archive_file.checksum_url(), None)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.request_log.append(url)
        if url in self.server_errors:
            return httpx.Response(self.server_errors[url])
        payload = self.files.get(url)
        if payload is None:
            return httpx.Response(404)
        return httpx.Response(200, content=payload)

    def client(self) -> BinanceVisionClient:
        transport = httpx.MockTransport(self.handler)
        return BinanceVisionClient(
            httpx.Client(transport=transport),
            max_retries=2,
            request_delay_s=0.0,
        )


@pytest.fixture
def server() -> FakeArchiveServer:
    return FakeArchiveServer()


@pytest.fixture
def repo(tmp_path: Path):
    conn = connect(tmp_path / "md.db")
    apply_migrations(conn, now_ms=NOW)
    yield CandleRepository(conn)
    conn.close()


@pytest.fixture
def monthly() -> Callable[[date, Timeframe], ArchiveFile]:
    def _make(period: date, timeframe: Timeframe = Timeframe.M1) -> ArchiveFile:
        return ArchiveFile(SYMBOL, timeframe, period, Granularity.MONTHLY)

    return _make


@pytest.fixture
def daily() -> Callable[[date, Timeframe], ArchiveFile]:
    def _make(period: date, timeframe: Timeframe = Timeframe.M1) -> ArchiveFile:
        return ArchiveFile(SYMBOL, timeframe, period, Granularity.DAILY)

    return _make
