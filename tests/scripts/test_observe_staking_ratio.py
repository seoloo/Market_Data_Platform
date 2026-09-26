"""고정 상품 기간·SHA·일별 완전성·코인 수량 증가율의 재현 검증."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from scripts.audit_staking_rates import midnight_ms
from scripts.observe_staking_ratio import (
    END,
    START,
    annualized_pct,
    load_ratios,
    period_gain_pct,
    save_report,
    summarize,
    validated_ratios,
)


def _rows(start: date, end: date) -> list[dict[str, int | str]]:
    return [
        {
            "time": midnight_ms(start + timedelta(days=i)),
            "exchangeRate": str(Decimal(1) + Decimal(i) / 1000),
        }
        for i in range((end - start).days + 1)
    ]


def test_ratio_gain_and_annualization_are_coins_not_apr_field() -> None:
    first, last = date(2025, 1, 1), date(2026, 1, 1)
    ratios = {first: Decimal("1"), last: Decimal("1.1")}
    assert period_gain_pct(ratios, first, last) == Decimal("10.0000")
    assert annualized_pct(ratios, first, last) == Decimal("10.0000")
    assert period_gain_pct({first: Decimal("1"), last: Decimal("0.9")}, first, last) == Decimal(
        "-10.0000"
    )


def test_daily_validator_rejects_missing_and_bad_ratios() -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 3)
    rows = _rows(start, end)
    assert len(validated_ratios(rows, start=start, end=end)) == 3
    with pytest.raises(ValueError, match="결측"):
        validated_ratios(rows[:1] + rows[2:], start=start, end=end)
    rows[1]["exchangeRate"] = "NaN"
    with pytest.raises(ValueError, match="범위"):
        validated_ratios(rows, start=start, end=end)


def test_snapshot_requires_exact_hash_and_count(tmp_path: Path) -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 3)
    rows = _rows(start, end)
    blob = json.dumps(rows).encode()
    digest = hashlib.sha256(blob).hexdigest()
    assets = {}
    for asset in ("ETH", "SOL"):
        path = f"{asset.lower()}.json"
        (tmp_path / path).write_bytes(blob)
        assets[asset] = {"path": path, "sha256": digest, "rows": len(rows)}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"end_exclusive": "2026-09-01", "assets": assets}))
    expected = {"ETH": digest, "SOL": digest}
    assert len(load_ratios(manifest, expected_sha=expected, start=start, end=end)["ETH"]) == 3
    (tmp_path / "eth.json").write_bytes(blob + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        load_ratios(manifest, expected_sha=expected, start=start, end=end)


def test_result_is_immutable(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    save_report({"x": 1}, destination)
    assert save_report({"x": 1}, destination) == destination
    with pytest.raises(ValueError, match="덮어쓰지"):
        save_report({"x": 2}, destination)


def test_prelaunch_ratio_does_not_enter_fixed_result() -> None:
    ratios = {
        date(2024, 8, 28): Decimal("0.5"),
        START: Decimal("1"),
        date(2025, 1, 1): Decimal("1.02"),
        date(2026, 1, 1): Decimal("1.05"),
        END: Decimal("1.1"),
    }
    report = summarize({"ETH": ratios, "SOL": ratios})
    assert report["start"] == "2024-09-13"
    assert report["assets"]["SOL"]["full_gain_pct"] == "10.0000"
