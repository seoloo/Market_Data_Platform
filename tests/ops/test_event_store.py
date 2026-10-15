import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.ops.event_store import EARNINGS_TTL_MS, earnings_day

DAY = date(2026, 10, 20)
NOW = 1_792_500_000_000


def test_shared_rows_and_provenance_are_reused(tmp_path: Path) -> None:
    calls: list[date] = []

    def fetch(day: date) -> dict[str, Any]:
        calls.append(day)
        return {"data": {"rows": [{"symbol": "NVDA"}]}}

    a = earnings_day(tmp_path, DAY, NOW, fetch=fetch)
    b = earnings_day(tmp_path, DAY, NOW + 1, fetch=fetch)
    assert a == b and a.ok and len(calls) == 1
    assert a.day == DAY and len(a.digest) == 64 and "nasdaq.com" in a.source
    earnings_day(tmp_path, DAY, NOW + EARNINGS_TTL_MS, fetch=fetch)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": {"rows": None}},
        {"data": {"rows": [{}]}},
        {"status": {"rCode": 500}, "data": {"rows": []}},
    ],
)
def test_missing_or_error_payload_is_not_empty_success(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    result = earnings_day(tmp_path, DAY, NOW, fetch=lambda _: payload)
    assert not result.ok and not result.rows


def test_explicit_empty_is_usable_but_stale_success_does_not_hide_failure(tmp_path: Path) -> None:
    a = earnings_day(tmp_path, DAY, NOW, fetch=lambda _: {"data": {"rows": []}})
    assert a.ok and not a.rows

    def down(_: date) -> dict[str, Any]:
        raise httpx.ConnectError("down")

    b = earnings_day(tmp_path, DAY, NOW + EARNINGS_TTL_MS, fetch=down)
    assert not b.ok
    assert not earnings_day(tmp_path, DAY, NOW + EARNINGS_TTL_MS + 1, fetch=down).ok


@pytest.mark.parametrize(
    "field,value",
    [("fetched_at_ms", NOW + 1), ("digest", "wrong"), ("day", "2026-10-21"), ("source", "wrong")],
)
def test_corrupt_or_future_cache_is_refetched(tmp_path: Path, field: str, value: object) -> None:
    earnings_day(tmp_path, DAY, NOW, fetch=lambda _: {"data": {"rows": []}})
    path = tmp_path / "data/event_calendar/earnings/2026-10-20.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = earnings_day(
        tmp_path, DAY, NOW, fetch=lambda _: {"data": {"rows": [{"symbol": "NEW"}]}}
    )
    assert result.ok and result.rows[0]["symbol"] == "NEW"


def test_force_refresh_detects_changed_calendar(tmp_path: Path) -> None:
    earnings_day(tmp_path, DAY, NOW, fetch=lambda _: {"data": {"rows": []}})
    changed = earnings_day(
        tmp_path, DAY, NOW + 1, force=True, fetch=lambda _: {"data": {"rows": [{"symbol": "NVDA"}]}}
    )
    assert changed.rows[0]["symbol"] == "NVDA"


def test_weekend_has_no_earnings_without_fetch(tmp_path: Path) -> None:
    def boom(_: date) -> dict[str, Any]:
        raise AssertionError("주말은 받지 않는다")

    for d in (date(2026, 10, 17), date(2026, 10, 18)):  # 토 · 일 — Nasdaq 은 rows:null
        r = earnings_day(tmp_path, d, NOW, fetch=boom)
        assert r.ok and r.rows == ()
