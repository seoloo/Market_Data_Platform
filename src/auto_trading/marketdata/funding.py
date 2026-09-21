"""funding rate 백필.

**왜 필요한가**: 리스크 게이트는 진입 전에 `funding_rate` 와 `next_funding_ms` 를
요구한다 (05-risk-engine.md G5.4). 이 데이터가 없으면 게이트가 fail-closed 되어
**모든 진입이 DATA_QUALITY 로 거부된다** — 즉 funding 없이는 백테스트 자체가 불가능하다.

무기한 선물의 funding 은 손익에 직접 들어가는 실제 현금 흐름이기도 하다. 2~24시간
보유면 정산을 0~3회 지나므로, 무시하면 비용을 체계적으로 과소평가한다.

캔들과 달리 funding 아카이브는 **월 단위만** 제공되므로 진행 중인 달은 REST 로
보충해야 한다. MVP 는 아카이브만 쓰고, 부족분은 fail-closed 로 남긴다 (Phase 5 에서
거래소 어댑터가 실시간 값을 채운다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from auto_trading.core.errors import AutoTradingError
from auto_trading.core.types import FundingRate
from auto_trading.marketdata.archive import (
    BinanceVisionClient,
    FundingArchiveFile,
    months_between,
)
from auto_trading.storage.repositories.funding import FundingRateRepository

__all__ = ["FundingBackfillResult", "FundingBackfiller", "plan_funding_files"]


@dataclass(slots=True)
class FundingBackfillResult:
    """funding 백필 결과. 실패는 모아서 돌려준다 (캔들 백필과 같은 규약)."""

    symbol: str
    files_planned: int = 0
    files_fetched: int = 0
    files_missing: list[str] = field(default_factory=list)
    rates_written: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        status = "OK" if self.ok else f"오류 {len(self.errors)}건"
        missing = f", 누락 {len(self.files_missing)}개" if self.files_missing else ""
        return (
            f"[{status}] {self.symbol} funding: "
            f"{self.files_fetched}/{self.files_planned} 파일, "
            f"{self.rates_written:,}건{missing}"
        )


def plan_funding_files(
    symbol: str,
    start: date,
    end: date,
    *,
    resume_from: date | None = None,
) -> tuple[FundingArchiveFile, ...]:
    """받아야 할 월별 아카이브 목록.

    **끝나지 않은 달은 제외한다.** 월별 아카이브는 그 달이 끝나야 생성되므로,
    포함하면 매번 404 가 나고 "누락" 으로 기록되어 진짜 결측과 구별되지 않는다.

    ``end`` 가 그 달의 **마지막 날**이면 그 달은 완료된 것으로 본다. 과거 구간을
    ``--end 2024-12-31`` 처럼 명시했을 때 12월이 조용히 빠지는 것을 막는다.
    """
    effective_start = max(start, resume_from) if resume_from is not None else start
    if effective_start > end:
        return ()
    cutoff = end.replace(day=1) if end < _last_day_of_month(end) else _next_month(end)
    return tuple(
        FundingArchiveFile(symbol=symbol, period=month)
        for month in months_between(effective_start, end)
        if month < cutoff
    )


def _next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _last_day_of_month(day: date) -> date:
    return _next_month(day) - timedelta(days=1)


class FundingBackfiller:
    """아카이브 → ``funding_rates`` 적재."""

    __slots__ = ("_client", "_repo", "verify_checksum")

    def __init__(
        self,
        client: BinanceVisionClient,
        repository: FundingRateRepository,
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
    ) -> FundingBackfillResult:
        """``start`` 부터 ``end`` 직전 달까지 적재한다.

        ``resume`` 이면 이미 저장된 마지막 정산 시각이 속한 달부터 다시 받는다.
        그 달은 일부만 저장됐을 수 있으므로 통째로 다시 받는다 — upsert 라 안전하다.
        """
        resume_from = self._resume_point(symbol) if resume else None
        files = plan_funding_files(symbol, start, end, resume_from=resume_from)
        result = FundingBackfillResult(symbol=symbol, files_planned=len(files))

        for archive in files:
            try:
                rates = self._client.fetch_funding(archive, verify_checksum=self.verify_checksum)
            except (AutoTradingError, OSError) as exc:
                result.errors.append(f"{archive}: {exc}")
                continue
            if rates is None:
                result.files_missing.append(str(archive))
                continue
            result.files_fetched += 1
            result.rates_written += self._write(symbol, rates)
        return result

    def _write(self, symbol: str, rates: tuple[FundingRate, ...]) -> int:
        return self._repo.upsert_many(symbol, rates)

    def _resume_point(self, symbol: str) -> date | None:
        last_ms = self._repo.last_funding_ms(symbol)
        if last_ms is None:
            return None
        return datetime.fromtimestamp(last_ms / 1000, tz=UTC).date().replace(day=1)
