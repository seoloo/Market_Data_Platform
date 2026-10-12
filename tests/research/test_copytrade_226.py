"""문서 226 — 고르기 · 물타기 · 진입 전 24 시간 · 추세 · 요약 계산."""

from __future__ import annotations

from auto_trading.research.copytrade_226 import (
    DAY_MS,
    HOUR_MS,
    adds_against,
    entry_move,
    pick_top,
    summarize_trader,
    trend_on,
)


def test_pick_top_filters_inactive_and_thin() -> None:
    rows = [
        {"leadPortfolioId": "a", "status": "ACTIVE"},
        {"leadPortfolioId": "b", "status": "CLOSED"},
        {"leadPortfolioId": "c", "status": "ACTIVE"},
        {"leadPortfolioId": "d", "status": "ACTIVE"},
    ]
    got = pick_top(rows, {"a": 50, "b": 99, "c": 5, "d": 30}, n=2)
    assert [r["leadPortfolioId"] for r in got] == ["a", "d"]


def test_adds_against_detects_averaging_down() -> None:
    pos = {"symbol": "X", "side": "Long", "opened": 10_000, "closed": 50_000}
    fills = [
        {"symbol": "X", "side": "BUY", "positionSide": "LONG", "time": 10_000, "price": 100},
        {"symbol": "X", "side": "BUY", "positionSide": "LONG", "time": 20_000, "price": 95},
        {"symbol": "X", "side": "SELL", "positionSide": "LONG", "time": 50_000, "price": 99},
        {"symbol": "Y", "side": "BUY", "positionSide": "LONG", "time": 20_000, "price": 1},
    ]
    assert adds_against(pos, fills) == (2, True)
    up = [dict(fills[0]), {**fills[1], "price": 105}]
    assert adds_against(pos, up) == (2, False)
    assert adds_against(pos, []) is None


def test_entry_move_uses_only_closed_bars() -> None:
    bars = [(i * HOUR_MS, 100.0 + i) for i in range(48)]
    at = 30 * HOUR_MS + 10  # 30 시 직후 진입 → 마지막 닫힌 봉은 29 시 열림 봉
    got = entry_move(bars, at)
    assert got is not None
    assert abs(got - ((100 + 29) / (100 + 5) - 1)) < 1e-12
    assert entry_move(bars, 10 * HOUR_MS) is None


def test_trend_on() -> None:
    up = [(i * DAY_MS, 100.0 + i) for i in range(200)]
    assert trend_on(up, 200 * DAY_MS) is True
    down = [(i * DAY_MS, 300.0 - i) for i in range(200)]
    assert trend_on(down, 200 * DAY_MS) is False
    assert trend_on(up[:50], 50 * DAY_MS) is None


def test_summarize_trader_basic_shape() -> None:
    def pos(sym: str, side: str, o: int, c: int, pnl: float) -> dict[str, object]:
        return {
            "symbol": sym,
            "side": side,
            "opened": o,
            "closed": c,
            "closingPnl": pnl,
            "leverage": "5",
        }

    ps = [
        pos("BTCUSDT", "Long", 0, HOUR_MS // 2, 10.0),
        pos("ETHUSDT", "Short", 0, 2 * DAY_MS, 5.0),
        pos("XUSDT", "Long", 3 * DAY_MS, 11 * DAY_MS, -30.0),
    ]
    s = summarize_trader(
        ps, [], moves=[-0.01, 0.02, None], trends=[True, False, None], since_ms=0, split_ms=DAY_MS
    )
    assert s["positions"] == 3
    assert s["pnl"] == -15.0
    assert abs(s["major_share"] - 2 / 3) < 1e-12
    assert s["hold_buckets"]["<1h"] == 1 / 3
    assert s["hold_buckets"][">7d"] == 1 / 3
    assert s["worst_over_avg_win"] == -30.0 / 7.5
    assert s["entry_after_drop_share"] == 0.5
    assert s["btc_trend_on_share"] == 0.5
    assert s["halves_pnl"] == [10.0, -25.0]
