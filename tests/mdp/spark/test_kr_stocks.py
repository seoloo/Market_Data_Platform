"""국내 주식 Silver · Gold 시험 — 실제 자료에서 본 경우를 작은 자료로 흉내 낸다.

늦게 연 날의 16:30 마감 봉 · 마감 봉 없는 날 · 장 후 봉 · 시장 전체 30 분 빔 · 같은 분 두 번 ·
50:1 분할 · 분봉이 수정 가격으로 온 날(ETF).
Spark 이미지 안에서만 돈다(호스트에는 pyspark 가 없어 건너뜀).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pyspark")

from mdp.gold.kr_stocks import adjustment_events, daily_from_minutes
from mdp.silver.kr_stocks import (
    CALENDAR_SCHEMA,
    DAILY_SCHEMA,
    MINUTE_SCHEMA,
    audit_days,
    find_gaps,
    label_sessions,
    parse_calendar,
    parse_daily,
    parse_minutes,
)
from mdp.spark.session import spark_session

BASE = "file:///lake/bronze/kis/domestic"


@pytest.fixture(scope="module")
def spark() -> Iterator[Any]:
    session = spark_session("test-kr-stocks")
    session.sparkContext.setLogLevel("ERROR")
    yield session


def _bar(day: str, hhmm: str, price: int = 100, volume: int = 10, **change: Any) -> dict[str, str]:
    row = {
        "stck_bsop_date": day.replace("-", ""),
        "stck_cntg_hour": f"{hhmm.replace(':', '')}00",
        "stck_prpr": str(price),
        "stck_oprc": str(price),
        "stck_hgpr": str(price),
        "stck_lwpr": str(price),
        "cntg_vol": str(volume),
        "acml_tr_pbmn": "0",
    }
    return {**row, **{k: str(v) for k, v in change.items()}}


def _minute_pages(spark: Any, pages: list[tuple[str, str, int, list[dict[str, str]]]]) -> Any:
    rows = [
        (
            f"{BASE}/minute-bars/market=J/symbol={symbol}/date={day}/page-{page:05d}.json.gz",
            "0",
            bars,
        )
        for symbol, day, page, bars in pages
    ]
    return spark.createDataFrame(rows, f"file_path STRING, {MINUTE_SCHEMA}")


def _times(start: str, end: str) -> list[str]:
    a = int(start[:2]) * 60 + int(start[3:])
    b = int(end[:2]) * 60 + int(end[3:])
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(a, b + 1)]


def _labelled(spark: Any, pages: list[tuple[str, str, int, list[dict[str, str]]]]) -> Any:
    clean, _ = parse_minutes(_minute_pages(spark, pages), "r")
    return label_sessions(clean)


# ------------------------------------------------------------------ Silver 분봉


def test_parse_minutes_dedups_and_quarantines(spark: Any) -> None:
    day = "2026-03-03"
    pages = [
        ("005930", day, 0, [_bar(day, "09:00"), _bar(day, "09:01"), _bar(day, "09:02", 0)]),
        ("005930", day, 1, [_bar(day, "09:01"), _bar(day, "09:03", stck_hgpr=90)]),
        ("000660", day, 0, [_bar(day, "09:00", 5), _bar(day, "09:00", 6)]),
    ]
    clean, quarantine = parse_minutes(_minute_pages(spark, pages), "r")
    kept = {(r["symbol"], r["bar_time"]): r for r in clean.collect()}
    assert set(kept) == {("005930", "090000"), ("005930", "090100")}
    assert kept[("005930", "090100")]["duplicate_copies"] == 1  # 같은 값 두 번 → 하나만
    utc_midnight = datetime(2026, 3, 3, tzinfo=UTC).replace(tzinfo=None)  # Spark 는 UTC 로 돌려준다
    assert kept[("005930", "090000")]["bar_start"] == utc_midnight  # 서울 09:00 = UTC 00:00
    reasons = sorted((r["symbol"], r["bar_time"], r["reason"]) for r in quarantine.collect())
    assert reasons == [
        ("000660", "090000", "duplicate_with_different_values"),
        ("000660", "090000", "duplicate_with_different_values"),
        ("005930", "090200", "non_positive_price"),
        ("005930", "090300", "ohlc_inconsistent"),
    ]


def test_sessions_follow_the_closing_call_not_the_clock(spark: Any) -> None:
    normal, late, broken = "2026-09-15", "2025-11-13", "2026-06-29"
    pages = [
        # 보통 날: 15:19 까지 → 10 분 빔 → 15:30 마감 봉 → 16:00 장 후 봉
        ("005930", normal, 0, [_bar(normal, t) for t in ["15:18", "15:19", "15:30", "16:00"]]),
        # 늦게 연 날: 16:19 까지 → 16:30 마감. 15:30 봉은 그냥 정규장
        ("005930", late, 0, [_bar(late, t) for t in ["15:29", "15:30", "15:31", "16:19", "16:30"]]),
        # 마감 봉이 없는 날
        ("005930", broken, 0, [_bar(broken, t) for t in ["15:18", "15:19"]]),
    ]
    got = {
        (str(r["trade_date"]), r["bar_time"][:4]): r["session"]
        for r in _labelled(spark, pages).collect()
    }
    assert got[(normal, "1519")] == "regular" and got[(normal, "1530")] == "closing_auction"
    assert got[(normal, "1600")] == "after_hours"
    assert got[(late, "1530")] == "regular" and got[(late, "1630")] == "closing_auction"
    assert got[(broken, "1519")] == "regular"


def test_gaps_mark_market_wide_halts_and_late_opens(spark: Any) -> None:
    day = "2026-03-09"
    stock = _times("09:00", "10:31") + _times("11:02", "11:10")
    etf = _times("09:00", "10:31") + _times("11:01", "11:10")  # 1 분 먼저 다시 열림
    lonely = _times("09:00", "09:04") + _times("09:06", "11:10")  # 한 종목만 1 분 빔
    late_day = "2026-01-02"
    pages = [
        ("005930", day, 0, [_bar(day, t) for t in stock]),
        ("069500", day, 0, [_bar(day, t) for t in etf]),
        ("000660", day, 0, [_bar(day, t) for t in lonely]),
        ("005930", late_day, 0, [_bar(late_day, t) for t in _times("10:00", "10:05")]),
        ("000660", late_day, 0, [_bar(late_day, t) for t in _times("10:00", "10:05")]),
    ]
    gaps = {
        (r["symbol"], str(r["trade_date"]), r["kind"], r["start_time"]): r
        for r in find_gaps(_labelled(spark, pages), "r").collect()
    }
    assert gaps[("005930", day, "intraday", "10:32")]["minutes"] == 30
    assert gaps[("005930", day, "intraday", "10:32")]["market_wide"] is False  # 000660 은 안 빔
    assert gaps[("000660", day, "intraday", "09:05")]["market_wide"] is False
    opening = gaps[("005930", late_day, "opening", "09:00")]
    assert (opening["end_time"], opening["minutes"], opening["market_wide"]) == ("09:59", 60, True)


def test_market_wide_needs_every_symbol(spark: Any) -> None:
    day = "2026-03-09"
    pages = [
        (
            "005930",
            day,
            0,
            [_bar(day, t) for t in _times("09:00", "10:31") + _times("11:02", "11:05")],
        ),
        (
            "069500",
            day,
            0,
            [_bar(day, t) for t in _times("09:00", "10:31") + _times("11:01", "11:05")],
        ),
    ]
    gaps = find_gaps(_labelled(spark, pages), "r").collect()
    assert {(r["symbol"], r["market_wide"]) for r in gaps} == {("005930", True), ("069500", True)}


def test_audit_days_status(spark: Any) -> None:
    ok, broken, closed = "2026-09-15", "2026-06-29", "2026-10-09"
    pages = [
        ("005930", ok, 0, [_bar(ok, t) for t in ["15:19", "15:30", "16:00"]]),
        ("005930", broken, 0, [_bar(broken, t) for t in ["15:18", "15:19"]]),
        ("005930", closed, 0, [_bar(closed, t) for t in ["15:19", "15:30"]]),
    ]
    labelled = _labelled(spark, pages)
    calendar = spark.createDataFrame(
        [(date(2026, 9, 15), True), (date(2026, 10, 9), False)], "trade_date DATE, is_open BOOLEAN"
    )
    rows = {
        str(r["trade_date"]): r
        for r in audit_days(labelled, find_gaps(labelled, "r"), calendar, "r").collect()
    }
    assert rows[ok]["status"] == "ok" and rows[ok]["after_hours_bars"] == 1
    assert rows[ok]["closing_bar_time"] == "153000" and rows[ok]["calendar_is_open"] is True
    assert rows[broken]["status"] == "no_closing_bar" and rows[broken]["calendar_is_open"] is None
    assert rows[closed]["status"] == "not_open_in_calendar"


# ------------------------------------------------------------------ Silver 달력 · 일봉


def test_calendar_keeps_only_days_of_the_unit_month(spark: Any) -> None:
    def day(text: str, opened: str) -> dict[str, str]:
        return {
            "bass_dt": text,
            "wday_dvsn_cd": "01",
            "bzdy_yn": opened,
            "tr_day_yn": opened,
            "opnd_yn": opened,
            "sttl_day_yn": opened,
        }

    pages = spark.createDataFrame(
        [
            (
                f"{BASE}/holiday/month=2025-10/page-00001.json.gz",
                "0",
                [day("20251031", "Y"), day("20251101", "N")],
            ),
            (f"{BASE}/holiday/month=2025-11/page-00000.json.gz", "0", [day("20251101", "Y")]),
        ],
        f"file_path STRING, {CALENDAR_SCHEMA}",
    )
    got = {str(r["trade_date"]): r["is_open"] for r in parse_calendar(pages, "r").collect()}
    assert got == {"2025-10-31": True, "2025-11-01": True}  # 11-01 은 11 월 단위의 말을 따른다


def _daily_row(day: str, close: int, volume: int = 100, **change: Any) -> dict[str, str]:
    row = {
        "stck_bsop_date": day.replace("-", ""),
        "stck_oprc": str(close),
        "stck_hgpr": str(close),
        "stck_lwpr": str(close),
        "stck_clpr": str(close),
        "acml_vol": str(volume),
        "acml_tr_pbmn": "0",
        "flng_cls_code": "00",
        "prtt_rate": "0.00",
        "mod_yn": "N",
        "revl_issu_reas": "",
    }
    return {**row, **{k: str(v) for k, v in change.items()}}


def _daily(spark: Any, rows: list[tuple[str, str, str, list[dict[str, str]]]]) -> Any:
    pages = spark.createDataFrame(
        [
            (
                f"{BASE}/daily-bars/price={basis}/symbol={symbol}/month={month}/page-00000.json.gz",
                "0",
                out,
            )
            for basis, symbol, month, out in rows
        ],
        f"file_path STRING, {DAILY_SCHEMA}",
    )
    return parse_daily(pages, "r")


def test_parse_daily_marks_halt_days(spark: Any) -> None:
    daily = _daily(spark, [("raw", "005930", "2018-05", [_daily_row("2018-05-02", 2650000, 0)])])
    [row] = daily.collect()
    assert (row["price_basis"], row["close"], row["volume_zero"]) == (
        "raw",
        Decimal("2650000.00"),
        True,
    )


# ------------------------------------------------------------------ Gold


def test_adjustment_events_find_the_split_and_ignore_rounding(spark: Any) -> None:
    raw = [_daily_row("2018-05-02", 2650000), _daily_row("2018-05-04", 51900)]
    adjusted = [_daily_row("2018-05-02", 53000), _daily_row("2018-05-04", 51900)]
    etf_raw = [_daily_row("2015-01-05", 12568), _daily_row("2015-01-06", 12567)]
    etf_adj = [_daily_row("2015-01-05", 10000), _daily_row("2015-01-06", 9999)]  # 반올림 흔들림
    daily = _daily(
        spark,
        [
            ("raw", "005930", "2018-05", raw),
            ("adjusted", "005930", "2018-05", adjusted),
            ("raw", "069500", "2015-01", etf_raw),
            ("adjusted", "069500", "2015-01", etf_adj),
        ],
    )
    [event] = adjustment_events(daily, "r").collect()
    assert (event["symbol"], str(event["trade_date"])) == ("005930", "2018-05-04")
    assert event["step"] == pytest.approx(50.0)


def test_daily_from_minutes_names_the_cause(spark: Any) -> None:
    days = {
        "match": "2026-09-15",
        "broken": "2026-06-29",
        "etf": "2025-10-16",
        "none": "2026-10-23",
    }
    pages = [
        (
            "005930",
            days["match"],
            0,
            [
                _bar(days["match"], "09:00", 100),
                _bar(days["match"], "15:19", 104),
                _bar(days["match"], "15:30", 105, volume=50),
                _bar(days["match"], "16:00", 200, volume=7),
            ],
        ),
        (
            "005930",
            days["broken"],
            0,
            [_bar(days["broken"], "09:00", 100), _bar(days["broken"], "15:19", 104)],
        ),
        (
            "069500",
            days["etf"],
            0,
            [
                _bar(days["etf"], "09:00", 99),
                _bar(days["etf"], "15:19", 99),
                _bar(days["etf"], "15:30", 99),
            ],
        ),
        (
            "005930",
            days["none"],
            0,
            [_bar(days["none"], "09:00", 100), _bar(days["none"], "15:30", 100)],
        ),
    ]
    minutes = _labelled(spark, pages)

    def bar(day: str, o: int, h: int, low: int, c: int, v: int) -> dict[str, str]:
        return _daily_row(day, c, v, stck_oprc=o, stck_hgpr=h, stck_lwpr=low)

    daily = _daily(
        spark,
        [
            ("raw", "005930", "2026-09", [bar(days["match"], 100, 105, 100, 105, 90)]),
            ("adjusted", "005930", "2026-09", [bar(days["match"], 100, 105, 100, 105, 90)]),
            ("raw", "005930", "2026-06", [bar(days["broken"], 100, 104, 100, 103, 20)]),
            ("adjusted", "005930", "2026-06", [bar(days["broken"], 100, 104, 100, 103, 20)]),
            ("raw", "069500", "2025-10", [bar(days["etf"], 100, 100, 100, 100, 30)]),
            ("adjusted", "069500", "2025-10", [bar(days["etf"], 98, 98, 98, 98, 30)]),
        ],
    )
    got = {
        r["trade_date"].isoformat(): r for r in daily_from_minutes(minutes, daily, "r").collect()
    }
    assert got[days["match"]]["result"] == "match_raw"
    assert got[days["match"]]["m_volume"] == 77 and got[days["match"]]["m_after_hours_volume"] == 7
    assert got[days["match"]]["volume_gap"] == 90 - 77
    assert got[days["broken"]]["result"] == "no_closing_bar"
    assert got[days["etf"]]["result"] == "price_basis_differs"
    assert got[days["etf"]]["close_ratio_to_raw"] == pytest.approx(0.99)
    assert got[days["none"]]["result"] == "no_daily"
