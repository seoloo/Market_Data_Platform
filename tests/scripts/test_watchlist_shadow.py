from __future__ import annotations

import math
import statistics
from datetime import UTC, date, datetime

from scripts.watchlist_shadow import first_days, panic_share, pooled_lower, w1_eval_ms


def test_w1_eval_is_next_hour_after_session_close_plus_lag() -> None:
    # 2026-09-14 (EDT): 16:00 ET = 20:00 UTC → + 24 시간 = 09-15 20:00 UTC (이미 정시)
    ms = w1_eval_ms(date(2026, 9, 14))
    assert datetime.fromtimestamp(ms / 1000, UTC) == datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
    # 2026-12-14 (EST): 16:00 ET = 21:00 UTC
    ms = w1_eval_ms(date(2026, 12, 14))
    assert datetime.fromtimestamp(ms / 1000, UTC) == datetime(2026, 12, 15, 21, 0, tzinfo=UTC)


def test_first_days_keeps_only_episode_starts() -> None:
    assert first_days([10, 11, 12, 40, 41, 61, 100]) == [10, 40, 100]


def test_panic_share_counts_deep_dips_and_skips_share_jumps() -> None:
    flat: list[float | None] = [100.0] * 24
    closes: dict[str, list[float | None]] = {
        "a": [*flat, 70.0],  # 25 일선 대비 −29% → 셈
        "b": [*flat, 99.0],  # 아님
        "c": [*flat, 50.0],  # 주식 수가 두 배 → 분할 의심으로 세지 않음
        "d": [None, *flat],  # 자료 빈칸 → 세지 않음
    }
    one: list[float | None] = [1e6] * 25
    shares: dict[str, list[float | None]] = {
        "a": one,
        "b": one,
        "c": [*one[:24], 2e6],
        "d": one,
    }
    below, n = panic_share(closes, shares, ["a", "b", "c", "d"], 24)
    assert (below, n) == (1, 2)


def test_pooled_lower_matches_direct_computation() -> None:
    hist = [0.01, -0.02, 0.03, 0.005, 0.0]
    fwd = [0.02, -0.01]
    m, sd = statistics.fmean(hist), statistics.stdev(hist)
    got = pooled_lower(m, sd, len(hist), fwd)
    allx = hist + fwd
    want = statistics.fmean(allx) - 2.24 * statistics.stdev(allx) / math.sqrt(len(allx))
    assert abs(got - want) < 1e-12
