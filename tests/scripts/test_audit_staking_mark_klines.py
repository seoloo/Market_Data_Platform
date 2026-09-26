"""mark 1h 자료 감사기의 경계·OHLC·페이지네이션·보존 테스트."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from scripts.audit_staking_funding import END_MS, START_MS
from scripts.audit_staking_mark_klines import (
    HOUR_MS,
    fetch_asset,
    missing_hours,
    parse_page,
    save_snapshot,
)


def _row(open_ms: int, high: str = "102") -> list[Any]:
    return [open_ms, "100", high, "99", "101", "0", open_ms + HOUR_MS - 1, "0", 0, "0", "0", "0"]


def test_parse_rejects_invalid_ohlc_and_timestamp() -> None:
    assert parse_page([_row(START_MS)], START_MS, END_MS)[0]["high"] == "102"
    with pytest.raises(ValueError, match="OHLC"):
        parse_page([_row(START_MS, "100")], START_MS, END_MS)
    with pytest.raises(ValueError, match="시각"):
        parse_page([_row(START_MS + 1)], START_MS, END_MS)
    with pytest.raises(ValueError, match="시각"):
        parse_page([_row(START_MS), _row(START_MS)], START_MS, END_MS)


class _FakeReader:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        assert path == "/fapi/v1/markPriceKlines"
        self.calls.append(params)
        if len(self.calls) == 1:
            return [_row(START_MS + i * HOUR_MS) for i in range(1000)]
        return [_row(START_MS + 1000 * HOUR_MS)]


def test_fetch_uses_public_mark_1h_and_paginates() -> None:
    fake = _FakeReader()
    rows = fetch_asset(fake, "ETH")
    assert len(rows) == 1001
    assert fake.calls[0]["interval"] == "1h"
    assert fake.calls[1]["startTime"] == START_MS + 1000 * HOUR_MS


def test_manifest_hash_and_missing_hour(tmp_path: Path) -> None:
    rows = [parse_page([_row(START_MS)], START_MS, END_MS)[0]]
    manifest_file = save_snapshot(
        {"ETH": rows, "SOL": rows}, destination=tmp_path, fetched_at_ms=123
    )
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert missing_hours(rows) == (END_MS - START_MS) // HOUR_MS - 1
    for asset in ("ETH", "SOL"):
        item = manifest["assets"][asset]
        assert hashlib.sha256((tmp_path / item["path"]).read_bytes()).hexdigest() == item["sha256"]
