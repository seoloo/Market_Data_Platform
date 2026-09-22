#!/usr/bin/env python
"""사전등록한 R1 21일 상대 추세를 TRAIN 2023~2024에서 한 번 선별한다."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from r1_power import LOOKBACK_DAYS as UNIVERSE_LOOKBACK_DAYS  # noqa: E402
from r1_power import build_universe, to_ms  # noqa: E402

from auto_trading.backtest.signals_r1 import (  # noqa: E402
    DAY_MS,
    LOOKBACK_DAYS,
    R1Observation,
    R1Result,
    cumulative_funding_bps,
    evaluate_r1,
    momentum_signal,
    r1_verdict,
)
from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.hashing import sha256_hex  # noqa: E402

TRAIN_FROM = date(2023, 1, 1)
TRAIN_TO = date(2024, 12, 31)
HORIZONS = {"1d": 1, "3d": 3}
COST_BPS = 22.0
LEG = 3
BUCKETS = 5
MIN_SAMPLES = 200
MIN_FUNDING_COVERAGE = 0.90

DailyBars = dict[str, dict[int, tuple[float, float, float]]]
FundingSeries = dict[str, dict[int, float]]


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def load_data(from_date: date, to_date: date) -> tuple[DailyBars, FundingSeries]:
    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    load_from = to_ms(from_date) - (LOOKBACK_DAYS + UNIVERSE_LOOKBACK_DAYS + 2) * DAY_MS
    load_to = to_ms(to_date + timedelta(days=max(HORIZONS.values()) + 1))
    daily: DailyBars = defaultdict(dict)
    for symbol, at_ms, open_, close, quote_volume in conn.execute(
        "SELECT symbol, open_ms, open, close, quote_volume FROM candles"
        " WHERE exchange=? AND timeframe='1d' AND open_ms>=? AND open_ms<?",
        (config.exchange, load_from, load_to),
    ):
        daily[str(symbol)][int(at_ms)] = (float(open_), float(close), float(quote_volume))
    funding: FundingSeries = defaultdict(dict)
    for symbol, at_ms, rate in conn.execute(
        "SELECT symbol, funding_ms, rate FROM funding_rates"
        " WHERE exchange=? AND funding_ms>=? AND funding_ms<?",
        (config.exchange, to_ms(from_date), load_to),
    ):
        funding[str(symbol)][int(at_ms)] = float(rate)
    conn.close()
    return dict(daily), dict(funding)


def funding_coverage(funding: FundingSeries, symbols: set[str]) -> float:
    return len(set(funding) & symbols) / len(symbols) if symbols else 0.0


def build_observations(
    daily: DailyBars,
    funding: FundingSeries,
    universe: dict[str, list[str]],
    *,
    from_date: date,
    to_date: date,
    horizon_days: int,
) -> tuple[list[R1Observation], int]:
    """미래 가격·funding 결측이 하나라도 있는 단면은 통째로 제외한다."""
    closes = {
        symbol: {at_ms: values[1] for at_ms, values in series.items()}
        for symbol, series in daily.items()
    }
    observations: list[R1Observation] = []
    rejected_incomplete = 0
    current = from_date
    while current <= to_date:
        entry_ms = to_ms(current)
        exit_ms = entry_ms + horizon_days * DAY_MS
        members = universe.get(current.strftime("%Y-%m"), [])
        signals: dict[str, float] = {}
        entries: dict[str, float] = {}
        for symbol in members:
            value = momentum_signal(closes.get(symbol, {}), entry_ms)
            entry = daily.get(symbol, {}).get(entry_ms)
            if value is not None and entry is not None and entry[0] > 0:
                signals[symbol] = value
                entries[symbol] = entry[0]
        if len(signals) < max(2 * LEG, 2 * BUCKETS):
            rejected_incomplete += 1
            current += timedelta(days=1)
            continue

        prices: dict[str, float] = {}
        rates: dict[str, float] = {}
        complete = True
        minimum_funding_points = 3 * horizon_days - 1
        for symbol in signals:
            exit_bar = daily.get(symbol, {}).get(exit_ms)
            series = funding.get(symbol, {})
            points = [at_ms for at_ms in series if entry_ms < at_ms < exit_ms]
            cumulative = cumulative_funding_bps(series, entry_ms, exit_ms)
            if (
                exit_bar is None
                or exit_bar[0] <= 0
                or cumulative is None
                or len(points) < minimum_funding_points
            ):
                complete = False
                break
            prices[symbol] = (exit_bar[0] - entries[symbol]) / entries[symbol] * 10_000
            rates[symbol] = cumulative
        if complete:
            observations.append(R1Observation(entry_ms, signals, prices, rates))
        else:
            rejected_incomplete += 1
        current += timedelta(days=1)
    return observations, rejected_incomplete


def result_row(result: R1Result) -> dict[str, object]:
    return {
        "horizon": result.horizon,
        "n": result.sample_count,
        "gross_mean_bps": result.gross_mean_bps,
        "funding_mean_bps": result.funding_mean_bps,
        "net_mean_bps": result.net_mean_bps,
        "net_stderr_bps": result.net_stderr_bps,
        "lower_bound_bps": result.lower_bound_bps,
        "positive_rate": result.positive_rate,
        "quantiles_price_bps": list(result.quantile_means_bps),
        "monotone_rising": result.is_monotone_rising,
        "yearly_net_bps": {str(year): value for year, value in result.yearly_net_means_bps.items()},
        "years_positive": result.years_positive,
        "numeric_pass": result.numeric_pass(MIN_SAMPLES),
    }


def main() -> int:
    _configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", type=date.fromisoformat, default=TRAIN_FROM)
    parser.add_argument("--to", dest="to_date", type=date.fromisoformat, default=TRAIN_TO)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/phase4/r1_screen.json")
    args = parser.parse_args()
    if args.from_date >= args.to_date:
        parser.error("--from은 --to보다 빨라야 한다")

    daily, funding = load_data(args.from_date, args.to_date)
    volume_daily = {
        symbol: {at_ms: (values[1], values[2]) for at_ms, values in series.items()}
        for symbol, series in daily.items()
    }
    universe = build_universe(volume_daily, from_date=args.from_date, to_date=args.to_date)
    universe_symbols = {symbol for members in universe.values() for symbol in members}
    coverage = funding_coverage(funding, universe_symbols)
    if coverage < MIN_FUNDING_COVERAGE:
        raise SystemExit(
            f"funding 적재율 {coverage:.1%} < {MIN_FUNDING_COVERAGE:.0%}: 측정을 중단한다"
        )

    print(
        f"R1 TRAIN {args.from_date} ~ {args.to_date} · 21일 상대 추세 · "
        f"유니버스 {len(universe_symbols)}심볼 · funding {coverage:.1%}"
    )
    results: list[R1Result] = []
    rows: dict[str, dict[str, object]] = {}
    rejected: dict[str, int] = {}
    for label, days in HORIZONS.items():
        observations, rejected_count = build_observations(
            daily,
            funding,
            universe,
            from_date=args.from_date,
            to_date=args.to_date,
            horizon_days=days,
        )
        result = evaluate_r1(
            label,
            observations,
            embargo_ms=days * DAY_MS,
            cost_bps=COST_BPS,
            leg=LEG,
            buckets=BUCKETS,
        )
        results.append(result)
        rows[label] = result_row(result)
        rejected[label] = rejected_count
        print(
            f"  {label}: n={result.sample_count} · gross {result.gross_mean_bps:+.2f} · "
            f"funding {result.funding_mean_bps:+.2f} · net {result.net_mean_bps:+.2f}"
            f"±{result.net_stderr_bps:.2f}bps · 하한 {result.lower_bound_bps:+.2f}"
        )
        rounded_years = {
            year: round(value, 2) for year, value in result.yearly_net_means_bps.items()
        }
        print(
            f"      분위 {[round(value, 2) for value in result.quantile_means_bps]} · "
            f"연도 {rounded_years}"
        )

    passed, reasons = r1_verdict(results, min_samples=MIN_SAMPLES)
    payload: dict[str, object] = {
        "research_id": "R1-C",
        "preregistration": "docs/phase0/26-r1-preregistration.md",
        "from": args.from_date.isoformat(),
        "to": args.to_date.isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "cost_bps": COST_BPS,
        "leg": LEG,
        "min_samples": MIN_SAMPLES,
        "funding_coverage": coverage,
        "incomplete_days": rejected,
        "horizons": rows,
        "passes": passed,
        "failure_reasons": list(reasons),
        "verdict": "TRAIN 통과" if passed else "R1 기각",
    }
    payload["digest"] = sha256_hex(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"판정: {payload['verdict']} · 실패 사유 {list(reasons)}")
    print(f"기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
