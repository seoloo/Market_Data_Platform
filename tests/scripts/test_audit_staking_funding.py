"""펀딩 mark 공개 이력의 페이지 경계와 로컬 자료 불일치 검사."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from scripts.audit_staking_funding import END_MS, START_MS, audit_local, fetch_asset, parse_page


def _row(timestamp: int, *, rate: str = "0.0001", mark: str = "100") -> dict[str, Any]:
    return {
        "symbol": "ETHUSDT",
        "fundingTime": timestamp,
        "fundingRate": rate,
        "markPrice": mark,
        "rateType": "Regular",
    }


def test_page_accepts_empty_historical_mark_and_rejects_wrong_symbol() -> None:
    valid = _row(START_MS, mark="")
    assert parse_page([valid], "ETHUSDT", START_MS, END_MS)[0]["markPrice"] == ""
    with pytest.raises(ValueError, match="심볼"):
        parse_page([valid], "SOLUSDT", START_MS, END_MS)
    with pytest.raises(ValueError, match="범위"):
        parse_page([_row(END_MS)], "ETHUSDT", START_MS, END_MS)


class _FakeReader:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        assert path == "/fapi/v1/fundingRate"
        self.calls.append(params)
        if len(self.calls) == 1:
            return [_row(START_MS + i * 28_800_000) for i in range(1000)]
        return [_row(START_MS + 1000 * 28_800_000)]


def test_fetch_paginates_from_last_event_plus_one() -> None:
    fake = _FakeReader()
    rows = fetch_asset(fake, "ETH")
    assert len(rows) == 1001
    assert fake.calls[1]["startTime"] == rows[999]["fundingTime"] + 1


def test_local_audit_reports_missing_event_and_rate_difference(tmp_path: Path) -> None:
    db_path = tmp_path / "market.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE funding_rates (exchange TEXT, symbol TEXT, funding_ms INT, "
            "rate TEXT, mark_price TEXT)"
        )
        conn.executemany(
            "INSERT INTO funding_rates VALUES (?, ?, ?, ?, ?)",
            [
                ("binance_usdm", "ETHUSDT", START_MS, "0.0002", None),
                ("binance_usdm", "ETHUSDT", START_MS + 1, "0.0001", None),
            ],
        )
    result = audit_local(db_path, "ETH", [_row(START_MS), _row(START_MS + 2)])
    assert result["local_rows"] == 2
    assert result["remote_missing_from_local"] == 1
    assert result["local_missing_from_remote"] == 1
    assert result["rate_mismatches"] == 1
    assert result["remote_mark_missing"] == 0
