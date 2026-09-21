"""funding rate 파싱·적재·조회 테스트.

funding 이 없으면 리스크 게이트가 모든 진입을 거부하므로, 여기서 검증하는 인과성
(``funding_ms <= as_of_ms``)이 깨지면 미래의 정산 내역으로 거래한 셈이 된다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import FundingRate
from auto_trading.marketdata.archive import FundingArchiveFile, parse_funding_csv
from auto_trading.marketdata.funding import FundingBackfiller, plan_funding_files
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.funding import FundingRateRepository
from tests.marketdata.conftest import FakeArchiveServer, make_zip

D = Decimal
SYMBOL = "BTCUSDT"
HOUR = 3_600_000
BASE_MS = 1_704_067_200_000  # 2024-01-01 00:00 UTC


def csv_rows(count: int, *, start_ms: int = BASE_MS, header: bool = True, hours: int = 8) -> str:
    lines = ["calc_time,funding_interval_hours,last_funding_rate"] if header else []
    for i in range(count):
        lines.append(f"{start_ms + i * hours * HOUR},{hours},0.000{i:03d}")
    return "\n".join(lines) + "\n"


@pytest.fixture
def funding_repo(tmp_path):
    conn = connect(tmp_path / "f.db")
    apply_migrations(conn, now_ms=BASE_MS)
    yield FundingRateRepository(conn)
    conn.close()


# ------------------------------------------------------------------ 파싱


class TestParse:
    def test_parses_header_and_rows(self) -> None:
        rates = parse_funding_csv(csv_rows(3).encode())
        assert len(rates) == 3
        assert rates[0].funding_ms == BASE_MS
        assert rates[0].interval_hours == 8
        assert rates[1].rate == D("0.000001")

    def test_headerless_file_is_accepted(self) -> None:
        assert len(parse_funding_csv(csv_rows(2, header=False).encode())) == 2

    def test_rows_are_sorted_by_settlement_time(self) -> None:
        body = (
            "calc_time,funding_interval_hours,last_funding_rate\n"
            f"{BASE_MS + 8 * HOUR},8,0.0002\n"
            f"{BASE_MS},8,0.0001\n"
        )
        rates = parse_funding_csv(body.encode())
        assert [r.funding_ms for r in rates] == [BASE_MS, BASE_MS + 8 * HOUR]

    def test_duplicate_settlement_time_is_rejected(self) -> None:
        body = f"{BASE_MS},8,0.0001\n{BASE_MS},8,0.0002\n"
        with pytest.raises(DataIntegrityError, match="중복"):
            parse_funding_csv(body.encode())

    def test_short_row_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="컬럼 수"):
            parse_funding_csv(f"{BASE_MS},8\n".encode())

    def test_microsecond_timestamps_are_normalized(self) -> None:
        rates = parse_funding_csv(f"{BASE_MS * 1000},8,0.0001\n".encode())
        assert rates[0].funding_ms == BASE_MS

    def test_four_hour_interval_is_preserved(self) -> None:
        rates = parse_funding_csv(csv_rows(2, hours=4).encode())
        assert rates[0].interval_hours == 4
        assert rates[0].next_funding_ms == BASE_MS + 4 * HOUR

    def test_empty_file_yields_nothing(self) -> None:
        assert parse_funding_csv(b"") == ()


class TestFundingRateType:
    def test_next_funding_uses_the_stored_interval(self) -> None:
        assert FundingRate(BASE_MS, D("0.0001"), 4).next_funding_ms == BASE_MS + 4 * HOUR

    @pytest.mark.parametrize("hours", [0, -8, 25])
    def test_absurd_interval_is_rejected(self, hours: int) -> None:
        with pytest.raises(DataIntegrityError, match="주기"):
            FundingRate(BASE_MS, D("0.0001"), hours)


# ------------------------------------------------------------------ 계획


class TestPlanning:
    def test_month_in_progress_is_excluded(self) -> None:
        files = plan_funding_files(SYMBOL, date(2024, 1, 1), date(2024, 3, 15))
        assert [f.period.month for f in files] == [1, 2]

    def test_month_ending_exactly_at_end_is_included(self) -> None:
        files = plan_funding_files(SYMBOL, date(2024, 1, 1), date(2024, 3, 31))
        assert [f.period.month for f in files] == [1, 2, 3]

    def test_resume_skips_already_stored_months(self) -> None:
        files = plan_funding_files(
            SYMBOL, date(2024, 1, 1), date(2024, 5, 31), resume_from=date(2024, 4, 1)
        )
        assert [f.period.month for f in files] == [4, 5]

    def test_reversed_range_plans_nothing(self) -> None:
        assert plan_funding_files(SYMBOL, date(2024, 5, 1), date(2024, 1, 1)) == ()

    def test_url_points_at_the_funding_path(self) -> None:
        url = FundingArchiveFile(SYMBOL, date(2024, 1, 1)).url()
        assert url.endswith(
            "/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip"
        )


# ------------------------------------------------------------------ 저장


class TestRepository:
    def test_upsert_is_idempotent(self, funding_repo: FundingRateRepository) -> None:
        rates = parse_funding_csv(csv_rows(3).encode())
        funding_repo.upsert_many(SYMBOL, rates)
        funding_repo.upsert_many(SYMBOL, rates)
        assert funding_repo.count(SYMBOL) == 3

    def test_latest_as_of_never_sees_the_future(self, funding_repo: FundingRateRepository) -> None:
        funding_repo.upsert_many(SYMBOL, parse_funding_csv(csv_rows(3).encode()))
        second = BASE_MS + 8 * HOUR
        # 두 번째 정산 **직전** — 첫 번째만 알 수 있다.
        just_before = funding_repo.latest_as_of(SYMBOL, second - 1)
        assert just_before is not None
        assert just_before.funding_ms == BASE_MS
        # 정산 시각 정각에는 그 값이 확정된다.
        at_settlement = funding_repo.latest_as_of(SYMBOL, second)
        assert at_settlement is not None
        assert at_settlement.funding_ms == second

    def test_latest_as_of_before_any_history_is_none(
        self, funding_repo: FundingRateRepository
    ) -> None:
        funding_repo.upsert_many(SYMBOL, parse_funding_csv(csv_rows(3).encode()))
        assert funding_repo.latest_as_of(SYMBOL, BASE_MS - 1) is None

    def test_gap_detection_uses_each_rows_own_interval(
        self, funding_repo: FundingRateRepository
    ) -> None:
        """8h → 4h 전환을 결측으로 오인하면 안 된다."""
        funding_repo.upsert_many(
            SYMBOL,
            [
                FundingRate(BASE_MS, D("0.0001"), 8),
                FundingRate(BASE_MS + 8 * HOUR, D("0.0001"), 4),
                FundingRate(BASE_MS + 12 * HOUR, D("0.0001"), 4),
            ],
        )
        assert funding_repo.find_gaps(SYMBOL, from_ms=0, to_ms=BASE_MS + 99 * HOUR) == ()

    def test_millisecond_jitter_is_not_a_gap(self, funding_repo: FundingRateRepository) -> None:
        """실데이터에서 7,305건 중 2,256건이 8시간을 1~4ms 초과했다 (2026-09-21 실측).

        정확히 비교하면 결측 0건인 데이터에서 2,256건을 오탐한다.
        """
        funding_repo.upsert_many(
            SYMBOL,
            [
                FundingRate(BASE_MS, D("0.0001"), 8),
                FundingRate(BASE_MS + 8 * HOUR + 1, D("0.0001"), 8),
                FundingRate(BASE_MS + 16 * HOUR - 3, D("0.0001"), 8),
            ],
        )
        assert funding_repo.find_gaps(SYMBOL, from_ms=0, to_ms=BASE_MS + 99 * HOUR) == ()

    def test_delay_beyond_tolerance_is_still_a_gap(
        self, funding_repo: FundingRateRepository
    ) -> None:
        funding_repo.upsert_many(
            SYMBOL,
            [
                FundingRate(BASE_MS, D("0.0001"), 8),
                FundingRate(BASE_MS + 8 * HOUR + 61_000, D("0.0001"), 8),
            ],
        )
        assert len(funding_repo.find_gaps(SYMBOL, from_ms=0, to_ms=BASE_MS + 99 * HOUR)) == 1

    def test_real_gap_is_reported(self, funding_repo: FundingRateRepository) -> None:
        funding_repo.upsert_many(
            SYMBOL,
            [
                FundingRate(BASE_MS, D("0.0001"), 8),
                FundingRate(BASE_MS + 32 * HOUR, D("0.0001"), 8),
            ],
        )
        gaps = funding_repo.find_gaps(SYMBOL, from_ms=0, to_ms=BASE_MS + 99 * HOUR)
        assert gaps == ((BASE_MS, BASE_MS + 32 * HOUR),)

    def test_accrued_counts_settlements_after_entry(
        self, funding_repo: FundingRateRepository
    ) -> None:
        funding_repo.upsert_many(
            SYMBOL,
            [
                FundingRate(BASE_MS, D("0.001"), 8),
                FundingRate(BASE_MS + 8 * HOUR, D("0.001"), 8),
            ],
        )
        # 첫 정산 **직후** 진입 → 두 번째 정산만 지불한다.
        paid = funding_repo.accrued_between(
            SYMBOL,
            from_ms=BASE_MS,
            to_ms=BASE_MS + 8 * HOUR,
            notional=D("1000"),
            is_long=True,
        )
        assert paid == D("1")

    def test_short_receives_positive_funding(self, funding_repo: FundingRateRepository) -> None:
        funding_repo.upsert_many(SYMBOL, [FundingRate(BASE_MS + HOUR, D("0.001"), 8)])
        paid = funding_repo.accrued_between(
            SYMBOL, from_ms=BASE_MS, to_ms=BASE_MS + 8 * HOUR, notional=D("1000"), is_long=False
        )
        assert paid == D("-1")

    def test_exiting_just_before_settlement_pays_nothing(
        self, funding_repo: FundingRateRepository
    ) -> None:
        funding_repo.upsert_many(SYMBOL, [FundingRate(BASE_MS + 8 * HOUR, D("0.001"), 8)])
        paid = funding_repo.accrued_between(
            SYMBOL,
            from_ms=BASE_MS,
            to_ms=BASE_MS + 8 * HOUR - 1,
            notional=D("1000"),
            is_long=True,
        )
        assert paid == D(0)


# ------------------------------------------------------------------ 백필


class TestBackfiller:
    def _server(self, months: list[date], *, rows: int = 3) -> FakeArchiveServer:
        server = FakeArchiveServer()
        for month in months:
            archive = FundingArchiveFile(SYMBOL, month)
            payload = make_zip(archive.stem, csv_rows(rows, start_ms=BASE_MS + month.month * HOUR))
            server.add_raw(archive, payload)
        return server

    def test_downloads_and_stores(self, funding_repo: FundingRateRepository) -> None:
        server = self._server([date(2024, 1, 1), date(2024, 2, 1)])
        result = FundingBackfiller(server.client(), funding_repo).run(
            SYMBOL, start=date(2024, 1, 1), end=date(2024, 2, 29)
        )
        assert result.ok
        assert result.files_fetched == 2
        assert funding_repo.count(SYMBOL) == 6

    def test_missing_month_is_recorded_not_fatal(self, funding_repo: FundingRateRepository) -> None:
        server = self._server([date(2024, 1, 1)])  # 2월 없음
        result = FundingBackfiller(server.client(), funding_repo).run(
            SYMBOL, start=date(2024, 1, 1), end=date(2024, 2, 29)
        )
        assert result.ok  # 누락은 오류가 아니다
        assert len(result.files_missing) == 1
        assert funding_repo.count(SYMBOL) == 3

    def test_checksum_mismatch_is_an_error_not_silent_data(
        self, funding_repo: FundingRateRepository
    ) -> None:
        server = self._server([date(2024, 1, 1)])
        server.corrupt(FundingArchiveFile(SYMBOL, date(2024, 1, 1)))
        result = FundingBackfiller(server.client(), funding_repo).run(
            SYMBOL, start=date(2024, 1, 1), end=date(2024, 1, 31)
        )
        assert not result.ok
        assert funding_repo.count(SYMBOL) == 0

    def test_resume_refetches_only_the_last_stored_month(
        self, funding_repo: FundingRateRepository
    ) -> None:
        funding_repo.upsert_many(SYMBOL, [FundingRate(BASE_MS, D("0.0001"), 8)])  # 2024-01
        server = self._server([date(2024, 1, 1), date(2024, 2, 1)])
        result = FundingBackfiller(server.client(), funding_repo).run(
            SYMBOL, start=date(2023, 1, 1), end=date(2024, 2, 29), resume=True
        )
        # 2023 전체를 다시 받지 않는다.
        assert result.files_planned == 2
