"""문서 125 — RSI(2)·단기 평균회귀 매매 규칙 (다음 날 종가 실행)."""

from __future__ import annotations

from scripts.run_index_mr import COST, mr_returns, rsi2


def test_rsi2_wilder() -> None:
    r = rsi2([10.0, 11.0, 12.0, 11.0, 10.5])
    assert r[:2] == [None, None] and r[2] == 100.0
    # 첫 평균 g=1, l=0 → 다음 변화 −1: g=0.5, l=0.5 → 50
    assert r[3] == 50.0
    assert r[4] is not None and r[4] < 50.0


def test_mr_enters_next_day_after_oversold_and_exits_above_sma5() -> None:
    up = [100.0 + i * 0.5 for i in range(210)]  # 200 일선 위 상승
    closes = [*up, up[-1] - 3, up[-1] - 6, up[-1] - 1, up[-1] + 4, up[-1] + 5]
    daily, trades, held = mr_returns(closes, 10.0, "sma5")
    t0 = len(up) + 1  # 두 번째 하락일 신호 → 다음 날 진입
    assert daily[t0] == 0.0
    assert abs(daily[t0 + 1] - (-COST)) < 1e-12  # 진입 비용만 (그날 종가에 삼)
    assert trades and trades[-1][0] > t0
    same = mr_returns(closes, 10.0, "sma5", same_close=True)[0]
    assert same != daily  # 같은 날 종가 실행(이론값)은 다르게 나온다
    assert held >= 1
