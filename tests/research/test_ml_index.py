"""문서 204 — 주간 ML 순수 계산: 미래 정보 없음 · 주말 · 정답표 · 실행 지연 · Holm."""

from __future__ import annotations

import math
from datetime import date, timedelta

from scripts.run_index_ml_204 import auc, judge

from auto_trading.research.ml_index import (
    HOLD,
    WARMUP,
    daily_exposure,
    features,
    holm,
    label,
    ledger,
    max_drawdown,
    t_weight,
    weekly_ends,
)


def _calendar(n: int) -> list[str]:
    out: list[str] = []
    d = date(2001, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _prices(n: int) -> list[float]:
    return [100 * math.exp(0.01 * math.sin(i / 7) + 0.0003 * i) for i in range(n)]


def test_weekly_ends_last_trading_day_of_iso_week() -> None:
    dates = ["2026-10-05", "2026-10-06", "2026-10-09", "2026-10-12", "2026-10-13"]
    assert weekly_ends(dates) == [2, 4]


def test_features_use_no_future_prices_or_same_day_yield() -> None:
    n = WARMUP + 120
    dates = _calendar(n)
    px = _prices(n)
    ylds = {d: 4 + 0.01 * k for k, d in enumerate(dates)}
    base = features(dates, px, ylds)
    i = WARMUP + 80
    assert i in base and len(base[i]) == 16
    # i 뒤 가격 · i 당일과 그 뒤 금리를 바꿔도 i 의 특징은 그대로
    px2 = px[: i + 1] + [p * 3 for p in px[i + 1 :]]
    ylds2 = {d: (v if d < dates[i] else 99.0) for d, v in ylds.items()}
    assert features(dates, px2, ylds2)[i] == base[i]
    # 워밍업 전에는 특징이 없다
    assert min(base) >= WARMUP


def test_label_buys_next_close_and_holds_five_days() -> None:
    px = [100.0] * 3 + [110.0] + [100.0] * 5 + [99.0] + [100.0] * 3
    r, up, dn = label(px, 3) or (0.0, 0, 0)
    assert r == px[4 + HOLD] / px[4] - 1
    assert (up, dn) == (0, 0)
    assert label(px, len(px) - HOLD - 1) is None
    big = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 97.0, 97.0]
    r, up, dn = label(big, 0) or (0.0, 0, 0)
    assert math.isclose(r, -0.03) and (up, dn) == (0, 1)


def test_daily_exposure_starts_two_days_after_decision() -> None:
    exp = daily_exposure(8, {2: 1.0, 5: 0.5})
    assert exp == [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.5]


def test_ledger_charges_cost_on_change_and_compounds_by_month() -> None:
    dates = ["2026-01-30", "2026-02-02", "2026-02-03"]
    px = [100.0, 110.0, 110.0]
    by = ledger(dates, px, [0.0, 1.0, 0.0], 0.001)
    assert math.isclose(by["2026-02"], (1 + 0.1 - 0.001) * (1 - 0.001) - 1)


def test_t_weight_is_long_only_and_vol_scaled() -> None:
    down = [100 * 0.999**i for i in range(200)]
    assert t_weight(down, 150) == 0.0
    assert t_weight(down, 100) is None
    up = [100 * math.exp(0.001 * i + 0.02 * ((-1) ** i)) for i in range(200)]
    w = t_weight(up, 150)
    assert w is not None and 0 < w < 1


def test_holm_stops_at_first_failure() -> None:
    p = {"a": 0.001, "b": 0.012, "c": 0.0125, "d": 0.5}
    # α 0.025: 0.001 ≤ 0.025/4 · 0.012 ≤ 0.025/3 실패 → 뒤도 모두 실패
    assert holm(p) == {"a": True, "b": False, "c": False, "d": False}


def test_max_drawdown() -> None:
    assert math.isclose(max_drawdown([0.1, -0.5, 0.2]), 0.5)


def test_auc_ranks_with_ties() -> None:
    assert auc([(0.1, 0), (0.9, 1)]) == 1.0
    assert auc([(0.5, 0), (0.5, 1)]) == 0.5
    assert auc([(0.9, 0), (0.1, 1)]) == 0.0


def _comp(mean: float, p: float, b: bool = False) -> dict[str, object]:
    return {
        "mean_diff_month": mean,
        "p_one_sided": p,
        "halves": [mean, mean],
        "mean_diff_10bp": mean,
        "sharpe_model_vs_base": [1.0, 0.9],
        "mdd_model_vs_base": [0.30 if b else 0.2, 0.2],
    }


def test_judge_uses_all_eight_in_holm_and_b_drawdown_gate() -> None:
    comps = {
        "NDX_A_LR": _comp(0.002, 0.004),
        "NDX_A_XGB": _comp(0.001, 0.2),
        "NDX_B_LR": _comp(0.002, 0.0001, b=True),
        "NDX_B_XGB": _comp(-0.001, 0.9),
        "SPX_A_LR": _comp(-0.001, 0.9),
        "SPX_A_XGB": _comp(-0.001, 0.9),
        "SPX_B_LR": _comp(-0.001, 0.9),
        "SPX_B_XGB": _comp(-0.001, 0.9),
    }
    v = judge(comps, {"NDX_A": 0.0, "NDX_B": 0.0, "SPX_A": 0.0, "SPX_B": 0.0})
    # 0.004 는 2 번째: α/7 = 0.00357 → 실패 (양수만 넣었다면 α/3 로 통과했을 것)
    assert v["NDX_A_LR"] == "REJECT"
    # B 는 Holm 통과해도 낙폭 +10%p 라 P5 실패
    assert v["NDX_B_LR"] == "REJECT"
    assert comps["NDX_B_LR"]["criteria"] == {
        "P1": True,
        "P2": True,
        "P3": True,
        "P4": True,
        "P5": False,
    }
