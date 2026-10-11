# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 214 합성 검산 — 즉시 · 4 시간 · 조건부 대기, 24 시간 마감, 최신 목표로 취소 · 수량, 줄이기는 지연 없음, 평가 끝 미완료."""

from __future__ import annotations

from array import array
from collections.abc import Callable
from typing import Any

from scripts.run_qb_dc_210 import DAY, H4, MIN, WARM, Market, Params, Signals, simulate

PER = DAY // MIN
STEP = H4 // MIN
EV = WARM + 61 * DAY  # 평가 시작 (61 일째 00:00)
I0 = 61 * PER


def market(days: int, price: Callable[[int], float]) -> Market:
    n = days * PER
    xs = [price(i) for i in range(n)]
    a = array("d", xs)
    return Market(array("d", xs), array("d", xs), array("d", xs), a, array("d", [1e9] * n), 0)


def run(m: Market, w: Callable[[int], float], timing: str) -> dict[str, Any]:
    n_j = len(m.c) * MIN // H4
    sig = [w(j) for j in range(n_j)]
    return simulate(
        {"X": m},
        {"X": Signals({"T": sig})},
        "T",
        Params(decide_hours=24, sig_is_weight=True, timing=timing),
        eval_start=EV,
    )


def first_buy_minute(res: dict[str, Any]) -> int:
    return int(min(i for _s, i, q in res["book"].fill_log if q > 0))


def test_a_immediate_b_four_hours() -> None:
    m = market(63, lambda i: 100.0)
    a = run(m, lambda j: 0.3, "A")
    b = run(m, lambda j: 0.3, "B")
    assert first_buy_minute(a) == I0
    assert first_buy_minute(b) == I0 + STEP  # 4 시간 뒤 (그때도 필요하므로)
    assert b["book"].timing_log[0]["result"] == "4 시간 뒤"


def test_c_waits_while_rising_and_expires_at_24h() -> None:
    m = market(64, lambda i: 100.0 * (1 + 1e-6 * i))  # 계속 오름 → 4 시간봉 수익 > 0
    c = run(m, lambda j: 0.3, "C")
    assert first_buy_minute(c) == I0 + 6 * STEP  # 24 시간 만료에 주문 시작
    assert c["book"].timing_log[0]["result"] == "24 시간 만료"
    flat = market(63, lambda i: 100.0)
    c2 = run(flat, lambda j: 0.3, "C")
    assert first_buy_minute(c2) == I0 and c2["book"].timing_log[0]["result"] == "즉시(4 시간봉 ≤ 0)"


def test_c_executes_when_bar_turns_down() -> None:
    dip = (
        I0 + 2 * STEP
    )  # 8 시간째 시작하는 4 시간 동안 천천히 내림 → 12:00 칸에서 직전 봉 수익 < 0 (가격은 이어짐)
    path, lvl = [], 100.0
    for i in range(63 * PER):
        lvl *= 1 - 2e-6 if dip <= i < dip + STEP else 1 + 1e-6
        path.append(lvl)

    def px(i: int) -> float:
        return path[i]

    c = run(market(63, px), lambda j: 0.3, "C")
    log = c["book"].timing_log[0]
    assert log["result"] == "조건 충족" and log["end_j"] - log["start_j"] == 3
    assert first_buy_minute(c) == I0 + 3 * STEP


def test_cancel_when_target_drops_and_reductions_not_delayed() -> None:
    m = market(64, lambda i: 100.0 * (1 + 1e-6 * i))
    j_next_day = (EV + DAY - WARM) // H4
    c = run(m, lambda j: 0.3 if j < j_next_day else 0.0, "C")  # 다음 날 목표 0 → 24 시간 전에 취소
    assert c["book"].timing_log[0]["result"].startswith("취소")
    assert not any(q > 0 for _s, _i, q in c["book"].fill_log)
    # 줄이기: A 로 산 뒤 목표가 줄면 C 에서도 그 판단 칸에 바로 판다
    m2 = market(64, lambda i: 100.0)
    c2 = run(m2, lambda j: 0.3 if j < j_next_day else 0.05, "C")
    sells = [i for _s, i, q in c2["book"].fill_log if q < 0]
    assert sells and sells[0] == I0 + PER


def test_pending_at_end_is_reported() -> None:
    m = market(62, lambda i: 100.0 * (1 + 1e-6 * i))  # 평가가 하루뿐 — 24 시간 만료 전에 끝남
    c = run(m, lambda j: 0.3, "C")
    assert c["book"].timing_log[-1]["result"] == "미완료(평가 끝)"
