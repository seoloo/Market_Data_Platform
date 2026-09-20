"""과거 캔들 백필.

공개 아카이브에서 월/일 단위로 받아 DB 에 적재한다 (08-external-apis.md §2).

설계 요점:

* **재개 가능** — 이미 있는 구간은 건너뛴다. 수년치를 매번 다시 받지 않는다
* **멱등** — 같은 구간을 두 번 돌려도 결과가 같다 (repository 의 upsert)
* **부분 실패 허용** — 한 달치가 없거나 실패해도 나머지를 계속 진행하고 결과에 기록한다
  중간에 멈추면 어디까지 됐는지 알기 어려워진다
* **당월은 일 단위** — 월별 아카이브는 달이 끝나야 생성된다
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import AutoTradingError, DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.marketdata.archive import (
    ArchiveFile,
    BinanceVisionClient,
    Granularity,
    days_between,
    months_between,
)
from auto_trading.storage.repositories.candles import CandleRepository

__all__ = ["BackfillPlan", "BackfillResult", "Backfiller", "plan_archive_files"]

logger = logging.getLogger(__name__)

#: BTCUSDT 무기한 선물 상장일. 이보다 이전 구간은 존재하지 않는다.
DEFAULT_START = date(2019, 9, 8)


def _to_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date()


def _to_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def _first_of_next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    symbol: str
    timeframe: Timeframe
    files: tuple[ArchiveFile, ...]
    resume_from: date | None

    def __len__(self) -> int:
        return len(self.files)


@dataclass(slots=True)
class BackfillResult:
    """한 심볼·타임프레임 백필 결과.

    실패를 예외로 던지지 않고 모아서 돌려준다. 3년치를 받다가 한 달이 없다고
    전체가 중단되면 진행 상황을 파악하기 어렵다.
    """

    symbol: str
    timeframe: Timeframe
    files_planned: int = 0
    files_fetched: int = 0
    files_missing: list[str] = field(default_factory=list)
    candles_written: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        status = "OK" if self.ok else f"오류 {len(self.errors)}건"
        return (
            f"[{status}] {self.symbol} {self.timeframe.value}: "
            f"{self.files_fetched}/{self.files_planned} 파일, "
            f"{self.candles_written:,}봉"
            + (f", 누락 {len(self.files_missing)}개" if self.files_missing else "")
        )


def plan_archive_files(
    symbol: str,
    timeframe: Timeframe,
    start: date,
    end: date,
    *,
    resume_from: date | None = None,
) -> BackfillPlan:
    """받아야 할 아카이브 파일 목록을 만든다.

    완료된 달은 월별 파일로, ``end`` 가 속한 달은 일별 파일로 계획한다.
    월별 아카이브는 그 달이 끝나야 생성되기 때문이다.
    """
    effective_start = max(start, resume_from) if resume_from is not None else start
    if effective_start > end:
        return BackfillPlan(symbol, timeframe, (), resume_from)

    files: list[ArchiveFile] = []
    current_month_start = end.replace(day=1)

    # 완료된 달 → 월별
    last_complete_month = current_month_start - timedelta(days=1)
    for month in months_between(effective_start, last_complete_month):
        if month < effective_start.replace(day=1):
            continue
        files.append(
            ArchiveFile(
                symbol=symbol,
                timeframe=timeframe,
                period=month,
                granularity=Granularity.MONTHLY,
            )
        )

    # 진행 중인 달 → 일별
    daily_start = max(effective_start, current_month_start)
    for day in days_between(daily_start, end):
        files.append(
            ArchiveFile(
                symbol=symbol,
                timeframe=timeframe,
                period=day,
                granularity=Granularity.DAILY,
            )
        )

    return BackfillPlan(symbol, timeframe, tuple(files), resume_from)


class Backfiller:
    """아카이브 → DB 적재 오케스트레이터."""

    __slots__ = ("_client", "_repo", "verify_checksum")

    def __init__(
        self,
        client: BinanceVisionClient,
        repository: CandleRepository,
        *,
        verify_checksum: bool = True,
    ) -> None:
        self._client = client
        self._repo = repository
        self.verify_checksum = verify_checksum

    def resume_point(self, symbol: str, timeframe: Timeframe) -> date | None:
        """이미 받은 마지막 봉이 속한 **달의 1일**.

        마지막 봉의 다음 날부터 받지 않고 그 달을 통째로 다시 받는 이유:
        월별 아카이브는 달 단위로만 제공되고, upsert 이므로 재적재가 안전하다.
        """
        last = self._repo.last_open_ms(symbol, timeframe)
        return None if last is None else _to_date(last).replace(day=1)

    def run(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        start: date = DEFAULT_START,
        end: date,
        ingested_ms: int,
        resume: bool = True,
        progress: Callable[[ArchiveFile, int], None] | None = None,
    ) -> BackfillResult:
        """계획을 세우고 순서대로 적재한다."""
        result = BackfillResult(symbol=symbol, timeframe=timeframe)
        resume_from = self.resume_point(symbol, timeframe) if resume else None
        plan = plan_archive_files(symbol, timeframe, start, end, resume_from=resume_from)
        result.files_planned = len(plan)

        if not plan.files:
            logger.info("백필할 구간 없음: %s %s", symbol, timeframe.value)
            return result

        for archive_file in plan.files:
            try:
                candles = self._client.fetch_candles(
                    archive_file, verify_checksum=self.verify_checksum
                )
            except (DataIntegrityError, AutoTradingError) as exc:
                # 한 파일이 실패해도 나머지는 계속한다. 실패는 기록으로 남긴다.
                result.errors.append(f"{archive_file}: {exc}")
                logger.warning("백필 실패 %s: %s", archive_file, exc)
                continue

            if candles is None:
                result.files_missing.append(str(archive_file))
                continue

            written = self._write(symbol, timeframe, candles, ingested_ms=ingested_ms)
            result.files_fetched += 1
            result.candles_written += written
            if progress is not None:
                progress(archive_file, written)

        return result

    def run_all(
        self,
        symbol: str,
        timeframes: Sequence[Timeframe],
        *,
        start: date = DEFAULT_START,
        end: date,
        ingested_ms: int,
        resume: bool = True,
    ) -> list[BackfillResult]:
        """여러 타임프레임을 순서대로 백필한다."""
        return [
            self.run(
                symbol,
                timeframe,
                start=start,
                end=end,
                ingested_ms=ingested_ms,
                resume=resume,
            )
            for timeframe in timeframes
        ]

    def _write(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: tuple[Candle, ...],
        *,
        ingested_ms: int,
    ) -> int:
        if not candles:
            return 0
        return self._repo.upsert_many(
            symbol, timeframe, candles, source="bulk", ingested_ms=ingested_ms
        )
