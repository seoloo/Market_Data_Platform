"""국내 주식 유니버스 — KIND 폐지 현황 읽기 · 폐지 사유 묶기 · 상장 이력 줄 만들기 (Spark 없이)."""

from __future__ import annotations

from datetime import date

import pytest

from mdp.silver.kr_universe import kind_delistings, listings_rows, reason_group

KIND_HTML = """<html><head><meta charset="EUC-KR"></head><body><table>
<tr><th>번호</th><th>회사명</th><th>종목코드</th><th>폐지일자</th><th>폐지사유</th><th>비고</th></tr>
<tr><td>2</td><td>동양생명</td><td>082640</td><td>2026-08-31</td>
<td>지주회사(최대주주등)의   완전자회사화 등</td><td></td></tr>
<tr><td>1</td><td>A&amp;B</td><td>000470</td><td>2017-07-16</td>
<td>감사의견 의견거절</td><td></td></tr>
</table></body></html>""".encode("cp949")


def test_kind_table_is_read_from_euc_kr() -> None:
    rows = kind_delistings(KIND_HTML)
    assert rows == [
        {
            "symbol": "082640",
            "kind_name": "동양생명",
            "kind_delisted_on": "2026-08-31",
            "delist_reason": "지주회사(최대주주등)의 완전자회사화 등",
        },
        {
            "symbol": "000470",
            "kind_name": "A&B",
            "kind_delisted_on": "2017-07-16",
            "delist_reason": "감사의견 의견거절",
        },
    ]


@pytest.mark.parametrize(
    ("text", "group"),
    [
        ("지주회사(최대주주등)의 완전자회사화 등", "holding_company"),
        ("신청에 의한 상장폐지", "voluntary"),
        ("감사의견 의견거절 2년 계속", "audit_opinion"),
        ("자본잠식(자본금의 50/100이상 잠식) 2년 계속", "capital_impairment"),
        ("해산 사유 발생", "dissolution"),
        ("SPAC 상장예비심사청구서 미제출 등", "expiry"),
        ("최종부도발생", "default"),
        ("기업의 계속성, 경영의 투명성 및 기타 공익과 투자자 보호", "committee_decision"),
        ("처음 보는 사유", "other"),
    ],
)
def test_reason_groups(text: str, group: str) -> None:
    assert reason_group(text) == group


def test_listings_mark_disagreeing_delisting_dates() -> None:
    info = {
        "082640": {"name": "동양생명", "abol": "20260831", "lstg": "20091008", "kind_listed": ""},
        "000470": {"name": "옛 회사", "abol": "20170715", "lstg": "19760101", "kind_listed": ""},
        "005930": {"name": "삼성전자", "abol": "", "lstg": "19750611", "kind_listed": "1"},
    }
    rows = {r["symbol"]: r for r in listings_rows(info, kind_delistings(KIND_HTML))}
    assert rows["082640"]["delisted_on_agrees"] is True
    assert rows["082640"]["delist_reason_group"] == "holding_company"
    assert rows["000470"]["delisted_on_agrees"] is False  # 하루 다르다 — 고치지 않고 표시
    assert rows["000470"]["delisted_on"] == date(2017, 7, 15)
    assert rows["005930"]["delisted_on"] is None and rows["005930"]["delisted_on_agrees"] is None
    assert rows["005930"]["in_kind_listed"] is True and rows["005930"]["listed_on"] == date(
        1975, 6, 11
    )
