from __future__ import annotations

from scripts.audit_tiingo_terminal_sample import summarize


def _row(day: str, close: float, volume: int, adj: float | None = None) -> dict[str, object]:
    return {
        "date": f"{day}T00:00:00.000Z",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "adjClose": close if adj is None else adj,
        "divCash": 0.0,
        "splitFactor": 1.0,
    }


def test_rows_after_the_known_last_trade_and_zero_volume_are_counted() -> None:
    rows = [_row("2022-10-26", 53.35, 28_064_973), _row("2022-10-27", 53.7, 140_831_508)]
    rows.append(_row("2022-10-28", 53.7, 0))  # 거래 정지일의 채움 행
    s = summarize("TWTR", {"startDate": "2013-11-07", "endDate": "2022-10-28"}, rows)
    assert s["last_date"] == "2022-10-28" and s["meta_end"] == "2022-10-28"
    assert s["rows_after_known_last_trade"] == [{"date": "2022-10-28", "close": 53.7, "volume": 0}]
    assert s["zero_volume_rows"] == 1 and s["adj_differs_rows"] == 0


def test_unknown_last_trade_reports_nothing_after_and_adjusted_differences() -> None:
    rows = [_row("2022-10-10", 27.97, 3_578_429, adj=27.5), _row("2022-10-11", 27.98, 22_430_307)]
    s = summarize("NLSN", {}, rows)
    assert s["rows_after_known_last_trade"] == [] and s["adj_differs_rows"] == 1
    assert summarize("XXX", {}, [])["rows"] == 0
