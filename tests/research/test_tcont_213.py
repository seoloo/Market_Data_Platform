# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 213 합성 검산 — 연속 점수 공식 · 부호 점수(T/v2) · 미래 정보 방지 · 하루 한 번 판단 · 비중 상한."""

from __future__ import annotations

import math
import random
import statistics
from array import array

from scripts.run_qb_dc_210 import DAY, H4, MIN, WARM, Market, Params, Signals, simulate
from scripts.run_tcont_213 import CFG, cont_weight, sign_score

from auto_trading.backtest.trend import target_weight


def _walk(n: int, drift: float, seed: int = 3) -> list[float]:
    rng = random.Random(seed)
    p = [100.0]
    for _ in range(n - 1):
        p.append(p[-1] * math.exp(drift + rng.gauss(0, 0.03)))
    return p


def test_cont_weight_formula_and_vol_same_as_tv2() -> None:
    xs = _walk(200, 0.004)
    w = cont_weight(xs)
    steps = [math.log(xs[-k] / xs[-k - 1]) for k in range(1, 61)]
    vol = statistics.stdev(steps) * math.sqrt(365)
    sd = vol / math.sqrt(365)
    s = statistics.fmean(
        math.tanh(math.log(xs[-1] / xs[-1 - n]) / (sd * math.sqrt(n))) for n in (20, 60, 120)
    )
    assert w is not None and abs(w - max(0.0, s) * min(0.15 / vol, 0.5)) < 1e-12
    tv2 = target_weight(xs, CFG)
    assert (
        tv2 is not None and 0 <= w <= tv2 + 1e-12
    )  # 같은 변동성 비중에 |점수| ≤ 1 (부호 1 일 때 최대)
    assert sign_score(xs) == max(0.0, tv2 / min(0.15 / vol, 0.5))


def test_short_history_and_downtrend() -> None:
    assert cont_weight(_walk(120, 0.0)) is None and target_weight(_walk(120, 0.0), CFG) is None
    down = _walk(200, -0.01)
    assert cont_weight(down) == 0.0 and target_weight(down, CFG) == 0.0
    flat = [100.0] * 200
    assert cont_weight(flat) == 0.0 and target_weight(flat, CFG) == 0.0  # 변동성 0 → 둘 다 0


def test_causal_prefix() -> None:
    xs = _walk(260, 0.002)
    w = [cont_weight(xs[: k + 1]) for k in range(150, 200)]
    ys = xs[:200] + [x * 3 for x in xs[200:]]  # 뒤를 바꿔도
    assert [cont_weight(ys[: k + 1]) for k in range(150, 200)] == w


def _market(days: int) -> Market:
    n = days * DAY // MIN
    a = array("d", [100.0] * n)
    return Market(
        array("d", a), array("d", a), array("d", a), array("d", a), array("d", [1e9] * n), 0
    )


def test_daily_decision_only_and_weight_cap() -> None:
    m = _market(64)
    n_j = len(m.c) * MIN // H4
    w = [0.0] * n_j
    for j in range(n_j):
        d_ms = WARM + j * H4
        w[j] = 0.8 if d_ms % DAY else 0.3  # 00:00 칸만 0.3, 나머지 칸 값은 쓰면 안 됨
    res = simulate(
        {"X": m},
        {"X": Signals({"T": w})},
        "T",
        Params(decide_hours=24, sig_is_weight=True),
        eval_start=WARM + 61 * DAY,
    )
    bk = res["book"]
    assert all(
        (i * MIN) % DAY < 4 * MIN for _s, i, _q in bk.fill_log
    )  # 체결은 00:00 직후 4 분 안에서만
    assert abs(bk.qty["X"] * 100.0 / 10_000 - 0.3) < 0.01
    res2 = simulate(
        {"X": m},
        {"X": Signals({"T": [0.9] * n_j})},
        "T",
        Params(decide_hours=24, sig_is_weight=True),
        eval_start=WARM + 61 * DAY,
    )
    assert abs(res2["book"].qty["X"] * 100.0 / 10_000 - 0.5) < 0.01  # 종목 상한 0.5
