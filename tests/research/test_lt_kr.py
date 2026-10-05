from __future__ import annotations

from datetime import date, timedelta

import pytest
from scripts.fetch_kr_stocks import daily_rows, delisted_codes, listed_codes, table_rows, window
from scripts.run_lt_kr import (
    UNKNOWN,
    KrPanel,
    equal_weight_pit,
    ksic_sector,
    members,
    simulate_kr,
)


def _days(n: int, start: date = date(2008, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _panel(
    days: list[str], close: dict[str, list[float | None]], sector: dict[str, str]
) -> KrPanel:
    last = {s: max(i for i, x in enumerate(xs) if x is not None) for s, xs in close.items()}
    return KrPanel(
        days=days,
        close=close,
        value={s: [1e9] * len(days) for s in close},
        sector=sector,
        listed_from=dict.fromkeys(close, 0),
        last=last,
    )


def test_ksic_division_maps_to_gics_like_sector() -> None:
    assert ksic_sector("074701") == "경기소비재"  # G47 종합 소매업
    assert ksic_sector("116402") == "금융"  # K64
    assert ksic_sector("032611") == "IT"  # C26
    assert ksic_sector("033099") == "경기소비재"  # C30 자동차
    assert ksic_sector("031501") == "경기소비재"  # C15 가죽
    assert ksic_sector("") == UNKNOWN
    assert ksic_sector("0399xx") == UNKNOWN


def test_members_rank_by_liquidity_and_need_history_and_listing() -> None:
    days = _days(200)
    close: dict[str, list[float | None]] = {s: [100.0] * 200 for s in ("A", "B", "C", "D")}
    close["D"] = [None] * 100 + [100.0] * 100  # 6 달 이력이 없다
    p = _panel(days, close, dict.fromkeys(close, "IT"))
    p.value["A"] = [3e9] * 200
    p.value["B"] = [2e9] * 200
    p.listed_from["C"] = 190  # 유가증권시장 상장 전
    assert members(p, 150, size=1) == ["A"]
    assert members(p, 150) == ["A", "B"]


def _trend_panel() -> KrPanel:
    days = _days(320)
    close: dict[str, list[float | None]] = {}
    sector: dict[str, str] = {}
    for i in range(10):
        close[f"IT{i}"] = [100 * (1.003 + i * 0.0001) ** t for t in range(320)]
        sector[f"IT{i}"] = "IT"
    others = ("소재", "산업재", "금융", "헬스케어", "유틸리티")
    for j in range(50):
        close[f"X{j}"] = [100 * (0.999 - j * 0.00001) ** t for t in range(320)]
        sector[f"X{j}"] = others[j % 5]
    delist = days.index("2008-09-15")
    close["IT9"] = [x if t <= delist else None for t, x in enumerate(close["IT9"])]
    crash, halt = days.index("2008-10-01"), days.index("2008-10-02")
    peak = close["IT8"][crash - 1]
    assert peak is not None
    close["IT8"] = [
        x if t < crash else (None if t == halt else peak * 0.5) for t, x in enumerate(close["IT8"])
    ]
    return _panel(days, close, sector)


def test_simulation_buys_leaders_sells_delisted_on_last_day_and_waits_out_a_halt() -> None:
    p = _trend_panel()
    run = simulate_kr(p, [True] * len(p.days))
    assert set(run.leader_log["2008-06-30"]) == {f"IT{i}" for i in range(10)}
    by = {str(tr["symbol"]): tr for tr in run.trades}
    assert (by["IT9"]["exit"], by["IT9"]["reason"]) == ("2008-09-15", "delisted")
    # 10-01 종가에 −50% → 손절 예약, 10-02 거래정지라 못 팔고, 10-03 종가에 판다
    assert (by["IT8"]["exit"], by["IT8"]["reason"]) == ("2008-10-03", "stop_35")
    assert all(str(tr["entry"]) == "2008-07-01" for tr in by.values())


def test_no_new_buys_when_index_is_below_its_200_day_line() -> None:
    p = _trend_panel()
    run = simulate_kr(p, [False] * len(p.days))
    assert run.trades == [] and run.equity[-1] == pytest.approx(1.0)


def test_equal_weight_turns_a_delisted_stock_into_cash_then_rebalances() -> None:
    days = _days(260)
    end = days.index("2008-07-10")
    close: dict[str, list[float | None]] = {
        "S": [100.0] * 260,
        "D": [100.0 if t <= end else None for t in range(260)],
    }
    p = _panel(days, close, {"S": "IT", "D": "금융"})
    eq = equal_weight_pit(p, {})
    first = 1 - 0.0025  # 첫날 전액을 둘로 나눠 산다
    after = first - (first / 2) * 0.0025  # 7 월 말: D 의 현금을 S 로 옮긴다
    assert eq[0] == pytest.approx(first)
    assert eq[-1] == pytest.approx(after)


def test_kind_tables_and_kis_rows_parse() -> None:
    listed = (
        "<table><tr><th>회사명</th><th>시장구분</th><th>종목코드</th></tr>"
        "<tr><td>삼성전자</td><td>유가</td><td>5930</td></tr>"
        "<tr><td>새 종목</td><td>유가</td><td>0220W0</td></tr></table>"
    )
    assert listed_codes(table_rows(listed)) == ["005930", "0220W0"]
    gone = (
        "<table><tr><th>번호</th><th>회사명</th><th>종목코드</th><th>폐지일자</th></tr>"
        "<tr><td>1</td><td>한진해운</td><td>117930</td><td>2017-03-07</td></tr></table>"
    )
    assert delisted_codes(table_rows(gone)) == {"117930": "2017-03-07"}
    rows = daily_rows(
        [
            {
                "stck_bsop_date": "20080103",
                "stck_clpr": "10",
                "acml_vol": "5",
                "acml_tr_pbmn": "50",
            },
            {"stck_bsop_date": "20080102", "stck_clpr": "9", "acml_vol": "1", "acml_tr_pbmn": "9"},
            {"stck_bsop_date": "", "stck_clpr": "0"},
        ]
    )
    assert rows == [("20080102", 9.0, 1.0, 9.0), ("20080103", 10.0, 5.0, 50.0)]
    assert window({"lstg": "20100105", "abol": "20170307"}) == ("20100105", "20170307")
    assert window({"lstg": "19750611", "abol": ""}) == ("20070101", "20260831")
    assert window({"lstg": "20270101"}) is None


def test_raw_rows_keep_unadjusted_ohlcv_and_halted_days() -> None:
    from scripts.fetch_kr_stocks import raw_rows

    rows = raw_rows(
        [
            {
                "stck_bsop_date": "20180503",
                "stck_oprc": "2650000",
                "stck_hgpr": "2650000",
                "stck_lwpr": "2650000",
                "stck_clpr": "2650000",
                "acml_vol": "0",  # 분할 전 거래정지
                "acml_tr_pbmn": "0",
                "flng_cls_code": "00",
            },
            {
                "stck_bsop_date": "20180504",
                "stck_oprc": "53000",
                "stck_hgpr": "53900",
                "stck_lwpr": "51800",
                "stck_clpr": "51900",
                "acml_vol": "39565391",
                "acml_tr_pbmn": "2078601000000",
                "flng_cls_code": "05",
            },
            {"stck_bsop_date": "20180505", "stck_clpr": "0"},
        ]
    )
    assert [r[0] for r in rows] == ["20180503", "20180504"]
    assert rows[0][4] == "2650000" and rows[0][5] == "0"  # 비수정 종가 · 거래량 0 도 남김
    assert rows[1][1:5] == ("53000", "53900", "51800", "51900") and rows[1][7] == "05"
