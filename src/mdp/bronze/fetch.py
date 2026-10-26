"""Bronze 받기 — 원천 zip 을 디스크로 흘려 받으며 체크섬을 대조하고, 맞을 때만 Bronze 에 넣는다.

언제 쓰나: 파이프라인 맨 앞(README 3-1). 사람이 직접 또는 오케스트레이터(4-6)가 부른다.
읽고 쓰는 것:
- 읽기: ``https://data.binance.vision`` 의 zip · ``.CHECKSUM``
- 쓰기: Bronze 폴더 ``<root>/<원천 경로>`` 의 zip 과 ``.CHECKSUM`` (한 번 쓴 파일은 바꾸지 않는다),
        체크섬 목록 ``manifests/bronze/binance.csv`` (경로 · 크기 · SHA-256, 저장소에 커밋),
        실행 기록 PostgreSQL ``pipeline_runs`` · ``run_artifacts`` (``MDP_PG_DSN`` 이 있을 때만)

사용 예::

    python -m mdp.bronze.fetch --dataset aggTrades --symbol BTCUSDT --from-month 2024-03 --probe
    python -m mdp.bronze.fetch --dataset aggTrades --symbol BTCUSDT --from-month 2024-03
    python -m mdp.bronze.fetch --dataset klines --interval 1m --symbol BTCUSDT --from-month 2024-03

Bronze 위치는 ``--root`` > 환경 변수 ``MDP_BRONZE_ROOT`` > ``data/lake/bronze``(Git 밖) 순으로
정한다.

[이유] 기존 연구 코드(``auto_trading.marketdata.archive``)는 zip 을 통째로 메모리에 올린다. 체결 월
       파일은 GB 단위라, 여기서는 1 MiB 씩 디스크로 흘려 쓰며 해시를 함께 계산한다(메모리는 덩어리
       하나만큼).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeVar

import httpx

from mdp.bronze.archive import BASE_URL, ArchiveFile, Dataset, Granularity, months_between
from mdp.errors import BronzeConflictError, DataIntegrityError, SourceUnavailableError
from mdp.runlog.recorder import (
    Artifact,
    NullRecorder,
    PostgresRecorder,
    RunHandle,
    RunRecorder,
    record_run,
)

__all__ = [
    "BronzeFetcher",
    "FetchResult",
    "FetchStatus",
    "default_bronze_root",
    "log_json",
    "main",
    "parse_checksum",
    "read_manifest",
    "record_result",
    "run_fetch",
    "setup_cli_logging",
    "update_manifest",
    "write_manifest_entries",
]

log = logging.getLogger(__name__)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST: Final = REPO_ROOT / "manifests" / "bronze" / "binance.csv"
MANIFEST_COLUMNS: Final = ("relative_path", "size_bytes", "sha256")

_CHUNK_BYTES: Final = 1 << 20
"""한 번에 흘려 쓰는 크기 1 MiB — 메모리는 이만큼만 쓴다."""

_T = TypeVar("_T")


class FetchStatus(StrEnum):
    """[쓰임] 파일 하나를 받은 결과."""

    DOWNLOADED = "downloaded"
    """새로 받아 체크섬이 맞아서 Bronze 에 넣었다."""
    ALREADY_PRESENT = "already_present"
    """Bronze 에 이미 있고 원천 체크섬과 같다 — 다시 받지 않았다."""
    MISSING_AT_SOURCE = "missing_at_source"
    """원천에 그 기간 파일이 없다(404)."""
    SKIPPED_KNOWN = "skipped_known"
    """체크섬 목록에 있고 Bronze 파일 크기도 같아 원천에 묻지 않고 건너뛰었다(증분 받기)."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    """[쓰임] 받기 한 건의 결과 — 체크섬 목록과 실행 기록(3-2)에 그대로 쓴다."""

    file: ArchiveFile
    status: FetchStatus
    sha256: str | None = None
    size_bytes: int | None = None
    path: Path | None = None
    elapsed_s: float = 0.0


class _RetryableError(Exception):
    """[쓰임] 다시 시도하면 풀릴 수 있는 오류(네트워크 끊김 · 5xx · 429)를 한데 묶는 내부 표시."""


def parse_checksum(text: str) -> str:
    """[쓰임] ``.CHECKSUM`` 내용(``<sha256>  <파일 이름>``)에서 해시 64 자를 소문자로 뽑는다.
    [이유] 형식이 다르면 대조할 기준이 없으므로 받지 않는다."""
    parts = text.strip().split()
    if not parts or len(parts[0]) != 64 or any(c not in "0123456789abcdefABCDEF" for c in parts[0]):
        raise DataIntegrityError(f"CHECKSUM 형식을 읽을 수 없다: {text[:80]!r}")
    return parts[0].lower()


def default_bronze_root() -> Path:
    """[쓰임] ``--root`` 를 주지 않았을 때의 Bronze 위치 — 환경 변수 ``MDP_BRONZE_ROOT`` 또는
              ``<저장소>/data/lake/bronze``.
    [이유] 기본값은 Git 밖(``data/`` 는 .gitignore)이라 실수로 커밋되지 않는다. 큰 원본은 F:
           하드디스크에 두려고 환경 변수로 바꿀 수 있게 했다."""
    from_env = os.environ.get("MDP_BRONZE_ROOT")
    return Path(from_env) if from_env else REPO_ROOT / "data" / "lake" / "bronze"


def _file_sha256(path: Path) -> tuple[str, int]:
    """[쓰임] 디스크 파일의 SHA-256 과 크기를 덩어리씩 읽어 계산한다(메모리는 1 MiB)."""
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        while block := stream.read(_CHUNK_BYTES):
            digest.update(block)
            size_bytes += len(block)
    return digest.hexdigest(), size_bytes


def _write_text_atomic(path: Path, text: str) -> None:
    """[쓰임] 임시 파일에 쓰고 바꿔 끼운다 — 쓰다 끊겨도 반쯤 쓴 파일이 남지 않게."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


class BronzeFetcher:
    """[쓰임] 원천 파일을 Bronze 로 받는 창구 — 체크섬 대조 · 다시 시도 · 이미 있으면 건너뛰기.
    [이유] ``httpx.Client`` 와 ``sleep`` 을 밖에서 넣을 수 있어(의존성 주입) 시험에서는 가짜
           네트워크와 기다리지 않는 sleep 으로 모든 경로를 돌린다."""

    def __init__(
        self,
        bronze_root: Path,
        client: httpx.Client | None = None,
        *,
        base_url: str = BASE_URL,
        max_attempts: int = 3,
        retry_wait_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        chunk_bytes: int = _CHUNK_BYTES,
    ) -> None:
        """[쓰임] ``client`` 를 주지 않으면 직접 만들고 :meth:`close` 에서 그것만 닫는다.
        [이유] 밖에서 받은 연결은 부른 쪽 것이라 여기서 닫지 않는다."""
        self.bronze_root = bronze_root
        self.base_url = base_url
        self.max_attempts = max_attempts
        self.retry_wait_s = retry_wait_s
        self.chunk_bytes = chunk_bytes
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=120.0)

    def close(self) -> None:
        """[쓰임] 이 창구가 직접 만든 HTTP 연결만 닫는다."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BronzeFetcher:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------- 내부

    def _with_retry(self, url: str, action: Callable[[], _T]) -> _T:
        """[쓰임] ``action`` 을 실행하고, 다시 시도할 만한 오류면 기다렸다가 최대 ``max_attempts``
                  번 한다.
        [이유] 공개 아카이브도 가끔 끊기거나 5xx 를 낸다. 기다림은 시도마다 두 배."""
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return action()
            except (_RetryableError, httpx.TransportError) as exc:
                last_error = exc
                log.warning(
                    json.dumps(
                        {"event": "retry", "url": url, "attempt": attempt, "error": str(exc)},
                        ensure_ascii=False,
                    )
                )
                if attempt < self.max_attempts:
                    self._sleep(self.retry_wait_s * 2 ** (attempt - 1))
        raise SourceUnavailableError(
            f"{self.max_attempts}번 시도해도 받지 못했다: {url}"
        ) from last_error

    @staticmethod
    def _raise_if_retryable(response: httpx.Response, url: str) -> None:
        """[쓰임] 5xx · 429 는 다시 시도, 그 밖의 실패(404 제외)는 바로 멈춘다."""
        status = response.status_code
        if status >= 500 or status == httpx.codes.TOO_MANY_REQUESTS:
            raise _RetryableError(f"HTTP {status}: {url}")
        if status != httpx.codes.NOT_FOUND and not response.is_success:
            raise SourceUnavailableError(f"HTTP {status}: {url}")

    def _get_text(self, url: str) -> str | None:
        """[쓰임] 작은 텍스트 파일(``.CHECKSUM``)을 받는다. 404 면 None."""

        def action() -> str | None:
            response = self._client.get(url)
            self._raise_if_retryable(response, url)
            return None if response.status_code == httpx.codes.NOT_FOUND else response.text

        return self._with_retry(url, action)

    def _content_length(self, url: str) -> int | None:
        """[쓰임] HEAD 로 파일 크기(바이트)를 본다. 없으면(404) None, 크기를 안 알려 주면 -1."""

        def action() -> int | None:
            response = self._client.head(url)
            self._raise_if_retryable(response, url)
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            return int(response.headers.get("content-length", -1))

        return self._with_retry(url, action)

    def _stream_to_part(self, url: str, part_path: Path) -> tuple[str, int]:
        """[쓰임] zip 을 ``.part`` 파일로 흘려 쓰며 SHA-256 · 크기를 함께 계산한다.
        [이유] 다 받기 전에는 최종 이름을 쓰지 않는다 — 끊겨도 Bronze 에 반쪽 파일이 보이지 않게."""

        def action() -> tuple[str, int]:
            digest = hashlib.sha256()
            size_bytes = 0
            with self._client.stream("GET", url) as response:
                self._raise_if_retryable(response, url)
                if response.status_code == httpx.codes.NOT_FOUND:
                    raise DataIntegrityError(f"CHECKSUM 은 있는데 zip 이 없다: {url}")
                with part_path.open("wb") as stream:
                    for block in response.iter_bytes(self.chunk_bytes):
                        stream.write(block)
                        digest.update(block)
                        size_bytes += len(block)
                    stream.flush()
                    os.fsync(stream.fileno())
            return digest.hexdigest(), size_bytes

        return self._with_retry(url, action)

    # ------------------------------------------------------------- 공개

    def path_of(self, file: ArchiveFile) -> Path:
        """[쓰임] 그 파일이 놓일 Bronze 경로 (원천 경로 그대로)."""
        return self.bronze_root.joinpath(*file.relative_path.split("/"))

    def probe(self, file: ArchiveFile) -> int | None:
        """[쓰임] 받지 않고 원천 크기만 본다(바이트). 원천에 없으면 None.
        [이유] 큰 파일을 받기 전에 디스크 · 시간을 가늠하려고."""
        return self._content_length(file.url(self.base_url))

    def fetch(self, file: ArchiveFile) -> FetchResult:
        """[쓰임] 파일 하나를 Bronze 로 받는다 — 이미 있으면 체크섬만 대조하고 건너뛴다.
        [이유] 같은 명령을 몇 번 돌려도 결과가 같다(멱등). Bronze 파일이 원천과 다르면 덮어쓰지 않고
               멈춘다."""
        started = time.perf_counter()
        final_path = self.path_of(file)

        # 1 단계: 원천 체크섬을 받는다. 없으면 그 기간 파일이 있는지 본다.
        checksum_text = self._get_text(file.checksum_url(self.base_url))
        if checksum_text is None:
            if self._content_length(file.url(self.base_url)) is None:
                return FetchResult(
                    file, FetchStatus.MISSING_AT_SOURCE, elapsed_s=time.perf_counter() - started
                )
            raise DataIntegrityError(f"CHECKSUM 이 없어 검증할 수 없다: {file.relative_path}")
        expected_sha256 = parse_checksum(checksum_text)
        checksum_path = final_path.with_name(final_path.name + ".CHECKSUM")

        # 2 단계: 이미 Bronze 에 있으면 대조만 한다 (다르면 덮어쓰지 않는다).
        if final_path.exists():
            local_sha256, size_bytes = _file_sha256(final_path)
            if local_sha256 != expected_sha256:
                raise BronzeConflictError(
                    f"Bronze 파일이 원천 체크섬과 다르다: {final_path}\n"
                    f"  원천: {expected_sha256[:16]}  Bronze: {local_sha256[:16]}"
                )
            if not checksum_path.exists():
                _write_text_atomic(checksum_path, checksum_text)
            return FetchResult(
                file,
                FetchStatus.ALREADY_PRESENT,
                local_sha256,
                size_bytes,
                final_path,
                time.perf_counter() - started,
            )

        # 3 단계: .part 로 흘려 받고 체크섬이 맞을 때만 최종 이름으로 바꾼다.
        final_path.parent.mkdir(parents=True, exist_ok=True)
        part_path = final_path.with_name(final_path.name + ".part")
        try:
            actual_sha256, size_bytes = self._stream_to_part(file.url(self.base_url), part_path)
            if actual_sha256 != expected_sha256:
                raise DataIntegrityError(
                    f"체크섬 불일치: {file.relative_path}\n"
                    f"  원천: {expected_sha256[:16]}  받은 것: {actual_sha256[:16]}"
                )
            _write_text_atomic(checksum_path, checksum_text)
            part_path.replace(final_path)
        finally:
            part_path.unlink(missing_ok=True)
        return FetchResult(
            file,
            FetchStatus.DOWNLOADED,
            actual_sha256,
            size_bytes,
            final_path,
            time.perf_counter() - started,
        )


def read_manifest(manifest_path: Path) -> dict[str, tuple[int, str]]:
    """[쓰임] 체크섬 목록 CSV → {상대 경로: (크기, SHA-256)}. 파일이 없으면 빈 사전.
    [이유] 증분 받기(:mod:`mdp.bronze.sync`)가 '이미 받은 것' 을 이 목록으로 판단한다."""
    entries: dict[str, tuple[int, str]] = {}
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                entries[row["relative_path"]] = (int(row["size_bytes"]), row["sha256"])
    return entries


def update_manifest(manifest_path: Path, results: Iterable[FetchResult]) -> int:
    """[쓰임] 체크섬 목록 CSV 에 받은 파일(경로 · 크기 · SHA-256)을 더하거나 고친다. 바뀐 줄 수를
              돌려준다.
    [이유] 데이터는 커밋하지 않고 이 목록만 커밋한다(절대 규칙 5) — 누가 다시 받아도 같은 파일인지
           이 목록으로 확인한다. 경로 순으로 정렬해 두 번 돌려도 같은 파일이 나온다."""
    return write_manifest_entries(
        manifest_path,
        (
            (result.file.relative_path, result.size_bytes, result.sha256)
            for result in results
            if result.sha256 is not None and result.size_bytes is not None
        ),
    )


def write_manifest_entries(manifest_path: Path, rows: Iterable[tuple[str, int, str]]) -> int:
    """[쓰임] 체크섬 목록 CSV 에 (상대 경로, 크기, SHA-256) 줄을 더하거나 고친다.
    바뀐 줄 수를 돌려준다.
    [이유] 원천마다(바이낸스 · 한국투자증권 · 들여온 자료) 목록 파일은 따로지만
           모양 · 정렬 · 원자적 쓰기는 같게."""
    entries = read_manifest(manifest_path)
    changed_count = 0
    for relative_path, size_bytes, sha256 in rows:
        entry = (size_bytes, sha256)
        if entries.get(relative_path) != entry:
            entries[relative_path] = entry
            changed_count += 1
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(MANIFEST_COLUMNS)]
    lines += [f"{path},{size},{sha}" for path, (size, sha) in sorted(entries.items())]
    _write_text_atomic(manifest_path, "\n".join(lines) + "\n")
    return changed_count


def _parse_month(text: str) -> date:
    """[쓰임] ``YYYY-MM`` → 그 달 1 일."""
    year_text, month_text = text.split("-")
    return date(int(year_text), int(month_text), 1)


def _build_parser() -> argparse.ArgumentParser:
    """[쓰임] 명령줄 인자 정의."""
    parser = argparse.ArgumentParser(
        prog="python -m mdp.bronze.fetch", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--dataset", required=True, choices=[d.value for d in Dataset])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from-month", required=True, type=_parse_month, help="YYYY-MM")
    parser.add_argument("--to-month", type=_parse_month, help="YYYY-MM (주지 않으면 첫 달만)")
    parser.add_argument("--interval", help="봉 간격 (klines 일 때만, 예: 1m)")
    parser.add_argument(
        "--root", type=Path, help="Bronze 위치 (기본: MDP_BRONZE_ROOT 또는 data/lake/bronze)"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--probe", action="store_true", help="받지 않고 원천 크기만 본다")
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("MDP_PG_DSN"),
        help="실행 기록 PostgreSQL 주소 (기본: MDP_PG_DSN, 없으면 기록 안 함)",
    )
    return parser


def run_fetch(
    fetcher: BronzeFetcher,
    files: Sequence[ArchiveFile],
    manifest_path: Path,
    recorder: RunRecorder,
    params: Mapping[str, object],
) -> list[FetchResult]:
    """[쓰임] 파일들을 차례로 받고, 하나 받을 때마다 실행 기록에 산출물을 남기고, 끝에 체크섬 목록을
              고친다.
    [이유] 중간에 실패해도 그때까지 받은 파일은 체크섬 목록에 남기고(``finally``), 실행 기록에는
           실패와 오류 한 줄이 남는다(:func:`record_run`)."""
    results: list[FetchResult] = []
    try:
        with record_run(recorder, "bronze.fetch", params) as run:
            for file in files:
                result = fetcher.fetch(file)
                results.append(result)
                record_result(run, result)
            log_json(event="run", run_id=str(run.run_id), job="bronze.fetch")
    finally:
        changed_count = update_manifest(manifest_path, results)
        log_json(event="manifest", path=str(manifest_path), changed=changed_count)
    return results


def record_result(run: RunHandle, result: FetchResult) -> None:
    """[쓰임] 받기 한 건을 실행 기록(산출물 한 줄)과 JSON 로그 한 줄로 남긴다.
    [이유] 받기 · 증분 받기가 같은 모양으로 남겨야 표 하나로 함께 본다."""
    fields = {
        "path": result.file.relative_path,
        "status": result.status.value,
        "size_bytes": result.size_bytes,
        "sha256": result.sha256,
        "elapsed_s": round(result.elapsed_s, 3),
    }
    run.add(
        Artifact(
            path=result.file.relative_path,
            layer="bronze",
            status=result.status.value,
            size_bytes=result.size_bytes,
            sha256=result.sha256,
            elapsed_s=round(result.elapsed_s, 3),
        )
    )
    log_json(event="fetch", **fields)


def log_json(**fields: object) -> None:
    """[쓰임] 한 줄 JSON 로그 — 사람이 읽고, 오케스트레이터(4-6)도 그대로 읽는다."""
    log.info(json.dumps(fields, ensure_ascii=False))


def setup_cli_logging() -> None:
    """[쓰임] 명령줄 실행용 로그 설정 — 메시지(JSON)만 표준 출력으로, httpx 요청 줄은 숨긴다."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 — 달마다 받고(또는 크기만 보고) 체크섬 목록 · 실행 기록을 남긴다.
    [이유] DB 주소가 없으면 기록만 빼고 그대로 돈다 — DB 를 안 띄운 PC 에서도 받기는 된다."""
    setup_cli_logging()
    args = _build_parser().parse_args(argv)
    last_month: date = args.to_month or args.from_month
    files = [
        ArchiveFile(Dataset(args.dataset), args.symbol, month, Granularity.MONTHLY, args.interval)
        for month in months_between(args.from_month, last_month)
    ]
    bronze_root: Path = args.root or default_bronze_root()
    with BronzeFetcher(bronze_root) as fetcher:
        if args.probe:
            for file in files:
                log_json(event="probe", path=file.relative_path, size_bytes=fetcher.probe(file))
            return 0
        params: dict[str, object] = {
            "dataset": args.dataset,
            "symbol": args.symbol,
            "from_month": f"{args.from_month:%Y-%m}",
            "to_month": f"{last_month:%Y-%m}",
            "interval": args.interval,
            "bronze_root": str(bronze_root),
        }
        if args.pg_dsn:
            with PostgresRecorder(args.pg_dsn) as recorder:
                run_fetch(fetcher, files, args.manifest, recorder, params)
        else:
            log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이 받는다")
            run_fetch(fetcher, files, args.manifest, NullRecorder(), params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
