"""🌟 Deneb 그림자 기록 — 실거래와 같은 함수로 첫 달 매수 · T 브레이크 매도 · 추세 회복 되사기."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from auto_trading.ops import deneb_shadow as sh
from auto_trading.ops.deneb_guard import Brake
from auto_trading.ops.deneb_live import DenebState


def _days(n: int, start: str = "2026-05-01") -> list[str]:
    d = date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _since(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _run(
    st: DenebState, b: Brake, day: str, closes: sh.Closes, trend: bool | None = True
) -> list[str]:
    return sh.step(st, b, day, closes, monthly_krw=300_000, fx=1344.5, fee=0.001, events=[],
                   trend_on=lambda _c: trend, days_since=_since)  # fmt: skip


def _flat(days: list[str]) -> dict[str, list[tuple[str, float]]]:
    return {
        "QQQ": [(d, 500.0 + i * 0.01) for i, d in enumerate(days)],  # 변동성 거의 0 → TQQQ 60%
        "TQQQ": [(d, 80.0) for d in days],
        "SCHD": [(d, 33.0) for d in days],
    }


def test_first_month_deposit_and_buy_like_live() -> None:
    days = _days(140)
    closes = _flat(days)
    first = next(d for d in days if d >= "2026-10-01")
    st, b = DenebState(start=first), Brake(ref="T")
    lines = _run(st, b, first, closes)
    assert st.deposits[0][1] == 300_000 and st.done_month == first[:7]
    assert st.qty["TQQQ"] >= 1 and st.qty["SCHD"] >= 1
    assert st.cash_usd < 223.14 and "TQQQ 60%" in lines[0]
    assert all(o.status == "FILLED" and o.filled_usd > 0 for o in st.orders)
    # 같은 달 다음 날은 비율 맞추기 없음
    n = len(st.orders)
    _run(st, b, days[days.index(first) + 1], closes)
    assert len(st.orders) == n


def test_brake_sells_half_at_15pct_then_rebuys_after_trend_returns() -> None:
    days = _days(160)
    closes = _flat(days)
    i0 = next(i for i, d in enumerate(days) if d >= "2026-10-01")
    st, b = DenebState(start=days[i0]), Brake(ref="T")
    st.cash_usd = 0.0
    _run(st, b, days[i0], closes)
    st.qty["TQQQ"] = 10.0  # 브레이크를 보기 쉽게
    lev = closes["TQQQ"]
    lev[i0 + 2] = (days[i0 + 2], 67.0)  # 고점 80 대비 −16.25%
    lev[i0 + 3] = (days[i0 + 3], 66.0)
    out = _run(st, b, days[i0 + 3], closes)  # 전날 종가로 판단 · 그날 종가 체결
    assert b.stage == 1 and st.qty["TQQQ"] == 5 and b.reserve_usd == pytest.approx(5 * 66 * 0.999)
    assert any("브레이크" in x for x in out)
    _run(st, b, days[i0 + 4], closes, trend=False)  # 추세 꺼짐을 봄
    assert b.off_seen
    lev[i0 + 5] = (days[i0 + 5], 66.0)
    _run(st, b, days[i0 + 5], closes, trend=True)  # 다시 켜짐 → 되삼
    assert b.stage == 0 and b.reserve_usd == 0 and st.qty["TQQQ"] > 5


def test_value_counts_cash_reserve_and_holdings() -> None:
    days = _days(130)
    closes = _flat(days)
    st, b = DenebState(start=days[-1], cash_usd=10.0, qty={"TQQQ": 2.0, "SCHD": 1.0}), Brake()
    b.reserve_usd = 5.0
    assert sh.value_usd(st, b, closes, days[-1]) == pytest.approx(10 + 5 + 160 + 33)
