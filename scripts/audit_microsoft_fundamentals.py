"""문서 135: Microsoft 고정 소표본/과거 공시 연결 감사. 매매·네트워크 없음."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.fundamental_pit import Concept, quarter_from_ytd, select_as_of
from auto_trading.research.sec_fundamentals import (
    FactReview,
    SubmissionSource,
    normalize_companyfacts,
    read_filings,
)

ROOT = Path(__file__).resolve().parents[1]
CIK = "0000789019"
ORIGINAL = "0000950170-23-035122"
INTERIM = "0000950170-24-048288"
ANNUAL = "0000950170-24-087843"
REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"
OPERATING = "OperatingIncomeLoss"
CASHFLOW = "NetCashProvidedByUsedInOperatingActivities"
BASIS = "msft-fy2023-2024-reviewed-consolidated-reported-totals-v1"
REFERENCE = "docs/research/135-microsoft-fundamental-pilot.md#manual-source-review"

# 고정 원자료 manifest: (name, URL, SHA256, acquired_at_ms). 연락처 저장 없음.
SOURCES = (
    (
        "msft-submissions.json",
        "https://data.sec.gov/submissions/CIK0000789019.json",
        "2484a2a3b5677d892a1b25af573ce4eda7b6f449f5eda41bac6b3f106dc3139c",
        1791120939126,
    ),
    (
        "msft-companyfacts.json",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json",
        "f8aae2965b20ad0df44bdf7ccbedf797d275b6b8dc030154a7a311361bb7246f",
        1791120939319,
    ),
    (
        "msft-fy2024-10k.html",
        "https://www.sec.gov/Archives/edgar/data/789019/000095017024087843/msft-20240630.htm",
        "63ef83c20c2df925e4903497c43294c69dd3d85e9b1f7d41343d56f50f151846",
        1791121026064,
    ),
    (
        "msft-fy2024-q3-10q.html",
        "https://www.sec.gov/Archives/edgar/data/789019/000095017024048288/msft-20240331.htm",
        "3ed229a25ebbfc0c4a1e1ff57cd55cbc19b8d0f994bac321d7a92d9ae23004e5",
        1791121026432,
    ),
    (
        "msft-fy2023-10k.html",
        "https://www.sec.gov/Archives/edgar/data/789019/000095017023035122/msft-20230630.htm",
        "53060ccda091da700be2eb8f729bc7f723242a17309addbc40d28caaef19eae1",
        1791121026916,
    ),
    (
        "msft-submissions-001.json",
        "https://data.sec.gov/submissions/CIK0000789019-submissions-001.json",
        "de768837e66425317895fba9214f56fd17b72b676804d1d5ea48827c5e24afba",
        1791121027263,
    ),
    (
        "msft-fy2024-q4-ir.html",
        "https://www.microsoft.com/en-us/investor/earnings/fy-2024-q4/press-release-webcast",
        "70a59b975bb8122c7a793014616f3c67b002f8bd8fe60d2fa3773535573f3653",
        1791121071517,
    ),
    (
        "msft-fy2024-q3-ir.html",
        "https://www.microsoft.com/en-us/Investor/earnings/FY-2024-Q3/press-release-webcast",
        "ee48b4d2a9e71ebd9c0b36b020988fd489a478fc3b9ac0198e4b2452321525a7",
        1791121072292,
    ),
)


def utc_ms(value: str) -> int:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() != UTC.utcoffset(instant) or instant.microsecond % 1000:
        raise DataIntegrityError("MSFT_AUDIT_REQUIRES_UTC_MILLISECONDS")
    delta = instant - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


# 다음 세 종가는 연구용 수동 가정. 일반 거래 달력/역사적 first-seen 인증 아님.
ORIGINAL_AVAILABLE_MS = utc_ms("2023-07-28T20:00:00Z")
INTERIM_AVAILABLE_MS = utc_ms("2024-04-26T20:00:00Z")
ANNUAL_AVAILABLE_MS = utc_ms("2024-07-31T20:00:00Z")
PERIODS = (
    (date(2022, 7, 1), date(2023, 6, 30), ORIGINAL, ORIGINAL_AVAILABLE_MS),
    (date(2022, 7, 1), date(2023, 6, 30), ANNUAL, ANNUAL_AVAILABLE_MS),
    (date(2023, 7, 1), date(2024, 3, 31), INTERIM, INTERIM_AVAILABLE_MS),
    (date(2023, 7, 1), date(2024, 6, 30), ANNUAL, ANNUAL_AVAILABLE_MS),
)
# 기대값은 원문 연결 재무표 수동 대조. JSON 산출값으로 생성하지 않는다.
EXPECTED_USD = {
    REVENUE: (211915000000, 211915000000, 180395000000, 245122000000),
    OPERATING: (88523000000, 88523000000, 81508000000, 109433000000),
    CASHFLOW: (87582000000, 87582000000, 81353000000, 118548000000),
}
Q4_EXPECTED_USD = {REVENUE: 64727000000, OPERATING: 27925000000, CASHFLOW: 37195000000}


def reviews_for(tag: str) -> tuple[FactReview, ...]:
    concept = Concept(CIK, "us-gaap", tag, "USD")
    return tuple(
        FactReview(concept, accession, start, end, available, BASIS, REFERENCE)
        for start, end, accession, available in PERIODS
    )


def audit_sample(facts_raw: bytes, current_raw: bytes, history_raw: bytes) -> dict[str, object]:
    sources = (SubmissionSource(CIK, current_raw), SubmissionSource(CIK, history_raw))
    current = read_filings(sources[:1], cik=CIK)
    history = read_filings(sources[1:], cik=CIK)
    combined = read_filings(sources, cik=CIK)
    amounts: dict[str, list[str]] = {}
    quarters: dict[str, str] = {}
    exclusions: dict[str, dict[str, int]] = {}
    for tag, expected in EXPECTED_USD.items():
        concept = Concept(CIK, "us-gaap", tag, "USD")
        result = normalize_companyfacts(
            facts_raw, concept=concept, submissions=sources, reviews=reviews_for(tag)
        )
        # 각 버전은 그 버전의 가용 시점에서 선택한다. 동일 금액이어도 출처를 검사.
        selected = [
            select_as_of(result.values, concept=concept, start=start, end=end, as_of_ms=available)
            for start, end, _, available in PERIODS
        ]
        for value, amount, (_, _, accession, _) in zip(selected, expected, PERIODS, strict=True):
            if (
                value is None
                or value.value != Decimal(amount)
                or value.source_accessions != (accession,)
            ):
                raise DataIntegrityError("MSFT_PRIMARY_SOURCE_AMOUNT_OR_VERSION_MISMATCH")
        annual, interim = selected[3], selected[2]
        if annual is None or interim is None:
            raise DataIntegrityError("MSFT_REQUIRED_PERIOD_MISSING")
        quarter = quarter_from_ytd(annual, interim, as_of_ms=ANNUAL_AVAILABLE_MS)
        if quarter.value != Decimal(Q4_EXPECTED_USD[tag]):
            raise DataIntegrityError("MSFT_Q4_DIFFERENCE_MISMATCH")
        # 가용 직전 연간/누적 금액 미노출. 다른 기간 값으로 대체하지 않는다.
        for start, end, _, available in PERIODS[2:]:
            if (
                select_as_of(
                    result.values, concept=concept, start=start, end=end, as_of_ms=available - 1
                )
                is not None
            ):
                raise DataIntegrityError("MSFT_AVAILABILITY_BACKDATED")
        amounts[tag] = [str(value.value) for value in selected if value is not None]
        quarters[tag] = str(quarter.value)
        exclusions[tag] = dict(sorted(Counter(x.reason for x in result.excluded).items()))
    return {
        "status": "REAL_SAMPLE_NUMERIC_AND_VERSION_MATCH",
        "historical_first_seen": "NOT_VERIFIED",
        "availability_policy": "MANUAL_NEXT_SESSION_CLOSE_ASSUMPTION_NOT_CALENDAR_CERTIFIED",
        "strategy_result": "NOT_TESTED",
        "economic_comparability": "REPORTED_TOTALS_ONLY_ORGANIC_IMPROVEMENT_NOT_CERTIFIED",
        "manual_cautions": [
            "ACQUISITION_CONSOLIDATION",
            "DEPRECIATION_ESTIMATE_CHANGE",
            "PRIOR_YEAR_CHARGE",
        ],
        "filings_current": len(current),
        "filings_history": len(history),
        "filings_combined": len(combined),
        "unknown_acceptance_count": sum(x.accepted_at_ms is None for x in combined),
        "historical_financial_rows_reviewed": 0,
        "fiscal_year_lengths_days": [(PERIODS[i][1] - PERIODS[i][0]).days + 1 for i in (0, 3)],
        "verified_amounts_usd": amounts,
        "q4_amounts_usd": quarters,
        "q4_source_accessions": list(quarter.source_accessions),
        "original_and_comparative_accessions": [ORIGINAL, ANNUAL],
        "excluded_rows_not_research_coverage": exclusions,
    }


def audit_local(data_dir: Path) -> dict[str, object]:
    raws: dict[str, bytes] = {}
    for name, _, digest, _ in SOURCES:
        try:
            raw = (data_dir / name).read_bytes()
        except OSError as exc:
            raise DataIntegrityError(f"MSFT_SOURCE_NOT_READABLE:{name}") from exc
        if hashlib.sha256(raw).hexdigest() != digest:
            raise DataIntegrityError(f"MSFT_SOURCE_HASH_MISMATCH:{name}")
        raws[name] = raw
    result = audit_sample(
        raws["msft-companyfacts.json"],
        raws["msft-submissions.json"],
        raws["msft-submissions-001.json"],
    )
    result["sources"] = [
        {
            "name": name,
            "url": url,
            "sha256": digest,
            "acquired_at_ms": acquired,
            "bytes": len(raws[name]),
        }
        for name, url, digest, acquired in SOURCES
    ]
    result["code_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in (
            "scripts/audit_microsoft_fundamentals.py",
            "src/auto_trading/research/sec_fundamentals.py",
            "src/auto_trading/research/fundamental_pit.py",
        )
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/research/fundamental_pilot")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/fundamental_pilot/microsoft-fy2024-audit.json",
    )
    args = parser.parse_args()
    try:
        result = audit_local(args.data_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
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
