from __future__ import annotations

from scripts.audit_dart_feasibility import corp_map, find_measures, receipt_date, sample, years_for


def test_measures_found_by_account_id_or_name() -> None:
    rows = [
        {"account_id": "ifrs-full_Revenue", "account_nm": "수익(매출액)"},
        {"account_id": "-표준계정코드 미사용-", "account_nm": "영업 이익"},  # 이름의 빈칸은 무시
    ]
    assert find_measures(rows) == {"revenue": True, "operating_income": True, "operating_cf": False}
    bank = [
        {"account_id": "x", "account_nm": "영업수익"},
        {"account_id": "x", "account_nm": "영업활동으로 인한 현금흐름"},
    ]
    assert find_measures(bank) == {"revenue": True, "operating_income": False, "operating_cf": True}


def test_receipt_date_from_first_eight_digits() -> None:
    assert receipt_date([{"rcept_no": "20240312000736"}]) == "2024-03-12"
    assert receipt_date([{"rcept_no": ""}]) is None


def test_corp_map_keeps_only_listed_codes() -> None:
    xml = (
        "<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name>"
        "<stock_code>005930</stock_code></list>"
        "<list><corp_code>00999999</corp_code><corp_name>비상장</corp_name>"
        "<stock_code> </stock_code></list></result>"
    )
    assert corp_map(xml) == {"005930": "00126380"}


def test_sample_is_deterministic_and_balanced() -> None:
    info = {f"{i:06d}": {"kind_listed": "1" if i % 2 else ""} for i in range(40)}
    ever = sorted(info)
    picked = sample(ever, info, n=5)
    assert picked == sample(ever, info, n=5) and len(picked) == 10
    assert sum(1 for c in picked if info[c]["kind_listed"]) == 5


def test_years_follow_listing_and_delisting() -> None:
    assert years_for({"lstg": "19750611", "abol": ""}) == [2016, 2023]
    assert years_for({"lstg": "20091229", "abol": "20170307"}) == [2016]
    assert years_for({"lstg": "20100101", "abol": "20150601"}) == []  # DART 재무 API 이전
    assert years_for({"lstg": "20200101", "abol": ""}) == [2023]
