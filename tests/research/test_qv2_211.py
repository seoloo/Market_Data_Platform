"""문서 211 — 거래대금 봉 T/v2 신호의 인과성 · 공식 검산 (합성)."""

from __future__ import annotations

from array import array

from scripts.run_qb_dc_210 import DAY, MIN, Market
from scripts.run_qv2_211 import _tv2, build_211

PER = DAY // MIN


def _market(days: int) -> Market:
    n = days * PER
    prices = [100.0 * (1 + 0.02 * ((i // 997) % 7 - 3) / 3) + i * 1e-4 for i in range(n)]
    qv = [1.0] * (30 * PER) + [144.0] * (n - 30 * PER)  # 30 일 뒤엔 10 분마다 한 봉
    a = array("d", prices)
    return Market(array("d", prices), array("d", prices), array("d", prices), a, array("d", qv), 0)


def test_tv2_formula() -> None:
    xs = [100.0] * 121
    xs[-1] = 110.0
    assert _tv2(xs, 120, (20, 60, 120)) == 1.0
    xs[-21] = 120.0  # 20 기간 전보다 낮음 → (−1 + 1 + 1) / 3
    assert abs(_tv2(xs, 120, (20, 60, 120)) - 1 / 3) < 1e-12
    xs[-1] = 90.0
    assert _tv2(xs, 120, (20, 60, 120)) == 0.0  # 롱만


def test_qv2_causal_prefix() -> None:
    full = build_211(_market(34))
    part = build_211(_market(33))
    n = len(part["QV2"])
    assert full["QV2"][:n] == part["QV2"]
    assert any(x > 0 for x in part["QV2"]) and any(x == 0 for x in part["QV2"])
