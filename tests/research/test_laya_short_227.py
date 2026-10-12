"""문서 227 — 특징 · 판단 규칙 · 숏 7 일 · 비상 손절 · funding · 무작위 기준."""

from __future__ import annotations

from auto_trading.research.laya_short_227 import (
    DAY_MS,
    FEE,
    Bar,
    claude_ok,
    features,
    laya_ok,
    max_drawdown,
    random_totals,
    simulate,
    state_text,
)


def test_features_and_text_have_no_levels() -> None:
    closes = [100.0 + i for i in range(250)]
    f = features(closes, [0.0001] * 21)
    assert f is not None
    assert abs(f.r1 - (349 / 348 - 1)) < 1e-12
    assert f.trend == 1.0
    text = state_text(f)
    assert "349" not in text and "Bitcoin daily snapshot" in text
    assert features(closes[:100], [0.0001]) is None


def test_rules() -> None:
    f = features([100.0 + i for i in range(250)], [0.0001] * 21)
    assert f is not None
    assert laya_ok("drop", 0.6) and not laya_ok("drop", 0.4) and not laya_ok("flat", 0.9)
    assert claude_ok(f)
    neg = features([100.0 + i for i in range(250)], [-0.0001] * 21)
    assert neg is not None and not claude_ok(neg)
    crash = features([100.0 + i for i in range(243)] + [300.0] * 6 + [250.0], [0.0001] * 21)
    assert crash is not None and not claude_ok(crash)


def _flat(n: int, px: float = 100.0) -> dict[int, Bar]:
    return {i * DAY_MS: Bar(px, px, px, px) for i in range(n)}


def test_simulate_plain_and_stop_and_funding() -> None:
    perp = _flat(40)
    funding = [(i * DAY_MS // 3, 0.0001) for i in range(120)]
    [t] = simulate([0], perp, funding)
    assert t.entry_day == DAY_MS and t.exit_day == 8 * DAY_MS
    assert abs(t.funding - 21 * 0.0001) < 1e-12
    assert abs(t.ret - (21 * 0.0001 - 2 * FEE)) < 1e-12
    perp[3 * DAY_MS] = Bar(100, 120, 100, 110)  # 고가 +20% → 손절
    [s] = simulate([0], perp, [])
    assert s.stopped and abs(s.exit - 115 * 1.01) < 1e-9 and s.exit_day == 4 * DAY_MS
    assert max_drawdown([s], perp) > 0.15


def test_simulate_skips_overlap_and_random_no_overlap() -> None:
    perp = _flat(60)
    got = simulate([0, 2 * DAY_MS, 7 * DAY_MS], perp, [])
    assert [t.signal_day for t in got] == [0, 7 * DAY_MS]
    totals = random_totals([i * DAY_MS for i in range(40)], 3, perp, [], reps=5)
    assert len(totals) == 5
    assert all(abs(x - ((1 - 2 * FEE) ** 3 - 1)) < 1e-9 for x in totals)
