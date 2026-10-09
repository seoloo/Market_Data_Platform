from __future__ import annotations

from datetime import date, timedelta

from scripts.run_brake_185 import simulate, verdict


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2000, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _path(days: list[str]) -> dict[str, float]:
    # 오름 → 25% 하락 → V 자 반등
    px: dict[str, float] = {}
    for i, d in enumerate(days):
        if i < 200:
            px[d] = 100 * 1.004**i
        elif i < 230:
            px[d] = px[days[199]] * (1 - 0.25 * (i - 199) / 30)
        else:
            px[d] = px[days[229]] * 1.01 ** (i - 229)
    return px


def test_price_rebuy_catches_v_rebound_before_trend() -> None:
    days = _days(300)
    px = _path(days)
    trend = {d: (None if i < 10 else i < 215) for i, d in enumerate(days)}
    t0 = simulate(days, px, trend)
    a = simulate(days, px, trend, price_rebuy=True)
    assert t0["sales"] == 1 and t0["rebuys"] == 0  # 추세가 다시 켜지기 전 → 아직 보관
    assert a["rebuys"] == 1 and a["cagr"] > t0["cagr"]


def test_trend_sell_skips_dip_while_trend_on() -> None:
    days = _days(300)
    px = _path(days)
    trend_on = {d: (None if i < 10 else True) for i, d in enumerate(days)}
    assert simulate(days, px, trend_on, trend_sell=True)["sales"] == 0
    assert simulate(days, px, trend_on)["sales"] == 1
    assert simulate(days, px, trend_on, brake=False)["sales"] == 0


def test_verdict_counts_wins_and_medians() -> None:
    def row(c: float, m: float) -> dict[str, float]:
        return {"cagr": c, "mdd": m}

    res = {
        str(i): {
            "T0": row(0.1, 0.5),
            "A": row(0.11, 0.52),
            "B": row(0.09, 0.5),
            "AB": row(0.1, 0.6),
        }
        for i in range(12)
    }
    v = verdict(res)
    assert v["A"]["pass"] and not v["B"]["pass"] and not v["AB"]["pass"] and v["choice"] == "A"
