"""백필 테스트 — 계획 수립, 재개, 멱등, 부분 실패 허용."""

from __future__ import annotations

from datetime import date

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.marketdata.archive import Granularity
from auto_trading.marketdata.backfill import (
    DEFAULT_START,
    Backfiller,
    plan_archive_files,
)
from auto_trading.storage.repositories.candles import CandleRepository
from tests.marketdata.conftest import NOW, SYMBOL, FakeArchiveServer, make_csv_rows

TF = Timeframe.M1
JAN_MS = 1_704_067_200_000  # 2024-01-01T00:00:00Z
FEB_MS = 1_706_745_600_000  # 2024-02-01T00:00:00Z
MAR_MS = 1_709_251_200_000  # 2024-03-01T00:00:00Z


def month_start_ms(year: int, month: int) -> int:
    from datetime import UTC, datetime

    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def day_start_ms(d: date) -> int:
    from datetime import UTC, datetime

    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


class TestPlanning:
    def test_complete_months_are_monthly(self):
        plan = plan_archive_files(SYMBOL, TF, date(2024, 1, 1), date(2024, 3, 1))
        monthly = [f for f in plan.files if f.granularity is Granularity.MONTHLY]
        assert [f.period for f in monthly] == [date(2024, 1, 1), date(2024, 2, 1)]

    def test_current_month_is_daily(self):
        plan = plan_archive_files(SYMBOL, TF, date(2024, 1, 1), date(2024, 3, 5))
        daily = [f for f in plan.files if f.granularity is Granularity.DAILY]
        assert [f.period for f in daily] == [date(2024, 3, i) for i in range(1, 6)]

    def test_monthly_archive_not_requested_for_current_month(self):
        """월별 아카이브는 달이 끝나야 생성된다."""
        plan = plan_archive_files(SYMBOL, TF, date(2024, 3, 1), date(2024, 3, 10))
        assert all(f.granularity is Granularity.DAILY for f in plan.files)

    def test_resume_skips_earlier_months(self):
        plan = plan_archive_files(
            SYMBOL, TF, date(2024, 1, 1), date(2024, 5, 1), resume_from=date(2024, 3, 1)
        )
        assert min(f.period for f in plan.files) >= date(2024, 3, 1)

    def test_resume_beyond_end_yields_nothing(self):
        plan = plan_archive_files(
            SYMBOL, TF, date(2024, 1, 1), date(2024, 2, 1), resume_from=date(2024, 6, 1)
        )
        assert len(plan) == 0

    def test_single_day_range(self):
        plan = plan_archive_files(SYMBOL, TF, date(2024, 3, 5), date(2024, 3, 5))
        assert len(plan) == 1
        assert plan.files[0].granularity is Granularity.DAILY

    def test_default_start_is_listing_date(self):
        assert date(2019, 9, 8) == DEFAULT_START


class TestBackfillRun:
    def test_writes_candles(self, server: FakeArchiveServer, repo: CandleRepository, monthly):
        jan = monthly(date(2024, 1, 1), TF)
        server.add(jan, make_csv_rows(JAN_MS, 10, TF))

        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
            )
        assert result.ok
        assert result.candles_written == 10
        assert repo.count(SYMBOL, TF) == 10

    def test_multiple_months(self, server: FakeArchiveServer, repo: CandleRepository, monthly):
        server.add(monthly(date(2024, 1, 1), TF), make_csv_rows(JAN_MS, 5, TF))
        server.add(monthly(date(2024, 2, 1), TF), make_csv_rows(FEB_MS, 7, TF))

        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 3, 1), ingested_ms=NOW
            )
        assert result.files_fetched == 2
        assert repo.count(SYMBOL, TF) == 12

    def test_missing_month_is_recorded_not_fatal(
        self, server: FakeArchiveServer, repo: CandleRepository, monthly
    ):
        """한 달이 없다고 전체가 중단되면 진행 상황을 파악하기 어렵다."""
        server.add(monthly(date(2024, 2, 1), TF), make_csv_rows(FEB_MS, 5, TF))

        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 3, 1), ingested_ms=NOW
            )
        assert result.ok  # 누락은 오류가 아니다
        # 1월(월별) + 3월 1일(일별) 이 없다. 2월만 받힌다.
        assert len(result.files_missing) == 2
        assert result.files_fetched == 1
        assert repo.count(SYMBOL, TF) == 5

    def test_corrupt_file_is_recorded_and_others_continue(
        self, server: FakeArchiveServer, repo: CandleRepository, monthly
    ):
        jan = monthly(date(2024, 1, 1), TF)
        server.add(jan, make_csv_rows(JAN_MS, 5, TF))
        server.corrupt(jan)
        server.add(monthly(date(2024, 2, 1), TF), make_csv_rows(FEB_MS, 5, TF))

        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 3, 1), ingested_ms=NOW
            )
        assert not result.ok
        assert len(result.errors) == 1
        assert "체크섬" in result.errors[0]
        assert repo.count(SYMBOL, TF) == 5  # 2월치는 들어갔다

    def test_is_idempotent(self, server: FakeArchiveServer, repo: CandleRepository, monthly):
        server.add(monthly(date(2024, 1, 1), TF), make_csv_rows(JAN_MS, 10, TF))
        with server.client() as client:
            backfiller = Backfiller(client, repo)
            for _ in range(3):
                backfiller.run(
                    SYMBOL,
                    TF,
                    start=date(2024, 1, 1),
                    end=date(2024, 2, 1),
                    ingested_ms=NOW,
                    resume=False,
                )
        assert repo.count(SYMBOL, TF) == 10

    def test_resume_skips_downloaded_months(
        self, server: FakeArchiveServer, repo: CandleRepository, monthly
    ):
        server.add(monthly(date(2024, 1, 1), TF), make_csv_rows(JAN_MS, 5, TF))
        server.add(monthly(date(2024, 2, 1), TF), make_csv_rows(FEB_MS, 5, TF))

        with server.client() as client:
            backfiller = Backfiller(client, repo)
            backfiller.run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
            )
            server.request_log.clear()
            second = backfiller.run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 3, 1), ingested_ms=NOW
            )

        # 재개 지점이 1월이므로 1월(재적재) + 2월 + 3/1(일별) = 3개
        assert second.files_planned == 3
        assert second.files_fetched == 2
        assert any("2024-02" in url for url in server.request_log)

    def test_resume_point_is_month_start(
        self, server: FakeArchiveServer, repo: CandleRepository, monthly
    ):
        server.add(monthly(date(2024, 1, 1), TF), make_csv_rows(JAN_MS, 10, TF))
        with server.client() as client:
            backfiller = Backfiller(client, repo)
            assert backfiller.resume_point(SYMBOL, TF) is None
            backfiller.run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
            )
            assert backfiller.resume_point(SYMBOL, TF) == date(2024, 1, 1)

    def test_empty_plan_returns_clean_result(
        self, server: FakeArchiveServer, repo: CandleRepository
    ):
        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 5, 1), end=date(2024, 1, 1), ingested_ms=NOW
            )
        assert result.ok
        assert result.files_planned == 0

    def test_progress_callback(self, server: FakeArchiveServer, repo: CandleRepository, monthly):
        server.add(monthly(date(2024, 1, 1), TF), make_csv_rows(JAN_MS, 4, TF))
        seen: list[tuple[str, int]] = []

        with server.client() as client:
            Backfiller(client, repo).run(
                SYMBOL,
                TF,
                start=date(2024, 1, 1),
                end=date(2024, 2, 1),
                ingested_ms=NOW,
                progress=lambda f, n: seen.append((str(f), n)),
            )
        assert seen == [("BTCUSDT-1m-2024-01.zip", 4)]

    def test_run_all_timeframes(self, server: FakeArchiveServer, repo: CandleRepository, monthly):
        for tf in (Timeframe.M1, Timeframe.H1):
            server.add(monthly(date(2024, 1, 1), tf), make_csv_rows(JAN_MS, 3, tf))

        with server.client() as client:
            results = Backfiller(client, repo).run_all(
                SYMBOL,
                [Timeframe.M1, Timeframe.H1],
                start=date(2024, 1, 1),
                end=date(2024, 2, 1),
                ingested_ms=NOW,
            )
        assert len(results) == 2
        assert all(r.ok for r in results)
        assert repo.count(SYMBOL, Timeframe.M1) == 3
        assert repo.count(SYMBOL, Timeframe.H1) == 3

    def test_daily_files_for_partial_month(
        self, server: FakeArchiveServer, repo: CandleRepository, daily
    ):
        for day in (1, 2, 3):
            d = date(2024, 3, day)
            server.add(daily(d, TF), make_csv_rows(day_start_ms(d), 2, TF))

        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 3, 1), end=date(2024, 3, 3), ingested_ms=NOW
            )
        assert result.files_fetched == 3
        assert repo.count(SYMBOL, TF) == 6


class TestBackfillResult:
    def test_summary_is_readable(self, server: FakeArchiveServer, repo: CandleRepository, monthly):
        server.add(monthly(date(2024, 1, 1), TF), make_csv_rows(JAN_MS, 1440, TF))
        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
            )
        text = result.summary()
        assert "OK" in text
        assert "1,440봉" in text

    def test_summary_reports_errors(
        self, server: FakeArchiveServer, repo: CandleRepository, monthly
    ):
        jan = monthly(date(2024, 1, 1), TF)
        server.add(jan, make_csv_rows(JAN_MS, 1, TF))
        server.corrupt(jan)
        with server.client() as client:
            result = Backfiller(client, repo).run(
                SYMBOL, TF, start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
            )
        assert "오류 1건" in result.summary()


@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_all_timeframes_round_trip(
    server: FakeArchiveServer, repo: CandleRepository, monthly, timeframe: Timeframe
):
    """6개 타임프레임 전부 파싱·적재가 동작해야 한다."""
    server.add(monthly(date(2024, 1, 1), timeframe), make_csv_rows(JAN_MS, 3, timeframe))
    with server.client() as client:
        result = Backfiller(client, repo).run(
            SYMBOL, timeframe, start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
        )
    assert result.ok
    assert repo.count(SYMBOL, timeframe) == 3
