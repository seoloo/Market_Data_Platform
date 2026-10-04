"""Microsoft 실제 숫자 발췌 + 미래 비교값/별도 과거 metadata 경계 회귀."""

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.fundamental_pit import Concept, select_as_of
from auto_trading.research.sec_fundamentals import (
    SubmissionSource,
    normalize_companyfacts,
    read_filings,
)
from scripts import audit_microsoft_fundamentals as pilot

FIXTURE = Path(__file__).parents[1] / "fixtures/fundamental/microsoft-2024-excerpt.json"


def excerpt() -> tuple[bytes, bytes, bytes]:
    content = json.loads(FIXTURE.read_bytes())
    assert content["kind"] == "MANUALLY_EXTRACTED_REAL_FILING_ROWS_NOT_FULL_RAW_SNAPSHOT"
    return (
        json.dumps(content["companyfacts"]).encode(),
        json.dumps(content["submissions"]).encode(),
        json.dumps(content["history"]).encode(),
    )


def test_real_totals_calendar_fiscal_year_and_q4() -> None:
    result = pilot.audit_sample(*excerpt())
    assert result["fiscal_year_lengths_days"] == [365, 366]
    assert result["q4_amounts_usd"] == {
        pilot.REVENUE: "64727000000",
        pilot.OPERATING: "27925000000",
        pilot.CASHFLOW: "37195000000",
    }
    assert result["q4_source_accessions"] == [pilot.INTERIM, pilot.ANNUAL]
    assert result["filings_combined"] == 4
    assert result["historical_financial_rows_reviewed"] == 0
    assert result["historical_first_seen"] == "NOT_VERIFIED"
    assert result["strategy_result"] == "NOT_TESTED"
    assert result["economic_comparability"] != "ORGANIC_IMPROVEMENT_CERTIFIED"


@pytest.mark.leakage
def test_later_changed_comparative_never_replaces_original_before_available() -> None:
    facts, current, history = excerpt()
    content = json.loads(facts)
    content["facts"]["us-gaap"][pilot.REVENUE]["units"]["USD"][1]["val"] = 1
    concept = Concept(pilot.CIK, "us-gaap", pilot.REVENUE, "USD")
    result = normalize_companyfacts(
        json.dumps(content).encode(),
        concept=concept,
        submissions=[SubmissionSource(pilot.CIK, current), SubmissionSource(pilot.CIK, history)],
        reviews=pilot.reviews_for(pilot.REVENUE),
    )
    for as_of_ms in (pilot.ORIGINAL_AVAILABLE_MS, pilot.ANNUAL_AVAILABLE_MS - 1):
        original = select_as_of(
            result.values,
            concept=concept,
            start=pilot.PERIODS[0][0],
            end=pilot.PERIODS[0][1],
            as_of_ms=as_of_ms,
        )
        assert original is not None and original.value == 211915000000
        assert original.source_accessions == (pilot.ORIGINAL,)
    later = select_as_of(
        result.values,
        concept=concept,
        start=pilot.PERIODS[0][0],
        end=pilot.PERIODS[0][1],
        as_of_ms=pilot.ANNUAL_AVAILABLE_MS,
    )
    assert later is not None and later.value == 1
    assert later.source_accessions == (pilot.ANNUAL,)


def test_identical_amounts_still_require_original_accession() -> None:
    facts, current, history = excerpt()
    content = json.loads(facts)
    content["facts"]["us-gaap"][pilot.REVENUE]["units"]["USD"].pop(0)
    with pytest.raises(DataIntegrityError, match="VERSION_MISMATCH"):
        pilot.audit_sample(json.dumps(content).encode(), current, history)


def test_wrong_q4_input_fails_numeric_gate() -> None:
    facts, current, history = excerpt()
    with pytest.raises(DataIntegrityError, match="AMOUNT_OR_VERSION_MISMATCH"):
        pilot.audit_sample(facts.replace(b"245122000000", b"245122000001"), current, history)


def test_history_utc_crosses_filing_date_without_midnight_guess() -> None:
    _, _, history = excerpt()
    filing = read_filings([SubmissionSource(pilot.CIK, history)], cik=pilot.CIK)[0]
    assert filing.filed.isoformat() == "2020-07-30"
    assert filing.accepted_at_ms == pilot.utc_ms("2020-07-31T00:44:46Z")
    with pytest.raises(DataIntegrityError, match="SOURCE_CIK_MISMATCH"):
        read_filings([SubmissionSource("0000320193", history)], cik=pilot.CIK)


def test_unknown_historical_acceptance_is_not_invented() -> None:
    facts, current, history = excerpt()
    content = json.loads(history)
    del content["acceptanceDateTime"]
    result = pilot.audit_sample(facts, current, json.dumps(content).encode())
    assert result["unknown_acceptance_count"] == 1
    assert result["historical_financial_rows_reviewed"] == 0


def local_excerpt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sources = []
    for name, raw in zip(
        ("msft-companyfacts.json", "msft-submissions.json", "msft-submissions-001.json"),
        excerpt(),
        strict=True,
    ):
        (tmp_path / name).write_bytes(raw)
        sources.append(
            (
                name,
                "https://example.invalid/synthetic-test-only",
                hashlib.sha256(raw).hexdigest(),
                0,
            )
        )
    monkeypatch.setattr(pilot, "SOURCES", tuple(sources))


def test_integrity_and_read_only_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_excerpt(tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = pilot.audit_local(tmp_path)
    assert len(cast(list[object], result["sources"])) == 3
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    (tmp_path / "msft-submissions-001.json").write_bytes(b"changed")
    with pytest.raises(DataIntegrityError, match="SOURCE_HASH_MISMATCH"):
        pilot.audit_local(tmp_path)


def test_missing_source_fails(tmp_path: Path) -> None:
    with pytest.raises(DataIntegrityError, match="SOURCE_NOT_READABLE"):
        pilot.audit_local(tmp_path)


def test_cli_refuses_report_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_excerpt(tmp_path, monkeypatch)
    report = tmp_path / "audit.json"
    monkeypatch.setattr("sys.argv", ["audit", "--data-dir", str(tmp_path), "--output", str(report)])
    assert pilot.main() == 0
    before = report.read_bytes()
    assert pilot.main() == 1
    assert report.read_bytes() == before


@pytest.mark.parametrize(
    "instant", ["2024-07-31T16:00:00", "2024-07-31T16:00:00-04:00", "2024-07-31T20:00:00.0001Z"]
)
def test_no_guessed_timezone_or_truncated_precision(instant: str) -> None:
    with pytest.raises(DataIntegrityError, match="REQUIRES_UTC_MILLISECONDS"):
        pilot.utc_ms(instant)
