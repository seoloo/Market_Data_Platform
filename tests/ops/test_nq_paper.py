"""NQ Paper — 나스닥 100 상위 10 교체판 엔진."""

from __future__ import annotations

from datetime import date, timedelta

from auto_trading.ops.lt_paper import PaperState
from auto_trading.ops.nq_paper import TOP_N, ranking, rebalance_days, step


def business_days(start: str, n: int) -> list[str]:
    d = date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_rebalance_days_are_first_trading_day_and_two_weeks_later() -> None:
    days = business_days("2026-09-01", 50)  # 9/1 ~ 11/9
    picked = [days[i] for i in sorted(rebalance_days(days))]
    assert picked == ["2026-09-15", "2026-10-01", "2026-10-15", "2026-11-02"]


def test_rebalance_second_check_rolls_to_next_trading_day() -> None:
    days = [d for d in business_days("2026-10-01", 25) if d != "2026-10-15"]  # 15일 휴장
    picked = [days[i] for i in sorted(rebalance_days(days))]
    assert "2026-10-16" in picked and "2026-10-15" not in picked


def growth_panel(n_days: int, n_syms: int) -> dict[str, list[float | None]]:
    """종목 i 는 하루 (i+1)·0.01% 씩 오른다 → 번호가 클수록 순위가 높다."""
    return {
        f"S{i:02d}": [100 * (1 + (i + 1) * 1e-4) ** t for t in range(n_days)] for i in range(n_syms)
    }


def test_ranking_orders_by_momentum() -> None:
    closes = growth_panel(200, 30)
    top = [s for s, _ in ranking(closes, 199)[:TOP_N]]
    assert top == [f"S{i:02d}" for i in range(29, 19, -1)]


def test_first_run_buys_top_ten_at_ten_percent_each() -> None:
    days = business_days("2026-01-01", 200)
    closes = growth_panel(200, 30)
    state = PaperState(cash=1_000.0)
    a = step(state, days, closes, 199, rebalance=False)
    assert len(a["bought"]) == TOP_N and len(state.positions) == TOP_N
    assert all(abs(b["usd"] - 100.0) < 1e-6 for b in a["bought"])
    assert abs(state.cash) < 1e-6


def test_rank_out_is_sold_only_on_rebalance_day() -> None:
    days = business_days("2026-01-01", 200)
    closes = growth_panel(200, 30)
    state = PaperState(cash=1_000.0)
    step(state, days, closes, 190, rebalance=False)  # 첫 실행 매수
    # S20 을 순위 밖으로 밀어낸다: 마지막 며칠 크게 떨어뜨림(추세는 유지되도록 조금만)
    last = closes["S20"][190]
    assert last is not None
    closes["S20"] = [*closes["S20"][:191], *[last * 0.97] * 9]
    closes["S05"] = [100 * (1 + 0.004) ** t for t in range(200)]  # 새 강자
    a = step(state, days, closes, 192, rebalance=False)
    assert "S20" in state.positions and not a["bought"]
    b = step(state, days, closes, 193, rebalance=True)
    assert [x["symbol"] for x in b["sold"]] == ["S20"] and b["sold"][0]["reason"] == "rank_out"
    assert [x["symbol"] for x in b["bought"]] == ["S05"]


def test_trend_off_is_sold_next_day_like_t2() -> None:
    days = business_days("2026-01-01", 200)
    closes = growth_panel(200, 30)
    state = PaperState(cash=1_000.0)
    step(state, days, closes, 150, rebalance=False)
    held = sorted(state.positions)[0]
    # 120 일 전보다도 낮게 떨어뜨려 20 · 60 · 120 수익이 모두 음수가 되게 한다
    closes[held] = [*closes[held][:151], *[50.0] * 49]
    a = step(state, days, closes, 151, rebalance=False)
    assert a["pending_sell"].get(held) == "trend_off"
    b = step(state, days, closes, 152, rebalance=False)
    assert [x["symbol"] for x in b["sold"]] == [held] and held not in state.positions
    assert not b["bought"]  # 판 돈은 다음 판단일까지 현금


def test_next_rebalance_projects_past_known_days() -> None:
    from auto_trading.ops.nq_paper import next_rebalance

    days = business_days("2026-09-01", 29)  # 9/1 ~ 10/9
    assert next_rebalance(days, len(days) - 1) == "2026-10-15"
    assert next_rebalance(days, days.index("2026-09-30")) == "2026-10-01"
    later = business_days("2026-10-01", 12)  # 10/1 ~ 10/16
    assert next_rebalance(later, len(later) - 1) == "2026-11-02"


def test_sn20_skips_overlap_and_buys_twenty_at_five_percent() -> None:
    from auto_trading.ops.nq_paper import combined_top

    days = business_days("2026-01-01", 200)
    sp = growth_panel(200, 30)  # S29 ~ S20 이 상위
    nq = {k: v for k, v in growth_panel(200, 30).items() if k >= "S15"}  # 겹치는 종목군
    nq.update({f"N{i:02d}": [100.0 * (1 + 1e-5) ** t for t in range(200)] for i in range(10)})
    top = combined_top(sp, nq, 199)
    names = [s for s, _ in top]
    assert names[:10] == [f"S{i:02d}" for i in range(29, 19, -1)]
    assert len(set(names)) == 20 and names[10:15] == [f"S{i:02d}" for i in range(19, 14, -1)]
    merged = {**sp, **nq}
    state = PaperState(cash=1_000.0)
    a = step(state, days, merged, 199, False, pick=lambda t: combined_top(sp, nq, t), slot=0.05)
    assert len(a["bought"]) == 20 and all(abs(b["usd"] - 50.0) < 1e-6 for b in a["bought"])
