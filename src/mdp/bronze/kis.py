"""국내 주식 Bronze 받기 — 한국투자증권 응답 본문을 받은 그대로 보관하고 SHA-256 을 매긴다(K-2 ②).

언제 쓰나: 국내 주식 파이프라인 맨 앞. 사람이 직접 또는 오케스트레이터가 부른다.
읽고 쓰는 것:
- 읽기: 한국투자증권 휴장일 · 일봉 · 지난 날 분봉 (:mod:`mdp.kis.client` 의 허용 목록 조회만)
- 쓰기: Bronze ``<root>/kis/...`` 의 페이지 파일과 완료 표시 ``_SUCCESS.json``
  (한 번 쓴 폴더는 바꾸지 않는다), 체크섬 목록 ``manifests/bronze/kis.csv``,
  실행 기록 PostgreSQL ``pipeline_runs`` · ``run_artifacts`` (``MDP_PG_DSN`` 이 있을 때만)

받는 단위 (단위 하나 = 폴더 하나, ``kis/domestic/`` 아래):

- 휴장일 — 달: ``holiday/month=2025-10/``
- 일봉 — 수정 · 원주가 × 종목 × 달: ``daily-bars/price=adjusted/symbol=005930/month=2018-05/``
- 분봉 — 종목 × 날: ``minute-bars/market=J/symbol=005930/date=2026-10-23/``

폴더 안: ``page-00000.json.gz`` … (응답 본문 한 번 = 파일 하나, gzip 으로 감싸기만 함)과
``_SUCCESS.json`` (페이지마다 요청 인자 · 크기 · SHA-256 · 본문 SHA-256 · 건수, 받은 시각).

사용 예::

    python -m mdp.bronze.kis --kind holiday --start 2025-10-01 --end 2026-09-30
    python -m mdp.bronze.kis --kind daily --symbols 005930,000660,069500 \
        --start 2015-01-01 --end 2026-09-30
    python -m mdp.bronze.kis --kind minute --symbols 005930,000660,069500 \
        --start 2025-10-01 --end 2026-10-26

[이유] 원천이 체크섬을 주지 않으므로 받을 때 우리가 매긴다. ``.part`` 폴더에 다 쓴 뒤 이름을
       바꿔 반쪽 단위가 Bronze 에 보이지 않게 한다. 분봉은 **장이 안 열린 날을 물으면 그 전
       거래일 분봉을 돌려준다**(2026-10-09 한글날 → 10-08 분봉, 2026-10-26 확인) — 그래서 응답
       날짜가 물은 날과 다르면 저장하지 않는다.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from mdp.bronze.archive import months_between
from mdp.bronze.fetch import (
    REPO_ROOT,
    _file_sha256,
    _write_text_atomic,
    default_bronze_root,
    log_json,
    read_manifest,
    setup_cli_logging,
    write_manifest_entries,
)
from mdp.errors import BronzeConflictError, DataIntegrityError
from mdp.kis.client import (
    CHK_HOLIDAY,
    DAILY_BARS,
    MINUTE_BARS_PAST,
    Credentials,
    KisClient,
    MissingCredentialsError,
    Quotation,
)
from mdp.runlog.recorder import (
    Artifact,
    NullRecorder,
    PostgresRecorder,
    RunHandle,
    RunRecorder,
    record_run,
)

__all__ = [
    "DEFAULT_MANIFEST",
    "SEOUL",
    "Kind",
    "Price",
    "Unit",
    "UnitResult",
    "UnitStatus",
    "fetch_unit",
    "main",
    "plan_units",
    "run_units",
]

DEFAULT_MANIFEST: Final = REPO_ROOT / "manifests" / "bronze" / "kis.csv"
SEOUL: Final = ZoneInfo("Asia/Seoul")
SUCCESS: Final = "_SUCCESS.json"
MARKET: Final = "J"
"""시장 구분 — ``J`` 는 KRX (넥스트레이드 ``NX`` · 통합 ``UN`` 은 아직 받지 않는다)."""
READY_HOUR: Final = 20
READY_MINUTE: Final = 30
"""그 날 20:30(서울) 뒤에 받는다 — 분봉은 18:00 시간외 봉까지 있고(2026-10-26 확인), 넉넉히."""
MINUTE_START: Final = "200000"
"""분봉은 이 시각부터 거슬러 받는다(18:00 뒤 봉은 없었지만 놓치지 않게)."""
MINUTE_PAGE_ROWS: Final = 120
"""지난 날 분봉 한 번 최대 건수(공식 예제)."""
OPEN_TIME: Final = "090000"
"""정규장 첫 봉 — 이 봉까지 오면 그 날을 다 받은 것이다(09:05 부터 물으면 09:00 에서 끝났다)."""


class Kind(StrEnum):
    """[쓰임] 받을 자료 종류."""

    HOLIDAY = "holiday"
    DAILY = "daily"
    MINUTE = "minute"


class Price(StrEnum):
    """[쓰임] 일봉 가격 — 수정주가(분할 · 병합을 거슬러 맞춤) 또는 원주가.
    값은 원천 인자 ``FID_ORG_ADJ_PRC`` 로 간다."""

    ADJUSTED = "adjusted"
    RAW = "raw"

    @property
    def code(self) -> str:
        """[쓰임] 원천 인자 값 — 0 수정주가 · 1 원주가(공식 예제)."""
        return "0" if self is Price.ADJUSTED else "1"


class UnitStatus(StrEnum):
    """[쓰임] 단위 하나를 받은 결과."""

    DOWNLOADED = "downloaded"
    """새로 받아 Bronze 에 넣었다."""
    ALREADY_PRESENT = "already_present"
    """Bronze 에 이미 있고, 페이지 파일이 완료 표시의 SHA-256 과 같다."""
    SKIPPED_KNOWN = "skipped_known"
    """체크섬 목록에 있고 완료 표시 크기도 같아 원천에 묻지 않았다(증분 받기)."""
    NOT_READY = "not_ready"
    """아직 받을 수 없다 — 그 날(달)이 끝나지 않았다."""
    NOT_OPEN = "not_open"
    """분봉: 그 날 장이 안 열렸다(원천이 그 전 거래일 분봉을 돌려줬다). 저장하지 않는다."""
    MISSING_AT_SOURCE = "missing_at_source"
    """분봉: 원천에 그 날 분봉이 없다(보관 기간 밖 등). 저장하지 않는다."""


class _NotStoredError(Exception):
    """[쓰임] 첫 페이지를 보고 '저장하지 않을 단위' 라고 판단했다 — 상태를 들고 간다."""

    def __init__(self, status: UnitStatus) -> None:
        super().__init__(status.value)
        self.status = status


def _month_last_day(first: date) -> date:
    """[쓰임] 그 달 마지막 날."""
    return date(first.year + first.month // 12, first.month % 12 + 1, 1) - timedelta(days=1)


def _rows(body: bytes, key: str) -> list[dict[str, Any]]:
    """[쓰임] 응답의 ``key`` 배열. 없거나 배열이 아니면 멈춘다
    (엉뚱한 응답을 Bronze 에 넣지 않게)."""
    decoded = json.loads(body)
    value = decoded.get(key) if isinstance(decoded, dict) else None
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise DataIntegrityError(f"응답에 {key!r} 배열이 없다")
    return value


def _minus_one_minute(hhmmss: str) -> str:
    """[쓰임] ``HHMMSS`` 에서 1 분 앞 — 분봉을 거슬러 이어 받을 다음 기준 시각."""
    total = int(hhmmss[:2]) * 60 + int(hhmmss[2:4]) - 1
    return f"{total // 60:02d}{total % 60:02d}{hhmmss[4:6]}"


@dataclass(frozen=True, slots=True)
class Unit:
    """[쓰임] 받는 단위 하나 — 종류 · 기간(분봉은 그 날, 나머지는 달 1 일) · 종목 · 가격(일봉만).
    [이유] 원천 요청 · Bronze 경로 · '언제부터 받을 수 있나' 를 한 곳에서 만든다."""

    kind: Kind
    period: date
    symbol: str | None = None
    price: Price | None = None

    def __post_init__(self) -> None:
        """[쓰임] 종류마다 필요한 것만 있어야 한다."""
        if (self.kind is Kind.HOLIDAY) != (self.symbol is None):
            raise ValueError(f"휴장일만 종목이 없다: {self.kind} {self.symbol}")
        if (self.kind is Kind.DAILY) != (self.price is not None):
            raise ValueError(f"일봉만 가격(수정 · 원)이 있다: {self.kind} {self.price}")
        if self.kind is not Kind.MINUTE and self.period.day != 1:
            raise ValueError(f"달 단위는 1 일로: {self.period}")

    @property
    def relative_dir(self) -> str:
        """[쓰임] Bronze 안의 폴더 경로 (체크섬 목록 · 실행 기록에도 이 이름)."""
        month = f"month={self.period:%Y-%m}"
        if self.kind is Kind.HOLIDAY:
            return f"kis/domestic/holiday/{month}"
        if self.kind is Kind.DAILY:
            price = self.price.value if self.price else ""
            return f"kis/domestic/daily-bars/price={price}/symbol={self.symbol}/{month}"
        return f"kis/domestic/minute-bars/market={MARKET}/symbol={self.symbol}/date={self.period}"

    @property
    def quotation(self) -> Quotation:
        """[쓰임] 물을 원천 조회."""
        return {Kind.HOLIDAY: CHK_HOLIDAY, Kind.DAILY: DAILY_BARS}.get(self.kind, MINUTE_BARS_PAST)

    @property
    def ready_at(self) -> datetime:
        """[쓰임] 이 시각(UTC)이 지나야 받는다
        — 분봉은 그 날, 나머지는 그 달 마지막 날의 20:30(서울).
        [이유] 끝나지 않은 날 · 달을 받으면 Bronze 에 반쪽이 영원히 남는다(Bronze 는 다시
               쓰지 않는다). 휴장일도 달이 끝난 뒤에 받는다 — 임시공휴일처럼 늦게 바뀌는
               날이 있어서."""
        last = self.period if self.kind is Kind.MINUTE else _month_last_day(self.period)
        local = datetime(last.year, last.month, last.day, READY_HOUR, READY_MINUTE, tzinfo=SEOUL)
        return local.astimezone(UTC)

    def _first_params(self) -> dict[str, str]:
        """[쓰임] 첫 요청 인자."""
        if self.kind is Kind.HOLIDAY:
            return {"BASS_DT": f"{self.period:%Y%m%d}", "CTX_AREA_FK": "", "CTX_AREA_NK": ""}
        if self.kind is Kind.DAILY:
            return {
                "FID_COND_MRKT_DIV_CODE": MARKET,
                "FID_INPUT_ISCD": str(self.symbol),
                "FID_INPUT_DATE_1": f"{self.period:%Y%m%d}",
                "FID_INPUT_DATE_2": f"{_month_last_day(self.period):%Y%m%d}",
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": self.price.code if self.price else "",
            }
        return {
            "FID_COND_MRKT_DIV_CODE": MARKET,
            "FID_INPUT_ISCD": str(self.symbol),
            "FID_INPUT_HOUR_1": MINUTE_START,
            "FID_INPUT_DATE_1": f"{self.period:%Y%m%d}",
            "FID_PW_DATA_INCU_YN": "N",
            "FID_FAKE_TICK_INCU_YN": "",
        }

    def pages(self, client: KisClient) -> Iterator[tuple[dict[str, str], bytes, int]]:
        """[쓰임] (요청 인자, 응답 본문, 건수)를 한 페이지씩 내준다.
        저장하지 않을 단위면 첫 페이지에서 :class:`_NotStoredError`.
        [이유] 이어 받는 방식이 종류마다 다르다 — 휴장일은 ``tr_cont`` 와 위치 키(그 달을
               넘으면 멈춤), 일봉은 한 달이 한 번(최대 100 건 > 한 달 거래일), 분봉은 마지막
               봉의 1 분 앞 시각으로 거슬러."""
        params = self._first_params()
        if self.kind is Kind.HOLIDAY:
            end = f"{_month_last_day(self.period):%Y%m%d}"
            for body in client.pages(self.quotation, params):
                rows = _rows(body, "output")
                yield params, body, len(rows)
                if not rows or str(rows[-1].get("bass_dt", "")) >= end:
                    return
            return
        if self.kind is Kind.DAILY:
            body = client.get(self.quotation, params).body
            rows = _rows(body, "output2")
            first, last = params["FID_INPUT_DATE_1"], params["FID_INPUT_DATE_2"]
            outside = [r for r in rows if not first <= str(r.get("stck_bsop_date", "")) <= last]
            if outside:
                raise DataIntegrityError(f"요청한 달 밖의 일봉이 왔다: {self.relative_dir}")
            yield params, body, len(rows)
            return
        yield from self._minute_pages(client, params)

    def _minute_pages(
        self, client: KisClient, params: dict[str, str]
    ) -> Iterator[tuple[dict[str, str], bytes, int]]:
        """[쓰임] 분봉 — 20:00 부터 09:00 봉까지 거슬러. 첫 페이지 날짜가 다르면 저장하지 않는다."""
        day = f"{self.period:%Y%m%d}"
        cursor = MINUTE_START
        first_page = True
        while True:
            query = {**params, "FID_INPUT_HOUR_1": cursor}
            body = client.get(self.quotation, query).body
            rows = [r for r in _rows(body, "output2") if r.get("stck_bsop_date")]
            dates = {str(r["stck_bsop_date"]) for r in rows}
            if first_page and not rows:
                raise _NotStoredError(UnitStatus.MISSING_AT_SOURCE)
            if first_page and dates != {day}:
                raise _NotStoredError(UnitStatus.NOT_OPEN)
            if dates and dates != {day}:
                raise DataIntegrityError(f"분봉 중간에 다른 날이 섞였다: {self.relative_dir}")
            first_page = False
            yield query, body, len(rows)
            earliest = min((str(r.get("stck_cntg_hour", "")) for r in rows), default=OPEN_TIME)
            if len(rows) < MINUTE_PAGE_ROWS or earliest <= OPEN_TIME:
                return
            following = _minus_one_minute(earliest)
            if following >= cursor:
                raise DataIntegrityError(
                    f"분봉 이어 받기가 앞으로 가지 않는다: {self.relative_dir}"
                )
            cursor = following


@dataclass(frozen=True, slots=True)
class UnitResult:
    """[쓰임] 단위 하나의 결과 — 체크섬 목록 줄 · 실행 기록 · 로그에 쓴다."""

    unit: Unit
    status: UnitStatus
    records: int | None = None
    pages: int = 0
    rows: tuple[tuple[str, int, str], ...] = field(default=())
    """체크섬 목록에 쓸 (상대 경로, 크기, SHA-256) — 페이지 파일들과 완료 표시."""
    elapsed_s: float = 0.0

    @property
    def size_bytes(self) -> int | None:
        """[쓰임] 폴더 안 파일 크기 합."""
        return sum(size for _, size, _ in self.rows) if self.rows else None

    @property
    def marker_sha256(self) -> str | None:
        """[쓰임] 완료 표시의 SHA-256 — 이 단위 전체를 가리키는 값."""
        return next((sha for path, _, sha in self.rows if path.endswith(SUCCESS)), None)


def _folder_rows(unit: Unit, folder: Path) -> tuple[tuple[str, int, str], ...]:
    """[쓰임] 폴더 안 모든 파일의 (상대 경로, 크기, SHA-256)."""
    rows = []
    for path in sorted(folder.iterdir()):
        sha256, size_bytes = _file_sha256(path)
        rows.append((f"{unit.relative_dir}/{path.name}", size_bytes, sha256))
    return tuple(rows)


def _verify_present(unit: Unit, folder: Path) -> dict[str, Any]:
    """[쓰임] 이미 있는 폴더의 페이지 파일이 완료 표시와 같은지 본다. 다르면 멈춘다."""
    marker = json.loads((folder / SUCCESS).read_text(encoding="utf-8"))
    for page in marker["pages"]:
        sha256, size_bytes = _file_sha256(folder / page["name"])
        if (sha256, size_bytes) != (page["sha256"], page["size_bytes"]):
            raise BronzeConflictError(
                f"Bronze 페이지가 완료 표시와 다르다: {unit.relative_dir}/{page['name']}"
            )
    return dict(marker)


def fetch_unit(
    client: KisClient,
    bronze_root: Path,
    unit: Unit,
    *,
    known: Mapping[str, tuple[int, str]],
    now: datetime,
) -> UnitResult:
    """[쓰임] 단위 하나를 Bronze 로 받는다 — 이미 있으면 대조만, 아직 이르면 받지 않는다.
    [이유] 같은 명령을 몇 번 돌려도 결과가 같다(멱등).
           받은 뒤 다른 것이 보이면 덮어쓰지 않고 멈춘다."""
    started = time.perf_counter()
    folder = bronze_root.joinpath(*unit.relative_dir.split("/"))
    marker_path = folder / SUCCESS
    marker_key = f"{unit.relative_dir}/{SUCCESS}"

    def done(status: UnitStatus, **kwargs: Any) -> UnitResult:
        return UnitResult(unit, status, elapsed_s=time.perf_counter() - started, **kwargs)

    # 1 단계: 아직 받을 수 없으면 건너뛴다.
    if now < unit.ready_at:
        return done(UnitStatus.NOT_READY)

    # 2 단계: 체크섬 목록에 있고 완료 표시 크기가 같으면 원천에 묻지 않는다(증분 받기).
    if (
        marker_key in known
        and marker_path.exists()
        and marker_path.stat().st_size == known[marker_key][0]
    ):
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        return done(UnitStatus.SKIPPED_KNOWN, records=marker["records"], pages=len(marker["pages"]))

    # 3 단계: 폴더가 이미 있으면 대조만 한다.
    if marker_path.exists():
        marker = _verify_present(unit, folder)
        return done(
            UnitStatus.ALREADY_PRESENT,
            records=marker["records"],
            pages=len(marker["pages"]),
            rows=_folder_rows(unit, folder),
        )
    if folder.exists():
        raise BronzeConflictError(f"완료 표시 없는 Bronze 폴더가 있다(사람이 확인): {folder}")

    # 4 단계: .part 폴더에 페이지를 다 쓰고, 완료 표시를 쓴 뒤 이름을 바꾼다.
    part = folder.with_name(folder.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    try:
        pages: list[dict[str, Any]] = []
        try:
            for index, (params, body, records) in enumerate(unit.pages(client)):
                name = f"page-{index:05d}.json.gz"
                packed = gzip.compress(body, compresslevel=6, mtime=0)
                with (part / name).open("wb") as stream:
                    stream.write(packed)
                    stream.flush()
                    os.fsync(stream.fileno())
                pages.append(
                    {
                        "name": name,
                        "request": params,
                        "records": records,
                        "size_bytes": len(packed),
                        "sha256": hashlib.sha256(packed).hexdigest(),
                        "body_sha256": hashlib.sha256(body).hexdigest(),
                    }
                )
        except _NotStoredError as skipped:
            return done(skipped.status)
        marker = {
            "source": "kis",
            "env": client.env.value,
            "path": unit.quotation.path,
            "tr_id": unit.quotation.tr_id,
            "received_at": now.astimezone(UTC).isoformat(),
            "records": sum(page["records"] for page in pages),
            "pages": pages,
        }
        _write_text_atomic(
            part / SUCCESS, json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        part.replace(folder)
    finally:
        shutil.rmtree(part, ignore_errors=True)
    return done(
        UnitStatus.DOWNLOADED,
        records=int(marker["records"]),
        pages=len(pages),
        rows=_folder_rows(unit, folder),
    )


def record_unit(run: RunHandle, result: UnitResult) -> None:
    """[쓰임] 단위 하나를 실행 기록(산출물 한 줄)과 JSON 로그 한 줄로 남긴다."""
    run.add(
        Artifact(
            path=result.unit.relative_dir,
            layer="bronze",
            status=result.status.value,
            size_bytes=result.size_bytes,
            sha256=result.marker_sha256,
            row_count=result.records,
            elapsed_s=round(result.elapsed_s, 3),
        )
    )
    log_json(
        event="fetch",
        path=result.unit.relative_dir,
        status=result.status.value,
        records=result.records,
        pages=result.pages,
        elapsed_s=round(result.elapsed_s, 3),
    )


def run_units(
    client: KisClient,
    bronze_root: Path,
    units: Iterable[Unit],
    manifest_path: Path,
    run: RunHandle,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[UnitResult]:
    """[쓰임] 단위들을 차례로 받고, 하나마다 실행 기록에 남기고, 끝에 체크섬 목록을 고친다.
    [이유] 중간에 실패해도 그때까지 받은 것은 목록에 남긴다(``finally``)."""
    known = read_manifest(manifest_path)
    results: list[UnitResult] = []
    try:
        for unit in units:
            result = fetch_unit(client, bronze_root, unit, known=known, now=now())
            results.append(result)
            record_unit(run, result)
    finally:
        changed = write_manifest_entries(manifest_path, (row for r in results for row in r.rows))
        log_json(event="manifest", path=str(manifest_path), changed=changed)
    return results


def plan_units(kind: Kind, symbols: Sequence[str], start: date, end: date) -> list[Unit]:
    """[쓰임] 받을 단위 목록 — 휴장일 · 일봉은 달마다, 분봉은 평일마다(주말은 묻지 않는다).
    [이유] 분봉은 장이 안 열린 평일(공휴일)도 묻는다 — 원천이 그 전 거래일을 돌려주므로
           받기가 스스로 '장 안 열림' 을 가른다. 휴장일 달력과의 대조는 Silver 에서 한다."""
    months = months_between(date(start.year, start.month, 1), date(end.year, end.month, 1))
    if kind is Kind.HOLIDAY:
        return [Unit(Kind.HOLIDAY, month) for month in months]
    if kind is Kind.DAILY:
        return [
            Unit(Kind.DAILY, month, symbol, price)
            for symbol in symbols
            for price in Price
            for month in months
        ]
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [
        Unit(Kind.MINUTE, day, symbol) for symbol in symbols for day in days if day.weekday() < 5
    ]


def _parse_day(text: str) -> date:
    """[쓰임] ``YYYY-MM-DD`` → 날짜."""
    return date.fromisoformat(text)


def _build_parser() -> argparse.ArgumentParser:
    """[쓰임] 명령줄 인자 정의."""
    parser = argparse.ArgumentParser(
        prog="python -m mdp.bronze.kis", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--kind", required=True, choices=[k.value for k in Kind])
    parser.add_argument("--symbols", default="", help="쉼표로, 예: 005930,000660 (휴장일은 없음)")
    parser.add_argument("--start", required=True, type=_parse_day, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=_parse_day, help="YYYY-MM-DD (포함)")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="아직 받을 수 없는 단위가 있으면 종료 코드 2 (오케스트레이터가 다시 시도하게)",
    )
    parser.add_argument(
        "--root", type=Path, help="Bronze 위치 (기본: MDP_BRONZE_ROOT 또는 data/lake/bronze)"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("MDP_PG_DSN"),
        help="실행 기록 PostgreSQL 주소 (기본: MDP_PG_DSN, 없으면 기록 안 함)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행. 키가 없으면 종료 코드 2 와 안내 한 줄."""
    setup_cli_logging()
    args = _build_parser().parse_args(argv)
    kind = Kind(args.kind)
    symbols = sorted({s.strip() for s in args.symbols.split(",") if s.strip()})
    if kind is not Kind.HOLIDAY and not symbols:
        log_json(event="no_symbols", error="--symbols 가 필요하다")
        return 2
    try:
        credentials = Credentials.from_env()
    except MissingCredentialsError as exc:
        log_json(event="no_credentials", error=str(exc))
        return 2
    bronze_root: Path = args.root or default_bronze_root()
    units = plan_units(kind, symbols, args.start, args.end)
    params: dict[str, object] = {
        "kind": kind.value,
        "symbols": ",".join(symbols),
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "env": credentials.env.value,
        "bronze_root": str(bronze_root),
    }
    recorder: RunRecorder = PostgresRecorder(args.pg_dsn) if args.pg_dsn else NullRecorder()
    if not args.pg_dsn:
        log_json(event="runlog_off", reason="MDP_PG_DSN 없음 — 실행 기록 없이 받는다")
    try:
        with KisClient(credentials) as client, record_run(recorder, "bronze.kis", params) as run:
            results = run_units(client, bronze_root, units, args.manifest, run)
    finally:
        if isinstance(recorder, PostgresRecorder):
            recorder.close()
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status.value] = counts.get(result.status.value, 0) + 1
    log_json(event="summary", kind=kind.value, units=len(results), **counts)
    if args.require_all and counts.get(UnitStatus.NOT_READY.value):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
