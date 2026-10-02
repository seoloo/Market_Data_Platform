"""스테이킹 비율 이력 감사기의 창 경계·응답 거부·보존 검사."""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts.audit_staking_rates import (
    DAY_MS,
    fetch_asset,
    midnight_ms,
    missing_dates,
    parse_page,
    save_snapshot,
    windows,
)


def _row(day: date, ratio: str = "1.01") -> dict[str, int | str]:
    return {"time": midnight_ms(day), "annualPercentageRate": "0.04", "exchangeRate": ratio}


def test_windows_cover_each_day_once_and_fit_single_page() -> None:
    start, end = date(2024, 7, 1), date(2025, 1, 1)
    ranges = list(windows(start, end))
    assert ranges[0][0] == midnight_ms(start)
    assert ranges[-1][1] == midnight_ms(end)
    assert all(left[1] == right[0] for left, right in itertools.pairwise(ranges))
    assert all(0 < (finish - begin) // DAY_MS <= 80 for begin, finish in ranges)


def test_page_rejects_truncated_out_of_range_and_invalid_values() -> None:
    begin = midnight_ms(date(2024, 9, 1))
    valid = _row(date(2024, 9, 1))
    assert parse_page({"rows": [valid], "total": "1"}, begin, begin + DAY_MS) == [valid]
    with pytest.raises(ValueError, match="잘렸거나"):
        parse_page({"rows": [valid], "total": "2"}, begin, begin + DAY_MS)
    with pytest.raises(ValueError, match="범위 이탈"):
        parse_page({"rows": [valid], "total": "1"}, begin + DAY_MS, begin + 2 * DAY_MS)
    with pytest.raises(ValueError, match="수치 범위"):
        parse_page(
            {"rows": [_row(date(2024, 9, 1), "NaN")], "total": "1"},
            begin,
            begin + DAY_MS,
        )


class _FakeReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any], bool]] = []

    def _request(
        self, path: str, params: Mapping[str, Any] | None = None, *, signed: bool = False
    ) -> Any:
        assert params is not None
        self.calls.append((path, params, signed))
        day = datetime.fromtimestamp(params["startTime"] / 1000, tz=UTC).date()
        return {"rows": [_row(day)], "total": "1"}


def test_fetch_only_signed_rate_windows() -> None:
    fake = _FakeReader()
    rows = fetch_asset(fake, "SOL", end_exclusive=date(2024, 9, 1), pause=lambda _: None)
    assert len(rows) == 1
    assert len(fake.calls) == 1
    assert fake.calls[0][0].endswith("/history/rateHistory")
    assert fake.calls[0][1]["endTime"] == midnight_ms(date(2024, 9, 1)) - 1
    assert fake.calls[0][2] is True


def test_snapshot_keeps_digest_and_reports_missing_date(tmp_path: Path) -> None:
    eth = [_row(date(2024, 9, 1)), _row(date(2024, 9, 3))]
    sol = [_row(date(2024, 9, 1))]
    assert missing_dates(eth) == ["2024-09-02"]
    manifest = save_snapshot({"ETH": eth, "SOL": sol}, destination=tmp_path, fetched_at_ms=123)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["assets"]["ETH"]["missing_dates"] == ["2024-09-02"]
    for asset in ("ETH", "SOL"):
        details = data["assets"][asset]
        contents = (tmp_path / details["path"]).read_bytes()
        assert hashlib.sha256(contents).hexdigest() == details["sha256"]
