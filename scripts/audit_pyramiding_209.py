"""209 Phase 0: read-only coverage and existing T/v2 regime counts, never candidate PnL.

Run: python -m scripts.audit_pyramiding_209
No downloader, account API, order engine or performance simulator is imported.
The JSON goes to stdout; the market database is opened with mode=ro/query_only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist

from auto_trading.backtest.trend import TrendConfig, target_weight

DAY_MS = 86_400_000
SYMBOLS = ("BTCUSDT", "ETHUSDT")
START_MS = int(datetime(2020, 3, 1, tzinfo=UTC).timestamp() * 1000)
END_MS = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
SPLITS = (
    ("historical_train", START_MS, int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)),
    (
        "historical_validation",
        int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000),
        int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000),
    ),
    (
        "historical_test_not_fresh_oos",
        int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000),
        END_MS,
    ),
)


@dataclass(frozen=True)
class Coverage:
    rows: int
    first_ms: int | None
    last_ms: int | None
    gaps: int
    digest: str


@dataclass(frozen=True)
class SymbolAudit:
    symbol: str
    spot_daily: Coverage
    perp_daily: Coverage
    funding: Coverage
    funding_mark_missing: int
    transitions_by_year: dict[int, int]
    transitions_by_split: dict[str, int]
    boundary_carry_by_split: dict[str, bool]


def coverage(
    rows: Sequence[tuple[int, str]], step_ms: int, *, jitter_tolerance_ms: int = 0
) -> Coverage:
    """Digest and missing-time count; tolerance is ONLY a coverage diagnostic.

    Funding timestamps are never rounded or moved for a future execution replay.
    """
    digest = hashlib.sha256()
    gaps = 0
    previous: int | None = None
    for timestamp_ms, value in rows:
        if previous is not None and timestamp_ms <= previous:
            raise ValueError("timestamps must be strictly increasing")
        if previous is not None and timestamp_ms - previous > step_ms + jitter_tolerance_ms:
            gaps += 1
        digest.update(f"{timestamp_ms}\t{value}\n".encode())
        previous = timestamp_ms
    return Coverage(
        len(rows),
        rows[0][0] if rows else None,
        rows[-1][0] if rows else None,
        gaps,
        digest.hexdigest(),
    )


def regime_starts(rows: Sequence[tuple[int, str]]) -> tuple[list[int], list[tuple[int, bool]]]:
    """Count ONLY the already frozen spot T/v2 off->on state, with its causal history.

    T/v2 target_weight is the source of truth. Entry dates here label signal bars,
    not fills. Missing daily history fails closed rather than crossing a gap.
    """
    history: list[float] = []
    starts: list[int] = []
    states: list[tuple[int, bool]] = []
    previous_ms: int | None = None
    previous_on = False
    cfg = TrendConfig(long_only=True)
    for timestamp_ms, close in rows:
        if previous_ms is not None and timestamp_ms != previous_ms + DAY_MS:
            raise ValueError("spot daily history is not contiguous")
        price = float(close)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("positive finite close required")
        history.append(price)
        weight = target_weight(history, cfg)
        on = weight is not None and weight > 0
        if on and not previous_on:
            starts.append(timestamp_ms)
        states.append((timestamp_ms, on))
        previous_ms, previous_on = timestamp_ms, on
    return starts, states


def audit_symbol(conn: sqlite3.Connection, symbol: str) -> SymbolAudit:
    if symbol not in SYMBOLS:
        raise ValueError("209 permits BTCUSDT/ETHUSDT only")
    spot = [
        (int(ms), str(value))
        for ms, value in conn.execute(
            "SELECT open_ms,close FROM candles WHERE exchange='binance_spot'"
            " AND symbol=? AND timeframe='1d' AND open_ms<? ORDER BY open_ms",
            (symbol, END_MS),
        )
    ]
    perp = [
        (int(ms), str(value))
        for ms, value in conn.execute(
            "SELECT open_ms,close FROM candles WHERE exchange='binance_usdm'"
            " AND symbol=? AND timeframe='1d' AND open_ms>=? AND open_ms<? ORDER BY open_ms",
            (symbol, START_MS, END_MS),
        )
    ]
    funding_rows = conn.execute(
        "SELECT funding_ms,rate,mark_price FROM funding_rates WHERE exchange='binance_usdm'"
        " AND symbol=? AND funding_ms>=? AND funding_ms<? ORDER BY funding_ms",
        (symbol, START_MS, END_MS),
    ).fetchall()
    funding = [(int(ms), str(rate)) for ms, rate, _mark in funding_rows]
    starts, states = regime_starts(spot)
    years = Counter(
        datetime.fromtimestamp(ms / 1000, UTC).year for ms in starts if START_MS <= ms < END_MS
    )
    counts = {name: sum(lo <= ms < hi for ms in starts) for name, lo, hi in SPLITS}
    carries = {}
    for name, lo, _hi in SPLITS:
        prior = [on for ms, on in states if ms < lo]
        carries[name] = bool(prior and prior[-1])
    return SymbolAudit(
        symbol,
        coverage(spot, DAY_MS),
        coverage(perp, DAY_MS),
        coverage(funding, 8 * 3_600_000, jitter_tolerance_ms=1000),
        sum(mark is None for _ms, _rate, mark in funding_rows),
        dict(sorted(years.items())),
        counts,
        carries,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/trading.db"))
    args = parser.parse_args()
    db: Path = args.db
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        symbols = [audit_symbol(conn, symbol) for symbol in SYMBOLS]
    counts = {
        name: sum(row.transitions_by_split[name] for row in symbols) for name, _lo, _hi in SPLITS
    }
    # Even allowing one left-boundary carry per symbol cannot reach 100 test episodes.
    test_name = SPLITS[-1][0]
    test_upper_bound = counts[test_name] + len(SYMBOLS)
    record = {
        "research": 209,
        "stage": "PHASE0",
        "status": "DATA_INSUFFICIENT",
        "candidate_performance_computed": False,
        "fresh_oos_claimed": False,
        "end_exclusive_ms": END_MS,
        "symbols": [asdict(row) for row in symbols],
        "pooled_transition_counts": counts,
        "test_episode_upper_bound_including_boundary_carries": test_upper_bound,
        "phase4_min_actual_episodes": 100,
        "phase4_sample_gate_possible": test_upper_bound >= 100,
        "power": {
            "target": 0.8,
            "z_alpha": 2.0,
            "paired_sharpe_variance": "NOT_MEASURED",
            "achieved": None,
            "required_se_for_true_delta_sharpe_0_40_against_floor_0_20": 0.20
            / (2.0 + NormalDist().inv_cdf(0.8)),
            "note": "SE threshold is a design diagnostic, NOT proof of achieved power",
        },
    }
    print(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
