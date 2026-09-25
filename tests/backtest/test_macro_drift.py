"""거시 지표 발표 첫 반응 이벤트 합성 테스트 (49)."""

from __future__ import annotations

import pytest

from auto_trading.backtest.macro_drift import reaction_events
from auto_trading.backtest.perp_events import BAR_MS, Bars, settle_trade

START = 1_609_459_200_000  # 2021-01-01 00:00 UTC


def bars(closes: list[float]) -> Bars:
    opens = [closes[0], *closes[:-1]]
    return Bars(
        open_ms=[START + i * BAR_MS for i in range(len(closes))],
        open=opens,
        high=[max(o, c) + 0.1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 0.1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        oi=[None] * len(closes),
        funding=[None] * len(closes),
    )


def test_first_reaction_defines_side_entry_and_extremes() -> None:
    closes = [100.0] * 10 + [101.0, 102.0, 101.5] + [101.5] * 60
    b = bars(closes)
    (e,) = reaction_events(b, [START + 10 * BAR_MS])
    assert e.side == 1  # 100 → 101.5
    assert e.signal_index == 12 and e.entry_index == 13
    assert e.extreme_high == pytest.approx(102.1)
    assert e.extreme_low == pytest.approx(99.9)  # 반응 첫 봉 시가 100 − 0.1


def test_continuation_long_uses_window_low_as_stop() -> None:
    closes = [100.0] * 10 + [101.0, 102.0, 101.5] + [99.0] * 60  # 되돌아 창 저점 아래로
    b = bars(closes)
    (e,) = reaction_events(b, [START + 10 * BAR_MS])
    t = settle_trade(
        b, e, direction=1, hold_bars=48, cost_per_side=0.0, settlements=[], use_stop=True
    )
    assert t is not None and t.stopped
    assert t.exit == pytest.approx(99.9)  # 시가 101.5 → 저점 98.9 봉에서 스톱 99.9


def test_skips_off_grid_missing_flat_and_overlapping() -> None:
    closes = [100.0] * 10 + [101.0, 102.0, 101.5] + [101.5] * 100
    b = bars(closes)
    t = START + 10 * BAR_MS
    assert reaction_events(b, [t + 60_000]) == []  # 5분 격자 밖
    assert reaction_events(b, [START + 10_000 * BAR_MS]) == []  # 자료 밖
    assert reaction_events(b, [START + 30 * BAR_MS]) == []  # 반응 0
    # 두 번째 발표가 첫 거래(진입 + 48봉) 안에 있으면 건너뛴다
    assert len(reaction_events(b, [t, t + 20 * BAR_MS])) == 1
    with pytest.raises(ValueError):
        reaction_events(b, [t], window_bars=0)


def test_not_enough_bars_after_release() -> None:
    closes = [100.0] * 10 + [101.0, 102.0, 101.5] + [101.5] * 10
    assert reaction_events(bars(closes), [START + 10 * BAR_MS]) == []


@pytest.mark.leakage
def test_later_bars_do_not_change_event() -> None:
    closes = [100.0] * 10 + [101.0, 102.0, 101.5] + [101.5] * 60
    shocked = closes[:13] + [c * 2 for c in closes[13:]]
    t = [START + 10 * BAR_MS]
    assert reaction_events(bars(closes), t) == reaction_events(bars(shocked), t)
