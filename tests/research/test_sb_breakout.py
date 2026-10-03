"""문서 111 SB: 돌파 조건(베이스·추격 금지·거래량)과 나가기(돌파 실패·굼뜬 종목·10 일선)."""

from __future__ import annotations

from datetime import date, timedelta

from scripts.run_sb_breakout import Series, simulate_symbol


def _dates(n: int) -> list[str]:
    start = date(2010, 1, 4)
    return [(start + timedelta(days=k)).isoformat() for k in range(n)]


def _series(closes: list[float], volumes: list[float]) -> Series:
    return Series(
        _dates(len(closes)),
        list(closes),
        [c * 1.001 for c in closes],
        [c * 0.999 for c in closes],
        list(closes),
        list(volumes),
    )


def _rally_then_base(
    breakout_close: float, breakout_volume: float
) -> tuple[list[float], list[float]]:
    closes = [100.0] * 60
    closes += [100.0 + k for k in range(1, 81)]  # 80 일 동안 +80% (모멘텀)
    closes += [180.0 + (1.0 if k % 2 else -1.0) for k in range(20)]  # 좁은 베이스 (약 ±1%)
    closes += [breakout_close]
    volumes = [1_000.0] * (len(closes) - 1) + [breakout_volume]
    return closes, volumes


def test_breakout_entered_and_failed_breakout_sells_below_pivot() -> None:
    closes, vols = _rally_then_base(183.0, 2_000.0)
    closes += [184.0, 181.0, 181.0]  # 다음 날 오름 → 그다음 날 돌파선(약 181.18) 아래 마감
    vols += [1_000.0] * 3
    s = _series(closes, vols)
    trades = simulate_symbol("X", s, dict.fromkeys(s.dates, True), dict.fromkeys(s.dates, -1.0))
    events = [t for t in trades if t.kind == "E"]
    assert len(events) == 1
    assert events[0].entry == 183.0
    assert events[0].reason == "fail"


def test_no_breakout_when_chasing_or_low_volume() -> None:
    for close, vol in ((195.0, 2_000.0), (183.0, 1_200.0)):  # +5% 넘게 추격 · 거래량 1.2 배
        closes, vols = _rally_then_base(close, vol)
        closes += [close] * 3
        vols += [1_000.0] * 3
        s = _series(closes, vols)
        trades = simulate_symbol("X", s, dict.fromkeys(s.dates, True), dict.fromkeys(s.dates, -1.0))
        assert not [t for t in trades if t.kind == "E"]


def test_no_trades_when_market_filter_off() -> None:
    closes, vols = _rally_then_base(183.0, 2_000.0)
    s = _series(closes + [184.0] * 3, vols + [1_000.0] * 3)
    trades = simulate_symbol("X", s, dict.fromkeys(s.dates, False), dict.fromkeys(s.dates, -1.0))
    assert trades == []
