"""Bronze 받기 시험 — 가짜 네트워크(httpx.MockTransport)로 모든 경로를
돌린다(실제 네트워크 없음)."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest

from mdp.bronze.archive import ArchiveFile, Dataset, Granularity, months_between
from mdp.bronze.fetch import (
    BronzeFetcher,
    FetchResult,
    FetchStatus,
    main,
    parse_checksum,
    update_manifest,
)
from mdp.errors import BronzeConflictError, DataIntegrityError, SourceUnavailableError

TRADES = ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2024, 3, 1))
PAYLOAD = b"agg_trade_id,price,quantity\n1,70000.1,0.5\n" * 1000
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
CHECKSUM_TEXT = f"{PAYLOAD_SHA256}  BTCUSDT-aggTrades-2024-03.zip\n"


def _transport(
    files: dict[str, bytes], *, fail_first: int = 0, status_on_fail: int = 503
) -> tuple[httpx.MockTransport, list[str]]:
    """경로 → 내용 사전으로 가짜 원천을 만든다.

    처음 ``fail_first`` 번은 ``status_on_fail`` 로 답한다."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if len(calls) <= fail_first:
            return httpx.Response(status_on_fail)
        body = files.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(body))})
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler), calls


def _source(payload: bytes = PAYLOAD, checksum: str = CHECKSUM_TEXT) -> dict[str, bytes]:
    path = "/" + TRADES.relative_path
    return {path: payload, path + ".CHECKSUM": checksum.encode()}


def _fetcher(tmp_path: Path, transport: httpx.MockTransport) -> BronzeFetcher:
    return BronzeFetcher(
        tmp_path / "bronze",
        httpx.Client(transport=transport),
        sleep=lambda _s: None,
        chunk_bytes=4096,
    )


# ---------------------------------------------------------------- 이름표


def test_archive_paths_follow_source_layout() -> None:
    assert (
        TRADES.relative_path
        == "data/futures/um/monthly/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-03.zip"
    )
    klines = ArchiveFile(Dataset.KLINES, "BTCUSDT", date(2024, 3, 1), interval="1m")
    assert klines.url() == (
        "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-03.zip"
    )
    daily = ArchiveFile(Dataset.FUNDING_RATE, "ETHUSDT", date(2024, 3, 5), Granularity.DAILY)
    assert daily.filename == "ETHUSDT-fundingRate-2024-03-05.zip"


def test_klines_need_interval_and_others_must_not_have_it() -> None:
    with pytest.raises(ValueError, match="interval"):
        ArchiveFile(Dataset.KLINES, "BTCUSDT", date(2024, 3, 1))
    with pytest.raises(ValueError, match="interval"):
        ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2024, 3, 1), interval="1m")


def test_months_between_inclusive_and_crosses_year() -> None:
    assert months_between(date(2023, 11, 15), date(2024, 2, 1)) == [
        date(2023, 11, 1),
        date(2023, 12, 1),
        date(2024, 1, 1),
        date(2024, 2, 1),
    ]
    with pytest.raises(ValueError):
        months_between(date(2024, 2, 1), date(2024, 1, 1))


def test_parse_checksum_rejects_bad_format() -> None:
    assert parse_checksum(CHECKSUM_TEXT.upper()) == PAYLOAD_SHA256
    for bad in ("", "abc file.zip", "z" * 64 + " file.zip"):
        with pytest.raises(DataIntegrityError):
            parse_checksum(bad)


# ---------------------------------------------------------------- 받기


def test_download_writes_zip_and_checksum_and_is_idempotent(tmp_path: Path) -> None:
    transport, calls = _transport(_source())
    with _fetcher(tmp_path, transport) as fetcher:
        first = fetcher.fetch(TRADES)
        second = fetcher.fetch(TRADES)

    assert first.status is FetchStatus.DOWNLOADED
    assert first.sha256 == PAYLOAD_SHA256 and first.size_bytes == len(PAYLOAD)
    assert first.path is not None and first.path.read_bytes() == PAYLOAD
    assert first.path.with_name(first.path.name + ".CHECKSUM").read_text() == CHECKSUM_TEXT
    assert not list(first.path.parent.glob("*.part"))

    assert second.status is FetchStatus.ALREADY_PRESENT
    assert second.sha256 == PAYLOAD_SHA256
    # 두 번째는 체크섬만 받고 zip 은 다시 받지 않는다
    assert calls.count(f"GET /{TRADES.relative_path}") == 1


def test_checksum_mismatch_leaves_nothing_in_bronze(tmp_path: Path) -> None:
    transport, _ = _transport(_source(payload=PAYLOAD + b"corrupt"))
    with (
        _fetcher(tmp_path, transport) as fetcher,
        pytest.raises(DataIntegrityError, match="체크섬 불일치"),
    ):
        fetcher.fetch(TRADES)
    folder = fetcher.path_of(TRADES).parent
    assert list(folder.iterdir()) == []


def test_existing_file_that_differs_is_never_overwritten(tmp_path: Path) -> None:
    transport, _ = _transport(_source())
    with _fetcher(tmp_path, transport) as fetcher:
        target = fetcher.path_of(TRADES)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"someone edited this")
        with pytest.raises(BronzeConflictError):
            fetcher.fetch(TRADES)
    assert target.read_bytes() == b"someone edited this"


def test_missing_period_is_reported_not_raised(tmp_path: Path) -> None:
    transport, _ = _transport({})
    with _fetcher(tmp_path, transport) as fetcher:
        result = fetcher.fetch(TRADES)
    assert result.status is FetchStatus.MISSING_AT_SOURCE and result.path is None


def test_zip_without_checksum_is_refused(tmp_path: Path) -> None:
    transport, _ = _transport({"/" + TRADES.relative_path: PAYLOAD})
    with (
        _fetcher(tmp_path, transport) as fetcher,
        pytest.raises(DataIntegrityError, match="CHECKSUM"),
    ):
        fetcher.fetch(TRADES)


def test_server_errors_are_retried_then_succeed(tmp_path: Path) -> None:
    transport, calls = _transport(_source(), fail_first=2)
    waits: list[float] = []
    fetcher = BronzeFetcher(
        tmp_path / "bronze", httpx.Client(transport=transport), sleep=waits.append, retry_wait_s=1.0
    )
    assert fetcher.fetch(TRADES).status is FetchStatus.DOWNLOADED
    assert waits == [1.0, 2.0]  # 시도마다 두 배
    assert len(calls) == 4  # 실패 2 + CHECKSUM 1 + zip 1


def test_gives_up_after_max_attempts(tmp_path: Path) -> None:
    transport, _ = _transport(_source(), fail_first=99)
    with (
        _fetcher(tmp_path, transport) as fetcher,
        pytest.raises(SourceUnavailableError, match="3번"),
    ):
        fetcher.fetch(TRADES)


def test_client_errors_other_than_404_stop_immediately(tmp_path: Path) -> None:
    transport, calls = _transport(_source(), fail_first=1, status_on_fail=403)
    with (
        _fetcher(tmp_path, transport) as fetcher,
        pytest.raises(SourceUnavailableError, match="403"),
    ):
        fetcher.fetch(TRADES)
    assert len(calls) == 1


def test_probe_reports_size_without_downloading(tmp_path: Path) -> None:
    transport, calls = _transport(_source())
    with _fetcher(tmp_path, transport) as fetcher:
        assert fetcher.probe(TRADES) == len(PAYLOAD)
        assert fetcher.probe(ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2019, 1, 1))) is None
    assert all(call.startswith("HEAD") for call in calls)
    assert not (tmp_path / "bronze").exists()


# ---------------------------------------------------------------- 체크섬 목록


def test_manifest_is_sorted_deterministic_and_counts_changes(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.csv"
    later = ArchiveFile(Dataset.AGG_TRADES, "BTCUSDT", date(2024, 4, 1))
    results = [
        FetchResult(later, FetchStatus.DOWNLOADED, "b" * 64, 20),
        FetchResult(TRADES, FetchStatus.ALREADY_PRESENT, "a" * 64, 10),
        FetchResult(TRADES, FetchStatus.MISSING_AT_SOURCE),
    ]
    assert update_manifest(manifest, results) == 2
    first_text = manifest.read_bytes()
    assert update_manifest(manifest, results) == 0
    assert manifest.read_bytes() == first_text
    assert first_text.decode().splitlines() == [
        "relative_path,size_bytes,sha256",
        f"{TRADES.relative_path},10,{'a' * 64}",
        f"{later.relative_path},20,{'b' * 64}",
    ]
    assert b"\r\n" not in first_text


def test_main_probe_and_fetch_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mdp.bronze.fetch")
    monkeypatch.delenv("MDP_PG_DSN", raising=False)  # 실행 기록 없이
    transport, _ = _transport(_source())
    real_client: Callable[..., httpx.Client] = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    manifest = tmp_path / "manifests" / "binance.csv"
    common = [
        "--dataset",
        "aggTrades",
        "--symbol",
        "BTCUSDT",
        "--from-month",
        "2024-03",
        "--root",
        str(tmp_path / "bronze"),
        "--manifest",
        str(manifest),
    ]

    assert main([*common, "--probe"]) == 0
    assert f'"size_bytes": {len(PAYLOAD)}' in caplog.text
    assert not manifest.exists()

    caplog.clear()
    assert main(common) == 0
    out = caplog.text
    assert '"status": "downloaded"' in out and '"changed": 1' in out
    assert '"event": "runlog_off"' in out
    assert PAYLOAD_SHA256 in manifest.read_text()
