"""258 — 익절 세 가지(전부 · 절반 · 따라 올림)와 대조(256 T_ARM 과 같음) · 판정 (가짜 자료)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from scripts.run_exit_242 import Market

from scripts import run_dip_256 as dip
from scripts import run_takeprofit_258 as m


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2008, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _mk(xs: list[float | None]) -> Market:
    n = len(xs)
    return Market(_days(n), {"X": xs}, None, None, [True] * n, lambda _t: ["X"])


def _path() -> list[float | None]:
    # 130 일 꾸준히 오름(추세) → 매수(t=130) 뒤 +30% 까지 오르고 → 크게 빠짐
    xs: list[float | None] = [100.0 * 1.003**t for t in range(131)]
    entry = xs[130]
    assert entry is not None
    xs += [entry * (1 + 0.01 * k) for k in range(1, 31)]  # +30%
    top = xs[-1]
    assert top is not None
    xs += [top * (1 - 0.01 * k) for k in range(1, 40)]
    return xs


def test_arm_matches_256_trend_arm() -> None:
    mk = _mk(_path())
    a = m.simulate(mk, "ARM", start_date=mk.days[0])
    b = dip.simulate(mk, "T", True, start_date=mk.days[0])
    assert a.equity == pytest.approx(b.equity)


def test_tp20_sells_all_at_first_close_over_20pct() -> None:
    mk = _mk(_path())
    r = m.simulate(mk, "TP20", start_date=mk.days[0])
    tp = [x for x in r.trades if x["reason"] == "tp"]
    assert tp and tp[0]["exit_px"] / tp[0]["entry_px"] >= 1.2
    assert tp[0]["exit_px"] / tp[0]["entry_px"] < 1.21


def test_half20_sells_half_then_rest() -> None:
    mk = _mk(_path())
    r = m.simulate(mk, "HALF20", start_date=mk.days[0])
    reasons = [x["reason"] for x in r.trades]
    assert reasons[0] == "tp_half" and r.trades[0]["w"] == pytest.approx(0.05)
    assert len(r.trades) == 2  # 나머지 절반은 느린 나가기 또는 손절로


def test_trail20_stop_follows_peak() -> None:
    mk = _mk(_path())
    r = m.simulate(mk, "TRAIL20", start_date=mk.days[0])
    tr = [x for x in r.trades if x["reason"] == "trail"]
    assert tr
    # 최고 +30% × 0.85 ≈ +10.5% 근처에서 판다(국장식 종가 확인)
    assert 1.09 < tr[0]["exit_px"] / tr[0]["entry_px"] < 1.12


def test_judge() -> None:
    def row(s: float, s1: float, s2: float, mdd: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": s, "mdd": mdd},
            "first": {"sharpe": s1},
            "second": {"sharpe": s2},
        }

    base = row(0.75, 0.43, 0.95, 0.28)
    us = {"ARM": base, "TP20": row(0.90, 0.5, 1.0, 0.30), "HALF20": row(0.80, 0.5, 1.0, 0.2)}
    us["TRAIL20"] = row(0.70, 0.5, 1.0, 0.2)
    kr = {k: row(0.3, 0, 0, 0.5) for k in m.ARMS}
    assert m.judge(us, kr)["verdict"] == "ADOPT_TP20"
    kr["TP20"] = row(0.2, 0, 0, 0.5)
    assert m.judge(us, kr)["verdict"] == "US_ONLY"
    us["TP20"] = row(0.80, 0.5, 1.0, 0.30)
    assert m.judge(us, kr)["verdict"] == "KEEP_ARM"
