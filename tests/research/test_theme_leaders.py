from __future__ import annotations

import pytest

from auto_trading.research.theme_leaders import (
    leader_mcap,
    leader_momentum,
    nw_se,
    rolling_horizon,
    us_mktcap,
)


def test_leader_momentum_rank_sum_and_tie_break() -> None:
    rets = {"A": (0.10, 0.20, 0.30), "B": (0.05, 0.25, 0.40), "C": (-0.1, 0.0, 0.0)}
    # 순위 합 (21 일: C0 B1 A2 · 63 일: C0 A1 B2 · 126 일: C0 A1 B2) → A 4, B 5, C 0
    assert leader_momentum(rets) == "B"
    tie = {"A": (0.2, 0.1, 0.5), "B": (0.1, 0.2, 0.4)}
    assert leader_momentum(tie) == "A"  # 순위 합 같음 → 126 일 수익이 큰 쪽


def test_leader_mcap_and_switch_example() -> None:
    # 삼성전자 → SK하이닉스: 시가총액 순위가 바뀌면 1 등이 바뀐다
    assert leader_mcap({"005930": 500.0, "000660": 400.0, "042700": 50.0}) == "005930"
    assert leader_mcap({"005930": 500.0, "000660": 510.0, "042700": 50.0}) == "000660"
    assert leader_mcap({"B": 1.0, "A": 1.0}) == "A"


def test_us_mktcap_pit_split_and_backfill() -> None:
    shares = [("2019-12-31", "2020-02-01", 100.0), ("2021-12-31", "2022-02-01", 90.0)]
    splits = [("2021-06-01", 4.0), ("2024-06-10", 10.0)]
    # 2020-06: 2020-02 제출 값(기준일 2019-12-31) — 그 뒤 분할 4 × 10
    v = us_mktcap(shares, splits, 2.0, "2020-06-30")
    assert v is not None and not v.backfilled
    assert v.value == pytest.approx(100 * 40 * 2.0)
    # 2022-06: 2022-02 제출 값(분할 4 반영된 90) — 기준일 뒤 분할 10 만
    v = us_mktcap(shares, splits, 2.0, "2022-06-30")
    assert v is not None and v.value == pytest.approx(90 * 10 * 2.0)
    # 아직 아무것도 제출 전 → 가장 이른 값으로 메우고 표시
    v = us_mktcap(shares, splits, 2.0, "2019-06-30")
    assert v is not None and v.backfilled
    assert us_mktcap([], splits, 2.0, "2020-01-01") is None


def test_nw_se_matches_plain_se_without_autocorrelation_terms() -> None:
    xs = [0.01, -0.02, 0.03, 0.0, 0.01, -0.01]
    m = sum(xs) / len(xs)
    plain = (sum((x - m) ** 2 for x in xs) / len(xs) / len(xs)) ** 0.5
    assert nw_se(xs, lag=0) == pytest.approx(plain)


def test_rolling_horizon_counts_windows() -> None:
    a = [0.02] * 24
    b = [0.01] * 24
    h = rolling_horizon(a, b, 12)
    assert h is not None and h["windows"] == 13 and h["a_wins"] == 1.0
    assert h["median_rel"] == pytest.approx(1.02**12 / 1.01**12 - 1, abs=1e-4)
    assert rolling_horizon(a, b, 25) is None
