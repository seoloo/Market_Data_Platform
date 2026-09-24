"""시장 경로 감사는 4h/1m 시세만 읽고 DB를 수정하지 않는다."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from auto_trading.core.enums import Timeframe
from scripts import audit_ew0_minute_path as audit_script


def test_streamed_audit_is_market_only_and_database_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "market.db"
    start = audit_script.FROM_MS
    h4_ms = Timeframe.H4.ms
    m1_ms = Timeframe.M1.ms
    prices = ["100"] * 10 + ["103"] * 10 + ["100"] * 10 + ["97"] * 210
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE candles (exchange TEXT, symbol TEXT, timeframe TEXT, "
            "open_ms INTEGER, close_ms INTEGER, open TEXT, high TEXT, low TEXT, "
            "close TEXT, volume TEXT)"
        )
        connection.executemany(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "binance_usdm",
                    "BTCUSDT",
                    "4h",
                    start,
                    start + h4_ms - 1,
                    "100",
                    "103",
                    "97",
                    "97",
                    "240",
                ),
                (
                    "binance_usdm",
                    "BTCUSDT",
                    "4h",
                    start + h4_ms,
                    start + 2 * h4_ms - 1,
                    "97",
                    "97",
                    "97",
                    "97",
                    "240",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "binance_usdm",
                    "BTCUSDT",
                    "1m",
                    start + index * m1_ms,
                    start + (index + 1) * m1_ms - 1,
                    price,
                    price,
                    price,
                    price,
                    "1",
                )
                for index, price in enumerate(prices)
            ]
            + [
                (
                    "binance_usdm",
                    "BTCUSDT",
                    "1m",
                    start + (240 + index) * m1_ms,
                    start + (241 + index) * m1_ms - 1,
                    "97",
                    "97",
                    "97",
                    "97",
                    "1",
                )
                for index in range(240)
                if index != 10
            ],
        )

    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(audit_script, "DB_PATH", path)
    monkeypatch.setattr(audit_script, "TO_MS", start + 2 * h4_ms)
    assert audit_script.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["four_hour_bars"] == 2
    assert result["quality_accepted_four_hour_bars"] == 1
    assert result["blocked_four_hour_reasons"] == {"MINUTE_GRID_INCOMPLETE": 1}
    assert result["confirmed_path_events"] == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_missing_database_is_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(audit_script, "DB_PATH", missing)
    with pytest.raises(FileNotFoundError):
        audit_script.main()
    assert not missing.exists()
