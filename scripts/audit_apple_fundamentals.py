"""문서 134의 고정된 Apple 소표본 감사. 로컬 공개 원자료만 읽고 주문/수익률 없음.

가용 시각은 수동 달력 대조한 연구용 지연 가정이며 역사적 first-seen 증거가 아니다.
원본 SHA256 불일치는 중단한다. 최신 JSON으로 조용히 교체하거나 네트워크 재수집하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.fundamental_pit import (
    Concept,
    PeriodValue,
    growth_pct,
    margin_pct,
    quarter_from_ytd,
    select_as_of,
)
from auto_trading.research.sec_fundamentals import (
    FactReview,
    SubmissionSource,
    normalize_companyfacts,
)

ROOT = Path(__file__).resolve().parents[1]
CIK = "0000320193"
ANNUAL, INTERIM = "0000320193-24-000123", "0000320193-24-000081"
REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"
OPERATING = "OperatingIncomeLoss"
CASHFLOW = "NetCashProvidedByUsedInOperatingActivities"
BASIS = "apple-fy2024-reviewed-consolidated-gaap-totals-v1"


@dataclass(frozen=True)
class RawSource:
    name: str
    url: str
    sha256: str
    acquired_at_ms: int


SOURCES = (
    RawSource(
        "apple-submissions.json",
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "3cf0928a15c79b842e4ecef56a0ce30c5edfeda0c98643a046233fafba40dae7",
        1791119149366,
    ),
    RawSource(
        "apple-companyfacts.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "73a86c6aedc31f77cac2ea4df5f80f0b3bd7e6eb58bb4e01444fbedf3afb9c43",
        1791119149529,
    ),
    RawSource(
        "apple-fy2024-10k.html",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
        "28f9ca979ae6462eaaf56324ed86a4f5719ad050a0dedc3b2f7ec0ff0546fe37",
        1791119259997,
    ),
    RawSource(
        "apple-fy2024-q3-10q.html",
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/aapl-20240629.htm",
        "fe6ee5a8e435077cab8d7d012a72fa20321f0ba8fa0be7e805cee7d2223d3c1c",
        1791119260129,
    ),
    RawSource(
        "apple-fy2024-q4.pdf",
        "https://www.apple.com/newsroom/pdfs/fy2024-q4/FY24_Q4_Consolidated_Financial_Statements.pdf",
        "af9646018330484238d357f1168ae74194fd768f4ea9921e4b2828c88309320c",
        1791119119195,
    ),
    RawSource(
        "apple-fy2024-q3.pdf",
        "https://www.apple.com/newsroom/pdfs/fy2024-q3/FY24_Q3_Consolidated_Financial_Statements.pdf",
        "e435ff39122eab86559349cc7795f91d81b2a0fda8e920e258a7803be8d4573b",
        1791119120150,
    ),
    RawSource(
        "nyse-2024-calendar.pdf",
        "https://www.nyse.com/publicdocs/ICE_NYSE_2024_Yearly_Trading_Calendar.pdf",
        "02282e733512d1f112230372efccfd18f81599c682af94bb6f21fd9bccbfd145",
        1791119514178,
    ),
)

# SEC 원문/PDF를 수동 대조한 USD 금액. 기대값을 계산 결과로 자동 생성하지 않는다.
EXPECTED_USD = {
    REVENUE: (Decimal("383285000000"), Decimal("296105000000"), Decimal("391035000000")),
    OPERATING: (Decimal("114301000000"), Decimal("93625000000"), Decimal("123216000000")),
    CASHFLOW: (Decimal("110543000000"), Decimal("91443000000"), Decimal("118254000000")),
}


def utc_ms(value: str) -> int:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() != UTC.utcoffset(instant):
        raise DataIntegrityError("PILOT_REQUIRES_UTC")
    delta = instant - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


ANNUAL_AVAILABLE_MS = utc_ms("2024-11-04T21:00:00Z")
INTERIM_AVAILABLE_MS = utc_ms("2024-08-02T20:00:00Z")
PERIODS = (
    (date(2022, 9, 25), date(2023, 9, 30), ANNUAL, ANNUAL_AVAILABLE_MS),
    (date(2023, 10, 1), date(2024, 6, 29), INTERIM, INTERIM_AVAILABLE_MS),
    (date(2023, 10, 1), date(2024, 9, 28), ANNUAL, ANNUAL_AVAILABLE_MS),
)


def audit_sample(companyfacts_raw: bytes, submissions_raw: bytes) -> dict[str, object]:
    """숫자 일치만 판정. 원자료 해시 검사는 파일 경계에서 별도로 수행한다."""
    checked: dict[str, tuple[PeriodValue, PeriodValue, PeriodValue]] = {}
    exclusions: dict[str, dict[str, int]] = {}
    for tag, expected in EXPECTED_USD.items():
        concept = Concept(CIK, "us-gaap", tag, "USD")
        reviews = [
            FactReview(
                concept,
                accession,
                start,
                end,
                available,
                BASIS,
                "docs/research/134-apple-fundamental-pilot.md#manual-source-review",
            )
            for start, end, accession, available in PERIODS
        ]
        result = normalize_companyfacts(
            companyfacts_raw,
            concept=concept,
            submissions=[SubmissionSource(CIK, submissions_raw)],
            reviews=reviews,
        )
        periods: list[PeriodValue] = []
        for (start, end, accession, _), expected_value in zip(PERIODS, expected, strict=True):
            selected = select_as_of(
                result.values, concept=concept, start=start, end=end, as_of_ms=ANNUAL_AVAILABLE_MS
            )
            if (
                selected is None
                or selected.value != expected_value
                or selected.source_accessions != (accession,)
            ):
                raise DataIntegrityError("PILOT_PRIMARY_SOURCE_AMOUNT_MISMATCH")
            periods.append(selected)
        # 2024년에 접수된 비교연도 값도 2023년 가용 자료로 소급하지 않는다.
        if (
            select_as_of(
                result.values,
                concept=concept,
                start=PERIODS[0][0],
                end=PERIODS[0][1],
                as_of_ms=utc_ms("2023-12-01T00:00:00Z"),
            )
            is not None
        ):
            raise DataIntegrityError("PILOT_COMPARATIVE_BACKDATED")
        if (
            select_as_of(
                result.values,
                concept=concept,
                start=PERIODS[2][0],
                end=PERIODS[2][1],
                as_of_ms=utc_ms("2024-11-01T23:59:59Z"),
            )
            is not None
        ):
            raise DataIntegrityError("PILOT_DELAY_POLICY_NOT_RESPECTED")
        checked[tag] = (periods[0], periods[1], periods[2])
        exclusions[tag] = dict(sorted(Counter(item.reason for item in result.excluded).items()))
    quarter = {
        tag: quarter_from_ytd(values[2], values[1], as_of_ms=ANNUAL_AVAILABLE_MS)
        for tag, values in checked.items()
    }
    if (
        quarter[REVENUE].value != Decimal("94930000000")
        or quarter[OPERATING].value != Decimal("29591000000")
        or quarter[CASHFLOW].value != Decimal("26811000000")
    ):
        raise DataIntegrityError("PILOT_Q4_DIFFERENCE_MISMATCH")
    revenue, operating, cashflow = checked[REVENUE], checked[OPERATING], checked[CASHFLOW]
    return {
        "status": "REAL_SAMPLE_NUMERIC_MATCH",
        "historical_first_seen": "NOT_VERIFIED",
        "availability_policy": "MANUALLY_REVIEWED_NEXT_SESSION_CLOSE_ASSUMPTION",
        "strategy_result": "NOT_TESTED",
        "fiscal_year_lengths_days": [
            (PERIODS[index][1] - PERIODS[index][0]).days + 1 for index in (0, 2)
        ],
        "verified_amounts_usd": {
            tag: [str(value.value) for value in values] for tag, values in checked.items()
        },
        "annual_revenue_growth_pct": str(
            growth_pct(revenue[2], revenue[0], as_of_ms=ANNUAL_AVAILABLE_MS)
        ),
        "annual_operating_margin_pct": str(
            margin_pct(operating[2], revenue[2], as_of_ms=ANNUAL_AVAILABLE_MS)
        ),
        "annual_cfo_to_sales_pct": str(
            margin_pct(cashflow[2], revenue[2], as_of_ms=ANNUAL_AVAILABLE_MS)
        ),
        "q4_amounts_usd": {tag: str(value.value) for tag, value in quarter.items()},
        "q4_source_accessions": list(quarter[CASHFLOW].source_accessions),
        "excluded_rows_not_research_coverage": exclusions,
    }


def audit_local(data_dir: Path) -> dict[str, object]:
    raws: dict[str, bytes] = {}
    for source in SOURCES:
        try:
            content = (data_dir / source.name).read_bytes()
        except OSError as exc:
            raise DataIntegrityError(f"PILOT_SOURCE_NOT_READABLE:{source.name}") from exc
        if hashlib.sha256(content).hexdigest() != source.sha256:
            raise DataIntegrityError(f"PILOT_SOURCE_HASH_MISMATCH:{source.name}")
        raws[source.name] = content
    result = audit_sample(raws["apple-companyfacts.json"], raws["apple-submissions.json"])
    result["sources"] = [
        {
            "name": item.name,
            "url": item.url,
            "sha256": item.sha256,
            "acquired_at_ms": item.acquired_at_ms,
            "bytes": len(raws[item.name]),
        }
        for item in SOURCES
    ]
    result["code_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "scripts/audit_apple_fundamentals.py",
            "src/auto_trading/research/sec_fundamentals.py",
            "src/auto_trading/research/fundamental_pit.py",
        )
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/research/fundamental_pilot")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/fundamental_pilot/apple-fy2024-audit.json"
    )
    args = parser.parse_args()
    try:
        result = audit_local(args.data_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # 새 결과만 생성. 기존 감사 기록/원자료를 덮어쓰지 않는다.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (DataIntegrityError, OSError) as exc:
        sys.stdout.write(
            json.dumps({"status": "AUDIT_FAILED", "error_type": type(exc).__name__}) + "\n"
        )
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "status": result["status"],
                "output": str(args.output),
                "strategy_result": "NOT_TESTED",
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
