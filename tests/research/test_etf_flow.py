from __future__ import annotations

from datetime import date

import pytest

from auto_trading.research.etf_flow import (
    MINUTE_MS,
    FlowRow,
    Minutes,
    Trade,
    abnormal,
    complete_vs,
    is_session,
    net_return,
    parse_farside,
    parse_num,
    short_ratio,
    signal_23,
    signal_24,
    simulate,
    threshold,
)

PAGE = """
<table class="etf"><tr><th></th><th>IBIT</th><th>GBTC</th><th>Total</th></tr>
<tr><td>Fee</td><td>0.25%</td><td>1.50%</td><td></td></tr>
<tr><td><span>11 Jan 2024</span></td><td>111.7</td><td>(95.1)</td><td>16.6</td></tr>
<tr><td>15 Jan 2024</td><td>-</td><td>-</td><td>0.0</td></tr>
<tr><td>16 Jan 2024</td><td>1,212.7</td><td>-</td><td>1,212.7</td></tr>
<tr><td>Total</td><td>1</td><td>2</td><td>3</td></tr>
</table>
"""


def test_parse_num() -> None:
    assert parse_num("(95.1)") == -95.1
    assert parse_num("1,212.7") == 1212.7
    assert parse_num("-") is None
    assert parse_num("") is None


def test_parse_farside_rows_and_sessions() -> None:
    rows = parse_farside(PAGE)
    assert [r.day for r in rows] == [date(2024, 1, 11), date(2024, 1, 15), date(2024, 1, 16)]
    assert rows[0].cells == {"IBIT": 111.7, "GBTC": -95.1}
    assert rows[0].total == pytest.approx(16.6)
    assert [is_session(r) for r in rows] == [True, False, True]
    # 최종본에서 숫자인 칸이 빠져 있으면 미완성
    final = FlowRow(date(2024, 1, 16), {"IBIT": 1.0, "GBTC": 2.0}, 3.0)
    assert not complete_vs(rows[2], final)
    assert complete_vs(rows[0], final)


def test_threshold_and_signal_23_use_only_past() -> None:
    assert threshold([1.0, 2.0, 3.0, 4.0, 5.0], 0.8) == 4.0
    totals = [float(i) for i in range(60)] + [100.0]
    assert signal_23(totals, 59) is None
    assert signal_23(totals, 60) is True
    # 문턱과 같으면 신호 아님, 음수면 문턱을 넘어도 아님
    flat = [-5.0] * 60 + [-1.0]
    assert signal_23(flat, 60) is False
    tie = [1.0] * 60 + [1.0]
    assert signal_23(tie, 60) is False
    # 미래 값이 바뀌어도 과거 신호는 같다
    assert signal_23([*totals, -1e9], 60) is True


def test_short_ratio_dollar_weighted_and_missing_price() -> None:
    rows = [("IBIT", 50.0, 100.0), ("GBTC", 10.0, 100.0), ("NEW", 100.0, 100.0)]
    s = short_ratio(rows, {"IBIT": 40.0, "GBTC": 60.0})
    assert s == pytest.approx((50 * 40 + 10 * 60) / (100 * 40 + 100 * 60))
    assert short_ratio(rows, {}) is None


def test_abnormal_and_signal_24() -> None:
    ratios = [0.5] * 20 + [0.5 + 0.001 * (i % 7) for i in range(60)] + [0.9]
    a = abnormal(ratios)
    assert a[:20] == [None] * 20
    assert a[20] == pytest.approx(0.0)
    i = len(ratios) - 1
    assert signal_24(a, i) is True
    assert signal_24(a, 50) is None  # 직전 60 개 A 가 다 있지 않다


def _minutes(prices: list[tuple[float, float, float, float]], t0: int = 0) -> Minutes:
    return Minutes(
        t=[t0 + k * MINUTE_MS for k in range(len(prices))],
        o=[p[0] for p in prices],
        h=[p[1] for p in prices],
        low=[p[2] for p in prices],
        c=[p[3] for p in prices],
    )


def test_simulate_long_fill_hold_and_exit() -> None:
    bars = [(100.0, 100.0, 100.0, 100.0)] + [(101.0, 101.5, 100.2, 101.0)] * 10
    m = _minutes(bars)
    tr = simulate(m, 1, 1, 3 * MINUTE_MS)
    assert tr is not None
    assert tr.entry == pytest.approx(100.3)  # 저가가 한도에 닿음 → min(100 × 1.003, 시가 101)
    assert tr.fill_ms == MINUTE_MS
    assert tr.exit_ms == 4 * MINUTE_MS  # 체결 분 + 3 분
    assert tr.exit == 101.0  # max(101 × 0.997, 시가 101)
    assert not tr.stopped
    # 첫 분 저가가 한도 위면 다음 분에 직전 종가로 다시 매긴다
    late = _minutes([(100.0,) * 4] + [(101.0, 101.5, 100.5, 101.0)] * 10)
    tr2 = simulate(late, 1, 1, 3 * MINUTE_MS)
    assert tr2 is not None and tr2.fill_ms == 2 * MINUTE_MS and tr2.entry == 101.0


def test_simulate_unfilled_after_retries() -> None:
    # 매분 직전 종가보다 1% 넘게 갭 상승 → 처음 + 다시 3 번 모두 미체결
    bars = [(100.0 * 1.02**k,) * 4 for k in range(10)]
    m = _minutes(bars)
    assert simulate(m, 1, 1, 2 * MINUTE_MS) is None


def test_simulate_short_stop_gap() -> None:
    bars = [(100.0, 100.0, 100.0, 100.0), (100.0, 100.0, 99.0, 100.0), (115.0, 116.0, 114.0, 115.0)]
    bars += [(115.0,) * 4] * 5
    m = _minutes(bars)
    tr = simulate(m, 1, -1, 5 * MINUTE_MS)
    assert tr is not None and tr.stopped
    assert tr.entry == 100.0  # max(100 × 0.997, 시가 100)
    assert tr.exit == pytest.approx(115.0 * 1.001)  # 시가가 스톱(110)을 이미 넘음 → 시가 + 0.1%


def test_net_return_funding_sign() -> None:
    long = Trade(1, 0, 100.0, 10, 110.0, False)
    net, gross, fund = net_return(long, [(5, 0.001), (10, 0.001), (11, 0.5)], 0.001)
    assert gross == pytest.approx(0.10)
    assert fund == pytest.approx(0.002)
    assert net == pytest.approx(0.10 - 0.002 - 0.002)
    short = Trade(-1, 0, 100.0, 10, 90.0, False)
    net, gross, fund = net_return(short, [(5, 0.001)], 0.0)
    assert gross == pytest.approx(0.10)
    assert net == pytest.approx(0.101)  # SHORT 는 양의 요율을 받는다
