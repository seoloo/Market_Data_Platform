"""미국 일봉 이어 붙이기 — 미완성 봉 버리기 · 최근 줄 바로잡기 (2026-10-12 결함 회귀 시험)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from auto_trading.core.types import Candle
from auto_trading.ops.us_daily_bars import last_settled_day, merge, settled, to_row

DAY_MS = 86_400_000


def bar(day: str, close: str, volume: str = "50000000") -> Candle:
    o = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)
    c = Decimal(close)
    return Candle(o, o + DAY_MS - 1, c, c, c, c, Decimal(volume))


def test_settled_day_follows_new_york_close() -> None:
    # 한국 21:10 = 뉴욕 08:10 (장 전) → 그날은 아직 끝나지 않았다
    assert last_settled_day(datetime(2026, 10, 9, 12, 10, tzinfo=UTC)) == "2026-10-08"
    # 뉴욕 16:29 는 아직, 16:30 부터 그날이 끝난 것으로 본다 (EDT, UTC−4)
    assert last_settled_day(datetime(2026, 10, 9, 20, 29, tzinfo=UTC)) == "2026-10-08"
    assert last_settled_day(datetime(2026, 10, 9, 20, 30, tzinfo=UTC)) == "2026-10-09"
    # 한국 다음 날 아침 09:00 = 뉴욕 전날 20:00
    assert last_settled_day(datetime(2026, 10, 10, 0, 0, tzinfo=UTC)) == "2026-10-09"


def test_unsettled_bar_is_not_stored() -> None:
    bars = [bar("2026-10-08", "774.13"), bar("2026-10-09", "776.23", "227325")]
    fresh = settled(bars, "2026-10-08")
    assert list(fresh) == ["2026-10-08"]


def test_partial_rows_already_stored_are_repaired_and_dropped() -> None:
    rows = [
        to_row("2026-10-05", bar("2026-10-05", "774.83")),
        to_row("2026-10-06", bar("2026-10-06", "778.43", "143395")),  # 장 전 미완성 줄
        to_row("2026-10-09", bar("2026-10-09", "776.23", "227325")),  # 아직 끝나지 않은 날
    ]
    fresh = settled([bar("2026-10-06", "770.10"), bar("2026-10-07", "772.00")], "2026-10-08")
    m = merge(rows, fresh, "2026-10-08")
    assert not m.needs_full
    assert [r["date"] for r in m.rows] == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert m.rows[1]["close"] == "770.10" and m.rows[1]["volume"] == "50000000"
    assert (m.appended, m.repaired, m.dropped_unsettled) == (1, 1, 1)


def test_old_close_change_means_adjustment_refetch() -> None:
    rows = [to_row("2026-09-01", bar("2026-09-01", "100"))]
    fresh = settled([bar("2026-09-01", "98")], "2026-10-08")
    assert merge(rows, fresh, "2026-10-08").needs_full


def test_same_values_change_nothing() -> None:
    rows = [to_row("2026-10-07", bar("2026-10-07", "772.00"))]
    m = merge(rows, settled([bar("2026-10-07", "772.00")], "2026-10-08"), "2026-10-08")
    assert (m.appended, m.repaired, m.dropped_unsettled, m.needs_full) == (0, 0, 0, False)
