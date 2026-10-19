"""Deneb 금 섞기 (사용자 2026-10-19 'A로 진행해줘', 문서 285 TG) — 순수 계산."""

from __future__ import annotations

import itertools
import math
import random

from auto_trading.ops.deneb_live import (
    DIV,
    GOLD,
    GOLD_MIX,
    LEV,
    DenebState,
    choose,
    fill,
    holdings_lines,
    limit_price,
    plan_message,
    ratio_text,
    targets,
)

PX = {LEV: 68.21, DIV: 34.35, GOLD: 41.54}


def _old_choose(qty, cash, last, share, fee):  # type: ignore[no-untyped-def]
    """10-19 전 두 종목 choose (범위 줄이기 전) — 금 몫 0 결과가 같은지 비교용."""
    syms = (LEV, DIV)
    total = cash + sum(qty.get(s, 0.0) * last[s] for s in syms)
    target = {LEV: share * total, DIV: (1 - share) * total}
    ranges = []
    for s in syms:
        hold = qty.get(s, 0.0)
        over = hold * last[s] - target[s]
        lo = -math.floor(over / last[s]) if over > last[s] else 0
        lo = max(lo, -math.floor(hold))
        hi = math.floor(cash / (limit_price(last[s], "BUY") * (1 + fee))) if cash > 0 else 0
        ranges.append(range(lo, hi + 1))
    best = None
    for combo in itertools.product(*ranges):
        d = dict(zip(syms, combo, strict=True))
        spend = sum(n * limit_price(last[s], "BUY") * (1 + fee) for s, n in d.items() if n > 0)
        if spend > cash + 1e-9:
            continue
        err = sum((target[s] - (qty.get(s, 0.0) + d[s]) * last[s]) ** 2 for s in syms)
        key = (round(err, 6), sum(abs(n) for n in combo))
        if best is None or key < (round(best[0], 6), sum(abs(n) for n in best[1].values())):
            best = (err, d)
    return {s: n for s, n in (best[1] if best else {}).items() if n != 0}


def test_without_gold_same_as_before() -> None:
    rng = random.Random(7)
    for _ in range(300):
        qty = {LEV: float(rng.randint(0, 6)), DIV: float(rng.randint(0, 12))}
        cash = rng.uniform(0, 900)
        last = {LEV: rng.uniform(40, 120), DIV: rng.uniform(25, 40), GOLD: 41.0}
        share = rng.choice([0.6, 0.45, 0.3, 0.0])
        assert choose(qty, cash, last, share, 0.001) == _old_choose(qty, cash, last, share, 0.001)


def test_targets_and_ratio_text() -> None:
    t = targets(1000.0, 0.6, GOLD_MIX)
    assert (round(t[LEV]), round(t[DIV]), round(t[GOLD])) == (600, 200, 200)
    assert targets(1000.0, 0.6)[GOLD] == 0.0
    assert ratio_text(0.6) == "TQQQ 60% : SCHD 40%"
    assert ratio_text(0.6, 0.5) == "TQQQ 60% : SCHD 20% : IAUM(금) 20%"


def test_first_month_220_usd_with_gold() -> None:
    # 10-19 남은 전략 현금 $220 → TQQQ 2 · SCHD 1 · IAUM 1 (금 없으면 TQQQ 2 · SCHD 2)
    assert choose({}, 220.0, PX, 0.6, 0.001, GOLD_MIX) == {LEV: 2, DIV: 1, GOLD: 1}
    assert choose({}, 220.0, PX, 0.6, 0.001) == {LEV: 2, DIV: 2}


def test_gold_turned_on_later_new_money_goes_to_gold_without_selling() -> None:
    plan = choose({LEV: 2.0, DIV: 2.0}, 215.0, PX, 0.6, 0.001, GOLD_MIX)
    assert plan == {LEV: 2, GOLD: 1}  # 파는 것 없이 새 돈이 금부터 채움


def test_gold_over_target_is_trimmed_and_turning_off_sells_gold() -> None:
    plan = choose({LEV: 2.0, DIV: 1.0, GOLD: 4.0}, 0.0, PX, 0.6, 0.001, GOLD_MIX)
    assert plan.get(GOLD, 0) < 0  # 금이 목표보다 1 주 넘게 많으면 판다
    off = choose({LEV: 2.0, DIV: 1.0, GOLD: 2.0}, 0.0, PX, 0.6, 0.001)
    assert off == {GOLD: -2}  # 금 섞기를 끄면 들고 있는 금은 비율 맞추기로 판다(1 주 값 넘을 때)


def test_brake_stage2_gold_and_schd_only() -> None:
    plan = choose({}, 300.0, PX, 0.0, 0.001, GOLD_MIX)
    assert LEV not in plan and plan[DIV] >= 3 and plan[GOLD] >= 3


def test_fill_buys_most_lacking_including_gold() -> None:
    got = fill({LEV: 2.0, DIV: 1.0}, 50.0, PX, 0.6, 0.001, gold=GOLD_MIX)
    assert got == {GOLD: 1}
    assert fill({LEV: 2.0, DIV: 1.0}, 50.0, PX, 0.6, 0.001) == {DIV: 1}


def test_big_month_is_fast_enough() -> None:
    # 월 5,000 만 원 ≈ $37,000 — 세 종목 찾기가 범위를 줄여 빨리 끝나야 한다
    plan = choose({}, 37_000.0, PX, 0.6, 0.001, GOLD_MIX)
    assert plan[LEV] > 300 and plan[GOLD] > 150


def test_messages_show_gold() -> None:
    st = DenebState("2026-10-09", cash_usd=10.0, qty={LEV: 2.0, DIV: 1.0, GOLD: 1.0})
    assert any("IAUM 1주" in x for x in holdings_lines(st, PX))
    st2 = DenebState("2026-10-09", qty={LEV: 2.0, DIV: 1.0})
    assert not any("IAUM" in x for x in holdings_lines(st2, PX))
    msg = plan_message(
        today="2026-11-02", krw=300_000, source="기본", fx_rate=1400, vol=0.15, share=0.6,
        plan={LEV: 2, DIV: 1, GOLD: 1}, prices=PX, fee=0.001, usd_now=500, need_all=0,
        gold=GOLD_MIX,
    )  # fmt: skip
    assert "IAUM(금) 20%" in msg and "🛒 IAUM 1주" in msg
