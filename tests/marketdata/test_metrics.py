"""포지셔닝 지표 파싱·적재·조회 테스트 (H2 입력).

특히 **중복 행 처리**를 고정한다. 2021년 아카이브는 288행이 아니라 577행으로 전 행이
중복되어 있어, 그대로 넣으면 표본 수가 두 배가 되고 모든 통계가 틀어진다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import PerpMetrics
from auto_trading.marketdata.archive import MetricsArchiveFile, parse_metrics_csv
from auto_trading.marketdata.metrics import (
    EARLIEST_AVAILABLE,
    MetricsBackfiller,
    plan_metrics_files,
)
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.metrics import PerpMetricsRepository
from tests.marketdata.conftest import FakeArchiveServer, make_zip

D = Decimal
SYMBOL = "BTCUSDT"
STEP = 300_000
BASE_MS = 1_609_459_200_000  # 2021-01-01 00:00 UTC
HEADER = (
    "create_time,symbol,sum_open_interest,sum_open_interest_value,"
    "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
    "count_long_short_ratio,sum_taker_long_short_vol_ratio"
)


def csv_rows(count: int, *, day: str = "2021-01-01", duplicate: bool = False) -> str:
    lines = [HEADER]
    for i in range(count):
        stamp = f"{day} {i // 12:02d}:{(i % 12) * 5:02d}:00"
        row = f"{stamp},BTCUSDT,{27000 + i}.5,{1.0e9 + i},1.9{i % 10},1.15,1.93,0.5{i % 10}"
        lines.append(row)
        if duplicate:
            lines.append(row)
    return "\n".join(lines) + "\n"


@pytest.fixture
def metrics_repo(tmp_path):
    conn = connect(tmp_path / "m.db")
    apply_migrations(conn, now_ms=BASE_MS)
    yield PerpMetricsRepository(conn)
    conn.close()


def sample(at_ms: int, *, oi: str = "27000", taker: str = "1.0") -> PerpMetrics:
    return PerpMetrics(
        at_ms=at_ms,
        open_interest=D(oi),
        open_interest_value=D("1000000000"),
        toptrader_account_ratio=D("1.9"),
        toptrader_position_ratio=D("1.15"),
        account_long_short_ratio=D("1.93"),
        taker_buy_sell_ratio=D(taker),
    )


# ------------------------------------------------------------------ 파싱


class TestParse:
    def test_parses_rows_and_converts_time(self) -> None:
        entries = parse_metrics_csv(csv_rows(3).encode())
        assert len(entries) == 3
        assert entries[0].at_ms == BASE_MS
        assert entries[1].at_ms == BASE_MS + STEP
        assert entries[0].open_interest == D("27000.5")

    def test_duplicated_rows_collapse_to_one(self) -> None:
        """2021년 실제 파일이 이 형태다. 그대로 넣으면 표본이 두 배가 된다."""
        entries = parse_metrics_csv(csv_rows(5, duplicate=True).encode())
        assert len(entries) == 5
        assert len({e.at_ms for e in entries}) == 5

    def test_conflicting_duplicate_is_rejected(self) -> None:
        """같은 시각에 다른 값이면 합칠 수 없다 — 조용히 하나를 고르면 안 된다."""
        body = (
            f"{HEADER}\n"
            "2021-01-01 00:00:00,BTCUSDT,27000,1e9,1.9,1.15,1.93,0.5\n"
            "2021-01-01 00:00:00,BTCUSDT,99999,1e9,1.9,1.15,1.93,0.5\n"
        )
        with pytest.raises(DataIntegrityError, match="서로 다른 지표"):
            parse_metrics_csv(body.encode())

    def test_rows_come_back_in_time_order(self) -> None:
        body = (
            f"{HEADER}\n"
            "2021-01-01 00:05:00,BTCUSDT,27001,1e9,1.9,1.15,1.93,0.5\n"
            "2021-01-01 00:00:00,BTCUSDT,27000,1e9,1.9,1.15,1.93,0.5\n"
        )
        entries = parse_metrics_csv(body.encode())
        assert [e.at_ms for e in entries] == [BASE_MS, BASE_MS + STEP]

    def test_short_row_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="컬럼 수"):
            parse_metrics_csv(f"{HEADER}\n2021-01-01 00:00:00,BTCUSDT,27000\n".encode())

    def test_unparsable_time_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="create_time"):
            parse_metrics_csv(
                f"{HEADER}\nnot-a-time,BTCUSDT,27000,1e9,1.9,1.15,1.93,0.5\n".encode()
            )

    def test_empty_file_yields_nothing(self) -> None:
        assert parse_metrics_csv(b"") == ()


class TestPerpMetricsType:
    @pytest.mark.parametrize("field_name", ["toptrader_account_ratio", "taker_buy_sell_ratio"])
    def test_non_positive_ratio_is_rejected(self, field_name: str) -> None:
        values = {
            "at_ms": BASE_MS,
            "open_interest": D("1"),
            "open_interest_value": D("1"),
            "toptrader_account_ratio": D("1"),
            "toptrader_position_ratio": D("1"),
            "account_long_short_ratio": D("1"),
            "taker_buy_sell_ratio": D("1"),
        }
        values[field_name] = D("0")
        with pytest.raises(DataIntegrityError, match=field_name):
            PerpMetrics(**values)  # type: ignore[arg-type]

    def test_negative_open_interest_is_rejected(self) -> None:
        with pytest.raises(DataIntegrityError, match="미결제약정"):
            sample(BASE_MS, oi="-1")


# ------------------------------------------------------------------ 계획


class TestPlanning:
    def test_days_are_planned_inclusively(self) -> None:
        files = plan_metrics_files(SYMBOL, date(2021, 3, 1), date(2021, 3, 3))
        assert [f.period.day for f in files] == [1, 2, 3]

    def test_dates_before_the_archive_start_are_skipped(self) -> None:
        """2020 은 아카이브 자체가 없다. 계획하면 404 가 결측으로 기록된다."""
        files = plan_metrics_files(SYMBOL, date(2020, 1, 1), date(2021, 1, 2))
        assert files[0].period == EARLIEST_AVAILABLE

    def test_resume_skips_already_stored_days(self) -> None:
        files = plan_metrics_files(
            SYMBOL, date(2021, 1, 1), date(2021, 1, 5), resume_from=date(2021, 1, 4)
        )
        assert [f.period.day for f in files] == [4, 5]

    def test_reversed_range_plans_nothing(self) -> None:
        assert plan_metrics_files(SYMBOL, date(2021, 5, 1), date(2021, 1, 1)) == ()

    def test_url_points_at_the_daily_metrics_path(self) -> None:
        url = MetricsArchiveFile(SYMBOL, date(2021, 1, 15)).url()
        assert url.endswith("/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2021-01-15.zip")


# ------------------------------------------------------------------ 저장


class TestRepository:
    def test_upsert_is_idempotent(self, metrics_repo: PerpMetricsRepository) -> None:
        entries = parse_metrics_csv(csv_rows(4).encode())
        metrics_repo.upsert_many(SYMBOL, entries)
        metrics_repo.upsert_many(SYMBOL, entries)
        assert metrics_repo.count(SYMBOL) == 4

    def test_values_survive_the_round_trip(self, metrics_repo: PerpMetricsRepository) -> None:
        metrics_repo.upsert_many(SYMBOL, [sample(BASE_MS, oi="27123.456", taker="1.0570")])
        stored = metrics_repo.latest_as_of(SYMBOL, BASE_MS)
        assert stored is not None
        assert stored.open_interest == D("27123.456")
        assert stored.taker_buy_sell_ratio == D("1.0570")

    @pytest.mark.leakage
    def test_latest_as_of_never_sees_the_future(self, metrics_repo: PerpMetricsRepository) -> None:
        metrics_repo.upsert_many(SYMBOL, parse_metrics_csv(csv_rows(3).encode()))
        second = BASE_MS + STEP
        before = metrics_repo.latest_as_of(SYMBOL, second - 1)
        assert before is not None
        assert before.at_ms == BASE_MS
        at = metrics_repo.latest_as_of(SYMBOL, second)
        assert at is not None
        assert at.at_ms == second

    def test_latest_before_any_history_is_none(self, metrics_repo: PerpMetricsRepository) -> None:
        metrics_repo.upsert_many(SYMBOL, parse_metrics_csv(csv_rows(3).encode()))
        assert metrics_repo.latest_as_of(SYMBOL, BASE_MS - 1) is None

    def test_regular_five_minute_spacing_has_no_gaps(
        self, metrics_repo: PerpMetricsRepository
    ) -> None:
        metrics_repo.upsert_many(SYMBOL, parse_metrics_csv(csv_rows(12).encode()))
        assert metrics_repo.find_gaps(SYMBOL, from_ms=0, to_ms=BASE_MS + 99 * STEP) == ()

    def test_missing_snapshot_is_reported_as_a_gap(
        self, metrics_repo: PerpMetricsRepository
    ) -> None:
        metrics_repo.upsert_many(SYMBOL, [sample(BASE_MS), sample(BASE_MS + 6 * STEP)])
        gaps = metrics_repo.find_gaps(SYMBOL, from_ms=0, to_ms=BASE_MS + 99 * STEP)
        assert gaps == ((BASE_MS, BASE_MS + 6 * STEP),)


# ------------------------------------------------------------------ 백필


class TestBackfiller:
    def _server(self, days: list[date], *, rows: int = 4) -> FakeArchiveServer:
        server = FakeArchiveServer()
        for day in days:
            archive = MetricsArchiveFile(SYMBOL, day)
            server.add_raw(archive, make_zip(archive.stem, csv_rows(rows, day=day.isoformat())))
        return server

    def test_downloads_and_stores(self, metrics_repo: PerpMetricsRepository) -> None:
        days = [date(2021, 1, 1), date(2021, 1, 2)]
        result = MetricsBackfiller(self._server(days).client(), metrics_repo).run(
            SYMBOL, start=days[0], end=days[-1]
        )
        assert result.ok
        assert result.files_fetched == 2
        assert metrics_repo.count(SYMBOL) == 8

    def test_missing_day_is_recorded_not_fatal(self, metrics_repo: PerpMetricsRepository) -> None:
        result = MetricsBackfiller(self._server([date(2021, 1, 1)]).client(), metrics_repo).run(
            SYMBOL, start=date(2021, 1, 1), end=date(2021, 1, 2)
        )
        assert result.ok
        assert len(result.files_missing) == 1
        assert metrics_repo.count(SYMBOL) == 4

    def test_checksum_mismatch_stores_nothing(self, metrics_repo: PerpMetricsRepository) -> None:
        server = self._server([date(2021, 1, 1)])
        server.corrupt(MetricsArchiveFile(SYMBOL, date(2021, 1, 1)))
        result = MetricsBackfiller(server.client(), metrics_repo).run(
            SYMBOL, start=date(2021, 1, 1), end=date(2021, 1, 1)
        )
        assert not result.ok
        assert metrics_repo.count(SYMBOL) == 0

    def test_resume_refetches_only_from_the_last_stored_day(
        self, metrics_repo: PerpMetricsRepository
    ) -> None:
        metrics_repo.upsert_many(SYMBOL, [sample(BASE_MS)])  # 2021-01-01
        days = [date(2021, 1, 1), date(2021, 1, 2), date(2021, 1, 3)]
        result = MetricsBackfiller(self._server(days).client(), metrics_repo).run(
            SYMBOL, start=date(2021, 1, 1), end=days[-1], resume=True
        )
        assert result.files_planned == 3  # 마지막 저장일은 일부만 받았을 수 있어 다시 받는다
