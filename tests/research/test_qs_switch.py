"""문서 110 QS: 꺼짐이면 현금, 켜짐 전환 때 현금 전액 레버리지, 꺼짐 전환 때 레버리지 매도·세금."""

from __future__ import annotations

from scripts.run_qs_switch import CONTRIBUTION, FEE, TAX, leveraged, simulate


def _months(n: int) -> list[str]:
    return [f"{2010 + k // 12}-{k % 12 + 1:02d}-01" for k in range(n)]


def test_a_buys_every_month_and_never_sells() -> None:
    days = _months(4)
    px = dict.fromkeys(days, 100.0)
    sig = dict.fromkeys(days, False)
    r = simulate(days, px, px, sig, {}, mode="A", start=days[0], end=days[-1])
    assert r.invested == 4 * CONTRIBUTION
    assert abs(r.final - 4 * CONTRIBUTION * (1 - FEE)) < 1e-6
    assert r.trades == 0


def test_qs_pauses_then_goes_leveraged_then_sells() -> None:
    days = _months(6)
    px = dict.fromkeys(days, 100.0)
    # 신호는 전날 값을 오늘 실행한다. 첫날은 꺼짐 취급.
    sig = dict(zip(days, [False, False, True, True, False, False], strict=True))
    r = simulate(days, px, px, sig, {}, mode="QS", start=days[0], end=days[-1])
    # D0·D1·D2 꺼짐(현금 3,000) → D3 켜짐 전환: 현금 4,000 레버리지 → D4 켜짐: 입금 QQQ →
    # D5 꺼짐 전환: 레버리지 매도, 입금은 현금
    assert r.trades == 2
    assert r.off_days == 4
    assert r.tax_paid == 0.0  # 값이 그대로라 손실
    expected = CONTRIBUTION * (1 - FEE) + 4 * CONTRIBUTION * (1 - FEE) ** 2 + CONTRIBUTION
    assert abs(r.final - expected) < 1e-6


def test_tax_on_realized_gain_at_year_end() -> None:
    days = ["2010-01-04", "2010-02-01", "2010-03-01", "2010-03-02"]
    qqq = dict.fromkeys(days, 100.0)
    lev = dict(zip(days, [100.0, 100.0, 100.0, 150.0], strict=True))
    sig = dict(zip(days, [True, True, False, False], strict=True))
    r = simulate(days, qqq, lev, sig, {}, mode="QS", start=days[0], end=days[-1])
    # D1 켜짐 전환: 2,000 레버리지(100) → D2 켜짐: 입금 QQQ → D3 꺼짐 전환: 150 에 매도
    gain = 2 * CONTRIBUTION * (1 - FEE) ** 2 * 1.5 - 2 * CONTRIBUTION
    assert r.trades == 2
    assert abs(r.tax_paid - TAX * gain) < 1e-6
    no_tax = simulate(days, qqq, lev, sig, {}, mode="QS", start=days[0], end=days[-1], tax=False)
    assert abs(no_tax.final - r.final - TAX * gain) < 1e-6


def test_leveraged_price_triples_daily_moves_without_carry() -> None:
    days = ["2020-01-01", "2020-01-02"]
    base = {days[0]: 100.0, days[1]: 101.0}
    lev = leveraged(days, base, {}, 3.0, 0.0)
    assert abs(lev[days[1]] / lev[days[0]] - 1.03) < 1e-12
