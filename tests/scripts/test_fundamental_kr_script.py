from __future__ import annotations

from scripts.fetch_kr_stocks import dividend_rows
from scripts.fundamental_kr import avail_index, stale


def test_dividend_rows_keep_common_cash_rows_sorted_and_unique() -> None:
    raw = [
        {"record_date": "20171231", "divi_kind": "결산", "per_sto_divi_amt": "21500",
         "divi_pay_dt": "2018/04/23", "face_val": "5000", "stk_kind": "보통"},
        {"record_date": "20170930", "divi_kind": "분기", "per_sto_divi_amt": "7000",
         "divi_pay_dt": "2017/11/20", "face_val": "5000", "stk_kind": "보통"},
        {"record_date": "20170930", "divi_kind": "분기", "per_sto_divi_amt": "7000",
         "divi_pay_dt": "2017/11/20", "face_val": "5000", "stk_kind": "보통"},
        {"record_date": "20171231", "divi_kind": "결산", "per_sto_divi_amt": "21550",
         "divi_pay_dt": "2018/04/23", "face_val": "5000", "stk_kind": "우선"},
        {"record_date": "", "divi_kind": "결산", "per_sto_divi_amt": "1", "stk_kind": "보통"},
    ]  # fmt: skip
    rows = dividend_rows(raw)
    assert [r["record_date"] for r in rows] == ["20170930", "20171231"]
    assert rows[1]["dps"] == "21500" and rows[1]["pay_date"] == "20180423"


def test_filing_is_usable_from_the_close_of_the_next_trading_day() -> None:
    days = ["2024-03-28", "2024-03-29", "2024-04-01"]
    assert avail_index(days, "20240328") == 1
    assert avail_index(days, "2024-03-30") == 2  # 주말 접수 → 다음 거래일
    assert avail_index(days, "20240401") == 3  # 기간 밖


def test_stale_after_180_days_from_quarter_end() -> None:
    assert not stale("2024-06-28", "2024-03")
    assert stale("2024-12-31", "2024-03")


def test_krx_rows_to_csv_keeps_chosen_fields_without_commas() -> None:
    from scripts.fetch_krx import FIELDS, to_csv

    text = to_csv([{"ISU_CD": "005930", "MKTCAP": "1,234", "EXTRA": "x"}]).decode("utf-8")
    head, row = text.strip().split("\n")
    assert head.split(",") == list(FIELDS)
    cells = row.split(",")
    assert cells[0] == "005930" and cells[FIELDS.index("MKTCAP")] == "1234"
