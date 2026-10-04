"""문서 131의 정규화 재무 자료 감사. I/O·시계·매매 신호 없는 연구 전용 계산.

접수 시각을 공개 시각으로 추정하지 않는다. available_at_ms와 회계 basis_id는
외부 원문/거래일 감사로 확인한 입력이어야 한다. SEC 원자료 파서는 아직 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from auto_trading.core.errors import CausalityError, DataIntegrityError

# SEC frames의 91±30일 / 365±30일과 같은 넓은 기간 sanity 검사.
# 회계 분기 식별을 대체하지 않으며 투자 성과로 조정하는 파라미터가 아니다.
QUARTER_MIN_DAYS, QUARTER_MAX_DAYS = 61, 121
YEAR_MIN_DAYS, YEAR_MAX_DAYS = 335, 395


@dataclass(frozen=True)
class Concept:
    cik: str
    taxonomy: str
    tag: str
    unit: str

    def __post_init__(self) -> None:
        if len(self.cik) != 10 or not self.cik.isascii() or not self.cik.isdigit():
            raise DataIntegrityError("INVALID_CIK")
        if any(not item.strip() for item in (self.taxonomy, self.tag, self.unit)):
            raise DataIntegrityError("EMPTY_CONCEPT_FIELD")


@dataclass(frozen=True)
class PeriodValue:
    concept: Concept
    start: date
    end: date
    value: Decimal
    accepted_at_ms: int
    available_at_ms: int
    basis_id: str
    source_accessions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise DataIntegrityError("REVERSED_PERIOD")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise DataIntegrityError("INVALID_DECIMAL_VALUE")
        _validate_ms(self.accepted_at_ms)
        _validate_ms(self.available_at_ms)
        if self.available_at_ms < self.accepted_at_ms:
            raise DataIntegrityError("AVAILABLE_BEFORE_ACCEPTED")
        try:
            accepted_date = datetime.fromtimestamp(self.accepted_at_ms // 1000, UTC).date()
        except (OverflowError, OSError, ValueError) as exc:
            raise DataIntegrityError("INVALID_EPOCH_MS") from exc
        if self.end > accepted_date:
            raise DataIntegrityError("PERIOD_END_AFTER_ACCEPTANCE")
        if not self.basis_id.strip() or not self.source_accessions:
            raise DataIntegrityError("MISSING_ACCOUNTING_PROVENANCE")
        if any(not accession.strip() for accession in self.source_accessions):
            raise DataIntegrityError("EMPTY_ACCESSION")


def _validate_ms(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DataIntegrityError("INVALID_EPOCH_MS")


def select_as_of(
    facts: Sequence[PeriodValue],
    *,
    concept: Concept,
    start: date,
    end: date,
    as_of_ms: int,
) -> PeriodValue | None:
    """당시 가용한 동일 기간의 최신 접수본. 결측은 None, 충돌은 중단."""
    _validate_ms(as_of_ms)
    if start > end:
        raise DataIntegrityError("REVERSED_QUERY_PERIOD")
    visible = [
        fact
        for fact in facts
        if fact.concept == concept
        and fact.start == start
        and fact.end == end
        and fact.available_at_ms <= as_of_ms
    ]
    if not visible:
        return None
    latest_ms = max(fact.accepted_at_ms for fact in visible)
    latest = [fact for fact in visible if fact.accepted_at_ms == latest_ms]
    if len({(fact.value, fact.basis_id) for fact in latest}) != 1:
        raise DataIntegrityError("CONFLICTING_VISIBLE_FACTS")
    return _combine(latest, start, end, latest[0].value)


def _visible(periods: Sequence[PeriodValue], as_of_ms: int) -> None:
    _validate_ms(as_of_ms)
    if any(period.available_at_ms > as_of_ms for period in periods):
        raise CausalityError("FUTURE_FINANCIAL_PERIOD")


def _same_series(periods: Sequence[PeriodValue]) -> None:
    first = periods[0]
    if any(item.concept != first.concept or item.basis_id != first.basis_id for item in periods):
        raise DataIntegrityError("INCOMPATIBLE_ACCOUNTING_SERIES")


def _combine(periods: Sequence[PeriodValue], start: date, end: date, value: Decimal) -> PeriodValue:
    first = periods[0]
    return PeriodValue(
        first.concept,
        start,
        end,
        value,
        max(item.accepted_at_ms for item in periods),
        max(item.available_at_ms for item in periods),
        first.basis_id,
        tuple(sorted({accession for item in periods for accession in item.source_accessions})),
    )


def _quarter_length(start: date, end: date) -> None:
    if not QUARTER_MIN_DAYS <= (end - start).days + 1 <= QUARTER_MAX_DAYS:
        raise DataIntegrityError("NOT_A_QUARTER_LENGTH")


def quarter_from_ytd(
    current: PeriodValue, previous: PeriodValue | None, *, as_of_ms: int
) -> PeriodValue:
    """확인된 동일 회계연도/기준의 누적 차분. Q1 외 이전 누적 결측은 거부."""
    periods = (current,) if previous is None else (current, previous)
    _visible(periods, as_of_ms)
    _same_series(periods)
    if previous is None:
        _quarter_length(current.start, current.end)
        return current
    if current.start != previous.start or previous.end >= current.end:
        raise DataIntegrityError("INCOMPATIBLE_YTD_PERIODS")
    start = previous.end + timedelta(days=1)
    _quarter_length(start, current.end)
    return _combine(periods, start, current.end, current.value - previous.value)


def trailing_year(quarters: Sequence[PeriodValue], *, as_of_ms: int) -> PeriodValue:
    """명시적으로 확인된 연속 4분기만 합산. 결측·중복·기간 중첩은 거부."""
    if len(quarters) != 4:
        raise DataIntegrityError("FOUR_QUARTERS_REQUIRED")
    _visible(quarters, as_of_ms)
    _same_series(quarters)
    ordered = sorted(quarters, key=lambda item: item.start)
    for quarter in ordered:
        _quarter_length(quarter.start, quarter.end)
    for previous, current in pairwise(ordered):
        if previous.end + timedelta(days=1) != current.start:
            raise DataIntegrityError("NONCONTIGUOUS_QUARTERS")
    start, end = ordered[0].start, ordered[-1].end
    if not YEAR_MIN_DAYS <= (end - start).days + 1 <= YEAR_MAX_DAYS:
        raise DataIntegrityError("NOT_A_YEAR_LENGTH")
    return _combine(ordered, start, end, sum((item.value for item in ordered), Decimal(0)))


def growth_pct(current: PeriodValue, previous: PeriodValue, *, as_of_ms: int) -> Decimal:
    """연속하는 확인된 TTM의 전년 대비. 0 이하 기저로 성장률 계산 금지."""
    _visible((current, previous), as_of_ms)
    _same_series((current, previous))
    for period in (current, previous):
        if not YEAR_MIN_DAYS <= (period.end - period.start).days + 1 <= YEAR_MAX_DAYS:
            raise DataIntegrityError("NOT_A_YEAR_LENGTH")
    if previous.end + timedelta(days=1) != current.start:
        raise DataIntegrityError("NONADJACENT_YEAR_PERIODS")
    if previous.value <= 0:
        raise DataIntegrityError("NONPOSITIVE_GROWTH_BASE")
    return (current.value / previous.value - Decimal(1)) * Decimal(100)


def margin_pct(profit: PeriodValue, revenue: PeriodValue, *, as_of_ms: int) -> Decimal:
    """동일 기업·통화·회계 기준·기간의 비율. 다른 태그 허용, 기간 혼합 금지."""
    _visible((profit, revenue), as_of_ms)
    if (
        profit.concept.cik != revenue.concept.cik
        or profit.concept.taxonomy != revenue.concept.taxonomy
        or profit.concept.unit != revenue.concept.unit
        or profit.basis_id != revenue.basis_id
        or profit.start != revenue.start
        or profit.end != revenue.end
    ):
        raise DataIntegrityError("MISALIGNED_MARGIN_PERIODS")
    if revenue.value <= 0:
        raise DataIntegrityError("NONPOSITIVE_REVENUE")
    return profit.value / revenue.value * Decimal(100)
