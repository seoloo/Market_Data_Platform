"""포지셔닝 지표 백필 (H2 / 16-h2-proposal.md) — 일별 아카이브를 받아 ``perp_metrics`` 표에 넣는다.

언제 쓰나: 사람이 H2 연구 전에 ``scripts/backfill_metrics.py`` 로 직접 실행한다 (예약 작업 없음).
읽고 쓰는 것: data.binance.vision 의 metrics zip (읽기) → ``perp_metrics`` 표 (쓰기).

funding 과 달리 **일 단위 파일만** 제공된다. 2021-01 부터 오늘까지면 약 2,100개이므로
캔들·funding 보다 요청 수가 훨씬 많다. 재개를 반드시 쓴다.

가격 파생 지표가 전부 실패한 뒤 (15-h1-postmortem.md) 남은 유일한 새 정보원이라,
결측을 조용히 메우지 않는 것이 특히 중요하다. 없는 날은 없는 대로 기록한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from auto_trading.core.errors import AutoTradingError
from auto_trading.core.types import PerpMetrics
from auto_trading.marketdata.archive import BinanceVisionClient, MetricsArchiveFile, days_between
from auto_trading.storage.repositories.metrics import PerpMetricsRepository

__all__ = ["MetricsBackfillResult", "MetricsBackfiller", "plan_metrics_files"]

EARLIEST_AVAILABLE = date(2021, 1, 1)
"""아카이브 시작일 (2026-09-22 확인: 2020-01 은 404, 2021-01 은 존재).

이 때문에 H2 의 TRAIN 은 2021~2022 로 고정된다 — 선택이 아니라 데이터 제약이다.
"""


@dataclass(slots=True)
class MetricsBackfillResult:
    """지표 백필 결과. 실패는 모아서 돌려준다 (캔들 백필과 같은 규약)."""

    symbol: str
    files_planned: int = 0
    files_fetched: int = 0
    files_missing: list[str] = field(default_factory=list)
    rows_written: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """[쓰임] 오류가 하나도 없으면 True (파일 없음은 '누락' 으로 따로 센다)."""
        return not self.errors

    def summary(self) -> str:
        """[쓰임] 사람이 읽는 한 줄 요약 — 예 ``[OK] BTCUSDT metrics: 2/3 일, 576건, 누락 1일``."""
        status = "OK" if self.ok else f"오류 {len(self.errors)}건"
        missing = f", 누락 {len(self.files_missing)}일" if self.files_missing else ""
        return (
            f"[{status}] {self.symbol} metrics: "
            f"{self.files_fetched}/{self.files_planned} 일, "
            f"{self.rows_written:,}건{missing}"
        )


def plan_metrics_files(
    symbol: str,
    start: date,
    end: date,
    *,
    resume_from: date | None = None,
) -> tuple[MetricsArchiveFile, ...]:
    """[쓰임] 받아야 할 일별 지표 아카이브 목록 (아카이브 시작일 ``EARLIEST_AVAILABLE`` 이후만).
    [이유] 시작일 이전을 계획하면 매번 404 가 나고 "누락" 으로 기록되어 진짜 결측과 섞인다."""
    effective_start = max(start, EARLIEST_AVAILABLE)
    if resume_from is not None:
        effective_start = max(effective_start, resume_from)
    if effective_start > end:
        return ()
    return tuple(
        MetricsArchiveFile(symbol=symbol, period=day) for day in days_between(effective_start, end)
    )


class MetricsBackfiller:
    """지표 아카이브 → ``perp_metrics`` 적재 실행기."""

    __slots__ = ("_client", "_repo", "verify_checksum")

    def __init__(
        self,
        client: BinanceVisionClient,
        repository: PerpMetricsRepository,
        *,
        verify_checksum: bool = True,
    ) -> None:
        self._client = client
        self._repo = repository
        self.verify_checksum = verify_checksum

    def run(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        resume: bool = True,
        progress_every: int = 0,
    ) -> MetricsBackfillResult:
        """[쓰임] ``start`` ~ ``end`` 를 하루씩 받기 → 저장하고 결과를 돌려준다 (이어 받기 가능).
        [이유] 파일이 2 천 개가 넘어 오래 걸린다 — ``progress_every`` 개마다 진행 상황을 찍는다."""
        resume_from = self._resume_point(symbol) if resume else None
        files = plan_metrics_files(symbol, start, end, resume_from=resume_from)
        result = MetricsBackfillResult(symbol=symbol, files_planned=len(files))

        for index, archive_file in enumerate(files, start=1):
            try:
                entries = self._client.fetch_metrics(
                    archive_file, verify_checksum=self.verify_checksum
                )
            except (AutoTradingError, OSError) as exc:
                result.errors.append(f"{archive_file}: {exc}")
                continue
            if entries is None:
                result.files_missing.append(str(archive_file))
                continue
            result.files_fetched += 1
            result.rows_written += self._write(symbol, entries)
            if progress_every and index % progress_every == 0:
                print(f"  {index}/{len(files)} ({archive_file.period})", flush=True)  # noqa: T201
        return result

    def _write(self, symbol: str, entries: tuple[PerpMetrics, ...]) -> int:
        """[쓰임] 받은 지표를 저장하고 개수를 돌려준다."""
        return self._repo.upsert_many(symbol, entries)

    def _resume_point(self, symbol: str) -> date | None:
        """[쓰임] 이어 받을 시작점 — 저장된 마지막 스냅샷의 날짜 (없으면 None).
        [이유] 그 날은 일부만 받았을 수 있으므로 **그 날부터** 다시 받는다."""
        last_ms = self._repo.last_at_ms(symbol)
        if last_ms is None:
            return None
        return datetime.fromtimestamp(last_ms / 1000, tz=UTC).date()


def utc_yesterday() -> date:
    """[쓰임] UTC 기준 어제 날짜 — 백필 끝 날짜 기본값 (오늘 파일은 아직 없다)."""
    return datetime.now(UTC).date() - timedelta(days=1)
