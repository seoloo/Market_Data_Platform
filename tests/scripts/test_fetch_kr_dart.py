from __future__ import annotations

from pathlib import Path

from scripts.fetch_kr_dart import (
    Budget,
    corrected,
    report_versions,
    reprt_code_for,
    targets,
    years_listed,
)


def test_targets_are_common_stocks_listed_in_the_window_without_sector_filter() -> None:
    info = {
        "000001": {"kind": "101", "lstg": "19750101", "abol": ""},
        "000002": {"kind": "101", "lstg": "19900101", "abol": "20141231"},  # 2015 전 폐지
        "000003": {"kind": "101", "lstg": "20270101", "abol": ""},  # 기간 뒤 상장
        "000004": {"kind": "", "lstg": "19900101", "abol": ""},  # 우선주 등
        "000005": {
            "kind": "101",
            "lstg": "19900101",
            "abol": "20170307",
            "ksic": "116402",
        },  # 금융도 받음
    }
    assert targets(info) == ["000001", "000005"]


def test_years_cover_listing_window_with_one_year_slack() -> None:
    assert years_listed({"lstg": "19750101", "abol": ""}) == list(range(2015, 2027))
    assert years_listed({"lstg": "20180601", "abol": "20210315"}) == [2017, 2018, 2019, 2020, 2021]


def test_versions_and_corrections() -> None:
    rows = [
        {"report_nm": "사업보고서 (2017.12)", "rcept_no": "20180402001410"},
        {"report_nm": "[기재정정]사업보고서 (2017.12)", "rcept_no": "20180618000261"},
        {"report_nm": "[기재정정]사업보고서 (2017.12)", "rcept_no": "20180618000237"},
        {"report_nm": "분기보고서 (2018.03)", "rcept_no": "20180515000100"},
    ]
    v = report_versions(rows)
    assert [x["rcept_no"] for x in v["사업보고서 (2017.12)"]] == [
        "20180402001410",
        "20180618000237",
        "20180618000261",
    ]
    # 마지막 정정은 재무 API 가 주므로 원본·중간 정정만
    assert corrected(v) == [
        ("사업보고서 (2017.12)", "11011", "20180402001410"),
        ("사업보고서 (2017.12)", "11011", "20180618000237"),
    ]


def test_report_codes() -> None:
    assert reprt_code_for("사업보고서 (2017.12)") == "11011"
    assert reprt_code_for("반기보고서 (2018.06)") == "11012"
    assert reprt_code_for("분기보고서 (2018.03)") == "11013"
    assert reprt_code_for("분기보고서 (2018.09)") == "11014"
    assert reprt_code_for("분기보고서 (2018.06)") is None  # 결산월이 12 월이 아닌 회사


def test_budget_stops_at_cap_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    b = Budget(path, cap=2, today="2026-10-05")
    assert b.spend() and b.spend() and not b.spend()
    again = Budget(path, cap=2, today="2026-10-05")
    assert again.used == 2 and not again.spend()
    assert Budget(path, cap=2, today="2026-10-06").spend()
