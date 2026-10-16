"""문서 239 — 지정가 진입 · 청산 체결 판정 · 수익 계산."""

from __future__ import annotations

from auto_trading.research.h1_exec_239 import MAKER, TAKER, entry_filled, exit_fill, short_return


def _b(i: int, o: float, h: float, lo: float, c: float) -> tuple[int, float, float, float, float]:
    return (i * 60_000, o, h, lo, c)


def test_entry_needs_trade_through() -> None:
    touch = [_b(i, 100, 100, 99, 99.5) for i in range(60)]
    assert not entry_filled(touch, 100.0)  # 닿기만 함
    through = [*touch[:30], _b(30, 100, 100.02, 99, 100)]
    assert entry_filled(through, 100.0)
    late = [_b(i, 100, 99.9, 99, 99.5) for i in range(60)] + [_b(60, 100, 101, 99, 100)]
    assert not entry_filled(late, 100.0)  # 60 분 뒤는 안 셈


def test_exit_maker_or_taker_fallback() -> None:
    hit = [_b(0, 100, 100, 99.98, 100)]
    assert exit_fill(hit, 100.0) == (100.0, MAKER)
    miss = [_b(i, 101, 102, 100.5, 101.5) for i in range(60)]
    assert exit_fill(miss, 100.0) == (101.5, TAKER)
    assert exit_fill(miss[:10], 100.0) is None


def test_short_return() -> None:
    r = short_return(100.0, 98.0, 0.001, MAKER, MAKER)
    assert abs(r - 0.5 * (0.02 + 0.001 - MAKER - MAKER * 0.98)) < 1e-12
