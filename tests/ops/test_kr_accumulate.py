"""국내 적립: 호가 단위 · 적립금(끈 기간 0) · 하루 1 건 · 체결 정리 · 종목 찾기."""

from __future__ import annotations

from auto_trading.ops.kr_accumulate import (
    KrOrder,
    KrState,
    Listed,
    accrual,
    advise,
    find,
    limit_for,
    settle,
    tick,
)


def test_tick_and_limit() -> None:
    assert tick(1_500, False) == 1 and tick(70_000, False) == 100 and tick(600_000, False) == 1_000
    assert tick(25_000, True) == 5  # ETF
    assert limit_for(70_000, False) == 70_400  # 70,350 → 100 원 단위 올림
    assert limit_for(9_990, True) == 10_040


def test_accrual_with_off_period() -> None:
    hist = [("2026-10-11", 5_000.0), ("2026-10-14", 0.0), ("2026-10-16", 5_000.0)]
    # 11 · 12 · 13 = 15,000 · 14 · 15 꺼짐 · 16 · 17 = 10,000
    assert accrual(hist, "2026-10-10", "2026-10-17") == 25_000
    assert accrual(hist, "2026-10-12", "2026-10-12") == 5_000
    assert accrual([], "2026-10-10", "2026-10-17") == 0


def test_advise_and_settle() -> None:
    st = KrState("069500", "KODEX 200", True, "2026-10-10")
    hist = [("2026-10-11", 10_000.0)]
    assert advise(st, hist, "2026-10-12", 25_000).status == "WAIT"  # 20,000 < 1 주
    adv = advise(st, hist, "2026-10-13", 25_000)
    assert adv.status == "BUY" and adv.limit == 25_125
    st.orders.append(KrOrder("2026-10-13", adv.limit, "c1", "OID"))
    assert advise(st, hist, "2026-10-14", 25_000).status == "OPEN"  # 확인 전엔 또 안 산다
    buy = settle(st.orders[0], "FILLED", 1, 25_050, 4)
    assert buy == {"date": "2026-10-13", "qty": 1, "cost_krw": 25_054}
    st.buys.append(buy)
    assert advise(st, hist, "2026-10-14", 25_000).accrued == 40_000 - 25_054
    assert settle(KrOrder("d", 1, "c"), "PENDING", 0, 0, 0) is None
    gone = KrOrder("d", 1, "c")
    assert settle(gone, "CANCELED", 0, 0, 0) is None and gone.status == "EXPIRED"
    off = advise(KrState("x", "x", False, "2026-10-10"), [("2026-10-11", 0.0)], "2026-10-12", 1)
    assert off.status == "OFF"


def test_find() -> None:
    u = [
        Listed("005930", "삼성전자", "KOSPI", "STOCK"),
        Listed("005935", "삼성전자우", "KOSPI", "STOCK"),
        Listed("NVDA", "엔비디아", "NASDAQ", "STOCK"),
    ]
    assert [x.symbol for x in find("삼성 전자", u, "KR")] == ["005930"]
    assert [x.symbol for x in find("005935", u, "KR")] == ["005935"]
    assert [x.symbol for x in find("nvda", u, "US")] == ["NVDA"]
    assert len(find("삼성", u, "KR")) == 2 and find("NVDA", u, "KR") == []
