"""242 — 💹 나가기 후보(−3% · 20 일선 · ATR 따라 올림) · 크기 · 판정 규칙 (가짜 판)."""

from __future__ import annotations

import pytest

from scripts import run_exit_242 as m


def _days(n: int) -> list[str]:
    return [f"2009-{1 + k // 20:02d}-{1 + k % 20:02d}" for k in range(n)]


def _market(
    close: list[float | None], low: list[float | None] | None = None, n_open: float | None = None
) -> m.Market:
    n = len(close)
    lows = {"AAA": low} if low is not None else None
    opens = {"AAA": [n_open if n_open is not None else c for c in close]} if low else None
    return m.Market(_days(n), {"AAA": close}, lows, opens, [True] * n, lambda t: ["AAA"])


@pytest.fixture(autouse=True)
def _rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "trend_up", lambda xs, t: True)


def test_atr_and_sma() -> None:
    xs: list[float | None] = [100.0 + (k % 2) * 2 for k in range(30)]  # 100,102,100,…
    assert m.atr(xs, 29) == pytest.approx(2.0)
    assert m.atr(xs, 10) is None  # 21 개 모자람
    assert m.sma([1.0, 2.0, 3.0], 2, 3) == pytest.approx(2.0)
    assert m.sma([1.0, None, 3.0], 2, 3) is None


def test_weight_is_one_percent_risk_capped_at_25() -> None:
    assert m.weight(100.0, 97.0) == pytest.approx(0.25)  # 1% ÷ 3% = 33% → 25%
    assert m.weight(100.0, 90.0) == pytest.approx(0.10)  # 1% ÷ 10%
    s3 = m.Exit("S3", 0.03, None, None)
    atr3 = m.Exit("B", None, 3.0, None)
    both = m.Exit("C", 0.03, 3.0, None)
    assert m.first_stop(s3, 100.0, None) == pytest.approx(97.0)
    assert m.first_stop(atr3, 100.0, 2.0) == pytest.approx(94.0)
    assert m.first_stop(atr3, 100.0, None) is None  # ATR 없으면 안 삼
    assert m.first_stop(both, 100.0, 2.0) == pytest.approx(97.0)  # 높은 쪽


def test_fixed_stop_fills_at_level_or_gap_open() -> None:
    close: list[float | None] = [100.0] * 30
    low: list[float | None] = [99.5] * 30
    low[22] = 96.5  # 21 일째(2 월 첫날) 100 에 진입 → 23 일째 저가 96.5
    run = m.simulate_exit(_market(close, low), m.ARMS[0], "2009-01-01")
    stop = next(x for x in run.trades if x["reason"] == "stop")
    assert stop["exit_px"] == pytest.approx(97.0) and stop["w"] == pytest.approx(0.25)
    mk = _market(close, low)
    assert mk.open is not None
    mk.open["AAA"][22] = 95.0
    gap = next(x for x in m.simulate_exit(mk, m.ARMS[0], "2009-01-01").trades)
    assert gap["exit_px"] == pytest.approx(95.0)


def test_close_only_market_stops_on_close() -> None:
    close: list[float | None] = [100.0] * 30
    close[23] = 96.0
    run = m.simulate_exit(_market(close), m.ARMS[0], "2009-01-01")
    assert run.trades[0]["exit_px"] == pytest.approx(96.0) and run.trades[0]["reason"] == "stop"


def test_ma_exit_sells_next_day_close() -> None:
    close: list[float | None] = [100.0 + k for k in range(40)]
    close[25] = 120.0  # 20 일선(약 114) 위
    close[26] = 105.0  # −3% 손절(진입 120 × 0.97 = 116.4)보다 먼저 걸리지 않게 종가만 봄
    arm = m.Exit("A", 0.50, None, 20)  # 손절은 멀리 두고 20 일선만 시험
    run = m.simulate_exit(_market(close), arm, "2009-01-01")
    x = run.trades[0]
    assert x["reason"] == "ma" and x["exit_i"] == 27 and x["exit_px"] == pytest.approx(127.0)


def test_atr_trail_ratchets_up_and_never_down() -> None:
    # 진입 뒤 꾸준히 오르다(ATR 1) 급락 → 따라 올라간 손절가에 걸림
    close: list[float | None] = [100.0 + k for k in range(60)]
    close[50] = 130.0
    arm = m.Exit("B", None, 3.0, None)
    run = m.simulate_exit(_market(close), arm, "2009-01-01")
    x = run.trades[0]
    assert x["reason"] == "stop" and x["exit_i"] == 50
    assert x["entry_px"] == pytest.approx(120.0) and x["ret"] > 0  # 최고 149 − 3 ≈ 146 아래


def test_judge_rules() -> None:
    def row(sh: float, mdd: float, h1: float, h2: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": sh, "mdd": mdd},
            "first": {"sharpe": h1},
            "second": {"sharpe": h2},
        }

    s3 = row(0.6, 0.6, 0.4, 0.8)
    us = {
        "S3": s3,
        "A_MA20": row(0.80, 0.30, 0.5, 0.9),
        "B_ATR3": row(0.90, 0.35, 0.5, 0.9),
        "C_S3ATR3": row(0.70, 0.30, 0.5, 0.9),  # 샤프 +0.10 — 모자람
    }
    kr = {
        "S3": row(0.3, 0.5, 0.2, 0.4),
        "A_MA20": row(0.5, 0.4, 0, 0),
        "B_ATR3": row(0.2, 0.4, 0, 0),
        "C_S3ATR3": row(0.6, 0.4, 0, 0),
    }
    out = m.judge(us, kr)
    assert out["verdict"] == "ADOPT_A_MA20"  # B 는 미국 최고지만 국장에서 S3 보다 낮음
    assert out["us_pass"] == ["A_MA20", "B_ATR3"]
    kr["A_MA20"] = row(0.1, 0.4, 0, 0)
    assert m.judge(us, kr)["verdict"] == "US_ONLY"
    us["A_MA20"] = row(0.80, 0.45, 0.5, 0.9)  # 낙폭 40% 넘음
    us["B_ATR3"] = row(0.90, 0.35, 0.3, 0.9)  # 앞 반쪽이 S3 보다 낮음
    assert m.judge(us, kr)["verdict"] == "NONE"
