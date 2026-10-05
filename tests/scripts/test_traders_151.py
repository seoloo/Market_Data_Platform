from __future__ import annotations

import pytest
from scripts.run_community_150 import KrStock
from scripts.run_traders_151 import ema, ep_trades, sma_full, timing_equity


def test_sma_needs_every_day_traded() -> None:
    assert sma_full([1.0, 2.0, 3.0], 2, 3) == 2.0
    assert sma_full([1.0, None, 3.0], 2, 3) is None
    assert sma_full([1.0, 2.0], 0, 2) is None


def test_ema_and_timing_trade_next_close_with_fee() -> None:
    assert ema([1.0, 2.0], 3) == [1.0, 1.5]
    closes = [100.0, 100.0, 110.0, 121.0]
    on = [True, True, False, False]
    eq = timing_equity(closes, on, 0.01)
    # t1 종가에 삼(비용) → t2 +10% → t3 종가에 팖(비용, 그날 수익 +10% 반영 뒤)
    assert eq[-1] == pytest.approx(0.99 * 1.1 * 1.1 * 0.99)


def _stock(o: list[float], c: list[float], vol: list[float]) -> KrStock:
    n = len(c)
    dates = [f"2020{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(n)]
    return KrStock(dates, o, c, vol, [1.0] * n, ["00"] * n, dict(zip(dates, c, strict=True)))


def test_ep_buys_gap_close_and_stops_at_gap_day_low() -> None:
    n = 40
    o = [100.0] * n
    c = [100.0] * n
    vol = [10.0] * n
    o[25], c[25], vol[25] = 112.0, 115.0, 50.0  # 갭 12% · 거래량 5 배 · 양봉
    for k in range(26, n):
        o[k] = c[k] = 116.0
    low = [99.0] * n
    low[25] = 110.0
    for k in range(26, n):
        low[k] = 115.5
    low[30] = 109.0  # 갭 날 저가 110 아래 → 손절
    s = _stock(o, c, vol)
    trades, _ = ep_trades({"X": s}, {d: {"X"} for d in s.dates}, {"X": low})
    assert len(trades) == 1
    t = trades[0]
    assert t.reason == "stop"
    assert t.ret == pytest.approx(110.0 / 115.0 - 1)


def test_ep_skips_limit_up_close_and_small_volume() -> None:
    n = 30
    o, c, vol = [100.0] * n, [100.0] * n, [10.0] * n
    o[25], c[25], vol[25] = 112.0, 130.0, 50.0  # 상한가 마감 → 살 수 없음
    s = _stock(o, c, vol)
    trades, drop = ep_trades({"X": s}, {d: {"X"} for d in s.dates}, {"X": [99.0] * n})
    assert trades == [] and drop["closes_limit_up_unfillable"] == 1
    o[25], c[25], vol[25] = 112.0, 115.0, 20.0  # 거래량 2 배뿐
    s = _stock(o, c, vol)
    trades, _ = ep_trades({"X": s}, {d: {"X"} for d in s.dates}, {"X": [99.0] * n})
    assert trades == []


def test_bnf_signal_first_cross_only_and_min_price() -> None:
    from scripts.run_lt_kr import KrPanel
    from scripts.run_traders_151 import BNF_MA, bnf_signals

    n = 40
    days = [f"2010-01-{i + 1:02d}" if i < 31 else f"2010-02-{i - 30:02d}" for i in range(n)]
    xs: list[float | None] = [1000.0] * n
    xs[30] = xs[31] = 700.0  # 이틀 연속 아래 → 첫날만 신호
    tiny: list[float | None] = [x / 500 if x else None for x in xs]  # 2 원 → 1.4 원
    panel = KrPanel(
        days, {"A": xs, "B": tiny}, {"A": [1.0] * n, "B": [1.0] * n}, {}, {"A": 0, "B": 0},
        {"A": n - 1, "B": n - 1},
    )  # fmt: skip
    assert BNF_MA == 25
    got = bnf_signals(panel)
    assert sorted(got) == [("A", 30), ("B", 30)]
    assert bnf_signals(panel, 100.0) == [("A", 30)]


def test_bnf_portfolio_puts_one_tenth_per_slot_and_pays_fees() -> None:
    from scripts.run_lt_kr import KrPanel
    from scripts.run_traders_151 import Trade, bnf_portfolio

    days = [f"2010-01-{i + 1:02d}" for i in range(31)]
    xs: list[float | None] = [100.0] * 31
    xs[20], xs[21], xs[25] = 70.0, 70.0, 77.0
    panel = KrPanel(days, {"A": xs}, {"A": [1.0] * 31}, {}, {"A": 0}, {"A": 30})
    trade = Trade("A", days[20], 77.0 / 70.0 - 1, 4, "back_to_ma")  # 21 일 종가 매수 → 25 일
    _, eq = bnf_portfolio(panel, [trade], fee=0.01)
    # 10% 를 넣고 +10% · 양쪽 비용 1% — 나머지 90% 는 현금
    assert eq[-1] == pytest.approx(0.9 + 0.1 * 0.99 * 1.1 * 0.99)
