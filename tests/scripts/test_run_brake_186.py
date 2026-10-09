from __future__ import annotations

from datetime import date, timedelta

from scripts.run_brake_185 import simulate as sim185
from scripts.run_brake_186 import ma_above, macd_above, rsi_wilder, simulate, verdict


def _days(n: int) -> list[str]:
    out, d = [], date(2000, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_indicators_basic_values() -> None:
    up = [float(i) for i in range(1, 60)]
    assert rsi_wilder(up)[-1] == 100.0 and rsi_wilder(up)[13] is None
    down = [float(60 - i) for i in range(60)]
    assert (rsi_wilder(down)[-1] or 0) < 1
    assert ma_above(up)[-1] is True and ma_above(up)[10] is None
    assert macd_above(down)[-1] is False and macd_above(up)[-1] is True


def test_trend_rebuy_matches_185_t0() -> None:
    days = _days(400)
    px = {
        d: 100
        * (
            1.004**i
            if i < 200
            else 1.004**200 * (0.99 ** (i - 199) if i < 240 else 0.99**40 * 1.006 ** (i - 239))
        )
        for i, d in enumerate(days)
    }
    trend = {d: (None if i < 10 else not (205 <= i < 300)) for i, d in enumerate(days)}
    a = simulate(days, px, trend, trend)
    b = sim185(days, px, trend)
    assert (a["cagr"], a["mdd"], a["sales"], a["rebuys"]) == (
        b["cagr"],
        b["mdd"],
        b["sales"],
        b["rebuys"],
    )
    bh = simulate(days, px, trend, None)
    assert bh["sales"] == 0


def test_verdict_two_thirds_rule() -> None:
    def row(c: float) -> dict[str, float]:
        return {"cagr": c, "mdd": 0.5}

    res = {
        str(i): {
            "trend": row(0.1),
            "rsi": row(0.11 if i < 9 else 0.0),
            "macd": row(0.09),
            "ma50": row(0.1),
        }
        for i in range(13)
    }
    v = verdict(res)
    assert (
        v["need_wins"] == 9 and v["rsi"]["wins"] == 9 and v["rsi"]["pass"] and v["choice"] == "rsi"
    )
    assert not v["macd"]["pass"] and not v["ma50"]["pass"]  # 차이 중앙값 0 은 통과 아님
