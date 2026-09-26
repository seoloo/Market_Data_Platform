"""문서 86 §3.2 — 자료 감사 A1~A3 계산."""

from __future__ import annotations

import pytest
from scripts.audit_x_data import missing_days, ratio_steps, return_match


def test_missing_days_counts_and_longest_run() -> None:
    cal = [f"2020-01-{d:02d}" for d in range(1, 11)]
    have = {d: 1.0 for d in cal if d not in ("2020-01-03", "2020-01-06", "2020-01-07")}
    r = missing_days(cal, have | {"2020-02-01": 1.0})
    assert (r["missing"], r["longest_run"], r["extra_days"]) == (3, 2, 1)


def test_return_match_perfect_copy() -> None:
    days = [f"2020-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
    a = {d: 100 + i for i, d in enumerate(days)}
    r = return_match(a, dict(a))
    assert r["corr"] == pytest.approx(1.0)
    assert r["annual_diff_median"] == pytest.approx(0.0)


def test_ratio_steps_counts_dividend_like_jumps() -> None:
    days = [f"2020-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 21)]
    raw = dict.fromkeys(days, 100.0)
    adj = {d: 100.0 * (0.99 ** (int(d[5:7]) // 4)) for d in days}  # 4·8·12월에 계단
    r = ratio_steps(adj, raw)
    assert r["steps_per_year"]["2020"] == 3
    assert r["max_dev_from_median"] == pytest.approx(1 - 0.99**3 / 0.99)  # 중앙값 0.99, 끝 0.99³
