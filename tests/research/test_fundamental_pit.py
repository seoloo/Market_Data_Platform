"""합성 회계 입력만 사용. 실제 기업 커버리지나 수익성을 주장하지 않는다."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research.fundamental_pit import (
    Concept,
    PeriodValue,
    growth_pct,
    margin_pct,
    quarter_from_ytd,
    select_as_of,
    trailing_year,
)

CONCEPT = Concept("0000000001", "us-gaap", "Revenues", "USD")
AS_OF_MS = 1_800_000_000_000


def ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def fact(
    value: str = "10",
    start: str = "2024-01-01",
    end: str = "2024-03-31",
    accepted: str = "2024-05-01",
    available: str = "2024-05-02",
    accession: str = "synthetic-original",
) -> PeriodValue:
    return PeriodValue(
        CONCEPT,
        date.fromisoformat(start),
        date.fromisoformat(end),
        Decimal(value),
        ms(accepted),
        ms(available),
        "synthetic-reviewed-basis-v1",
        (accession,),
    )


def select(facts: list[PeriodValue], as_of_ms: int) -> PeriodValue | None:
    return select_as_of(
        facts, concept=CONCEPT, start=date(2024, 1, 1), end=date(2024, 3, 31), as_of_ms=as_of_ms
    )


def test_acceptance_is_not_availability() -> None:
    assert select([fact()], ms("2024-05-01")) is None
    assert select([fact()], ms("2024-05-02")) == fact()


@pytest.mark.leakage
@given(value=st.integers(-1_000_000, 1_000_000))
def test_future_amendment_never_changes_earlier_selection(value: int) -> None:
    original = fact()
    revised = fact(str(value), accepted="2024-08-01", available="2024-08-02", accession="amended")
    assert select([original, revised], ms("2024-06-01")) == original
    assert select([revised, original], ms("2024-06-01")) == original
    assert select([revised, original], ms("2024-08-02")) == revised


def test_missing_and_zero_are_different() -> None:
    assert select([], AS_OF_MS) is None
    selected = select([fact("0")], AS_OF_MS)
    assert selected is not None and selected.value == Decimal(0)


@pytest.mark.parametrize("field,value", [("unit", "EUR"), ("tag", "Other"), ("cik", "0000000002")])
def test_other_series_are_not_fallback(field: str, value: str) -> None:
    item = replace(fact(), concept=replace(CONCEPT, **{field: value}))
    assert select([item], AS_OF_MS) is None


@pytest.mark.parametrize(
    "conflicting", [replace(fact(), value=Decimal(99)), replace(fact(), basis_id="other-basis")]
)
def test_conflicting_latest_values_fail_closed(conflicting: PeriodValue) -> None:
    with pytest.raises(DataIntegrityError, match="CONFLICTING_VISIBLE_FACTS"):
        select([fact(), conflicting], AS_OF_MS)


def test_duplicate_equal_values_merge_provenance_deterministically() -> None:
    original = fact(accession="a")
    duplicate = fact(accession="b")
    selected = select([original, duplicate], AS_OF_MS)
    assert selected is not None and selected.source_accessions == ("a", "b")
    assert selected == select([duplicate, original], AS_OF_MS)


def test_latest_accepted_not_latest_backfill_wins() -> None:
    original = fact(available="2024-09-01")
    revised = fact("20", accepted="2024-08-01", available="2024-08-02", accession="amended")
    assert select([original, revised], AS_OF_MS) == revised


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_values_rejected(value: str) -> None:
    with pytest.raises(DataIntegrityError, match="INVALID_DECIMAL_VALUE"):
        fact(value)


@pytest.mark.parametrize("value", [-1, True])
def test_invalid_ms_rejected(value: int) -> None:
    with pytest.raises(DataIntegrityError, match="INVALID_EPOCH_MS"):
        replace(fact(), accepted_at_ms=value)


def test_availability_before_acceptance_rejected() -> None:
    with pytest.raises(DataIntegrityError, match="AVAILABLE_BEFORE_ACCEPTED"):
        fact(available="2024-04-01")


@pytest.mark.parametrize("cik", ["1", "abcdefghij", "１２３４５６７８９０"])
def test_invalid_cik_rejected(cik: str) -> None:
    with pytest.raises(DataIntegrityError, match="INVALID_CIK"):
        replace(CONCEPT, cik=cik)


def test_missing_basis_and_sources_rejected() -> None:
    with pytest.raises(DataIntegrityError, match="MISSING_ACCOUNTING_PROVENANCE"):
        replace(fact(), basis_id="")
    with pytest.raises(DataIntegrityError, match="MISSING_ACCOUNTING_PROVENANCE"):
        replace(fact(), source_accessions=())


def quarters() -> list[PeriodValue]:
    q1 = fact()
    half = fact(
        "25", end="2024-06-30", accepted="2024-08-01", available="2024-08-02", accession="h1"
    )
    nine = fact(
        "40", end="2024-09-30", accepted="2024-11-01", available="2024-11-02", accession="m9"
    )
    year = fact(
        "60", end="2024-12-31", accepted="2025-02-01", available="2025-02-02", accession="fy"
    )
    return [
        quarter_from_ytd(q1, None, as_of_ms=AS_OF_MS),
        quarter_from_ytd(half, q1, as_of_ms=AS_OF_MS),
        quarter_from_ytd(nine, half, as_of_ms=AS_OF_MS),
        quarter_from_ytd(year, nine, as_of_ms=AS_OF_MS),
    ]


def test_ytd_and_q4_hand_calculation() -> None:
    items = quarters()
    assert [item.value for item in items] == [Decimal(10), Decimal(15), Decimal(15), Decimal(20)]
    assert items[-1].start == date(2024, 10, 1)
    assert items[-1].source_accessions == ("fy", "m9")
    ttm = trailing_year(items, as_of_ms=AS_OF_MS)
    assert ttm.value == Decimal(60)  # 누적값 10+25+40+60=135를 합산하면 안 됨
    assert (ttm.end - ttm.start).days + 1 == 366
    assert ttm.source_accessions == ("fy", "h1", "m9", "synthetic-original")
    assert ttm.available_at_ms == ms("2025-02-02")


def test_half_year_cannot_be_treated_as_first_quarter() -> None:
    with pytest.raises(DataIntegrityError, match="NOT_A_QUARTER_LENGTH"):
        quarter_from_ytd(
            fact(end="2024-06-30", accepted="2024-08-01", available="2024-08-02"),
            None,
            as_of_ms=AS_OF_MS,
        )


@pytest.mark.parametrize(
    "other",
    [replace(fact(), basis_id="unreviewed"), replace(fact(), concept=replace(CONCEPT, unit="EUR"))],
)
def test_ytd_basis_or_unit_mix_rejected(other: PeriodValue) -> None:
    half = fact("25", end="2024-06-30", accepted="2024-08-01", available="2024-08-02")
    with pytest.raises(DataIntegrityError, match="INCOMPATIBLE_ACCOUNTING_SERIES"):
        quarter_from_ytd(half, other, as_of_ms=AS_OF_MS)


def test_ytd_different_fiscal_start_rejected() -> None:
    with pytest.raises(DataIntegrityError, match="INCOMPATIBLE_YTD_PERIODS"):
        quarter_from_ytd(
            fact(
                "25",
                start="2024-02-01",
                end="2024-06-30",
                accepted="2024-08-01",
                available="2024-08-02",
            ),
            fact(),
            as_of_ms=AS_OF_MS,
        )


def test_period_not_yet_ended_cannot_be_a_filed_actual() -> None:
    with pytest.raises(DataIntegrityError, match="PERIOD_END_AFTER_ACCEPTANCE"):
        fact(end="2024-06-30")


@pytest.mark.leakage
def test_derived_values_reject_future_inputs() -> None:
    with pytest.raises(CausalityError):
        quarter_from_ytd(fact(), None, as_of_ms=ms("2024-05-01"))
    with pytest.raises(CausalityError):
        trailing_year(quarters(), as_of_ms=ms("2025-02-01"))


@pytest.mark.parametrize("count", [0, 1, 3, 5])
def test_exactly_four_quarters_required(count: int) -> None:
    items = (quarters() * 2)[:count]
    with pytest.raises(DataIntegrityError, match="FOUR_QUARTERS_REQUIRED"):
        trailing_year(items, as_of_ms=AS_OF_MS)


@pytest.mark.parametrize("start", [date(2024, 4, 2), date(2024, 3, 31)])
def test_ttm_gap_or_overlap_rejected(start: date) -> None:
    items = quarters()
    items[1] = replace(items[1], start=start)
    with pytest.raises(DataIntegrityError, match="NONCONTIGUOUS_QUARTERS"):
        trailing_year(items, as_of_ms=AS_OF_MS)


def test_growth_and_margin_hand_calculation() -> None:
    current = trailing_year(quarters(), as_of_ms=AS_OF_MS)
    previous = fact("50", start="2023-01-01", end="2023-12-31")
    assert growth_pct(current, previous, as_of_ms=AS_OF_MS) == Decimal(20)
    profit = replace(
        current, concept=replace(CONCEPT, tag="OperatingIncomeLoss"), value=Decimal(12)
    )
    assert margin_pct(profit, current, as_of_ms=AS_OF_MS) == Decimal(20)


@pytest.mark.parametrize("value", ["0", "-10"])
def test_nonpositive_denominators_not_converted_to_ratios(value: str) -> None:
    current = trailing_year(quarters(), as_of_ms=AS_OF_MS)
    previous = fact(value, start="2023-01-01", end="2023-12-31")
    with pytest.raises(DataIntegrityError, match="NONPOSITIVE_GROWTH_BASE"):
        growth_pct(current, previous, as_of_ms=AS_OF_MS)
    with pytest.raises(DataIntegrityError, match="NONPOSITIVE_REVENUE"):
        margin_pct(current, replace(current, value=Decimal(value)), as_of_ms=AS_OF_MS)


def test_mismatched_margin_period_rejected() -> None:
    with pytest.raises(DataIntegrityError, match="MISALIGNED_MARGIN_PERIODS"):
        margin_pct(fact(), fact(end="2024-03-30"), as_of_ms=AS_OF_MS)


@pytest.mark.leakage
def test_ratios_reject_future_inputs() -> None:
    current = trailing_year(quarters(), as_of_ms=AS_OF_MS)
    previous = fact("50", start="2023-01-01", end="2023-12-31")
    with pytest.raises(CausalityError):
        growth_pct(current, previous, as_of_ms=ms("2025-02-01"))
    with pytest.raises(CausalityError):
        margin_pct(current, current, as_of_ms=ms("2025-02-01"))
