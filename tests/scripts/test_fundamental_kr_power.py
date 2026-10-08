from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from scripts.fundamental_kr_power import cost, nw_sd, pct_rank, power_row, select


def _x(g: float, a: float, m: float, f: float, c: float) -> Any:
    return SimpleNamespace(g=g, accel=a, margin_chg=m, cf_chg=f, cf_ttm=c)


def test_pct_rank_ties_and_select_rules() -> None:
    assert pct_rank({"a": 1.0, "b": 1.0, "c": 3.0}) == {"a": 0.25, "b": 0.25, "c": 1.0}
    ok = cast(
        "dict[str, Any]",
        {
            "s1": _x(0.1, 0.3, 0.3, 0.3, 1.0),
            "s2": _x(0.1, 0.1, 0.1, 0.1, 1.0),
            "s3": _x(-0.1, 0.9, 0.9, 0.9, 1.0),  # g ≤ 0 → 자격 없음
            "s4": _x(0.1, 0.2, 0.2, 0.2, -1.0),  # C ≤ 0 → 자격 없음
        },
    )
    assert select(ok) == ["s1", "s2"]  # 점수 순 (s3 · s4 는 순위 계산에는 들어가지만 자격 없음)


def test_cost_table_and_power_math() -> None:
    assert cost("20180102", "buy") == 0.0025
    assert cost("20180102", "sell") == 0.00315
    assert cost("20210104", "sell") == 0.0025  # 0.015% + 0.23% < 0.25% → 0.25%
    xs = [0.01, -0.01] * 30
    assert abs(nw_sd(xs, lag=0) - 0.01) < 1e-12
    row = power_row(xs)
    assert row["months"] == 60 and "mean" not in str(row)  # 평균은 내보내지 않는다
