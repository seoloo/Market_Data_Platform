"""Silver ↔ 연구 216 비교 시험 — 작은 가짜 보고 · 추출 결과로 같음 · 다름 판정을 본다."""

from __future__ import annotations

from typing import Any

from mdp.verify.compare_216 import compare_month

SOURCE = "data/futures/um/monthly/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2024-03.zip"


def _report(trades_at_nine: int = 5) -> dict[str, Any]:
    return {
        "audit_file": [
            {
                "source_path": SOURCE,
                "rows": 10,
                "min_id": 1,
                "max_id": 10,
                "min_ms": 100,
                "max_ms": 900,
                "header_rows": 1,
                "time_units": ["ms"],
                "time_reversed_rows": 0,
            }
        ],
        "gaps": [],
        "check_minutes": [
            {
                "source_path": SOURCE,
                "minute_ms": 0,
                "quantity": "1.50000000",
                "taker_buy_quantity": "0.50000000",
                "trades": 3,
            },
            {
                "source_path": SOURCE,
                "minute_ms": 3_600_000,
                "quantity": "2.00000000",
                "taker_buy_quantity": "1.00000000",
                "trades": trades_at_nine,
            },
        ],
    }


RESEARCH: dict[str, Any] = {
    "rows": 10,
    "first_id": 1,
    "last_id": 10,
    "first_ts": 100,
    "last_ts": 900,
    "header": True,
    "ts_unit": "ms",
    "gaps": [],
    "nonmono": 0,
    # float 합은 decimal 합과 마지막 자리에서 다를 수 있다 → 상대 오차로 같다고 본다
    "check_min": {"0": [1.5000000000000002, 0.5, 3], "3600000": [2.0, 1.0, 5]},
}


def test_everything_equal_within_float_tolerance() -> None:
    comparison = compare_month(_report(), RESEARCH, "full", SOURCE)
    assert comparison.all_equal
    assert comparison.items["rows"] == (10, 10, True)


def test_minute_mismatch_is_reported() -> None:
    comparison = compare_month(_report(trades_at_nine=4), RESEARCH, "fast", SOURCE)
    assert not comparison.all_equal
    assert [m["minute_ms"] for m in comparison.minute_mismatches] == [3_600_000]
    assert comparison.minute_mismatches[0]["silver"] == ["2.00000000", "1.00000000", 4]


def test_item_difference_is_reported() -> None:
    research = {**RESEARCH, "nonmono": 2}
    comparison = compare_month(_report(), research, "fast", SOURCE)
    assert comparison.items["time_reversed"] == (2, 0, False) and not comparison.all_equal
