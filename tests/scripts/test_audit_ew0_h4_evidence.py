"""고정 4h 증거 coverage 감사는 시장 봉만 조회하고 DB를 바꾸지 않는다."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from auto_trading.core.enums import Timeframe
from scripts import audit_ew0_candidate_coverage as candidate_audit
from scripts import audit_ew0_h4_evidence as audit_script


def test_h4_coverage_audit_is_read_only_and_excludes_wa_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "market.db"
    start = 1_577_836_800_000
    h4_ms = Timeframe.H4.ms
    m1_ms = Timeframe.M1.ms
    first = ["100"] * 10 + ["101"] * 10 + ["95"] * 220
    second = ["95"] * 10 + ["103"] * 230
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
                    "101",
                    "95",
                    "95",
                    "240",
                ),
                (
                    "binance_usdm",
                    "BTCUSDT",
                    "4h",
                    start + h4_ms,
                    start + 2 * h4_ms - 1,
                    "95",
                    "103",
                    "95",
                    "103",
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
                for index, price in enumerate(first + second)
            ],
        )

    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(audit_script, "DB_PATH", path)
    assert audit_script.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["four_hour_bars"] == 2
    assert result["status"] == {"CANDIDATE": 1, "UNKNOWN": 1}
    assert result["unknown_reasons"] == {"SAME_FOUR_HOUR": 1}
    assert result["candidate_kinds"] == {"low": 1}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    monkeypatch.setattr(candidate_audit, "DB_PATH", path)
    assert candidate_audit.main() == 0
    window_result = json.loads(capsys.readouterr().out)
    assert window_result["coverage"]["four_hour_bars"] == 2
    assert window_result["coverage"]["bars_with_two_anchors"] == 0
    assert len(window_result["mapped_evidence_sha256"]) == 64
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_missing_database_is_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(audit_script, "DB_PATH", missing)
    with pytest.raises(FileNotFoundError):
        audit_script.main()
    monkeypatch.setattr(candidate_audit, "DB_PATH", missing)
    with pytest.raises(FileNotFoundError):
        candidate_audit.main()
    assert not missing.exists()
