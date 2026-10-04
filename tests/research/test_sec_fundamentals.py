"""SEC 형태의 합성 JSON: 실제 기업 원자료 확보/회계 검증과 구분한다."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.fundamental_pit import (
    Concept,
    quarter_from_ytd,
    select_as_of,
    trailing_year,
)
from auto_trading.research.sec_fundamentals import (
    FactReview,
    SubmissionSource,
    normalize_companyfacts,
    read_filings,
)

CIK = "0000000001"
CONCEPT = Concept(CIK, "us-gaap", "Revenues", "USD")
ORIGINAL = "0000000001-24-000001"
AMENDED = "0000000001-24-000002"
START, END = date(2024, 1, 1), date(2024, 3, 31)


def ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def source(*, historical: bool = False, amended: bool = False) -> SubmissionSource:
    columns: dict[str, object] = {
        "accessionNumber": [ORIGINAL],
        "form": ["10-Q"],
        "filingDate": ["2024-05-01"],
        "acceptanceDateTime": ["2024-05-01T20:00:00Z"],
    }
    if amended:
        columns = {
            "accessionNumber": [AMENDED],
            "form": ["10-Q/A"],
            "filingDate": ["2024-08-01"],
            "acceptanceDateTime": ["2024-08-01T20:00:00Z"],
        }
    root = columns if historical else {"cik": 1, "filings": {"recent": columns}}
    return SubmissionSource(CIK, json.dumps(root).encode())


def row(*, amended: bool = False, value: object = 10) -> dict[str, object]:
    return {
        "start": "2024-01-01",
        "end": "2024-03-31",
        "val": value,
        "accn": AMENDED if amended else ORIGINAL,
        "form": "10-Q/A" if amended else "10-Q",
        "filed": "2024-08-01" if amended else "2024-05-01",
        # 결정 시각이나 회계 분기 식별로 사용할 수 없는 보조 필드.
        "fy": 2024,
        "fp": "Q1",
        "frame": "CY2024Q1",
    }


def raw(rows: list[dict[str, object]] | None = None) -> bytes:
    return json.dumps(
        {
            "cik": 1,
            "facts": {
                "us-gaap": {"Revenues": {"units": {"USD": [row()] if rows is None else rows}}}
            },
        }
    ).encode()


def review(*, amended: bool = False) -> FactReview:
    return FactReview(
        CONCEPT,
        AMENDED if amended else ORIGINAL,
        START,
        END,
        ms("2024-08-02" if amended else "2024-05-02"),
        "synthetic-reviewed-basis",
        "synthetic-reviewed-calendar-and-primary-filing",
    )


def test_normalized_amount_dates_and_raw_hashes() -> None:
    document, filing = raw(), source()
    result = normalize_companyfacts(
        document, concept=CONCEPT, submissions=[filing], reviews=[review()]
    )
    assert not result.concept_missing and not result.excluded
    value = result.values[0]
    assert value.value == Decimal(10) and value.start == START and value.end == END
    assert value.accepted_at_ms == ms("2024-05-01T20:00:00")
    assert value.available_at_ms == ms("2024-05-02")
    assert value.source_accessions == (ORIGINAL,)
    assert result.companyfacts_sha256 == hashlib.sha256(document).hexdigest()
    assert result.submission_sha256 == (hashlib.sha256(filing.raw).hexdigest(),)
    assert result.review_references == (review().reference,)


def test_decimal_not_binary_float_and_large_integer() -> None:
    document = raw([row(value=0)]).replace(b'"val": 0', b'"val": 0.123456789012345678901')
    result = normalize_companyfacts(
        document, concept=CONCEPT, submissions=[source()], reviews=[review()]
    )
    assert result.values[0].value == Decimal("0.123456789012345678901")
    result = normalize_companyfacts(
        raw([row(value=10**30 + 1)]), concept=CONCEPT, submissions=[source()], reviews=[review()]
    )
    assert result.values[0].value == Decimal(10**30 + 1)


def test_zero_is_retained_without_imputation() -> None:
    result = normalize_companyfacts(
        raw([row(value=0)]), concept=CONCEPT, submissions=[source()], reviews=[review()]
    )
    assert result.values[0].value == Decimal(0)


@pytest.mark.leakage
@given(value=st.integers(-1000000, 1000000))
def test_amendment_is_never_backdated(value: int) -> None:
    result = normalize_companyfacts(
        raw([row(), row(amended=True, value=value)]),
        concept=CONCEPT,
        submissions=[source(amended=True), source(historical=True)],
        reviews=[review(), review(amended=True)],
    )
    before = select_as_of(
        result.values, concept=CONCEPT, start=START, end=END, as_of_ms=ms("2024-06-01")
    )
    after = select_as_of(
        result.values, concept=CONCEPT, start=START, end=END, as_of_ms=ms("2024-08-02")
    )
    assert before is not None and before.value == Decimal(10)
    assert after is not None and after.value == Decimal(value)


@pytest.mark.leakage
def test_filing_day_is_not_acceptance_or_availability() -> None:
    result = normalize_companyfacts(
        raw(), concept=CONCEPT, submissions=[source()], reviews=[review()]
    )
    assert (
        select_as_of(
            result.values, concept=CONCEPT, start=START, end=END, as_of_ms=ms("2024-05-01T21:00:00")
        )
        is None
    )


def test_current_and_historical_sources_merge_deterministically() -> None:
    current, history = source(), source(historical=True)
    forward = read_filings([current, history], cik=CIK)
    assert forward == read_filings([history, current], cik=CIK)
    assert forward[0].source_sha256 == tuple(
        sorted({hashlib.sha256(s.raw).hexdigest() for s in [current, history]})
    )


@pytest.mark.parametrize("field", ["acceptanceDateTime", "missing_accession", "reviews"])
def test_missing_proof_excludes_not_zero_fills(field: str) -> None:
    filing = source(historical=True)
    columns = json.loads(filing.raw)
    if field == "acceptanceDateTime":
        del columns[field]
    filings = (
        [] if field == "missing_accession" else [replace(filing, raw=json.dumps(columns).encode())]
    )
    audits = [] if field == "reviews" else [review()]
    result = normalize_companyfacts(raw(), concept=CONCEPT, submissions=filings, reviews=audits)
    assert not result.values and len(result.excluded) == 1
    expected = (
        "PERIOD_BASIS_AVAILABILITY_NOT_REVIEWED"
        if field == "reviews"
        else "ACCEPTANCE_NOT_VERIFIED"
    )
    assert result.excluded[0].reason == expected


@pytest.mark.parametrize(
    "field,value", [("tag", "SalesRevenueNet"), ("unit", "EUR"), ("taxonomy", "ifrs-full")]
)
def test_missing_exact_concept_does_not_fallback(field: str, value: str) -> None:
    concept = replace(CONCEPT, **{field: value})
    result = normalize_companyfacts(raw(), concept=concept, submissions=[source()], reviews=[])
    assert result.concept_missing and not result.values and not result.excluded


def test_instant_value_is_not_treated_as_quarter() -> None:
    item = row()
    del item["start"]
    result = normalize_companyfacts(
        raw([item]), concept=CONCEPT, submissions=[source()], reviews=[review()]
    )
    assert not result.values and result.excluded[0].reason == "INSTANT_FACT_NOT_DURATION"


@pytest.mark.parametrize("field,value", [("form", "10-K"), ("filed", "2024-05-03")])
def test_fact_and_submission_mismatch_raises(field: str, value: str) -> None:
    with pytest.raises(DataIntegrityError, match="SEC_FACT_FILING_MISMATCH"):
        normalize_companyfacts(
            raw([row() | {field: value}]),
            concept=CONCEPT,
            submissions=[source()],
            reviews=[review()],
        )


@pytest.mark.parametrize("value", [True, "10", None])
def test_invalid_amount_raises(value: object) -> None:
    with pytest.raises(DataIntegrityError, match="SEC_INVALID_FACT_VALUE"):
        normalize_companyfacts(
            raw([row(value=value)]), concept=CONCEPT, submissions=[source()], reviews=[review()]
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_rejected(value: str) -> None:
    document = raw().replace(b'"val": 10', f'"val": {value}'.encode())
    with pytest.raises(DataIntegrityError, match="SEC_NONFINITE_JSON_NUMBER"):
        normalize_companyfacts(document, concept=CONCEPT, submissions=[], reviews=[])


@pytest.mark.parametrize(
    "document,error",
    [
        (b'{"cik": 1, "cik": 2}', "DUPLICATE_JSON_KEY"),
        (b"not json", "INVALID_JSON"),
        (b"[]", "EXPECTED_OBJECT"),
    ],
)
def test_damaged_json_fails_closed(document: bytes, error: str) -> None:
    with pytest.raises(DataIntegrityError, match=error):
        normalize_companyfacts(document, concept=CONCEPT, submissions=[], reviews=[])


@pytest.mark.parametrize("value", [True, 2, "../1", "00000000001"])
def test_company_identity_is_verified(value: object) -> None:
    document = json.loads(raw())
    document["cik"] = value
    with pytest.raises(DataIntegrityError, match=r"SEC_(INVALID_CIK|CIK_MISMATCH)"):
        normalize_companyfacts(
            json.dumps(document).encode(), concept=CONCEPT, submissions=[], reviews=[]
        )


def test_historical_source_binding_is_verified() -> None:
    with pytest.raises(DataIntegrityError, match="SEC_SOURCE_CIK_MISMATCH"):
        read_filings([replace(source(historical=True), cik="0000000002")], cik=CIK)


@pytest.mark.parametrize(
    "value",
    ["2024-05-01T20:00:00", "2024-05-01T16:00:00-04:00", "2024-05-01", "2024-02-30T20:00:00Z"],
)
def test_naive_or_unsupported_acceptance_is_not_guessed(value: str) -> None:
    document = json.loads(source(historical=True).raw)
    document["acceptanceDateTime"] = [value]
    with pytest.raises(
        DataIntegrityError, match=r"SEC_(ACCEPTANCE_REQUIRES_EXPLICIT_UTC|INVALID_ACCEPTANCE)"
    ):
        read_filings([SubmissionSource(CIK, json.dumps(document).encode())], cik=CIK)


def test_explicit_zero_utc_offset_is_supported() -> None:
    document = source().raw.replace(b"20:00:00Z", b"20:00:00+00:00")
    assert read_filings([SubmissionSource(CIK, document)], cik=CIK)[0].accepted_at_ms == ms(
        "2024-05-01T20:00:00"
    )


@pytest.mark.parametrize(
    "fraction,expected_ms", [("000", 0), ("1", 100), ("12", 120), ("123", 123)]
)
def test_sec_explicit_utc_milliseconds_preserved_exactly(fraction: str, expected_ms: int) -> None:
    document = source().raw.replace(b"20:00:00Z", f"20:00:00.{fraction}Z".encode())
    assert (
        read_filings([SubmissionSource(CIK, document)], cik=CIK)[0].accepted_at_ms
        == ms("2024-05-01T20:00:00") + expected_ms
    )


@pytest.mark.parametrize(
    "time",
    ["2024-05-01T20:00:00.1234Z", "2024-05-01T20:00:00.123", "2024-05-01T20:00:00.123-04:00"],
)
def test_unsupported_fraction_or_timezone_is_not_truncated_or_guessed(time: str) -> None:
    document = source().raw.replace(b"2024-05-01T20:00:00Z", time.encode())
    with pytest.raises(DataIntegrityError, match="ACCEPTANCE_REQUIRES_EXPLICIT_UTC"):
        read_filings([SubmissionSource(CIK, document)], cik=CIK)


def test_column_lengths_and_duplicate_filing_conflict() -> None:
    document = json.loads(source(historical=True).raw)
    document["form"] = []
    with pytest.raises(DataIntegrityError, match="COLUMN_LENGTH_MISMATCH"):
        read_filings([SubmissionSource(CIK, json.dumps(document).encode())], cik=CIK)
    with pytest.raises(DataIntegrityError, match="CONFLICTING_SUBMISSIONS"):
        read_filings(
            [source(), replace(source(), raw=source().raw.replace(b"20:00:00Z", b"21:00:00Z"))],
            cik=CIK,
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda item: replace(item, reference=""),
        lambda item: replace(item, basis_id=""),
        lambda item: replace(item, available_at_ms=-1),
        lambda item: replace(item, accession="bad-id"),
        lambda item: replace(item, start=date(2024, 4, 1)),
    ],
)
def test_invalid_review_contract_rejected(change: Callable[[FactReview], FactReview]) -> None:
    with pytest.raises(DataIntegrityError):
        change(review())


def test_review_cannot_precede_acceptance() -> None:
    with pytest.raises(DataIntegrityError, match="AVAILABLE_BEFORE_ACCEPTED"):
        normalize_companyfacts(
            raw(),
            concept=CONCEPT,
            submissions=[source()],
            reviews=[replace(review(), available_at_ms=ms("2024-05-01"))],
        )


def test_reviews_require_exact_concept_and_unique_binding() -> None:
    with pytest.raises(DataIntegrityError, match="REVIEW_CONCEPT_MISMATCH"):
        normalize_companyfacts(
            raw(),
            concept=CONCEPT,
            submissions=[source()],
            reviews=[replace(review(), concept=replace(CONCEPT, unit="EUR"))],
        )
    with pytest.raises(DataIntegrityError, match="DUPLICATE_REVIEW"):
        normalize_companyfacts(
            raw(), concept=CONCEPT, submissions=[source()], reviews=[review(), review()]
        )


def test_unsupported_form_excluded() -> None:
    filing = replace(source(), raw=source().raw.replace(b"10-Q", b"8-K"))
    result = normalize_companyfacts(
        raw([row() | {"form": "8-K"}]), concept=CONCEPT, submissions=[filing], reviews=[review()]
    )
    assert not result.values and result.excluded[0].reason == "FORM_NOT_IN_RESEARCH_SCOPE"


def test_unsupported_taxonomy_rejected() -> None:
    with pytest.raises(DataIntegrityError, match="UNSUPPORTED_FINANCIAL_TAXONOMY"):
        normalize_companyfacts(
            raw(), concept=replace(CONCEPT, taxonomy="custom"), submissions=[], reviews=[]
        )


@pytest.mark.leakage
def test_future_fiscal_labels_do_not_determine_period_or_time() -> None:
    normal = normalize_companyfacts(
        raw(), concept=CONCEPT, submissions=[source()], reviews=[review()]
    )
    changed = normalize_companyfacts(
        raw([row() | {"fy": 2099, "fp": "FY", "frame": "CY2099"}]),
        concept=CONCEPT,
        submissions=[source()],
        reviews=[review()],
    )
    assert normal.values == changed.values


def test_output_order_is_deterministic_but_raw_hash_tracks_original_bytes() -> None:
    first = normalize_companyfacts(
        raw([row(), row(amended=True)]),
        concept=CONCEPT,
        submissions=[source(), source(amended=True)],
        reviews=[review(), review(amended=True)],
    )
    second = normalize_companyfacts(
        raw([row(amended=True), row()]),
        concept=CONCEPT,
        submissions=[source(amended=True), source()],
        reviews=[review(amended=True), review()],
    )
    assert first.values == second.values and first.submission_sha256 == second.submission_sha256
    assert first.companyfacts_sha256 != second.companyfacts_sha256


def test_missing_acceptance_can_be_completed_only_by_same_verified_filing() -> None:
    unknown = replace(source(), raw=source().raw.replace(b"2024-05-01T20:00:00Z", b""))
    result = read_filings([unknown, source()], cik=CIK)
    assert result[0].accepted_at_ms == ms("2024-05-01T20:00:00")
    assert len(result[0].source_sha256) == 2


def test_root_submission_company_must_match_manifest() -> None:
    with pytest.raises(DataIntegrityError, match="SEC_CIK_MISMATCH"):
        read_filings(
            [replace(source(), raw=source().raw.replace(b'"cik": 1', b'"cik": 2'))], cik=CIK
        )


def test_conflicting_values_are_not_silently_selected() -> None:
    result = normalize_companyfacts(
        raw([row(), row(value=99)]), concept=CONCEPT, submissions=[source()], reviews=[review()]
    )
    with pytest.raises(DataIntegrityError, match="CONFLICTING_VISIBLE_FACTS"):
        select_as_of(
            result.values, concept=CONCEPT, start=START, end=END, as_of_ms=ms("2024-06-01")
        )


@pytest.mark.leakage
def test_sec_json_to_ytd_quarters_to_ttm_preserves_provenance_and_causality() -> None:
    concept = replace(CONCEPT, tag="NetCashProvidedByUsedInOperatingActivities")
    periods = [
        ("2024-03-31", "2024-05-01", 10),
        ("2024-06-30", "2024-08-01", 25),
        ("2024-09-30", "2024-11-01", 40),
        ("2024-12-31", "2025-02-01", 60),
    ]
    rows: list[dict[str, object]] = []
    sources: list[SubmissionSource] = []
    audits: list[FactReview] = []
    for index, (end, filed, value) in enumerate(periods, 1):
        accession = f"0000000001-24-{index:06d}"
        form = "10-K" if index == 4 else "10-Q"
        rows.append(
            row(value=value) | {"end": end, "filed": filed, "accn": accession, "form": form}
        )
        sources.append(
            SubmissionSource(
                CIK,
                json.dumps(
                    {
                        "accessionNumber": [accession],
                        "form": [form],
                        "filingDate": [filed],
                        "acceptanceDateTime": [f"{filed}T20:00:00Z"],
                    }
                ).encode(),
            )
        )
        audits.append(
            FactReview(
                concept,
                accession,
                START,
                date.fromisoformat(end),
                ms(f"{filed}T21:00:00"),
                "synthetic-compatible-fiscal-year",
                "synthetic-availability-not-a-trading-calendar",
            )
        )
    document = raw(rows).replace(b"Revenues", concept.tag.encode())
    result = normalize_companyfacts(document, concept=concept, submissions=sources, reviews=audits)
    assert len(result.values) == 4 and not result.excluded
    as_of_ms = ms("2025-03-01")
    quarters = [
        quarter_from_ytd(current, result.values[index - 1] if index else None, as_of_ms=as_of_ms)
        for index, current in enumerate(result.values)
    ]
    total = trailing_year(quarters, as_of_ms=as_of_ms)
    assert total.value == Decimal(60)  # 누적값을 더한 135가 아님.
    assert [q.value for q in quarters] == [Decimal(10), Decimal(15), Decimal(15), Decimal(20)]
    assert total.source_accessions == tuple(f"0000000001-24-{i:06d}" for i in range(1, 5))
    assert total.available_at_ms == ms("2025-02-01T21:00:00")
    assert (
        select_as_of(
            result.values,
            concept=concept,
            start=START,
            end=date(2024, 9, 30),
            as_of_ms=ms("2024-10-01"),
        )
        is None
    )
