from __future__ import annotations

from datetime import date, timedelta

from scripts.run_brake_183 import signals
from scripts.run_brake_184 import Book, irr, simulate, tax_of


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2020, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_book_moving_average_gain_and_tax() -> None:
    bk = Book()
    bk.buy("L", 1000.0, 10.0)  # 수수료 0.15% 뺀 99.85 주, 원가 1,000
    bk.buy("L", 1000.0, 20.0)
    got = bk.sell("L", 50.0, 30.0, "2026")
    avg = 2000.0 / (99.85 + 49.925)
    assert round(bk.gain["2026"], 6) == round(got - avg * 50.0, 6)
    assert tax_of(2_400_000) == 0 and round(tax_of(3_500_000)) == 220_000


def test_irr_matches_simple_case() -> None:
    flows = [("2020-01-01", 100.0)]
    assert abs(irr(flows, "2021-01-01", 110.0) - 0.0997) < 0.002


def test_simulate_variants_run_and_brake_sales_realize_gains() -> None:
    days = _days(700)
    px: dict[str, float] = {}
    for i, d in enumerate(days):
        if i < 300:
            px[d] = 100 * 1.003**i
        elif i < 360:
            px[d] = px[days[299]] * (1 - 0.5 * (i - 299) / 60)
        else:
            px[d] = px[days[359]] * 1.002 ** (i - 359)
    div = {d: 50.0 * 1.0002**i for i, d in enumerate(days)}
    _, trend = signals(days, px)
    data = {
        "days": days,
        "lev": px,
        "div": div,
        "vol": dict.fromkeys(days, 0.1),
        "trend": trend,
        "rf": dict.fromkeys(days, 2.0),
        "start": days[130],
    }
    m0 = simulate(data, px, ref=None)
    t = simulate(data, px, ref="T", park="cash")
    tq = simulate(data, px, ref="T", park="Q")
    tm = simulate(data, px, ref="T", park="cash", monthly=True)
    assert not m0["sales"] and len(t["sales"]) >= 2 and t["episodes"]
    assert tq["all_sells"] > t["all_sells"]  # 보관처 정리 매도가 더 있다
    assert len(tm["sales"]) <= len(t["sales"])
    for s in (m0, t, tq, tm):
        assert s["after_tax_liq"] <= s["after_tax_hold"] + 1e-6
        assert s["contrib"] == m0["contrib"]


def test_ref_lev_keeps_brake_days_on_dollar_peak() -> None:
    from scripts.run_glide_krw_196 import to_krw

    days = _days(700)
    px: dict[str, float] = {}
    for i, d in enumerate(days):
        if i < 300:
            px[d] = 100 * 1.003**i
        elif i < 360:
            px[d] = px[days[299]] * (1 - 0.5 * (i - 299) / 60)
        else:
            px[d] = px[days[359]] * 1.002 ** (i - 359)
    div = {d: 50.0 * 1.0002**i for i, d in enumerate(days)}
    _, trend = signals(days, px)
    data = {
        "days": days,
        "lev": px,
        "div": div,
        "vol": dict.fromkeys(days, 0.1),
        "trend": trend,
        "rf": dict.fromkeys(days, 2.0),
        "start": days[130],
    }
    # 하락 구간에 원화 약세(환율 상승) — 원화 가격 낙폭은 달러보다 작다
    fx = {
        d: 1000.0 * (1 + 0.8 * min(max(i - 299, 0), 60) / 60)
        for i, d in enumerate(days)
        if i % 2 == 0
    }
    assert to_krw({days[1]: 2.0}, days[:2], fx) == {days[1]: 2000.0}  # 빈 날은 앞 값
    k_data = {**data, "lev": to_krw(px, days, fx), "div": to_krw(div, days, fx)}
    k_q = to_krw(px, days, fx)
    u = simulate(data, px, ref="T", park="cash")
    k = simulate(k_data, k_q, ref="T", park="cash", ref_lev=px)
    k_own = simulate(k_data, k_q, ref="T", park="cash")
    assert [s["day"] for s in k["sales"]] == [s["day"] for s in u["sales"]]
    assert len(k_own["sales"]) < len(u["sales"])  # 원화 고점으로 재면 낙폭 10% 라 안 판다
