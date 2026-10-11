"""Read-only, date-bounded 209 pilot replay; output only reports/pyramiding_209."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import math
import sqlite3
import statistics
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from zipfile import ZipFile

from auto_trading.backtest.trend import TrendConfig, target_weight
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.indicators import atr_series
from auto_trading.research.pyramiding_209 import (
    DAY_MS,
    END_MS,
    MINUTE_MS,
    SCENARIOS,
    START_MS,
    SYMBOLS,
    DailySignal,
    MinuteBar,
    PilotEngine,
    Position,
)

D = Decimal
ZERO = D(0)
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "pyramiding_209"


def read_daily(conn: sqlite3.Connection, exchange: str, symbol: str) -> list[Candle]:
    rows = conn.execute(
        "SELECT open_ms,close_ms,open,high,low,close,volume FROM candles "
        "WHERE exchange=? AND symbol=? AND timeframe='1d' AND open_ms<? ORDER BY open_ms",
        (exchange, symbol, END_MS),
    )
    candles = [
        Candle(int(t), int(ct), D(str(o)), D(str(h)), D(str(low)), D(str(c)), D(str(v)))
        for t, ct, o, h, low, c, v in rows
    ]
    if any(c.close_ms != c.open_ms + DAY_MS - 1 or c.close_ms >= END_MS for c in candles):
        raise DataIntegrityError("daily completion/boundary failure")
    if any(b.open_ms != a.open_ms + DAY_MS for a, b in itertools.pairwise(candles)):
        raise DataIntegrityError("daily gap or duplicate")
    return candles


def build_signals(spot: Sequence[Candle], perp: Sequence[Candle]) -> dict[int, DailySignal]:
    """Every signal is built from its own completed prefix; no whole-sample fitting."""
    atrs = atr_series(perp, 20)
    atr = {c.open_ms: a for c, a in zip(perp, atrs, strict=True) if a is not None}
    prices = {c.open_ms: c.close for c in perp}
    allowed: dict[int, bool] = {}
    closes: list[float] = []
    for candle in spot:
        closes.append(float(candle.close))
        weight = target_weight(closes, TrendConfig(long_only=True))
        allowed[candle.open_ms] = weight is not None and weight > 0
    result: dict[int, DailySignal] = {}
    for day_ms in range(START_MS, END_MS, DAY_MS):
        prev_ms = day_ms - DAY_MS
        if any(t not in allowed for t in (prev_ms, prev_ms - DAY_MS)) or prev_ms not in atr:
            raise DataIntegrityError("missing signal warmup")
        result[day_ms] = DailySignal(
            allowed[prev_ms],
            allowed[prev_ms] and not allowed[prev_ms - DAY_MS],
            prices[prev_ms],
            atr[prev_ms],
        )
    return result


def month_bounds() -> Iterator[tuple[str, int, int]]:
    year, month = 2020, 3
    while (year, month) <= (2026, 8):
        begin = int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        end = int(datetime(next_year, next_month, 1, tzinfo=UTC).timestamp() * 1000)
        yield f"{year:04d}-{month:02d}", begin, end
        year, month = next_year, next_month


def minute_frames(conn: sqlite3.Connection) -> Iterator[dict[str, MinuteBar]]:
    """Explicit monthly paths/SQL; no directory recursion or post-boundary ZIP reading."""
    for month, begin, end in month_bounds():
        eth_file = ROOT / "data/external/binance_vision/um/ETHUSDT/1m" / f"ETHUSDT-1m-{month}.zip"
        btc_rows = conn.execute(
            "SELECT open_ms,open,high,low,close,volume,close_ms FROM candles "
            "WHERE exchange='binance_usdm' AND symbol='BTCUSDT' AND timeframe='1m' "
            "AND open_ms>=? AND open_ms<? ORDER BY open_ms",
            (begin, end),
        )
        with ZipFile(eth_file) as archive:
            names = [n for n in archive.namelist() if n.endswith(".csv")]
            if len(names) != 1:
                raise DataIntegrityError("ambiguous ETH archive")
            with archive.open(names[0]) as handle:
                eth_rows = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
                eth_values = (r for r in eth_rows if r and r[0].isdigit())
                count = 0
                for btc, eth in zip(btc_rows, eth_values, strict=True):
                    t = int(btc[0])
                    if (
                        t != begin + count * MINUTE_MS
                        or int(eth[0]) != t
                        or not START_MS <= t < END_MS
                        or int(btc[6]) != t + MINUTE_MS - 1
                        or int(eth[6]) != t + MINUTE_MS - 1
                    ):
                        raise DataIntegrityError("209 minute boundary/coverage failure")
                    count += 1
                    frame = {
                        "BTCUSDT": MinuteBar(t, *(D(str(v)) for v in btc[1:6])),
                        "ETHUSDT": MinuteBar(t, *(D(v) for v in eth[1:6])),
                    }
                    for bar in frame.values():
                        if not (
                            ZERO
                            < bar.low
                            <= min(bar.open, bar.close)
                            <= max(bar.open, bar.close)
                            <= bar.high
                            and bar.volume >= ZERO
                        ):
                            raise DataIntegrityError("invalid minute OHLCV")
                    yield frame
                if count != (end - begin) // MINUTE_MS:
                    raise DataIntegrityError("incomplete month")


def read_funding(conn: sqlite3.Connection) -> dict[str, list[tuple[int, Decimal]]]:
    result: dict[str, list[tuple[int, Decimal]]] = {}
    for s in SYMBOLS:
        result[s] = [
            (int(t), D(str(rate)))
            for t, rate in conn.execute(
                "SELECT funding_ms,rate FROM funding_rates WHERE exchange='binance_usdm' "
                "AND symbol=? AND funding_ms>=? AND funding_ms<? ORDER BY funding_ms",
                (s, START_MS, END_MS),
            )
        ]
        if len(result[s]) != 7125 or any(
            abs(b[0] - a[0] - 28_800_000) > 1000
            for a, b in zip(result[s], result[s][1:], strict=False)
        ):
            raise DataIntegrityError("funding coverage")
    return result


def audit_minutes(
    conn: sqlite3.Connection, daily: Mapping[str, Sequence[Candle]]
) -> dict[str, object]:
    """No candidate PnL; verify every minute and aggregate daily OHLC before replay."""
    by_day = {s: {c.open_ms: c for c in candles} for s, candles in daily.items()}
    digests = {s: hashlib.sha256() for s in SYMBOLS}
    aggregate: dict[str, list[Decimal]] = {}
    count = 0
    volume_differences = Counter[str]()
    volume_details: dict[str, list[dict[str, object]]] = {s: [] for s in SYMBOLS}
    for bars in minute_frames(conn):
        t = bars[SYMBOLS[0]].open_ms
        for s, bar in bars.items():
            digests[s].update(json.dumps(asdict(bar), default=str, separators=(",", ":")).encode())
            if t % DAY_MS == 0:
                aggregate[s] = [bar.open, bar.high, bar.low, bar.close, bar.volume]
            else:
                a = aggregate[s]
                a[1], a[2], a[3], a[4] = (
                    max(a[1], bar.high),
                    min(a[2], bar.low),
                    bar.close,
                    a[4] + bar.volume,
                )
            if (t + MINUTE_MS) % DAY_MS == 0:
                candle = by_day[s][t // DAY_MS * DAY_MS]
                a = aggregate[s]
                if a[:4] != [candle.open, candle.high, candle.low, candle.close]:
                    raise DataIntegrityError(f"daily/minute price mismatch: {s} {candle.open_ms}")
                if abs(a[4] - candle.volume) > D("0.000001"):
                    volume_differences[s] += 1
                    volume_details[s].append(
                        {
                            "day_ms": candle.open_ms,
                            "minute_sum": str(a[4]),
                            "daily_volume": str(candle.volume),
                            "delta": str(a[4] - candle.volume),
                            "relative": str((a[4] - candle.volume) / candle.volume),
                        }
                    )
        count += 1
        if (t + MINUTE_MS) % (DAY_MS * 30) == 0:
            print(json.dumps({"audit_minutes": count, "last_ms": t}))
    return {
        "minutes_per_symbol": count,
        "digests": {s: h.hexdigest() for s, h in digests.items()},
        "volume_daily_mismatch_days": dict(volume_differences),
        "volume_details": volume_details,
        "price_mismatch_days": 0,
    }


def metrics(engine: PilotEngine) -> dict[str, object]:
    equities = [float(engine.cfg.capital), *(float(r.equity) for r in engine.daily)]
    returns = [b / a - 1 for a, b in itertools.pairwise(equities)]
    sd = statistics.stdev(returns)
    peak = equities[0]
    mdd = 0.0
    for eq in equities[1:]:
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return {
        "net_pnl": str(engine.equity() - engine.cfg.capital),
        "final_equity": str(engine.equity()),
        "cagr": (equities[-1] / equities[0]) ** (365 / len(returns)) - 1,
        "sharpe": statistics.mean(returns) / sd * math.sqrt(365) if sd else 0.0,
        "mdd": mdd,
        "minute_mdd": str(engine.minute_mdd),
        "average_daily_exposure": statistics.mean(float(r.exposure) for r in engine.daily),
        "max_minute_exposure": str(engine.max_exposure),
        "max_minute_reserved_risk": str(engine.max_risk),
        "traded_notional": str(engine.traded_notional),
        "turnover_initial_capital": str(engine.traded_notional / engine.cfg.capital),
        "fees": str(engine.fees),
        "slippage": str(engine.slippage),
        "emergency": str(engine.emergency),
        "funding_paid": str(engine.funding),
        "gross": str(engine.daily[-1].gross),
        "completed_episodes": len(engine.finished),
        "open_episodes": len(engine.positions),
        "terminal_unrealized": str(engine.equity() - engine.cash),
        "terminal_exit_cost_not_deducted": str(engine.exposure() * engine.cfg.cost_rate),
        "halted": engine.halted,
        "passive_cap_breach_minutes": engine.cap_breach_minutes,
        "worst_day": min(returns),
        "worst_30d": min(equities[i] / equities[i - 30] - 1 for i in range(30, len(equities))),
    }


def episode_record(pos: Position, price: Decimal) -> dict[str, object]:
    lots = []
    for lot in pos.lots.values():
        lots.append(
            dict(
                **asdict(lot),
                terminal_gross=lot.value(price),
                net=lot.net(price),
                giveback=max(lot.peak_gross - lot.value(price), ZERO),
            )
        )
    return {
        "symbol": pos.symbol,
        "start_ms": pos.start_ms,
        "end_ms": pos.end_ms,
        "reason": pos.reason,
        "q0": pos.q0,
        "p0": pos.p0,
        "atr": pos.atr,
        "stop": pos.stop,
        "net": pos.net(price),
        "lots": lots,
    }


def summarize(engine: PilotEngine) -> dict[str, object]:
    episodes = [*engine.finished, *engine.positions.values()]
    add_lots = [lot for p in episodes for stage, lot in p.lots.items() if stage > 0]
    add_net = [
        lot.net(engine.prices[p.symbol])
        for p in episodes
        for stage, lot in p.lots.items()
        if stage > 0
    ]
    yearly: dict[str, dict[str, Decimal]] = {}
    previous_eq = engine.cfg.capital
    previous_symbol = dict.fromkeys(SYMBOLS, ZERO)
    for r in engine.daily:
        year = str(datetime.fromtimestamp(r.day_ms / 1000, UTC).year)
        bucket = yearly.setdefault(year, {"net": ZERO, "BTCUSDT": ZERO, "ETHUSDT": ZERO})
        bucket["net"] += r.equity - previous_eq
        for s in SYMBOLS:
            bucket[s] += r.symbol_value[s] - previous_symbol[s]
        previous_eq = r.equity
        previous_symbol = dict(r.symbol_value)
    split_ranges = (
        ("historical_train", START_MS, 1_672_531_200_000),
        ("historical_validation", 1_672_531_200_000, 1_704_067_200_000),
        ("historical_test_NOT_OOS", 1_704_067_200_000, END_MS),
    )
    splits: dict[str, dict[str, object]] = {}
    for name, start, end in split_ranges:
        records = [r for r in engine.daily if start <= r.day_ms < end]
        earlier = [r for r in engine.daily if r.day_ms < start]
        first_eq = earlier[-1].equity if earlier else engine.cfg.capital
        splits[name] = {
            "net": records[-1].equity - first_eq,
            "return_pct": records[-1].equity / first_eq - 1,
        }
    stage_counts: dict[int, dict[str, object]] = {}
    for stage in range(3):
        events = [e for e in engine.events if e["stage"] == stage]
        reasons = Counter(reason for e in events for reason in cast(list[str], e["reasons"]))
        stage_counts[stage] = {
            "qualified": len(events),
            "approved": sum(D(str(e["approved"])) > ZERO for e in events),
            "executed": sum(D(str(e["filled"])) > ZERO for e in events),
            "reduced": sum(ZERO < D(str(e["filled"])) < D(str(e["requested"])) for e in events),
            "skipped": sum(D(str(e["filled"])) == ZERO for e in events),
            "reasons": dict(reasons),
        }
    return {
        "metrics": metrics(engine),
        "stage_counts": stage_counts,
        "reasons": dict(engine.reasons),
        "add_direct_net": sum(add_net, ZERO),
        "add_loss_sum": sum((v for v in add_net if v < ZERO), ZERO),
        "add_worst_lot": min(add_net, default=ZERO),
        "add_giveback": sum(
            (
                max(lot.peak_gross - lot.value(engine.prices[p.symbol]), ZERO)
                for p in episodes
                for stage, lot in p.lots.items()
                if stage > 0
            ),
            ZERO,
        ),
        "add_fees": sum((lot.fees for lot in add_lots), ZERO),
        "add_slippage": sum((lot.slippage for lot in add_lots), ZERO),
        "add_emergency": sum((lot.emergency for lot in add_lots), ZERO),
        "add_funding": sum((lot.funding for lot in add_lots), ZERO),
        "symbol_net": engine.daily[-1].symbol_value,
        "years": yearly,
        "splits": splits,
        "episodes": [episode_record(p, engine.prices[p.symbol]) for p in episodes],
        "events": engine.events,
    }


def replay(
    conn: sqlite3.Connection,
    signals: Mapping[str, Mapping[int, DailySignal]],
    funding: Mapping[str, list[tuple[int, Decimal]]],
    prices: Mapping[str, Decimal],
) -> dict[str, PilotEngine]:
    engines = {
        f"{cfg.name}_{variant}": PilotEngine(cfg, variant == "C", prices)
        for cfg in SCENARIOS
        for variant in ("B", "C")
    }
    indices = dict.fromkeys(SYMBOLS, 0)
    for bars in minute_frames(conn):
        t = bars[SYMBOLS[0]].open_ms
        payments: dict[str, list[tuple[int, Decimal]]] = {s: [] for s in SYMBOLS}
        for s in SYMBOLS:
            events = funding[s]
            while indices[s] < len(events) and events[indices[s]][0] < t + MINUTE_MS:
                event = events[indices[s]]
                if event[0] < t:
                    raise DataIntegrityError("funding missed by replay")
                payments[s].append(event)
                indices[s] += 1
        for engine in engines.values():
            if t % DAY_MS == 0:
                engine.begin_day(t, {s: signals[s][t] for s in SYMBOLS})
            engine.step(bars, payments)
            if (t + MINUTE_MS) % DAY_MS == 0:
                engine.end_day(t // DAY_MS * DAY_MS)
        if t % (DAY_MS * 30) == 0:
            print(json.dumps({"replay_through_ms": t, "status": "historical_pilot_no_PASS"}))
    if any(indices[s] != len(funding[s]) for s in SYMBOLS):
        raise DataIntegrityError("unconsumed funding")
    return engines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    if args.audit_only == args.replay:
        parser.error("select exactly --audit-only or --replay")
    conn = sqlite3.connect(f"file:{(ROOT / 'data/trading.db').as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    try:
        daily = {s: read_daily(conn, "binance_usdm", s) for s in SYMBOLS}
        signals = {s: build_signals(read_daily(conn, "binance_spot", s), daily[s]) for s in SYMBOLS}
        funding = read_funding(conn)
        code_digest = hashlib.sha256()
        for path in (
            Path(__file__),
            ROOT / "src/auto_trading/research/pyramiding_209.py",
            ROOT / "src/auto_trading/backtest/trend.py",
            ROOT / "src/auto_trading/features/indicators.py",
            ROOT / "src/auto_trading/core/money.py",
            ROOT / "docs/research/209-t2-pyramiding-pilot-registration.md",
        ):
            code_digest.update(path.read_bytes())
        manifest = {
            "registration": "pilot-v1",
            "code_sha256": code_digest.hexdigest(),
            "start_ms": START_MS,
            "end_exclusive_ms": END_MS,
            "adoption": "UNVERIFIED",
            "PASS": False,
            "funding_count": {s: len(funding[s]) for s in SYMBOLS},
            "transition_count": {
                s: sum(v.transition for v in signals[s].values()) for s in SYMBOLS
            },
            "scenarios": [asdict(cfg) for cfg in SCENARIOS],
        }
        OUTPUT.mkdir(parents=True, exist_ok=True)
        if args.audit_only:
            manifest["audit"] = audit_minutes(conn, daily)
            (OUTPUT / "audit.json").write_text(
                json.dumps(manifest, default=str, indent=2), encoding="utf-8"
            )
        else:
            audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
            if audit["code_sha256"] != manifest["code_sha256"]:
                raise DataIntegrityError(
                    "code changed after minute audit; re-audit before performance"
                )
            prices = {
                s: next(c.close for c in daily[s] if c.open_ms == START_MS - DAY_MS)
                for s in SYMBOLS
            }
            engines = replay(conn, signals, funding, prices)
            result = {
                "manifest": manifest,
                "audit": audit["audit"],
                "runs": {name: summarize(engine) for name, engine in engines.items()},
            }
            for name, engine in engines.items():
                (OUTPUT / f"{name}_daily.json").write_text(
                    json.dumps([asdict(r) for r in engine.daily], default=str, indent=2),
                    encoding="utf-8",
                )
                for ledger, records in (
                    ("fills", engine.fill_ledger),
                    ("funding", engine.funding_ledger),
                ):
                    (OUTPUT / f"{name}_{ledger}.json").write_text(
                        json.dumps(records, default=str, indent=2), encoding="utf-8"
                    )
            (OUTPUT / "results.json").write_text(
                json.dumps(result, default=str, indent=2), encoding="utf-8"
            )
            print(json.dumps({"output": str(OUTPUT / "results.json"), "status": "NO_PASS"}))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
