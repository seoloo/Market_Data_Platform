"""문서 284 — 국장 Sirius: 상장폐지 정답 · 미래 값 안 섞임 · 판정 (순수 함수)."""

from __future__ import annotations

from typing import Any

import pytest
from scripts.run_kr_flow import Flows
from scripts.run_lt_kr import KrPanel

from scripts import run_kr_leader_ml_284 as k


def test_fwd_ret_uses_delisting_and_halt_prices() -> None:
    xs: list[float | None] = [100.0, 110.0, None, 90.0, None, None, None]
    # 자료가 3 에서 끊김 → 마지막 거래일 종가 90
    assert k.fwd_ret(xs, 0, last=3, horizon=5) == pytest.approx(-0.10)
    # 거래정지로 t + h 값이 없으면 그 전 마지막 종가
    assert k.fwd_ret(xs, 0, last=6, horizon=2) == pytest.approx(0.10)
    # 기간 밖이면 모름
    assert k.fwd_ret(xs, 0, last=6, horizon=7) is None


def test_plan_hold() -> None:
    assert [k.plan_hold(s) for s in (6, 5, 4)] == [126, 105, 84]


def _acc(sharpe: float, cagr: float, mdd: float, h1: float | None = None) -> dict[str, Any]:
    half = {"sharpe": sharpe, "cagr": cagr if h1 is None else h1, "mdd": mdd}
    return {"full": {"sharpe": sharpe, "cagr": cagr, "mdd": mdd}, "first": half, "second": half}


def test_judge_plan() -> None:
    kospi = _acc(0.45, 0.07, 0.40)
    ew = _acc(0.30, 0.03, 0.55)
    assert k.judge_plan(_acc(0.60, 0.15, 0.45), ew, kospi)["status"] == "후속 가치(탐색)"
    # 낙폭이 KOSPI + 10%p 를 넘음
    v = k.judge_plan(_acc(0.60, 0.15, 0.55), ew, kospi)
    assert v["status"] == "불명" and not v["checks"]["d_mdd_le_kospi_plus"]
    # 반쪽 하나라도 KOSPI 연수익 아래
    assert k.judge_plan(_acc(0.60, 0.15, 0.45, h1=0.05), ew, kospi)["status"] == "불명"
    assert k.judge_plan(_acc(0.30, 0.02, 0.60), ew, kospi)["status"] == "불리"
    assert k.judge_plan(_acc(0.38, 0.05, 0.50), ew, kospi)["status"] == "불명"


def _panel(n_days: int, n_syms: int, bump: float = 1.0) -> tuple[KrPanel, Flows]:
    days = [f"D{i:04d}" for i in range(n_days)]
    close: dict[str, list[float | None]] = {}
    value: dict[str, list[float]] = {}
    for j in range(n_syms):
        xs: list[float | None] = [
            1000.0 * (1 + 0.0005 * j) ** i * (bump if i > 300 else 1.0) for i in range(n_days)
        ]
        close[f"S{j:02d}"] = xs
        value[f"S{j:02d}"] = [1e9 + j] * n_days
    syms = list(close)
    panel = KrPanel(
        days, close, value, dict.fromkeys(syms, "IT"), dict.fromkeys(syms, 0),
        dict.fromkeys(syms, n_days - 1),
    )  # fmt: skip
    net = {s: [float(j) for _ in days] for j, s in enumerate(syms)}
    return panel, Flows(net, {s: [1e9] * n_days for s in syms})


def test_rows_features_do_not_see_future() -> None:
    p1, f1 = _panel(450, 40)
    p2, f2 = _panel(450, 40, bump=3.0)  # 301 이후만 바뀜
    pools = {300: list(p1.close)}
    r1, r2 = k.kr_rows(p1, f1, pools), k.kr_rows(p2, f2, pools)
    assert len(r1) == 40
    assert [r["x"] for r in r1] == [r["x"] for r in r2]
    assert [r["fwd"] for r in r1] != [r["fwd"] for r in r2]
    # 상위 10% = 40 중 4 개가 정답 1
    assert sum(r["y"] for r in r1) == 4
    assert all(len(r["x"]) == len(k.FEATURES) for r in r1)


def test_rows_skip_cheap_and_small_pools() -> None:
    p, f = _panel(450, 40)
    p.close["S00"] = [500.0] * 450  # 1,000 원 미만
    rows = k.kr_rows(p, f, {300: list(p.close)})
    assert "S00" not in {r["sym"] for r in rows}
    assert k.kr_rows(p, f, {300: list(p.close)[:20]}) == []
