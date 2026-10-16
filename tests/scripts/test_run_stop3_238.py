"""238 — 고정 −3% 장중 손절 체결(갭 반영) · 판정 규칙 (가짜 판)."""

from __future__ import annotations

import pytest
from scripts.run_lt_leaders import Panel

from scripts import run_stop3_238 as m


def _panel(n: int = 30) -> tuple[Panel, m.Ohlc]:
    days = [f"2009-{1 + k // 20:02d}-{1 + k % 20:02d}" for k in range(n)]
    close: list[float | None] = [100.0] * n
    low: list[float | None] = [99.0] * n
    op: list[float | None] = [100.0] * n
    # 21 일째(2 월 첫날) 진입(100) → 23 일째 장중 저가 96.5 (손절 97 에 체결)
    low[22] = 96.5
    return Panel(days, {"AAA": close}, {"AAA": "Tech"}), m.Ohlc({"AAA": op}, {"AAA": low})


def test_fixed_stop_fills_at_level_or_gap_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "FROM", "2009-01-01")
    monkeypatch.setattr(m, "leaders", lambda panel, t: [("AAA", 1.0)])
    monkeypatch.setattr(m, "trend_up", lambda xs, t: True)
    panel, ohlc = _panel()
    arm = m.Arm("S3", 0.25, 4, 0.03, False)
    run = m.simulate_stop(panel, ohlc, [True] * 30, arm)
    stop = next(tr for tr in run.trades if tr["reason"] == "stop")
    assert stop["exit_px"] == pytest.approx(97.0)  # 저가가 손절가 아래 → 손절가 체결
    ohlc.open["AAA"][22] = 95.0  # 시가가 이미 손절가 아래(갭) → 시가 체결
    run2 = m.simulate_stop(panel, ohlc, [True] * 30, arm)
    gap = next(tr for tr in run2.trades if tr["reason"] == "stop")
    assert gap["exit_px"] == pytest.approx(95.0)
    st = m.stop_stats(panel, run2, 0.03)
    assert st["stops"] >= 1 and st["gap_share"] > 0 and st["recovered_within_20d"] > 0


def test_judge_rules() -> None:
    def row(
        sh: float, cagr: float, mdd: float, c1: float, c2: float
    ) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": sh, "cagr": cagr, "mdd": mdd},
            "first": {"cagr": c1},
            "second": {"cagr": c2},
        }

    base = row(0.6, 0.10, 0.30, 0.05, 0.15)
    assert m.judge(base, row(0.45, 0.09, 0.2, 0.05, 0.15)) == "S3_HARMFUL"  # 샤프 −0.15
    assert m.judge(base, row(0.6, 0.06, 0.2, 0.05, 0.15)) == "S3_HARMFUL"  # 연 −4%p
    assert m.judge(base, row(0.75, 0.12, 0.25, 0.06, 0.16)) == "S3_BETTER"
    assert m.judge(base, row(0.75, 0.12, 0.35, 0.06, 0.16)) == "S3_SIMILAR"  # 낙폭이 더 큼
