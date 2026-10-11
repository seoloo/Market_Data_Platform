"""문서 220 합성 검산 — G = 184 T, 추세 칸 매도 · 재매수, 반반 칸 분리."""

from __future__ import annotations

from typing import Any

from scripts.run_brake_184 import simulate as sim184
from scripts.run_glide_half_220 import simulate


def _data(lev: list[float], trend: list[bool | None]) -> tuple[dict[str, Any], dict[str, float]]:
    days = [f"2020-{1 + k // 20:02d}-{1 + k % 20:02d}" for k in range(len(lev))]
    return (
        {
            "days": days,
            "lev": dict(zip(days, lev, strict=True)),
            "div": dict.fromkeys(days, 50.0),
            "vol": dict.fromkeys(days, 0.4),
            "trend": dict(zip(days, trend, strict=True)),
            "start": days[1],
        },
        dict.fromkeys(days, 100.0),
    )


def _path() -> tuple[list[float], list[bool | None]]:
    lev = [100.0] * 25 + [90.0, 80.0, 65.0, 60.0, 62.0] + [70.0] * 30 + [80.0] * 25
    trend: list[bool | None] = [*([True] * 26), *([False] * 30), *([True] * 29)]
    return lev, trend


def test_g_equals_184_t() -> None:
    data, qqq = _data(*_path())
    a = simulate(data, qqq, 1.0, 0.0)
    b = sim184(data, qqq, ref="T", park="cash")
    assert a["navs"] == b["navs"] and a["after_tax_liq"] == b["after_tax_liq"]
    assert a["t_sales"] == len(b["sales"]) > 0


def test_trend_half_sells_on_off_and_rebuys_on() -> None:
    data, qqq = _data(*_path())
    q = simulate(data, qqq, 0.0, 1.0)
    assert q["q_sales"] == 1 and len(q["q_episodes"]) == 1
    ep = q["q_episodes"][0]
    assert ep["start"] == data["days"][27]  # 전날(26) 꺼짐 → 27 일 종가에 팖
    assert q["t_sales"] == 0
    assert 0 < q["q_out_share"] < 1


def test_half_runs_both_rules_on_their_own_slices() -> None:
    data, qqq = _data(*_path())
    h = simulate(data, qqq, 0.5, 0.5)
    assert h["t_sales"] > 0 and h["q_sales"] == 1
    g = simulate(data, qqq, 1.0, 0.0)
    q = simulate(data, qqq, 0.0, 1.0)
    lo, hi = sorted([g["after_tax_liq"], q["after_tax_liq"]])
    assert (
        lo - 1.0 <= h["after_tax_liq"] <= hi + 1.0
    )  # 세금 · 맞추기 때문에 정확한 평균은 아니지만 범위 안
