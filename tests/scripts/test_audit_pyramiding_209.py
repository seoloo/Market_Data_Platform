from __future__ import annotations

import sqlite3

import pytest
from scripts.audit_pyramiding_209 import DAY_MS, audit_symbol, coverage, regime_starts


def test_flat_warmup_and_rising_regime() -> None:
    rows = [(i * DAY_MS, str(100 + i)) for i in range(140)]
    starts, states = regime_starts(rows)
    assert starts == [120 * DAY_MS]
    assert not any(on for _ms, on in states[:120])
    assert all(on for _ms, on in states[120:])
    assert regime_starts([(i * DAY_MS, "100") for i in range(150)])[0] == []


@pytest.mark.leakage
def test_future_prices_cannot_rewrite_regime_prefix() -> None:
    prefix = [(i * DAY_MS, str(100 + i)) for i in range(140)]
    future = [(i * DAY_MS, "1") for i in range(140, 200)]
    starts, states = regime_starts(prefix)
    extended_starts, extended_states = regime_starts(prefix + future)
    assert extended_states[: len(states)] == states
    assert [ms for ms in extended_starts if ms < 140 * DAY_MS] == starts


def test_missing_or_invalid_close_fails_closed() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        regime_starts([(0, "100"), (2 * DAY_MS, "101")])
    for value in ("0", "-1", "NaN", "inf"):
        with pytest.raises(ValueError, match="positive finite"):
            regime_starts([(0, value)])


def test_coverage_digest_and_funding_jitter() -> None:
    result = coverage([(0, "0.001"), (28_800_047, "0.001")], 28_800_000, jitter_tolerance_ms=1000)
    assert result.rows == 2 and result.gaps == 0
    assert result.digest != coverage([(0, "0.002"), (28_800_047, "0.001")], 28_800_000).digest
    assert coverage([(0, "0"), (57_600_000, "0")], 28_800_000, jitter_tolerance_ms=1000).gaps == 1
    assert coverage([(0, "0"), (57_600_000, "0")], 28_800_000).gaps == 1
    assert coverage([], DAY_MS).first_ms is None
    with pytest.raises(ValueError, match="increasing"):
        coverage([(1, "1"), (1, "2")], DAY_MS)


def test_symbol_whitelist_prevents_unrelated_research_reads() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="BTCUSDT/ETHUSDT"):
            audit_symbol(conn, "SP500")
    finally:
        conn.close()


@pytest.mark.leakage
def test_database_audit_excludes_forward_rows_and_never_writes() -> None:
    from scripts.audit_pyramiding_209 import END_MS, START_MS

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE candles(exchange TEXT,symbol TEXT,timeframe TEXT,"
            "open_ms INTEGER,close TEXT)"
        )
        conn.execute(
            "CREATE TABLE funding_rates(exchange TEXT,symbol TEXT,funding_ms INTEGER,"
            "rate TEXT,mark_price TEXT)"
        )
        for i in range(140):
            conn.execute(
                "INSERT INTO candles VALUES('binance_spot','BTCUSDT','1d',?,?)",
                (START_MS + (i - 130) * DAY_MS, str(100 + i)),
            )
        before = audit_symbol(conn, "BTCUSDT")
        conn.execute(
            "INSERT INTO candles VALUES('binance_spot','BTCUSDT','1d',?,'999999999')", (END_MS,)
        )
        conn.commit()
        conn.execute("PRAGMA query_only=ON")
        assert audit_symbol(conn, "BTCUSDT") == before
        assert before.spot_daily.rows == 140
        assert before.perp_daily.rows == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM candles")
    finally:
        conn.close()
