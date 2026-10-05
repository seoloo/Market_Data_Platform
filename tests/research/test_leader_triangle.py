from __future__ import annotations

from datetime import date, timedelta

from scripts.run_leader_triangle import breakout, placebo_rule, simulate_entry
from scripts.run_lt_kr import KrPanel


def _days(n: int, start: date = date(2008, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _panel(days: list[str], close: dict[str, list[float | None]]) -> KrPanel:
    last = {s: max(i for i, x in enumerate(xs) if x is not None) for s, xs in close.items()}
    return KrPanel(
        days=days,
        close=close,
        value={s: [1e9] * len(days) for s in close},
        sector=dict.fromkeys(close, "IT"),
        listed_from=dict.fromkeys(close, 0),
        last=last,
    )


def _triangle(n_before: int) -> list[float | None]:
    """천천히 오르다 60 일 동안 99.5 에서 두 번 막히고 저점이 올라간 뒤 마지막 날 101 로 돌파."""
    rise = [50 + 40 * i / (n_before - 1) for i in range(n_before)]
    box: list[float] = []
    for i in range(60):
        third = i // 20
        low = (90, 93, 96)[third]
        box.append(99.5 if i in (10, 40) else low + (i % 20) * 0.1)
    return [*rise, *box, 101.0]


def test_breakout_is_judged_on_closes_and_skips_gaps() -> None:
    xs = _triangle(150)
    t = len(xs) - 1
    hit, res = breakout(xs, t)
    assert hit and res == 99.5
    assert not breakout(xs, t - 1)[0]  # 돌파 전날은 '형성 중'
    gap = list(xs)
    gap[t - 30] = None  # 61 일 안에 거래 없는 날
    assert breakout(gap, t) == (False, None)
    assert breakout(xs, 30) == (False, None)  # 자료 부족


def test_buys_only_on_breakout_next_day_and_never_without_one() -> None:
    xs = _triangle(150)
    n = len(xs) + 5
    xs = [*xs, *[101.0 + i for i in range(1, 6)]]
    flat: list[float | None] = [100.0] * n
    days = _days(n)
    p = _panel(days, {"A": xs, "B": flat})
    plan = {t: ["A", "B"] for t in range(n)}  # 매일 같은 주도주 (월말 무관하게 시험)
    run = simulate_entry(
        p,
        [True] * n,
        plan,
        lambda s, t: breakout(p.close[s], t)[0],
        start_date=days[0],
    )
    assert [e["symbol"] for e in run.entries] == ["A"]
    assert run.entries[0]["signal"] == days[n - 6]
    assert run.entries[0]["fill"] == days[n - 5]
    assert all(e["symbol"] != "B" for e in run.entries)  # 돌파 없는 종목은 현금
    assert run.exposure[n - 6] == 0.0 and run.exposure[-1] > 0.05


def test_placebo_buys_same_symbol_once_per_month_on_an_eligible_day() -> None:
    n = 400
    days = _days(n)
    xs: list[float | None] = [100 * 1.002**t for t in range(n)]
    p = _panel(days, {"A": xs})
    plan = {t: ["A"] for t in range(n - 1) if days[t + 1][:7] != days[t][:7]}
    month = "2008-10"
    entries = [{"symbol": "A", "month": month}]
    rule = placebo_rule(p, [True] * n, plan, entries, seed=3)
    picked = [t for t in range(n) if rule("A", t)]
    assert len(picked) == 1 and days[picked[0]][:7] == month
    again = placebo_rule(p, [True] * n, plan, entries, seed=3)
    assert [t for t in range(n) if again("A", t)] == picked  # 같은 씨앗 = 같은 날
