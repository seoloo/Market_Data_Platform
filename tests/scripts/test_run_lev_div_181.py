from __future__ import annotations

import pytest
from scripts.run_lev_div_181 import monthly, total_return_index


def test_total_return_index_adds_daily_dividend() -> None:
    days = ["2020-01-01", "2020-01-02"]
    out = total_return_index(days, {days[0]: 100.0, days[1]: 101.0}, 0.0252)
    assert out[days[1]] == pytest.approx(100.0 * (1.01 + 0.0001))


def test_monthly_uses_last_day_of_month() -> None:
    lv = {"2020-01-30": 100.0, "2020-01-31": 110.0, "2020-02-28": 121.0}
    assert monthly(lv) == {"2020-02": pytest.approx(0.10)}
