"""문서 240 — 반응일 · 중복 · 신호 · 초과 수익 · 부트스트랩."""

from __future__ import annotations

from auto_trading.research.pead_240 import (
    COST,
    Event,
    bootstrap_mean,
    dedupe,
    is_signal,
    outcome,
    reaction_index,
)

DAYS = ["2026-07-29", "2026-07-30", "2026-07-31", "2026-08-03"]


def test_reaction_index_after_close_and_premarket() -> None:
    # 2026-07-31 00:30Z = 7/30 20:30 ET (마감 뒤) → 다음 거래일 7/31
    assert reaction_index(DAYS, "2026-07-31T00:30:28.000Z") == 2
    # 7/30 12:00Z = 08:00 ET (장 전) → 그날
    assert reaction_index(DAYS, "2026-07-30T12:00:00.000Z") == 1
    # 토요일 8/1 → 다음 거래일 8/3
    assert reaction_index(DAYS, "2026-08-01T15:00:00.000Z") == 3
    assert reaction_index(DAYS, "2026-08-04T21:00:00.000Z") is None


def test_dedupe() -> None:
    assert dedupe([10, 12, 30, 31, 40]) == [10, 30, 40]


def test_is_signal() -> None:
    close = [100.0] * 21 + [106.0]
    vol = [1.0] * 21 + [2.5]
    assert is_signal(close, vol, 21) == 1
    assert is_signal(close, [1.0] * 22, 21) == 0
    assert is_signal([100.0] * 21 + [94.0], vol, 21) == -1


def test_outcome_and_bootstrap() -> None:
    days = [f"2026-01-{d:02d}" for d in range(1, 31)]
    opn = [100.0] * 30
    close = [100.0] * 30
    close[25] = 110.0
    spy = dict.fromkeys(days, (100.0, 100.0))
    spy[days[25]] = (100.0, 102.0)
    ev = outcome(days, opn, close, 5, spy, hold=20)
    assert ev is not None and abs(ev.excess - (0.10 - COST - 0.02)) < 1e-12
    assert outcome(days, opn, close, 15, spy, hold=20) is None
    m, lo, hi = bootstrap_mean(
        [Event("2026-01-02", 0.06, 0.01), Event("2026-02-02", 0.06, 0.03)], reps=50
    )
    assert abs(m - 0.02) < 1e-12 and lo <= m <= hi
