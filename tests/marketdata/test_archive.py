"""아카이브 클라이언트 테스트 — URL, 체크섬, CSV 파싱, 단위 오판 방어."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError, ExchangeError
from auto_trading.marketdata.archive import (
    ArchiveFile,
    BinanceVisionClient,
    Granularity,
    days_between,
    months_between,
    parse_klines_csv,
    utc_today,
)
from tests.marketdata.conftest import SYMBOL, FakeArchiveServer, make_csv_rows, make_zip

D = Decimal
TF = Timeframe.M1
JAN = date(2024, 1, 1)
JAN_MS = 1_704_067_200_000  # 2024-01-01T00:00:00Z


class TestArchiveFile:
    def test_monthly_naming(self):
        f = ArchiveFile(SYMBOL, TF, JAN, Granularity.MONTHLY)
        assert f.filename == "BTCUSDT-1m-2024-01.zip"
        assert f.url().endswith("/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip")

    def test_daily_naming(self):
        f = ArchiveFile(SYMBOL, TF, date(2024, 3, 5), Granularity.DAILY)
        assert f.filename == "BTCUSDT-1m-2024-03-05.zip"
        assert "/daily/klines/" in f.url()

    def test_checksum_url(self):
        f = ArchiveFile(SYMBOL, TF, JAN, Granularity.MONTHLY)
        assert f.checksum_url() == f.url() + ".CHECKSUM"

    def test_futures_um_path(self):
        """현물이 아니라 USD-M 선물 경로여야 한다."""
        assert "/futures/um/" in ArchiveFile(SYMBOL, TF, JAN, Granularity.MONTHLY).url()

    def test_spot_path_is_explicit(self):
        """현물은 명시적으로 요청할 때만 쓴다 (K 캐리 연구의 베이시스)."""
        f = ArchiveFile(SYMBOL, TF, JAN, Granularity.MONTHLY, market="spot")
        assert f.url().endswith("/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip")
        assert "/futures/" not in f.url()


class TestDateHelpers:
    def test_months_between_spans_year(self):
        months = months_between(date(2023, 11, 15), date(2024, 2, 3))
        assert months == [date(2023, 11, 1), date(2023, 12, 1), date(2024, 1, 1), date(2024, 2, 1)]

    def test_months_between_same_month(self):
        assert months_between(date(2024, 1, 5), date(2024, 1, 20)) == [JAN]

    def test_months_between_reversed_is_empty(self):
        assert months_between(date(2024, 2, 1), date(2024, 1, 1)) == []

    def test_days_between(self):
        assert len(days_between(date(2024, 1, 1), date(2024, 1, 5))) == 5

    def test_utc_today(self):
        assert utc_today(JAN_MS) == JAN


class TestParseCsv:
    def test_basic_parse(self):
        csv_text = make_csv_rows(JAN_MS, 3, TF)
        candles = parse_klines_csv(csv_text.encode(), TF)
        assert len(candles) == 3
        assert candles[0].open_ms == JAN_MS
        assert candles[0].open == D("60000.00")
        assert candles[0].trade_count == 42

    def test_header_row_is_skipped(self):
        csv_text = make_csv_rows(JAN_MS, 2, TF, with_header=True)
        assert len(parse_klines_csv(csv_text.encode(), TF)) == 2

    def test_decimal_precision_preserved(self):
        row = (
            f"{JAN_MS},99999.12345678,99999.2,99999.1,99999.12345678,"
            f"0.00000001,{JAN_MS + 59999},1.0,1,0,0,0\n"
        )
        candle = parse_klines_csv(row.encode(), TF)[0]
        assert candle.open == D("99999.12345678")
        assert candle.volume == D("0.00000001")

    def test_output_is_sorted(self):
        rows = make_csv_rows(JAN_MS, 3, TF).strip().split("\n")
        shuffled = "\n".join([rows[2], rows[0], rows[1]]) + "\n"
        candles = parse_klines_csv(shuffled.encode(), TF)
        assert [c.open_ms for c in candles] == sorted(c.open_ms for c in candles)

    def test_too_few_columns_rejected(self):
        with pytest.raises(DataIntegrityError, match="컬럼 수"):
            parse_klines_csv(b"1,2,3\n", TF)

    def test_unaligned_open_time_rejected(self):
        """봉 경계에 맞지 않으면 데이터가 잘못된 것이다."""
        bad = JAN_MS + 30_000
        row = f"{bad},1,1,1,1,1,{bad + 59999},1,1,0,0,0\n"
        with pytest.raises(DataIntegrityError, match="정렬"):
            parse_klines_csv(row.encode(), TF)

    def test_wrong_bar_length_rejected(self):
        """close_time - open_time 이 봉 길이와 다르면 단위가 다를 수 있다."""
        row = f"{JAN_MS},1,1,1,1,1,{JAN_MS + 12345},1,1,0,0,0\n"
        with pytest.raises(DataIntegrityError, match="봉 길이"):
            parse_klines_csv(row.encode(), TF)

    def test_microsecond_timestamps_are_normalized(self):
        """일부 시기 아카이브가 마이크로초로 제공된 적이 있다.

        단위를 잘못 읽으면 봉 시각이 통째로 어긋나고 알아채기 어렵다.
        """
        open_us = JAN_MS * 1000
        close_us = (JAN_MS + TF.ms - 1) * 1000
        row = f"{open_us},60000,60100,59900,60000,1,{close_us},1,1,0,0,0\n"
        candle = parse_klines_csv(row.encode(), TF)[0]
        assert candle.open_ms == JAN_MS
        assert candle.close_ms == JAN_MS + TF.ms - 1

    def test_bad_ohlc_rejected_by_candle_type(self):
        row = f"{JAN_MS},100,90,99,100,1,{JAN_MS + 59999},1,1,0,0,0\n"  # high < open
        with pytest.raises(DataIntegrityError, match="OHLC"):
            parse_klines_csv(row.encode(), TF)

    def test_non_numeric_open_time_rejected(self):
        csv_text = f"{JAN_MS},1,1,1,1,1,{JAN_MS + 59999},1,1,0,0,0\nGARBAGE,1,1,1,1,1,1,1,1,0,0,0\n"
        with pytest.raises(DataIntegrityError, match="숫자가 아니다"):
            parse_klines_csv(csv_text.encode(), TF)

    def test_higher_timeframe(self):
        csv_text = make_csv_rows(JAN_MS, 2, Timeframe.H4)
        candles = parse_klines_csv(csv_text.encode(), Timeframe.H4)
        assert candles[1].open_ms - candles[0].open_ms == Timeframe.H4.ms


class TestDownload:
    def test_successful_download(self, server: FakeArchiveServer, monthly):
        f = monthly(JAN, TF)
        server.add(f, make_csv_rows(JAN_MS, 5, TF))
        with server.client() as client:
            candles = client.fetch_candles(f)
        assert candles is not None
        assert len(candles) == 5

    def test_missing_file_returns_none(self, server: FakeArchiveServer, monthly):
        with server.client() as client:
            assert client.fetch_candles(monthly(JAN, TF)) is None

    def test_checksum_mismatch_rejected(self, server: FakeArchiveServer, monthly):
        """손상된 파일이 조용히 들어가면 이후 모든 검증이 무의미해진다."""
        f = monthly(JAN, TF)
        server.add(f, make_csv_rows(JAN_MS, 3, TF))
        server.corrupt(f)
        with server.client() as client, pytest.raises(DataIntegrityError, match="체크섬 불일치"):
            client.fetch_candles(f)

    def test_missing_checksum_rejected(self, server: FakeArchiveServer, monthly):
        f = monthly(JAN, TF)
        server.add(f, make_csv_rows(JAN_MS, 3, TF))
        server.drop_checksum(f)
        with (
            server.client() as client,
            pytest.raises(DataIntegrityError, match="CHECKSUM 파일이 없다"),
        ):
            client.fetch_candles(f)

    def test_checksum_can_be_skipped_explicitly(self, server: FakeArchiveServer, monthly):
        f = monthly(JAN, TF)
        server.add(f, make_csv_rows(JAN_MS, 3, TF))
        server.drop_checksum(f)
        with server.client() as client:
            assert client.fetch_candles(f, verify_checksum=False) is not None

    def test_server_error_is_retried_then_raises(self, server: FakeArchiveServer, monthly):
        f = monthly(JAN, TF)
        server.server_errors[f.url()] = 500
        with server.client() as client, pytest.raises(ExchangeError, match="다운로드 실패"):
            client.download(f)
        assert server.request_log.count(f.url()) == 2  # max_retries=2

    def test_zip_with_wrong_content_name_rejected(self, server: FakeArchiveServer, monthly):
        f = monthly(JAN, TF)
        server.add_raw(f, make_zip("UNEXPECTED-NAME", make_csv_rows(JAN_MS, 1, TF)))
        with (
            server.client() as client,
            pytest.raises(DataIntegrityError, match="이름이 예상과 다르다"),
        ):
            client.fetch_candles(f)

    def test_zip_with_multiple_csvs_rejected(self, server: FakeArchiveServer, monthly):
        import io
        import zipfile

        f = monthly(JAN, TF)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.csv", "x")
            archive.writestr("b.csv", "y")
        server.add_raw(f, buffer.getvalue())
        with server.client() as client, pytest.raises(DataIntegrityError, match="정확히 1개"):
            client.fetch_candles(f)

    def test_owns_client_lifecycle(self):
        """직접 만든 httpx.Client 는 close 로 정리된다."""
        client = BinanceVisionClient(
            httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        )
        client.close()  # 주입된 클라이언트는 닫지 않는다 (예외 없이 통과)
