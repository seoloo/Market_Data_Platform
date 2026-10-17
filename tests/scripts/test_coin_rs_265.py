"""265 — 상대 강도 갈아타기 계산 규칙 (순위 · 하루 늦춤 체결 · 폐지 정리 · 필터)."""

from __future__ import annotations

from scripts.run_coin_rs_265 import Px, day_range, rank, simulate


def test_rank_uses_only_coins_traded_that_day() -> None:
    days = day_range("2020-01-01", "2020-02-10")
    px = Px({
        "A": {d: 1.0 + i * 0.01 for i, d in enumerate(days)},
        "B": {d: 1.0 + i * 0.03 for i, d in enumerate(days)},
        "C": {d: 1.0 + i * 0.05 for i, d in enumerate(days[:-1])},  # 마지막 날 거래 없음
    })  # fmt: skip
    assert rank(px, ["A", "B", "C"], days[-1], 28, 2) == ["B", "A"]


def test_next_close_execution_and_delisting() -> None:
    days = day_range("2020-01-01", "2020-01-05")
    px = Px({"X": {"2020-01-01": 1.0, "2020-01-02": 2.0, "2020-01-03": 4.0}})
    r = simulate(px, days, lambda d, w: {"X": 1.0} if d == days[0] else None, 0.0)
    # 01-01 목표 → 01-02 종가 2 에 체결 → 01-03 4 (두 배) → 01-04 자료 끝, 마지막 종가에 팖
    assert r["navs"]["2020-01-01"] == 1.0
    assert r["navs"]["2020-01-03"] == 2.0
    assert r["navs"]["2020-01-05"] == 2.0
    assert r["delisted"] == [("2020-01-04", "X")]


def test_cost_charged_on_turnover() -> None:
    days = day_range("2020-01-01", "2020-01-03")
    px = Px({"X": dict.fromkeys(days, 1.0)})
    r = simulate(px, days, lambda d, w: {"X": 1.0} if d == days[0] else None, 0.01)
    assert abs(r["navs"]["2020-01-03"] - 0.99) < 1e-12
