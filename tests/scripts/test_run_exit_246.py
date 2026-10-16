"""246 — 느린 나가기(추세 꺾임 · 고점 −35%) · 판정 규칙 (가짜 판)."""

from __future__ import annotations

import pytest

from scripts import run_exit_242 as base
from scripts import run_exit_246 as m


def test_slow_exit_sells_next_day_after_peak_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "trend_up", lambda xs, t: True)
    monkeypatch.setattr(base, "trend_off", lambda xs, t: False)
    close: list[float | None] = [100.0 + 5 * k for k in range(40)]  # 진입 20 번째 = 200
    for k in range(30, 40):
        close[k] = 150.0  # 고점 245 의 −35% = 159.25 아래 → 다음 날 종가 매도
    arm = base.Exit("F", 0.50, None, None, slow=True)  # −3% 대신 멀리 둬 느린 나가기만 시험
    mk = base.Market(
        [f"2009-{1 + k // 20:02d}-{1 + k % 20:02d}" for k in range(40)],
        {"AAA": close},
        None,
        None,
        [True] * 40,
        lambda t: ["AAA"],
    )
    run = base.simulate_exit(mk, arm, "2009-01-01")
    x = run.trades[0]
    assert x["reason"] == "ma" and x["exit_i"] == 31 and x["ret"] < 0


def test_judge() -> None:
    def row(sh: float, mdd: float, h1: float, h2: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": sh, "mdd": mdd},
            "first": {"sharpe": h1},
            "second": {"sharpe": h2},
        }

    s3 = row(0.25, 0.72, 0.03, 0.41)
    assert m.judge({"S3": s3, "F_S3SLOW": row(0.40, 0.50, 0.10, 0.50)}) == "ADOPT"
    assert m.judge({"S3": s3, "F_S3SLOW": row(0.34, 0.50, 0.10, 0.50)}) == "NONE"  # +0.09
    assert m.judge({"S3": s3, "F_S3SLOW": row(0.40, 0.75, 0.10, 0.50)}) == "NONE"  # 낙폭
    assert m.judge({"S3": s3, "F_S3SLOW": row(0.40, 0.50, 0.01, 0.50)}) == "NONE"  # 앞 반쪽
