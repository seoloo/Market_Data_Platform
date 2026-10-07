from __future__ import annotations

from datetime import UTC, datetime

from scripts.run_alt_patterns_159 import (
    BAR_MS,
    END_MS,
    HOLD,
    Trade,
    eligible,
    net_return,
    scan_pair,
    simulate_trade,
)
from tests.research.test_patterns_lmw import IHS

from auto_trading.backtest.ema_box import Bars

T0 = int(datetime(2022, 3, 1, tzinfo=UTC).timestamp() * 1000)


def _bars(closes: list[float], start: int = T0, gap_at: int | None = None) -> Bars:
    t = [
        start + k * BAR_MS + (BAR_MS if gap_at is not None and k >= gap_at else 0)
        for k in range(len(closes))
    ]
    return Bars(
        t,
        list(closes),
        [c * 1.01 for c in closes],
        [c * 0.99 for c in closes],
        list(closes),
        [1.0] * len(closes),
    )


def test_eligible_needs_membership_and_continuous_bars() -> None:
    b = _bars([100.0] * 60)
    assert eligible(b, 40, {"2022-03"})
    assert not eligible(b, 40, {"2022-04"})
    assert not eligible(_bars([100.0] * 60, gap_at=20), 40, {"2022-03"})


def test_scan_pair_finds_textbook_ihs() -> None:
    closes = [100.0 + (0.3 if k % 2 else 0.0) for k in range(150)]
    closes[50 : 50 + len(IHS)] = IHS
    evs, n_ok = scan_pair(_bars(closes), {"2022-03"})
    assert n_ok > 0
    assert [e["kind"] for e in evs] == ["IHS"]


def test_fill_uses_better_of_limit_and_open_then_exits() -> None:
    closes = [100.0] * 80
    b = _bars(closes)
    tr = simulate_trade(b, 10)
    assert isinstance(tr, Trade)
    assert tr.fill == 11 and tr.entry == 100.0  # 시가 100 < 한도 100.5
    assert tr.exit_i == 11 + HOLD + 1 and tr.exit == 100.0  # 시가 100 > 한도 99.5
    assert abs(net_return(tr) - (100 * 0.9985 / (100 * 1.0015) - 1)) < 1e-12


def test_unfilled_delisted_and_incomplete() -> None:
    up = Bars(
        [T0 + k * BAR_MS for k in range(20)],
        [100.0 * 1.02**k for k in range(20)],
        [100.0 * 1.02**k for k in range(20)],
        [100.0 * 1.02**k for k in range(20)],
        [100.0 * 1.02**k for k in range(20)],
        [1.0] * 20,
    )
    assert simulate_trade(up, 2) is None  # 저가가 한도 아래로 안 내려옴
    short = _bars([100.0] * 25)
    tr = simulate_trade(short, 5)
    assert isinstance(tr, Trade) and tr.delisted and tr.exit_i == 24
    tail = _bars([100.0] * 25, start=END_MS - 25 * BAR_MS)
    assert simulate_trade(tail, 5) == "incomplete"
