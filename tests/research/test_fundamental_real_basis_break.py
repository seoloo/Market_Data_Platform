"""문서 136 실제 사례: 당시 보고값·나중 재작성·다른 정의의 주석을 혼합하지 않는다."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.fundamental_pit import Concept, PeriodValue, margin_pct, select_as_of
from auto_trading.research.sec_fundamentals import (
    FactReview,
    SubmissionSource,
    normalize_companyfacts,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/fundamental/microsoft-2017-2018-basis-excerpt.json"
CIK = "0000789019"
ORIGINAL, RESTATED = "0001564590-17-014900", "0001564590-18-019062"
START, END = date(2016, 7, 1), date(2017, 6, 30)
OLD_REVENUE = "SalesRevenueNet"
NEW_REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"
OPERATING = "OperatingIncomeLoss"
CFO = "NetCashProvidedByUsedInOperatingActivities"


def ms(value: str) -> int:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    delta = instant - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


# 테스트 제어 시각. 실제 최초 공개 시각이나 전략의 지연 정책으로 쓰지 않는다.
OLD_CUTOFF_MS, NEW_CUTOFF_MS = ms("2017-08-04T00:00:00Z"), ms("2018-08-07T00:00:00Z")


def normalized(tag: str, *, changed_later: bool = False) -> tuple[PeriodValue, ...]:
    fixture = json.loads(FIXTURE.read_bytes())
    if changed_later:
        fixture["companyfacts"]["facts"]["us-gaap"][OPERATING]["units"]["USD"][1]["val"] = 1
    concept = Concept(CIK, "us-gaap", tag, "USD")
    reviews = [
        FactReview(
            concept,
            row["accn"],
            START,
            END,
            OLD_CUTOFF_MS if row["accn"] == ORIGINAL else NEW_CUTOFF_MS,
            "msft-fy2017-as-reported-before-asc606"
            if row["accn"] == ORIGINAL
            else "msft-fy2017-restated-asc606",
            "docs/research/136-fundamental-universe-accounting-gates.md#basis-break",
        )
        for row in fixture["companyfacts"]["facts"]["us-gaap"][tag]["units"]["USD"]
    ]
    return normalize_companyfacts(
        json.dumps(fixture["companyfacts"]).encode(),
        concept=concept,
        submissions=[SubmissionSource(CIK, json.dumps(fixture["history"]).encode())],
        reviews=reviews,
    ).values


def selected(tag: str, at_ms: int) -> PeriodValue:
    value = select_as_of(
        normalized(tag),
        concept=Concept(CIK, "us-gaap", tag, "USD"),
        start=START,
        end=END,
        as_of_ms=at_ms,
    )
    assert value is not None
    return value


def test_actual_original_and_restated_totals_have_distinct_basis() -> None:
    old_revenue, new_revenue = (
        selected(OLD_REVENUE, OLD_CUTOFF_MS),
        selected(NEW_REVENUE, NEW_CUTOFF_MS),
    )
    old_profit, new_profit = selected(OPERATING, OLD_CUTOFF_MS), selected(OPERATING, NEW_CUTOFF_MS)
    assert [old_revenue.value, new_revenue.value, old_profit.value, new_profit.value] == [
        Decimal("89950000000"),
        Decimal("96571000000"),
        Decimal("22326000000"),
        Decimal("29025000000"),
    ]
    assert old_profit.basis_id == old_revenue.basis_id != new_revenue.basis_id
    assert old_profit.source_accessions == (ORIGINAL,)
    assert new_profit.source_accessions == (RESTATED,)
    assert new_profit.accepted_at_ms == ms("2018-08-03T15:03:33Z")


@pytest.mark.leakage
def test_future_restated_profit_change_does_not_rewrite_original() -> None:
    concept = Concept(CIK, "us-gaap", OPERATING, "USD")
    for at_ms in (OLD_CUTOFF_MS, NEW_CUTOFF_MS - 1):
        original = select_as_of(
            normalized(OPERATING), concept=concept, start=START, end=END, as_of_ms=at_ms
        )
        changed = select_as_of(
            normalized(OPERATING, changed_later=True),
            concept=concept,
            start=START,
            end=END,
            as_of_ms=at_ms,
        )
        assert original == changed and original is not None
        assert original.value == Decimal("22326000000")


@pytest.mark.leakage
def test_2018_standard_fact_is_not_backdated_or_auto_aliased() -> None:
    new = Concept(CIK, "us-gaap", NEW_REVENUE, "USD")
    values = normalized(OLD_REVENUE) + normalized(NEW_REVENUE)
    assert select_as_of(values, concept=new, start=START, end=END, as_of_ms=OLD_CUTOFF_MS) is None
    old = select_as_of(
        values,
        concept=Concept(CIK, "us-gaap", OLD_REVENUE, "USD"),
        start=START,
        end=END,
        as_of_ms=NEW_CUTOFF_MS,
    )
    assert old is not None and old.value == Decimal("89950000000")


@pytest.mark.parametrize("old_profit", [True, False])
def test_mixed_accounting_basis_margin_is_rejected(old_profit: bool) -> None:
    profit = selected(OPERATING, OLD_CUTOFF_MS if old_profit else NEW_CUTOFF_MS)
    revenue = selected(NEW_REVENUE if old_profit else OLD_REVENUE, NEW_CUTOFF_MS)
    with pytest.raises(DataIntegrityError, match="MISALIGNED_MARGIN_PERIODS"):
        margin_pct(profit, revenue, as_of_ms=NEW_CUTOFF_MS)


def test_matching_reported_basis_margin_can_be_calculated() -> None:
    for tag, at_ms in ((OLD_REVENUE, OLD_CUTOFF_MS), (NEW_REVENUE, NEW_CUTOFF_MS)):
        profit, revenue = selected(OPERATING, at_ms), selected(tag, at_ms)
        assert margin_pct(
            profit, revenue, as_of_ms=at_ms
        ) == profit.value / revenue.value * Decimal(100)


def test_missing_original_standard_cfo_is_not_zero_or_later_backfill() -> None:
    concept = Concept(CIK, "us-gaap", CFO, "USD")
    assert (
        select_as_of(normalized(CFO), concept=concept, start=START, end=END, as_of_ms=OLD_CUTOFF_MS)
        is None
    )
    assert selected(CFO, NEW_CUTOFF_MS).value == Decimal("39507000000")


def test_original_adjusted_note_is_not_mislabeled_as_later_only_information() -> None:
    fixture = json.loads(FIXTURE.read_bytes())
    note = fixture["source_note"]
    assert note["original_accession"] == ORIGINAL
    assert note["adjusted_revenue_usd"] == "96571000000"
    assert note["status"] == "DISCLOSED_IN_2017_NOTE_NOT_ORIGINAL_REPORTED_REVENUE_FACT"
    assert note["included_as_standard_fact"] is False
    # JSON 표준 fact 부재 ≠ 원문 공시 정보 부재. 주석을 fact로 자동 생성하지 않는다.
    assert selected(OLD_REVENUE, OLD_CUTOFF_MS).value != Decimal(note["adjusted_revenue_usd"])
