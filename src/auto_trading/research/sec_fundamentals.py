"""SEC JSON의 오프라인 감사 연결. 다운로드·달력 추정·투자 신호 없음.

현행 companyfacts의 filed/fy/fp/frame을 과거 가용 시각으로 쓰지 않는다.
접수 시각은 submissions에서, 가용 시각/회계 기준은 원문 대조 입력에서 받는다.
이 파서의 성공은 원자료의 역사적 완전성이나 회계 비교 가능성 인증이 아니다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.fundamental_pit import Concept, PeriodValue

ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}\Z")
FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})


@dataclass(frozen=True)
class SubmissionSource:
    """별도 과거 JSON에는 CIK가 없을 수 있어 수집 manifest의 기업 연결이 필요."""

    cik: str
    raw: bytes


@dataclass(frozen=True)
class FactReview:
    """정확한 공시/태그/단위/기간에 대한 외부 원문·가용 시각 감사 입력."""

    concept: Concept
    accession: str
    start: date
    end: date
    available_at_ms: int
    basis_id: str
    reference: str

    def __post_init__(self) -> None:
        _accession(self.accession)
        if not self.reference.strip():
            raise DataIntegrityError("SEC_MISSING_REVIEW_REFERENCE")
        # 외부 감사 입력 자체의 기본 검증. 실제 접수 시각 검사는 연결 후 수행.
        PeriodValue(
            self.concept,
            self.start,
            self.end,
            Decimal(0),
            self.available_at_ms,
            self.available_at_ms,
            self.basis_id,
            (self.accession,),
        )


@dataclass(frozen=True)
class FilingMetadata:
    accession: str
    form: str
    filed: date
    accepted_at_ms: int | None
    source_sha256: tuple[str, ...]


@dataclass(frozen=True)
class ExcludedFact:
    accession: str
    reason: str


@dataclass(frozen=True)
class SecImportResult:
    values: tuple[PeriodValue, ...]
    excluded: tuple[ExcludedFact, ...]
    companyfacts_sha256: str
    submission_sha256: tuple[str, ...]
    review_references: tuple[str, ...]
    concept_missing: bool


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DataIntegrityError("SEC_EXPECTED_OBJECT")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise DataIntegrityError("SEC_EXPECTED_ARRAY")
    return cast(list[object], value)


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataIntegrityError("SEC_EXPECTED_NONEMPTY_STRING")
    return value


def _accession(value: object) -> str:
    text = _string(value)
    if ACCESSION.fullmatch(text) is None:
        raise DataIntegrityError("SEC_INVALID_ACCESSION")
    return text


def _date(value: object) -> date:
    text = _string(value)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text) is None:
        raise DataIntegrityError("SEC_INVALID_DATE")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DataIntegrityError("SEC_INVALID_DATE") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DataIntegrityError("SEC_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise DataIntegrityError("SEC_NONFINITE_JSON_NUMBER")


def _decode(raw: bytes) -> dict[str, object]:
    try:
        # 정수는 유지하고 소수 리터럴은 처음부터 Decimal로 읽는다.
        value: object = json.loads(
            raw,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataIntegrityError("SEC_INVALID_JSON") from exc
    return _object(value)


def _check_cik(value: object, cik: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise DataIntegrityError("SEC_INVALID_CIK")
    text = str(value)
    if not text.isascii() or not text.isdigit() or len(text) > 10:
        raise DataIntegrityError("SEC_INVALID_CIK")
    if text.zfill(10) != cik:
        raise DataIntegrityError("SEC_CIK_MISMATCH")


def _acceptance(value: object) -> int | None:
    if value is None or value == "":
        return None
    text = _string(value)
    # 시간대 없는 시각을 ET/UTC로 추정하지 않는다. UTC 명시만 지원한다.
    if (
        re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|\+00:00)", text)
        is None
    ):
        raise DataIntegrityError("SEC_ACCEPTANCE_REQUIRES_EXPLICIT_UTC")
    try:
        accepted = datetime.fromisoformat(text.replace("Z", "+00:00"))
        result = int(accepted.timestamp() * 1000)
    except (ValueError, OverflowError, OSError) as exc:
        raise DataIntegrityError("SEC_INVALID_ACCEPTANCE") from exc
    if result < 0:
        raise DataIntegrityError("SEC_INVALID_ACCEPTANCE")
    return result


def read_filings(sources: Sequence[SubmissionSource], *, cik: str) -> tuple[FilingMetadata, ...]:
    """현재 filings.recent + 호출자가 별도 제공한 과거 열 배열. 자동 수집 안 함."""
    Concept(cik, "us-gaap", "identity-check", "USD")
    records: dict[str, FilingMetadata] = {}
    for source in sources:
        if source.cik != cik:
            raise DataIntegrityError("SEC_SOURCE_CIK_MISMATCH")
        root = _decode(source.raw)
        if "filings" in root:
            _check_cik(root.get("cik"), cik)
            columns = _object(_object(root["filings"]).get("recent"))
        else:
            # 기업 연결은 source.cik manifest가 책임진다. 본문에 있으면 교차 검사.
            if "cik" in root:
                _check_cik(root["cik"], cik)
            columns = root
        accessions = _array(columns.get("accessionNumber"))
        forms = _array(columns.get("form"))
        dates = _array(columns.get("filingDate"))
        times = (
            _array(columns["acceptanceDateTime"])
            if "acceptanceDateTime" in columns
            else [None] * len(accessions)
        )
        if any(len(items) != len(accessions) for items in (forms, dates, times)):
            raise DataIntegrityError("SEC_SUBMISSION_COLUMN_LENGTH_MISMATCH")
        digest = hashlib.sha256(source.raw).hexdigest()
        for accession, form, filed, accepted in zip(accessions, forms, dates, times, strict=True):
            record = FilingMetadata(
                _accession(accession), _string(form), _date(filed), _acceptance(accepted), (digest,)
            )
            previous = records.get(record.accession)
            if previous is not None:
                known_times = {
                    t for t in (previous.accepted_at_ms, record.accepted_at_ms) if t is not None
                }
                if (
                    previous.form != record.form
                    or previous.filed != record.filed
                    or len(known_times) > 1
                ):
                    raise DataIntegrityError("SEC_CONFLICTING_SUBMISSIONS")
                record = FilingMetadata(
                    record.accession,
                    record.form,
                    record.filed,
                    next(iter(known_times)) if known_times else None,
                    tuple(sorted(set(previous.source_sha256 + record.source_sha256))),
                )
            records[record.accession] = record
    return tuple(records[key] for key in sorted(records))


def normalize_companyfacts(
    raw: bytes,
    *,
    concept: Concept,
    submissions: Sequence[SubmissionSource],
    reviews: Sequence[FactReview],
) -> SecImportResult:
    """정확히 일치하는 duration 값만 연결. 미감사 값은 이유와 함께 제외, 0은 보존."""
    if concept.taxonomy not in {"us-gaap", "ifrs-full"}:
        raise DataIntegrityError("SEC_UNSUPPORTED_FINANCIAL_TAXONOMY")
    root = _decode(raw)
    _check_cik(root.get("cik"), concept.cik)
    filings = {item.accession: item for item in read_filings(submissions, cik=concept.cik)}
    review_map: dict[tuple[str, date, date], FactReview] = {}
    for review in reviews:
        if review.concept != concept:
            raise DataIntegrityError("SEC_REVIEW_CONCEPT_MISMATCH")
        _accession(review.accession)
        # 실제 접수 시각 검사는 연결 후 수행. 미사용 중복 감사도 거부한다.
        if not review.reference.strip():
            raise DataIntegrityError("SEC_MISSING_REVIEW_REFERENCE")
        key = (review.accession, review.start, review.end)
        if key in review_map:
            raise DataIntegrityError("SEC_DUPLICATE_REVIEW")
        review_map[key] = review
    facts = _object(root.get("facts"))
    taxonomy = _object(facts[concept.taxonomy]) if concept.taxonomy in facts else {}
    tag = _object(taxonomy[concept.tag]) if concept.tag in taxonomy else {}
    units = _object(tag.get("units")) if tag else {}
    rows = _array(units[concept.unit]) if concept.unit in units else []
    values: list[PeriodValue] = []
    excluded: list[ExcludedFact] = []
    references: set[str] = set()
    for item in rows:
        row = _object(item)
        accession = _accession(row.get("accn"))
        if "start" not in row:
            excluded.append(ExcludedFact(accession, "INSTANT_FACT_NOT_DURATION"))
            continue
        start, end = _date(row["start"]), _date(row.get("end"))
        amount = row.get("val")
        if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
            raise DataIntegrityError("SEC_INVALID_FACT_VALUE")
        filing = filings.get(accession)
        matched_review = review_map.get((accession, start, end))
        if filing is None or filing.accepted_at_ms is None:
            excluded.append(ExcludedFact(accession, "ACCEPTANCE_NOT_VERIFIED"))
        elif filing.form not in FORMS:
            excluded.append(ExcludedFact(accession, "FORM_NOT_IN_RESEARCH_SCOPE"))
        elif filing.form != _string(row.get("form")) or filing.filed != _date(row.get("filed")):
            raise DataIntegrityError("SEC_FACT_FILING_MISMATCH")
        elif matched_review is None:
            excluded.append(ExcludedFact(accession, "PERIOD_BASIS_AVAILABILITY_NOT_REVIEWED"))
        else:
            values.append(
                PeriodValue(
                    concept,
                    start,
                    end,
                    Decimal(amount),
                    filing.accepted_at_ms,
                    matched_review.available_at_ms,
                    matched_review.basis_id,
                    (accession,),
                )
            )
            references.add(matched_review.reference)
    values.sort(
        key=lambda v: (
            v.start,
            v.end,
            v.accepted_at_ms,
            v.available_at_ms,
            v.value,
            v.basis_id,
            v.source_accessions,
        )
    )
    return SecImportResult(
        tuple(values),
        tuple(sorted(excluded, key=lambda item: (item.accession, item.reason))),
        hashlib.sha256(raw).hexdigest(),
        tuple(sorted({hashlib.sha256(source.raw).hexdigest() for source in submissions})),
        tuple(sorted(references)),
        concept.unit not in units,
    )
