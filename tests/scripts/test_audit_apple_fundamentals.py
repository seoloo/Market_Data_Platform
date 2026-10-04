"""공식 숫자의 최소 발췌 회귀. 전체 SEC 원본은 로컬 SHA 검사 실행과 구분한다."""

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from auto_trading.core.errors import DataIntegrityError
from scripts import audit_apple_fundamentals as pilot

FIXTURE = Path(__file__).parents[1] / "fixtures/fundamental/apple-2024-excerpt.json"


def excerpt() -> tuple[bytes, bytes]:
    content = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert content["kind"] == "MANUALLY_EXTRACTED_REAL_FILING_ROWS_NOT_FULL_RAW_SNAPSHOT"
    return json.dumps(content["companyfacts"]).encode(), json.dumps(content["submissions"]).encode()


def test_official_excerpt_values_and_q4_difference() -> None:
    result = pilot.audit_sample(*excerpt())
    assert result["status"] == "REAL_SAMPLE_NUMERIC_MATCH"
    assert result["historical_first_seen"] == "NOT_VERIFIED"
    assert result["strategy_result"] == "NOT_TESTED"
    assert result["fiscal_year_lengths_days"] == [371, 364]
    assert result["q4_amounts_usd"] == {
        pilot.REVENUE: "94930000000",
        pilot.OPERATING: "29591000000",
        pilot.CASHFLOW: "26811000000",
    }
    assert result["q4_source_accessions"] == [pilot.INTERIM, pilot.ANNUAL]


def test_wrong_reported_number_is_rejected() -> None:
    facts, submissions = excerpt()
    with pytest.raises(DataIntegrityError, match="PRIMARY_SOURCE_AMOUNT_MISMATCH"):
        pilot.audit_sample(facts.replace(b"391035000000", b"391035000001"), submissions)


@pytest.mark.leakage
def test_comparative_number_keeps_2024_acceptance_and_delayed_availability() -> None:
    facts, submissions = excerpt()
    result = pilot.audit_sample(facts, submissions)
    assert result["availability_policy"] == "MANUALLY_REVIEWED_NEXT_SESSION_CLOSE_ASSUMPTION"
    # audit_sample 내부에서 비교연도의 2023년 선택 및 지연 이전 선택이 None임을 검증.
    assert result["historical_first_seen"] != "VERIFIED"


def local_excerpt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sources: list[pilot.RawSource] = []
    for name, raw in zip(
        ["apple-companyfacts.json", "apple-submissions.json"], excerpt(), strict=True
    ):
        (tmp_path / name).write_bytes(raw)
        sources.append(
            pilot.RawSource(
                name,
                "https://example.invalid/synthetic-test-only",
                hashlib.sha256(raw).hexdigest(),
                0,
            )
        )
    monkeypatch.setattr(pilot, "SOURCES", tuple(sources))


def test_local_integrity_gate_and_read_only_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_excerpt(tmp_path, monkeypatch)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = pilot.audit_local(tmp_path)
    assert len(cast(list[object], result["sources"])) == 2
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    (tmp_path / "apple-companyfacts.json").write_bytes(b"changed")
    with pytest.raises(DataIntegrityError, match="SOURCE_HASH_MISMATCH"):
        pilot.audit_local(tmp_path)


def test_missing_local_source_fails(tmp_path: Path) -> None:
    with pytest.raises(DataIntegrityError, match="SOURCE_NOT_READABLE"):
        pilot.audit_local(tmp_path)


def test_cli_preserves_existing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_excerpt(tmp_path, monkeypatch)
    output = tmp_path / "audit.json"
    monkeypatch.setattr("sys.argv", ["audit", "--data-dir", str(tmp_path), "--output", str(output)])
    assert pilot.main() == 0
    before = output.read_bytes()
    assert pilot.main() == 1
    assert output.read_bytes() == before


@pytest.mark.parametrize("instant", ["2024-11-04T16:00:00", "2024-11-04T16:00:00-05:00"])
def test_pilot_clock_does_not_guess_timezone(instant: str) -> None:
    with pytest.raises(DataIntegrityError, match="PILOT_REQUIRES_UTC"):
        pilot.utc_ms(instant)
