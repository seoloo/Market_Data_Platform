from __future__ import annotations

from datetime import date, timedelta

from scripts.run_brake_183 import breakeven_days, signals, simulate


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2020, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_brake_sells_in_two_steps_and_rebuys_after_trend_turns_back_on() -> None:
    days = _days(400)
    # 오름 → 40% 하락 → 회복 (레버리지 가격 = 신호 가격, 배당은 고정)
    px: dict[str, float] = {}
    for i, d in enumerate(days):
        if i < 150:
            px[d] = 100 + i * 0.2
        elif i < 200:
            px[d] = 130 * (1 - 0.4 * (i - 150) / 50)
        else:
            px[d] = 78 + (i - 200) * 0.5
    div = dict.fromkeys(days, 50.0)
    vol, trend = signals(days, px)
    vol = dict.fromkeys(days, 0.10)  # 변동성 맞춤은 끄고(60%) 브레이크만 본다
    rf = dict.fromkeys(days, 4.0)
    base = simulate(days, px, div, vol, trend, rf, start=days[0], ref=None)
    t = simulate(days, px, div, vol, trend, rf, start=days[0], ref="T")
    assert not base["sales"]
    assert [s["dd"] >= 0.15 for s in t["sales"]] == [True, True]
    assert t["sales"][1]["dd"] >= 0.30
    assert len(t["episodes"]) == 1 and t["episodes"][0]["rebuy"] > 0
    tb = simulate(days, px, div, vol, trend, rf, start=days[0], ref="T", park="tbill")
    assert tb["episodes"][0]["back_usd"] > t["episodes"][0]["back_usd"] * 0.99


def test_breakeven_days() -> None:
    # 연 4% · 세후 84.6% → 수수료 0.2% 를 메우는 데 약 15 거래일
    assert 14 < breakeven_days(4.0) < 16
    assert breakeven_days(0.0) == float("inf")
