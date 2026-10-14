"""AGS Paper — AG-CR 롱 + 232 숏 관찰 장부 (가상 자료)."""

from __future__ import annotations

import pytest

from auto_trading.ops.ag_short_paper import (
    DAY,
    FEE,
    H4,
    SYMBOLS,
    AgDay,
    Bar,
    Book,
    replay,
    short_signals,
)

BTC, ETH = SYMBOLS
T0 = 100 * DAY


def _bars(prices: list[float], *, high_bump: dict[int, float] | None = None) -> list[Bar]:
    bump = high_bump or {}
    return [Bar(T0 + k * H4, p, bump.get(k, p), p, p) for k, p in enumerate(prices)]


def _ag(target_btc: float = 0.0, target_eth: float = 0.0, equity: float = 10_000.0) -> list[AgDay]:
    return [
        AgDay(
            decision_ms=T0 - 1,
            equity=equity,
            target={BTC: target_btc, ETH: target_eth},
            wallet=equity,
            spot_qty={},
            fut_qty={},
        )
    ]


def _run(book: Book, btc: list[float], *, sig: set[int], ag: list[AgDay], **kw: object) -> Book:
    eth = [100.0] * len(btc)
    return replay(
        book,
        start_ms=T0,
        bars={BTC: _bars(btc, **kw), ETH: _bars(eth)},  # type: ignore[arg-type]
        signals={BTC: sig},
        ag_days=ag,
        funding={},
    )


def test_enters_next_open_and_exits_after_48h() -> None:
    prices = [100.0] * 20
    prices[1:] = [90.0] * 19
    b = _run(Book("AGS1", 1.0), prices, sig={0}, ag=_ag())
    t = b.trades[0]
    assert t["entry"] == 90.0 and t["reason"] == "48H"
    assert t["exit_ms"] - t["entry_ms"] == 48 * 3_600_000


def test_no_short_while_long_target_positive() -> None:
    b = _run(Book("AGS1", 1.0), [100.0] * 20, sig={0}, ag=_ag(target_btc=0.2))
    assert b.trades == [] and b.events[0]["skipped"] == "LONG_TARGET_POSITIVE"


def test_stop_at_3pct_with_slippage() -> None:
    b = _run(Book("AGS1", 1.0), [100.0] * 20, sig={0}, ag=_ag(), high_bump={3: 104.0})
    t = b.trades[0]
    assert t["reason"] == "STOP" and t["exit"] == pytest.approx(103.0 * 1.00245)
    assert t["net"] < 0


def test_size_1x_is_10pct_and_3x_is_capped_by_1pct_risk() -> None:
    one = _run(Book("AGS1", 1.0), [100.0] * 20, sig={0}, ag=_ag())
    three = _run(Book("AGS3", 3.0), [100.0] * 20, sig={0}, ag=_ag())
    assert one.trades[0]["qty"] * 100 == pytest.approx(1_000.0)
    assert three.trades[0]["qty"] * 100 == pytest.approx(10_000 * 0.01 / 0.0339)


def test_long_priority_closes_short_when_target_returns() -> None:
    ag = [*_ag(), AgDay(T0 + DAY - 1, 10_000.0, {BTC: 0.3, ETH: 0.0}, 10_000.0, {}, {})]
    b = _run(Book("AGS1", 1.0), [100.0] * 20, sig={4}, ag=ag)
    assert b.trades[0]["reason"] == "LONG_PRIORITY"
    assert b.trades[0]["exit_ms"] == T0 + DAY


def test_own_halt_after_short_losses() -> None:
    b = Book("AGS3", 3.0)
    prices = [100.0] * 60
    bumps = dict.fromkeys(range(3, 60, 6), 104.0)
    _run(b, prices, sig=set(range(0, 60, 6)), ag=_ag(), high_bump=bumps)
    assert b.halted_ms is not None
    assert any(e["skipped"] == "OWN_HALT" for e in b.events)
    loss = -sum(t["net"] for t in b.trades)
    assert loss >= 0.025 * b.peak


def test_fee_charged_both_sides() -> None:
    b = _run(Book("AGS1", 1.0), [100.0] * 20, sig={0}, ag=_ag())
    assert b.trades[0]["net"] == pytest.approx(-2 * 1_000.0 * FEE)


def test_signal_needs_rsi7_cross_and_daily_adx_down() -> None:
    daily = [Bar(T0 - (80 - j) * DAY, 0, 200 - j + 1, 200 - j - 1, 200 - j) for j in range(90)]
    closes = [100 + k for k in range(30)] + [120.0]
    bars = [Bar(T0 + k * H4, c, c, c, c) for k, c in enumerate(closes)]
    got = short_signals(bars, daily)
    assert got == {30}
    rising = [Bar(T0 - (80 - j) * DAY, 0, 100 + j + 1, 100 + j - 1, 100 + j) for j in range(90)]
    assert short_signals(bars, rising) == set()
