"""251 — 순유동성 계산 · 공표 지연 거래일 · 신호/수익 표 · 차이 · 판정 · 필터 (가짜 자료)."""

from __future__ import annotations

import datetime as dt

import pytest

from scripts import run_liquidity_251 as m


def test_net_liquidity_units_and_rrp_carry() -> None:
    walcl = {"2020-01-01": 4_000_000.0, "2020-01-08": 4_100_000.0}
    tga = {"2020-01-01": 400_000.0, "2020-01-08": 300_000.0}
    rrp = {"2020-01-06": 50.0}  # 첫 수요일엔 아직 없음 → 0, 다음 수요일엔 월요일 값
    got = m.net_liquidity(walcl, tga, rrp)
    assert [d for d, _ in got] == ["2020-01-01", "2020-01-08"]
    assert [v for _, v in got] == pytest.approx([3600.0, 3750.0])


def test_trade_day_waits_for_monday() -> None:
    days = ["2020-01-08", "2020-01-10", "2020-01-14", "2020-01-15"]
    assert m.trade_day(days, "2020-01-08") == 2  # 수 + 5 = 월(13일) → 그 뒤 첫 거래일 화(14일)
    assert m.trade_day(days, "2020-01-15") is None


def _weeks(n: int) -> list[str]:

    d0 = dt.date(2020, 1, 1)
    return [(d0 + dt.timedelta(days=7 * w)).isoformat() for w in range(n)]


def test_build_rows_signal_and_forward() -> None:
    weds = _weeks(12)
    nl = [(d, float(v)) for d, v in zip(weds, [1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4], strict=True)]

    days = [(dt.date(2020, 1, 6) + dt.timedelta(days=7 * w)).isoformat() for w in range(12)]
    close = [100.0 * (1.01**w) for w in range(12)]
    rows = m.build_rows(nl, days, close, k=4, h=2, start="2020-01-01", to="2099-01-01")
    assert rows[0].day == days[4] and rows[0].up  # 5 − 1 > 0
    assert rows[0].fwd == pytest.approx(1.01**2 - 1)
    assert not rows[3].up  # 2 − 5 ≤ 0
    assert len(rows) == 12 - 4 - 2
    cut = m.build_rows(nl, days, close, k=4, h=2, start="2020-01-01", to=days[7])
    assert all(r.day <= days[5] for r in cut)  # 끝 거래일이 to 를 넘으면 뺀다


def test_spread_boot_and_judge() -> None:
    rows = [m.Row(f"2020-{i:03d}", i % 2 == 0, 0.02 if i % 2 == 0 else -0.01) for i in range(40)]
    assert m.spread(rows) == pytest.approx(0.03)
    assert m.boot_p(rows, draws=200) == 0.0
    assert m.spread([r for r in rows if r.up]) is None
    assert m.judge(0.01, 0.01, 0.005, 0.002) == "지지"
    assert m.judge(0.01, 0.01, -0.001, 0.002) == "불명"  # 2008 ~ 2019 반쪽이 반대
    assert m.judge(0.01, 0.01, 0.005, -0.001) == "불명"  # 2020 ~ 2022 빼면 반대
    assert m.judge(0.03, 0.01, 0.005, 0.002) == "불명"
    assert m.judge(0.0, -0.001, 0.01, 0.01) == "불리"


def test_part_and_excl() -> None:
    rows = [m.Row(d, True, 0.0) for d in ("2019-12-30", "2020-06-01", "2023-01-02")]
    assert [r.day for r in m.part(rows, hi=m.SPLIT)] == ["2019-12-30"]
    assert [r.day for r in m.excl(rows)] == ["2019-12-30", "2023-01-02"]


def test_filter_path_cash_and_switch_cost() -> None:
    weds = _weeks(7)
    nl = [(d, float(v)) for d, v in zip(weds, [1, 1, 1, 1, 2, 0, 0], strict=True)]

    days = [(dt.date(2020, 1, 6) + dt.timedelta(days=7 * w)).isoformat() for w in range(7)]
    close = [100.0, 100.0, 100.0, 100.0, 100.0, 110.0, 99.0]
    d, hold, filt = m.filter_path(nl, days, close, {"2020-01-01": 5.2}, "2020-01-01", "2099", k=4)
    assert d == [days[4], days[5]]
    assert hold == pytest.approx([0.10, -0.10])
    assert filt[0] == pytest.approx(0.10)  # 늘어남 → 보유 (첫 주는 바꿈 비용 없음)
    assert filt[1] == pytest.approx(0.052 / 52 - m.SWITCH_COST)  # 줄어듦 → 현금 이자 − 바꿈 비용


def test_perf() -> None:
    p = m.perf([0.1, -0.5, 0.2])
    assert p["mdd"] == pytest.approx(0.5)
    assert m.perf([0.1]) == {"sharpe": 0.0, "cagr": 0.0, "mdd": 0.0}
