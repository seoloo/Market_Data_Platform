"""1m 순서 감사 스크립트는 읽기 전용이며 거래 자료를 참조하지 않는다."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from auto_trading.core.enums import Timeframe
from scripts import audit_ew0_intrabar as audit_script


def test_streamed_market_audit_does_not_modify_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "market.db"
    start_ms = audit_script.FROM_MS
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE candles (exchange TEXT, symbol TEXT, timeframe TEXT, "
            "open_ms INTEGER, close_ms INTEGER, open TEXT, high TEXT, low TEXT, "
            "close TEXT, volume TEXT)"
        )
        rows: list[tuple[str, str, str, int, int, str, str, str, str, str]] = [
            (
                "binance_usdm",
                "BTCUSDT",
                "4h",
                start_ms,
                start_ms + Timeframe.H4.ms - 1,
                "100",
                "110",
                "90",
                "100",
                "240",
            )
        ]
        rows.extend(
            (
                "binance_usdm",
                "BTCUSDT",
                "1m",
                start_ms + index * Timeframe.M1.ms,
                start_ms + (index + 1) * Timeframe.M1.ms - 1,
                "100",
                "110" if index == 20 else "101",
                "90" if index == 200 else "99",
                "100",
                "1",
            )
            for index in range(240)
        )
        connection.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(audit_script, "DB_PATH", path)
    monkeypatch.setattr(audit_script, "TO_MS", start_ms + Timeframe.H4.ms)
    assert audit_script.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["four_hour_bars"] == 1
    assert result["guarded_ambiguous_bars"] == 1
    assert result["extremum_order_guarded_ambiguous"] == {"HIGH_BEFORE_LOW": 1}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_missing_db_is_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(audit_script, "DB_PATH", missing)
    with pytest.raises(FileNotFoundError):
        audit_script.main()
    assert not missing.exists()
