from __future__ import annotations

from datetime import date, timedelta

from scripts.run_infinite_194 import Bar, buy_hold, run_infinite, stats, t_value


def _bars(closes: list[float], highs: list[float] | None = None) -> list[Bar]:
    out, d = [], date(2020, 1, 1)
    for i, c in enumerate(closes):
        h = highs[i] if highs else c
        out.append(Bar((d + timedelta(days=i)).isoformat(), c, max(h, c), c, c))
    return out


def test_t_value_rounds_up_to_one_decimal() -> None:
    assert t_value(0.025, 0.025) == 1.0
    assert t_value(0.0251, 0.025) == 1.1
    assert t_value(0.5, 0.025) == 20.0


def test_cycle_buys_on_dips_and_takes_profit_at_ten_percent() -> None:
    # 100 → 내려가며 평단 LOC 로 몇 번 산 뒤
    # → 고가가 평단 +10% 를 넘는 날 3/4 매도 + 종가 ≥ 별지점이면 1/4
    closes = [100.0, 98.0, 97.0, 96.0, 99.0, 110.0, 111.0]
    highs = [100.0, 98.0, 97.0, 96.0, 99.0, 115.0, 111.0]
    r = run_infinite(_bars(closes, highs), cost=0.0)
    assert len(r["cycles"]) == 1 and r["cycles"][0]["pnl"] > 0
    assert r["cycles"][0]["end"] == "2020-01-06"
    assert r["vals"][-1] > 1.0


def test_exhaustion_quarter_stop_sells_and_hold_does_not() -> None:
    closes = [100.0 * (0.985**i) for i in range(120)]  # 계속 내려감 → 원금 소진
    h = run_infinite(_bars(closes), cost=0.0, mode="H")
    q = run_infinite(_bars(closes), cost=0.0, mode="Q")
    assert h["exhausted_days"] > 0 and q["exhausted_days"] > 0
    assert q["vals"][-1] != h["vals"][-1]
    bh = buy_hold(_bars(closes), cost=0.0)
    assert stats(bh["vals"], bh["days"])["mdd"] > stats(h["vals"], h["days"])["mdd"]
