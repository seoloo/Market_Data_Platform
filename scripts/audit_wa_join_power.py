"""WA 결합 연구 설계 점검 — 시장 상태 기준 비율 + WA 진입 날짜 수로 검정력 추정 (문서 35).

WA 진입의 **방향·손익은 읽거나 출력하지 않는다.** 에피소드 시작 날짜 수만 센다.
시장은 Binance USD-M BTCUSDT (CROSS_VENUE_PROXY), DB 읽기 전용.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from bisect import bisect_left
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.explore_wonyotti_trips import SOURCE, build_trips, load_xbtusd  # noqa: E402

from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.exposure import ExposureIndex  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import causal_triggers, evaluate_triggers  # noqa: E402
from scripts import audit_ew0_minute_path as market  # noqa: E402

FROM_MS = 1_577_836_800_000  # 2020-01-01
TO_MS = 1_640_995_200_000  # 2022-01-01
WARMUP_MS = 60 * 86_400_000
ODDS_RATIO = 1.5
CONTROLS_PER_CASE = 5
TESTS = 4
ALPHA = 0.05


def _rows(conn: sqlite3.Connection, timeframe: str) -> Iterator[tuple[object, ...]]:
    return iter(conn.execute(market._QUERY, ("binance_usdm", "BTCUSDT", timeframe, FROM_MS, TO_MS)))


def _index() -> tuple[ExposureIndex, tuple[Candle, ...]]:
    conn = sqlite3.connect(market.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        four = tuple(market._candle(r) for r in _rows(conn, "4h"))
        hours = tuple(market._candle(r) for r in _rows(conn, "1h"))
        (replay,) = replay_minute_groups_ladder(
            four, market._minute_groups(conn, four), four[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
    finally:
        conn.close()
    triggers = causal_triggers(replay, hours, four)
    return ExposureIndex(triggers, evaluate_triggers(triggers, hours)), hours


def _power(p0: float, n_cases: float) -> float:
    """한쪽 두 비율 비교, Holm 최엄격 단계 α/TESTS, 대조 CONTROLS_PER_CASE 배."""
    if n_cases <= 0 or not 0 < p0 < 1:
        return 0.0
    p1 = ODDS_RATIO * p0 / (1 - p0 + ODDS_RATIO * p0)
    m = CONTROLS_PER_CASE * n_cases
    pbar = (n_cases * p1 + m * p0) / (n_cases + m)
    z = NormalDist().inv_cdf(1 - ALPHA / TESTS)
    se0 = math.sqrt(pbar * (1 - pbar) * (1 / n_cases + 1 / m))
    se1 = math.sqrt(p1 * (1 - p1) / n_cases + p0 * (1 - p0) / m)
    return NormalDist().cdf((p1 - p0 - z * se0) / se1)


def main() -> int:
    index, hours = _index()
    closes = [h.close_ms for h in hours]
    start = hours[0].open_ms + WARMUP_MS
    grid = [h.close_ms + 1 for h in hours if h.close_ms + 1 >= start]
    counts = {"ew_up_end": 0, "ew_down_end": 0, "fib_up_leg_zone": 0, "fib_down_leg_zone": 0}
    for t in grid:
        k = bisect_left(closes, t) - 1
        price = Decimal(hours[k].close)
        end = index.elliott_end(t)
        counts["ew_up_end"] += end == 1
        counts["ew_down_end"] += end == -1
        counts["fib_up_leg_zone"] += index.in_fib_zone(t, price, 1)
        counts["fib_down_leg_zone"] += index.in_fib_zone(t, price, -1)
    base = {k: round(v / len(grid), 4) for k, v in counts.items()}

    fills, funding, _ = load_xbtusd(SOURCE)
    opened = [trip.opened for trip in build_trips(fills, funding)]
    days_all = len({d.date() for d in opened})
    days_2020 = len({d.date() for d in opened if d.year >= 2020})
    tests = {
        "H1_short_after_up_impulse_end": base["ew_up_end"],
        "H2_long_after_down_impulse_end": base["ew_down_end"],
        "H5a_long_in_up_leg_fib_zone": base["fib_up_leg_zone"],
        "H5b_short_in_down_leg_fib_zone": base["fib_down_leg_zone"],
    }
    scenarios = {"A_2020_2021_futures": days_2020, "B_2018_2021_needs_spot_1m": days_all}
    power = {
        name: {test: round(_power(p0, days / 2), 3) for test, p0 in tests.items()}
        for name, days in scenarios.items()
    }
    result = {
        "market": "binance_usdm:BTCUSDT 2020-01-01/2022-01-01 (CROSS_VENUE_PROXY)",
        "hourly_grid_points": len(grid),
        "base_rates_calendar_B0": base,
        "wa_episodes": len(opened),
        "wa_distinct_entry_days": {"2018_2021": days_all, "2020_2021": days_2020},
        "power_assumptions": {
            "odds_ratio": ODDS_RATIO,
            "controls_per_case": CONTROLS_PER_CASE,
            "cases_per_direction": "distinct entry days / 2 (방향 비율 미열람)",
            "alpha": f"{ALPHA}/{TESTS} one-sided (Holm 최엄격 단계)",
        },
        "power": power,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "note": "방향·손익 미열람. 기준 비율은 달력 전체 1h 격자.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
