"""문서 87 — RSI 눌림목 적립 vs 매달 바로 사기 계산."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from scripts.run_rd_dip import CONTRIBUTION, FEE, irr, simulate, window_starts

D = 86_400_000


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def _days() -> list[int]:
    start = _ms(2020, 1, 1)
    return [start + i * D for i in range(90)]  # 1·2·3월 매일


def test_monthly_buys_immediately() -> None:
    days = _days()
    adj = dict.fromkeys(days, 100.0)
    acc = simulate(days, adj, {}, mode="D", start=days[0], end=days[-1])
    assert acc.buys == 3 and acc.cash == 0.0
    assert acc.final == pytest.approx(3 * CONTRIBUTION * (1 - FEE))


def test_dip_buys_day_after_signal_with_all_cash() -> None:
    days = _days()
    adj = {d: 100.0 if d < _ms(2020, 2, 20) else 80.0 for d in days}
    rsi = dict.fromkeys(days, 50.0) | {_ms(2020, 2, 19): 30.0}
    acc = simulate(days, adj, rsi, mode="R", start=days[0], end=days[-1])
    # 1·2월 입금 2,000 을 2/20 종가(80)에 한 번에, 3월 입금은 현금으로 남는다
    assert acc.buys == 1
    assert acc.shares == pytest.approx(2 * CONTRIBUTION * (1 - FEE) / 80.0)
    assert acc.cash == pytest.approx(CONTRIBUTION)
    assert acc.longest_wait_days >= (_ms(2020, 2, 20) - _ms(2020, 1, 1)) // D


def test_rsi_zero_is_a_signal() -> None:
    days = _days()
    adj = dict.fromkeys(days, 100.0)
    acc = simulate(days, adj, dict.fromkeys(days, 0.0), mode="R", start=days[0], end=days[-1])
    assert acc.buys == 3  # 첫날은 전날 신호가 없어 다음 날 산다


def test_cash_earns_reference_interest() -> None:
    days = _days()
    adj = dict.fromkeys(days, 100.0)
    rate = dict.fromkeys(days, 0.05)
    a = simulate(days, adj, {}, mode="R", start=days[0], end=days[-1], cash_rate=rate)
    b = simulate(days, adj, {}, mode="R", start=days[0], end=days[-1])
    assert a.final > b.final == pytest.approx(3 * CONTRIBUTION)


def test_irr_of_doubling_in_a_year() -> None:
    t0 = _ms(2020, 1, 1)
    assert irr([(t0, -100.0), (t0 + int(365.25 * D), 200.0)]) == pytest.approx(1.0, abs=1e-6)


def test_window_starts_first_trading_day_each_month() -> None:
    days = _days()
    starts = window_starts(days, "2020-01-01", "2020-02-28")
    assert starts == [_ms(2020, 1, 1), _ms(2020, 2, 1)]
