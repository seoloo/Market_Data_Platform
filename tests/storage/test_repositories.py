"""repository 테스트 — Decimal 왕복, 갭 탐지, causal 조회, 멱등 등록."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories import CandleRepository, MetaRepository

D = Decimal
NOW = 1_726_000_000_000
TF = Timeframe.M1
SYMBOL = "BTCUSDT"


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "t.db")
    apply_migrations(connection, now_ms=NOW)
    yield connection
    connection.close()


@pytest.fixture
def candles(conn: sqlite3.Connection) -> CandleRepository:
    return CandleRepository(conn)


@pytest.fixture
def meta(conn: sqlite3.Connection) -> MetaRepository:
    return MetaRepository(conn)


def make_candle(index: int, *, tf: Timeframe = TF, close: str = "100.5") -> Candle:
    open_ms = index * tf.ms
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + tf.ms - 1,
        open=D("100"),
        high=D("101"),
        low=D("99"),
        close=D(close),
        volume=D("12.345"),
        quote_volume=D("1234.5"),
        trade_count=42,
    )


class TestCandleRoundTrip:
    def test_insert_and_read(self, candles: CandleRepository):
        assert (
            candles.upsert_many(SYMBOL, TF, [make_candle(0)], source="bulk", ingested_ms=NOW) == 1
        )
        got = candles.get_range(SYMBOL, TF)
        assert len(got) == 1
        assert got[0] == make_candle(0)

    def test_decimal_precision_survives_round_trip(self, candles: CandleRepository):
        """TEXT 저장이므로 정밀도 손실이 없어야 한다 (F10)."""
        precise = Candle(
            open_ms=0,
            close_ms=TF.ms - 1,
            open=D("99999.12345678"),
            high=D("99999.12345679"),
            low=D("99999.12345677"),
            close=D("99999.12345678"),
            volume=D("0.00000001"),
            quote_volume=D("0.000000019999"),
        )
        candles.upsert_many(SYMBOL, TF, [precise], source="bulk", ingested_ms=NOW)
        got = candles.get_range(SYMBOL, TF)[0]
        assert got.open == D("99999.12345678")
        assert got.volume == D("0.00000001")
        assert got.quote_volume == D("0.000000019999")

    def test_upsert_is_idempotent(self, candles: CandleRepository):
        for _ in range(3):
            candles.upsert_many(SYMBOL, TF, [make_candle(0)], source="bulk", ingested_ms=NOW)
        assert candles.count(SYMBOL, TF) == 1

    def test_upsert_updates_existing(self, candles: CandleRepository):
        candles.upsert_many(SYMBOL, TF, [make_candle(0)], source="bulk", ingested_ms=NOW)
        candles.upsert_many(
            SYMBOL, TF, [make_candle(0, close="100.9")], source="ws", ingested_ms=NOW + 1
        )
        assert candles.get_range(SYMBOL, TF)[0].close == D("100.9")
        assert candles.count(SYMBOL, TF) == 1

    def test_empty_insert(self, candles: CandleRepository):
        assert candles.upsert_many(SYMBOL, TF, [], source="bulk", ingested_ms=NOW) == 0

    def test_timeframes_are_isolated(self, candles: CandleRepository):
        candles.upsert_many(SYMBOL, TF, [make_candle(0)], source="bulk", ingested_ms=NOW)
        candles.upsert_many(
            SYMBOL,
            Timeframe.H1,
            [make_candle(0, tf=Timeframe.H1)],
            source="bulk",
            ingested_ms=NOW,
        )
        assert candles.count(SYMBOL, TF) == 1
        assert candles.count(SYMBOL, Timeframe.H1) == 1

    def test_bad_data_in_db_is_caught_on_read(self, conn: sqlite3.Connection):
        """DB 에 잘못된 OHLC 가 있어도 타입 경계에서 막힌다."""
        from auto_trading.core.errors import DataIntegrityError

        conn.execute(
            "INSERT INTO candles (exchange, symbol, timeframe, open_ms, close_ms,"
            " open, high, low, close, volume, quote_volume, trade_count, source, ingested_ms)"
            " VALUES ('binance_usdm', ?, '1m', 0, 59999,"
            " '100', '90', '99', '100', '1', '1', 1, 'bulk', ?)",  # high < open
            (SYMBOL, NOW),
        )
        with pytest.raises(DataIntegrityError, match="OHLC"):
            CandleRepository(conn).get_range(SYMBOL, TF)


class TestCandleQueries:
    @pytest.fixture(autouse=True)
    def _seed(self, candles: CandleRepository):
        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in range(10)], source="bulk", ingested_ms=NOW
        )

    def test_range_filter(self, candles: CandleRepository):
        got = candles.get_range(SYMBOL, TF, from_ms=3 * TF.ms, to_ms=5 * TF.ms)
        assert [c.open_ms // TF.ms for c in got] == [3, 4, 5]

    def test_limit_returns_most_recent_ascending(self, candles: CandleRepository):
        got = candles.get_range(SYMBOL, TF, limit=3)
        assert [c.open_ms // TF.ms for c in got] == [7, 8, 9]

    def test_first_and_last(self, candles: CandleRepository):
        assert candles.first_open_ms(SYMBOL, TF) == 0
        assert candles.last_open_ms(SYMBOL, TF) == 9 * TF.ms

    def test_last_open_ms_none_when_empty(self, candles: CandleRepository):
        assert candles.last_open_ms("ETHUSDT", TF) is None

    def test_closed_series_excludes_forming_bar(self, candles: CandleRepository):
        """as_of 가 봉 중간이면 그 봉은 아직 마감되지 않았다."""
        as_of = 9 * TF.ms + 100  # 9번 봉 형성 중
        series = candles.get_closed_series(SYMBOL, TF, as_of, limit=100)
        assert len(series) == 9
        assert series.last is not None
        assert series.last.open_ms == 8 * TF.ms

    def test_closed_series_includes_just_closed_bar(self, candles: CandleRepository):
        as_of = 9 * TF.ms + TF.ms - 1  # 9번 봉 마감 순간
        series = candles.get_closed_series(SYMBOL, TF, as_of, limit=100)
        assert len(series) == 10

    def test_closed_series_respects_limit(self, candles: CandleRepository):
        series = candles.get_closed_series(SYMBOL, TF, 10 * TF.ms, limit=4)
        assert len(series) == 4
        assert series.candles[0].open_ms == 6 * TF.ms


class TestGapDetection:
    def test_no_gaps_in_continuous_data(self, candles: CandleRepository):
        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in range(5)], source="bulk", ingested_ms=NOW
        )
        assert candles.find_gaps(SYMBOL, TF) == ()

    def test_single_missing_bar(self, candles: CandleRepository):
        present = [make_candle(i) for i in (0, 1, 3, 4)]
        candles.upsert_many(SYMBOL, TF, present, source="bulk", ingested_ms=NOW)
        gaps = candles.find_gaps(SYMBOL, TF)
        assert len(gaps) == 1
        assert gaps[0].missing_from_ms == 2 * TF.ms
        assert gaps[0].missing_to_ms == 2 * TF.ms
        assert gaps[0].bar_count == 1

    def test_multi_bar_gap(self, candles: CandleRepository):
        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in (0, 5)], source="bulk", ingested_ms=NOW
        )
        gaps = candles.find_gaps(SYMBOL, TF)
        assert len(gaps) == 1
        assert gaps[0].bar_count == 4

    def test_multiple_gaps(self, candles: CandleRepository):
        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in (0, 2, 6)], source="bulk", ingested_ms=NOW
        )
        assert len(candles.find_gaps(SYMBOL, TF)) == 2

    def test_record_and_count(self, candles: CandleRepository):
        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in (0, 3)], source="bulk", ingested_ms=NOW
        )
        gaps = candles.find_gaps(SYMBOL, TF)
        assert candles.record_gaps(SYMBOL, gaps, detected_ms=NOW) == 1
        assert candles.open_gap_count(SYMBOL, TF) == 1

    def test_record_is_idempotent(self, candles: CandleRepository):
        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in (0, 3)], source="bulk", ingested_ms=NOW
        )
        gaps = candles.find_gaps(SYMBOL, TF)
        candles.record_gaps(SYMBOL, gaps, detected_ms=NOW)
        candles.record_gaps(SYMBOL, gaps, detected_ms=NOW + 1)
        assert candles.open_gap_count(SYMBOL, TF) == 1

    def test_filling_gap_then_resolving(self, candles: CandleRepository):
        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in (0, 3)], source="bulk", ingested_ms=NOW
        )
        candles.record_gaps(SYMBOL, candles.find_gaps(SYMBOL, TF), detected_ms=NOW)
        assert candles.open_gap_count(SYMBOL, TF) == 1

        candles.upsert_many(
            SYMBOL, TF, [make_candle(i) for i in (1, 2)], source="rest", ingested_ms=NOW
        )
        candles.resolve_gaps(SYMBOL, TF, resolved_ms=NOW + 10)
        assert candles.open_gap_count(SYMBOL, TF) == 0
        assert candles.find_gaps(SYMBOL, TF) == ()

    def test_empty_gap_list(self, candles: CandleRepository):
        assert candles.record_gaps(SYMBOL, [], detected_ms=NOW) == 0


class TestMetaRepository:
    def test_register_config_is_idempotent(self, meta: MetaRepository):
        """재기동마다 같은 설정으로 새 행이 생기면 이력이 무의미해진다."""
        a = meta.register_config(config_hash="h1", config_json='{"a":1}', created_ms=NOW)
        b = meta.register_config(config_hash="h1", config_json='{"a":1}', created_ms=NOW + 100)
        assert a.id == b.id

    def test_different_hash_creates_new_row(self, meta: MetaRepository):
        a = meta.register_config(config_hash="h1", config_json="{}", created_ms=NOW)
        b = meta.register_config(config_hash="h2", config_json="{}", created_ms=NOW)
        assert a.id != b.id

    def test_config_json_round_trip(self, meta: MetaRepository):
        payload = '{"risk": {"max_leverage": 10}}'
        version = meta.register_config(config_hash="h", config_json=payload, created_ms=NOW)
        assert meta.get_config_json(version.id) == payload

    def test_get_missing_config(self, meta: MetaRepository):
        assert meta.get_config_by_hash("nope") is None
        assert meta.get_config_json(999) is None

    def test_register_code_is_idempotent(self, meta: MetaRepository):
        deps = {"pydantic": "2.7.0"}
        a = meta.register_code(git_sha="abc", dirty=False, deps=deps, created_ms=NOW)
        b = meta.register_code(git_sha="abc", dirty=False, deps=deps, created_ms=NOW + 5)
        assert a.id == b.id

    def test_dirty_flag_distinguishes_versions(self, meta: MetaRepository):
        clean = meta.register_code(git_sha="abc", dirty=False, deps={}, created_ms=NOW)
        dirty = meta.register_code(git_sha="abc", dirty=True, deps={}, created_ms=NOW)
        assert clean.id != dirty.id

    def test_dep_change_creates_new_version(self, meta: MetaRepository):
        a = meta.register_code(git_sha="abc", dirty=False, deps={"x": "1"}, created_ms=NOW)
        b = meta.register_code(git_sha="abc", dirty=False, deps={"x": "2"}, created_ms=NOW)
        assert a.id != b.id

    def test_dep_order_does_not_matter(self, meta: MetaRepository):
        """dict 순서 때문에 같은 의존성이 다른 버전으로 기록되면 안 된다."""
        a = meta.register_code(git_sha="s", dirty=False, deps={"a": "1", "b": "2"}, created_ms=NOW)
        b = meta.register_code(git_sha="s", dirty=False, deps={"b": "2", "a": "1"}, created_ms=NOW)
        assert a.id == b.id

    def test_dirty_code_blocks_live_trading(self, meta: MetaRepository):
        """게이트 G1.4 — 재현할 수 없는 코드로 실거래하지 않는다."""
        dirty = meta.register_code(git_sha="abc", dirty=True, deps={}, created_ms=NOW)
        assert dirty.allows_live_trading is False
        clean = meta.register_code(git_sha="abc", dirty=False, deps={}, created_ms=NOW)
        assert clean.allows_live_trading is True

    def test_get_code_round_trip(self, meta: MetaRepository):
        created = meta.register_code(git_sha="deadbeef", dirty=True, deps={}, created_ms=NOW)
        fetched = meta.get_code(created.id)
        assert fetched is not None
        assert fetched.git_sha == "deadbeef"
        assert fetched.dirty is True

    def test_get_missing_code(self, meta: MetaRepository):
        assert meta.get_code(999) is None
