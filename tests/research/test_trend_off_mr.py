"""문서 126 — 추세 켜짐이면 추세 다리, 꺼짐에서만 평균회귀 (다음 날 종가)."""

from __future__ import annotations

from scripts.run_index_mr import COST
from scripts.run_trend_off_mr import combined


def test_mr_trades_only_while_trend_off() -> None:
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 100.0, 96.0, 97.0, 101.0, 104.0, 106.0]
    days = [f"d{i}" for i in range(len(closes))]
    off = [False] * len(closes)
    daily, trades = combined(days, closes, off, 10.0, "sma5")
    assert any(v != 0.0 for v in daily) and trades  # 꺼짐 기간에 평균회귀로 사고 팔았다
    on = [True] * len(closes)
    d_on, t_on = combined(days, closes, on, 10.0, "sma5")
    assert t_on == []  # 추세 켜짐이면 평균회귀 매매 없음
    assert abs(d_on[1] - (-COST)) < 1e-12  # 첫날 추세 다리 진입 비용
    d_trend, _ = combined(days, closes, off, 10.0, "sma5", use_mr=False)
    assert all(v == 0.0 for v in d_trend)  # 추세만 + 꺼짐 = 현금
