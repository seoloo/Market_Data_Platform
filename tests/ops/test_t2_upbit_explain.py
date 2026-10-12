"""Cruise 알림 설명 (2026-10-12 시안 B) — 추세 아이콘 · 점수 계산."""

from __future__ import annotations

from decimal import Decimal

from auto_trading.ops.t2_upbit import CoinSignal, score_calc, trend_arrows, trend_icons

D = Decimal


def _sig(parts: tuple[str, str, str], vol: str, raw: str, weight: str | None = None) -> CoinSignal:
    p = tuple(D(x) for x in parts)
    w = D(weight if weight is not None else raw)
    return CoinSignal(sum(1 for x in p if x > 0), (p[0], p[1], p[2]), D(vol), D(raw), w, D(0))


def test_trend_icons_and_arrows_in_20_60_120_order() -> None:
    sg = _sig(("-0.0321", "0.2646", "0.1630"), "0.368", "0.1359")
    assert trend_icons(sg) == "20일 🔻-3.2% · 60일 🔺+26.5% · 120일 🔺+16.3%"
    assert trend_arrows(sg) == "🔻🔺🔺"


def test_score_calc_like_btc_2026_10_11() -> None:
    sg = _sig(("-0.0321", "0.2646", "0.1630"), "0.368", "0.1359")
    assert score_calc(sg, D(1)) == "점수 3/3 → 1/3 (오름 2 − 내림 1) × 최대 비중 40.8% = 13.6%"
    assert score_calc(sg, D(1) / 3) == "점수 1/3 (오름 2 − 내림 1) × 최대 비중 40.8% = 13.6%"


def test_score_calc_long_only_zero_and_sum_scaling() -> None:
    off = _sig(("-0.1", "-0.2", "0.1"), "0.368", "0")
    assert score_calc(off, D(1) / 3) == "점수 1/3 → -1/3 (오름 1 − 내림 2) → 롱만이라 0%"
    scaled = _sig(("0.1", "0.2", "0.1"), "0.2", "0.5", "0.42")
    assert score_calc(scaled).endswith("× 최대 비중 50.0% = 50.0% → 세 코인 합 100% 넘어 42.0%")
