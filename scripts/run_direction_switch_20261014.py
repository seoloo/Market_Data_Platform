"""One approved explanatory replay; fixed inputs, no broker or live path."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import logging
import math
import statistics
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from auto_trading.core.errors import DataIntegrityError
from auto_trading.features.tsmom import trend_direction
from auto_trading.research.signal_switching_20261014 import Direction, Signal
from auto_trading.research.switching_replay_20261014 import (
    H4,
    MINUTE,
    Book,
    Frame,
    Market,
    Mode,
    Quote,
)
from auto_trading.research.wallet_grid_20261013 import SCENARIOS, SYMBOLS, Bar
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "reports/wallet_grid_20261013"
PRICES = ROOT / "data/research/switching_20261014"
OUT = ROOT / "reports/direction_switch_20261014"
INPUT_ID = "96dc97701d133ab3f4ec261534ed55c4129fd09507b535b884ff0e1c16107235"
FILES = (
    "docs/research/DIRECTION_SWITCH_PILOT_20261014.md",
    "src/auto_trading/research/signal_switching_20261014.py",
    "src/auto_trading/research/switching_replay_20261014.py",
    "scripts/run_direction_switch_20261014.py",
    "tests/research/test_signal_switching_20261014.py",
    "tests/research/test_switching_replay_20261014.py",
    "src/auto_trading/research/wallet_grid_20261013.py",
    "scripts/wallet_grid_data_20261013.py",
)
LOG = logging.getLogger(__name__)
D = Decimal
ZERO = D(0)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(type(value).__name__)


def save(name: str, value: Any) -> None:
    with (OUT / name).open("x", encoding="utf8") as stream:
        json.dump(value, stream, default=serialize, ensure_ascii=False, indent=2)


def inventory() -> dict[str, Any]:
    manifest = json.loads((SOURCE / "aligned_manifest.json").read_text(encoding="utf8"))
    if manifest["input_id"] != INPUT_ID:
        raise DataIntegrityError("changed canonical input")
    for entry in manifest["entries"]:
        for kind in ("values", "state"):
            if digest(SOURCE / "aligned" / entry[kind]) != entry[kind + "_sha256"]:
                raise DataIntegrityError("changed aligned price cache")
    entries = {
        str(p.relative_to(ROOT)).replace("\\", "/"): digest(p) for p in sorted(PRICES.glob("*.csv"))
    }
    if len(entries) != 6:
        raise DataIntegrityError("six frozen daily price files required")
    entries.update(
        {
            str(SOURCE.relative_to(ROOT) / n).replace("\\", "/"): digest(SOURCE / n)
            for n in ("features.json", "aligned_manifest.json")
        }
    )
    entries.update({name: digest(ROOT / name) for name in FILES})
    return {
        "canonical_input_id": INPUT_ID,
        "sha256": entries,
        "config": asdict(Book(Market.CRYPTO, Mode.SWITCH, D("0.7"), SCENARIOS[0]).cfg),
        "accounts": 64,
        "formal_pass": False,
        "approval": "USER_APPROVED_EXPLANATORY_ONE_SHOT",
    }


def books(market: Market) -> list[Book]:
    return [
        Book(market, mode, ratio, scenario)
        for ratio in (D("0.7"), D("0.9"))
        for scenario in SCENARIOS
        for mode in Mode
    ]


def quote_pairs(
    long: tuple[Bar, Bar],
    short: tuple[Bar, Bar],
    previous_long: tuple[Bar, Bar],
    previous_short: tuple[Bar, Bar],
) -> tuple[Quote, ...]:
    return tuple(
        Quote(asset, long[i], short[i], previous_long[i], previous_short[i])
        for i, asset in enumerate(SYMBOLS)
    )


def crypto(accounts: list[Book]) -> tuple[Quote, ...]:
    manifest = json.loads((SOURCE / "aligned_manifest.json").read_text(encoding="utf8"))
    features = json.loads((SOURCE / "features.json").read_text(encoding="utf8"))
    last: tuple[Quote, ...] = ()
    for index, block in enumerate(data.blocks(SOURCE / "aligned", manifest, features)):
        day = datetime.fromtimestamp(block.at_ms / 1000, UTC).date().isoformat()
        last = quote_pairs(block.spot, block.perp, block.previous_spot, block.previous_perp)
        boundary = Frame(block.at_ms, block.at_ms + H4, day, last)
        signals: dict[str, tuple[Signal, ...]] = {}
        weights: dict[str, Decimal] = {}
        for i, asset in enumerate(SYMBOLS):
            signal_events: list[Signal] = []
            if block.targets is not None:
                if block.target_as_of_ms != block.at_ms - 24 * 60 * MINUTE:
                    raise DataIntegrityError("daily target source boundary")
                weights[asset] = block.targets[i]
                signal_events.append(
                    Signal(
                        Direction.LONG if block.targets[i] > ZERO else Direction.FLAT,
                        f"{asset}:DAILY:{block.at_ms}",
                        block.at_ms,
                    )
                )
            if block.signals[i]:
                if block.signal_as_of_ms != block.at_ms:
                    raise DataIntegrityError("short source boundary")
                signal_events.append(
                    Signal(Direction.SHORT, f"{asset}:RSI:{block.at_ms}", block.at_ms)
                )
            signals[asset] = tuple(signal_events)
        for account in accounts:
            account.begin(boundary, signals, weights)
            account.prepare(boundary)
        event_buckets = {
            minute: tuple(
                tuple(
                    e
                    for e in group
                    if block.at_ms + minute * MINUTE
                    <= e.at_ms
                    < block.at_ms + (minute + 1) * MINUTE
                )
                for group in block.funding
            )
            for minute in {
                int((e.at_ms - block.at_ms) // MINUTE) for group in block.funding for e in group
            }
        }
        candidates = [a for a in accounts if a.active(block.at_ms)]
        for minute in range(240) if candidates else ():
            at_ms = block.at_ms + minute * MINUTE
            active = [a for a in candidates if a.active(at_ms)]
            events = event_buckets.get(minute, ((), ()))
            if active:
                bar = block.minutes.get(minute)
                quotes = quote_pairs(bar.spot, bar.perp, bar.previous_spot, bar.previous_perp)
                tick = Frame(at_ms, at_ms + MINUTE, day, quotes, events)
                for account in active:
                    account.tick(tick)
            if minute in event_buckets:
                active_ids = {id(a) for a in active}
                for account in accounts:
                    if id(account) not in active_ids:
                        for asset, group in zip(SYMBOLS, events, strict=True):
                            for event in group:
                                account.rates[asset] = event.rate
                                account.rate_ms[asset] = event.at_ms
        if not candidates:
            for account in accounts:
                for asset, group in zip(SYMBOLS, block.funding, strict=True):
                    for event in group:
                        account.rates[asset] = event.rate
                        account.rate_ms[asset] = event.at_ms
        for account in accounts:
            account.checkpoint(boundary)
        if index % 180 == 0:
            LOG.info(json.dumps({"market": "CRYPTO", "blocks": index, "at_ms": block.at_ms}))
    return last


def rows(symbol: str, suffix: str) -> list[dict[str, str]]:
    with (PRICES / f"{symbol}_{suffix}.csv").open(encoding="utf8") as stream:
        return list(csv.DictReader(stream))


def stock(accounts: list[Book]) -> tuple[Quote, ...]:
    adjusted = {symbol: rows(symbol, "adj") for symbol in ("QQQ", "TQQQ", "SQQQ")}
    indexed = {symbol: {r["date"]: r for r in values} for symbol, values in adjusted.items()}
    raw = {symbol: {r["date"]: r for r in rows(symbol, "raw")} for symbol in ("TQQQ", "SQQQ")}
    scale = {symbol: D(100) / D(adjusted[symbol][0]["open"]) for symbol in ("TQQQ", "SQQQ")}
    zone = ZoneInfo("America/New_York")
    closes: list[Decimal] = []
    previous_date = ""
    last: tuple[Quote, ...] = ()

    def bar(symbol: str, day: str) -> Bar:
        r, original = indexed[symbol][day], raw[symbol][day]
        prices = [D(r[k]) * scale[symbol] for k in ("open", "high", "low", "close")]
        # Conceptual adjusted economic units; conservative provider-dollar liquidity proxy.
        volume = D(original["open"]) * D(original["volume"]) / prices[0]
        return Bar(prices[0], prices[1], prices[2], prices[3], volume)

    for row in adjusted["QQQ"]:
        day = row["date"]
        if day >= "2026-09-01":
            raise DataIntegrityError("stock cutoff")
        direction = trend_direction(closes, (20, 60, 120))
        if day >= "2011-01-01":
            if direction is None or not previous_date:
                raise DataIntegrityError("stock warmup")
            now = date.fromisoformat(day)
            at_ms = int(datetime.combine(now, time(9, 30), zone).timestamp() * 1000)
            end_ms = int(datetime.combine(now, time(16), zone).timestamp() * 1000)
            long, short = bar("TQQQ", day), bar("SQQQ", day)
            previous_long, previous_short = bar("TQQQ", previous_date), bar("SQQQ", previous_date)
            last = (Quote("QQQ_PAIR", long, short, previous_long, previous_short),)
            frame = Frame(at_ms, end_ms, day, last)
            wanted = (
                Direction.LONG
                if direction > ZERO
                else Direction.SHORT
                if direction < ZERO
                else Direction.FLAT
            )
            # Decision time is this open; its entire signal input ended the previous session.
            signal = Signal(wanted, f"QQQ:{previous_date}:DECISION:{at_ms}", at_ms)
            for account in accounts:
                account.begin(frame, {"QQQ_PAIR": (signal,)}, {"QQQ_PAIR": D(1)})
                account.tick(frame)
                account.checkpoint(frame)
        closes.append(D(row["close"]))
        previous_date = day
    return last


def reconcile(result: dict[str, Any], quotes: tuple[Quote, ...], market: Market) -> dict[str, Any]:
    """Separate ledger arithmetic; does not call the decision or replay engine."""
    cash = D(10000) - result["tax_paid"]
    held: dict[str, tuple[Direction, Decimal, Decimal]] = {}
    fees = slips = turnover = ZERO
    for fill in result["fills"]:
        asset, direction, qty, price = (
            fill["asset"],
            fill["direction"],
            fill["quantity"],
            fill["price"],
        )
        is_future = market == Market.CRYPTO and direction == Direction.SHORT
        old_direction, old_qty, entry = held.get(asset, (direction, ZERO, ZERO))
        if old_qty and old_direction != direction:
            raise DataIntegrityError("reconciliation overlapping directions")
        if fill["action"] == "OPEN":
            total = old_qty + qty
            held[asset] = (direction, total, (old_qty * entry + qty * price) / total)
            delta = -fill["fee"] if is_future else -qty * price - fill["fee"]
        else:
            if qty > old_qty:
                raise DataIntegrityError("reconciliation over-close")
            delta = qty * (entry - price) - fill["fee"] if is_future else qty * price - fill["fee"]
            if qty == old_qty:
                held.pop(asset)
            else:
                held[asset] = (direction, old_qty - qty, entry)
        if delta != fill["cash_delta"]:
            raise DataIntegrityError("cash delta mismatch")
        cash += delta
        fees += fill["fee"]
        slips += qty * abs(price - fill["raw_price"])
        turnover += qty * price
        if fill["reason"] != "EXCHANGE_STOP_MARKET_EXCEPTION":
            buy = (fill["action"] == "OPEN") != is_future
            ref = fill["reference"]
            if (buy and price > ref * D("1.003")) or (not buy and price < ref * D("0.997")):
                raise DataIntegrityError("ordinary limit breached")
    funding = sum((e["quantity"] * e["mark"] * e["rate"] for e in result["funding_events"]), ZERO)
    cash += funding
    equity = cash - result["tax_reserve"]
    for q in quotes:
        if q.asset in held:
            direction, qty, entry = held[q.asset]
            price = q.bar(direction).c
            equity += (
                qty * (entry - price)
                if market == Market.CRYPTO and direction == Direction.SHORT
                else qty * price
            )
    differences = {
        "cash": abs(cash - result["cash"]),
        "equity": abs(equity - result["terminal_equity"]),
        "fees": abs(fees - result["fees"]),
        "slips": abs(slips - result["slips"]),
        "turnover": abs(turnover - result["turnover"]),
        "funding": abs(funding - result["funding"]),
    }
    if market == Market.STOCK:
        yearly: dict[str, Decimal] = {}
        for trade in result["trades"]:
            year = datetime.fromtimestamp(trade["exit_ms"] / 1000, UTC).strftime("%Y")
            yearly[year] = yearly.get(year, ZERO) + trade["net"]
        final_year = result["trace"][-1]["day"][:4]
        tax_by_year = {
            year: max(ZERO, gain - D("1786")) * D("0.22") for year, gain in yearly.items()
        }
        differences["tax_reserve"] = abs(tax_by_year.get(final_year, ZERO) - result["tax_reserve"])
        differences["tax_paid"] = abs(
            sum((tax for year, tax in tax_by_year.items() if year != final_year), ZERO)
            - result["tax_paid"]
        )
    if len(held) != len(result["positions"]):
        raise DataIntegrityError("terminal position count mismatch")
    for asset, (direction, qty, entry) in held.items():
        position = result["positions"][asset]
        if (
            position["direction"] != direction
            or position["quantity"] != qty
            or position["entry"] != entry
        ):
            raise DataIntegrityError("terminal position mismatch")
    if max(differences.values()) > D("0.000000001"):
        raise DataIntegrityError("independent ledger reconciliation failed")
    return {
        "differences": differences,
        "same_asset_overlap": 0,
        "ordinary_limit_breach": 0,
        "strategy_reruns": 0,
    }


def metrics(result: dict[str, Any], market: Market) -> dict[str, Any]:
    trace = result["trace"]
    daily = {r["day"]: float(r["equity"]) for r in trace}
    values = [10000.0, *daily.values()]
    returns = [b / a - 1 for a, b in pairwise(values)]
    peak, drawdown = 10000.0, 0.0
    for row in trace:
        value = float(row["equity"])
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    elapsed = (
        datetime.fromisoformat(trace[-1]["day"]) - datetime.fromisoformat(trace[0]["day"])
    ).days + 1
    years = elapsed / 365.25
    net = float(result["net"])
    annual_days = 252 if market == Market.STOCK else 365.25
    sd = statistics.stdev(returns) if len(returns) > 1 else 0
    annual: dict[str, float] = {}
    previous = 10000.0
    for day, value in daily.items():
        annual[day[:4]] = annual.get(day[:4], 0) + value - previous
        previous = value
    return {
        "case": result["case"],
        "net": net,
        "return_pct": net / 100,
        "cagr_pct": ((1 + net / 10000) ** (1 / years) - 1) * 100,
        "mdd_pct": drawdown * 100,
        "sharpe": statistics.mean(returns) / sd * math.sqrt(annual_days) if sd else 0,
        "long_trades": sum(t["direction"] == Direction.LONG for t in result["trades"]),
        "short_trades": sum(t["direction"] == Direction.SHORT for t in result["trades"]),
        "closed_long_net": float(
            sum((t["net"] for t in result["trades"] if t["direction"] == Direction.LONG), ZERO)
        ),
        "closed_short_net": float(
            sum((t["net"] for t in result["trades"] if t["direction"] == Direction.SHORT), ZERO)
        ),
        "fills": len(result["fills"]),
        "fees": float(result["fees"]),
        "slips": float(result["slips"]),
        "funding": float(result["funding"]),
        "tax_paid": float(result["tax_paid"]),
        "tax_reserve": float(result["tax_reserve"]),
        "terminal_reserve": float(result["terminal_reserve"]),
        "halted_at_ms": result["halted_at_ms"],
        "pending_orders": result["pending_orders"],
        "max_long_exposure_pct": float(max(r["long_exposure_pct"] for r in trace)) * 100,
        "max_short_exposure_pct": float(max(r["short_exposure_pct"] for r in trace)) * 100,
        "mean_short_exposure_pct": float(
            sum((r["short_exposure_pct"] for r in trace), ZERO) / len(trace)
        )
        * 100,
        "annual_equity_change": annual,
        "counts": result["counts"],
        "formal_pass": False,
        "start": trace[0]["day"],
        "end": trace[-1]["day"],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("audit", "pilot"))
    args = parser.parse_args()
    current = inventory()
    if args.command == "audit":
        OUT.mkdir(parents=True, exist_ok=True)
        save("input_manifest.json", current)
        LOG.info(
            json.dumps({"audit": "INPUTS_FROZEN", "accounts": 64, "performance_computed": False})
        )
        return
    committed = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True
    )
    if committed.strip():
        raise DataIntegrityError("commit registered source before returns")
    frozen = json.loads((OUT / "input_manifest.json").read_text(encoding="utf8"))
    if json.loads(json.dumps(current, default=serialize)) != frozen:
        raise DataIntegrityError("input/source changed after inventory freeze")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    save(
        "ONE_SHOT_STARTED.json",
        {
            "code_commit": revision,
            "manifest_sha256": digest(OUT / "input_manifest.json"),
            "started_at": datetime.now(UTC).isoformat(),
            "formal_pass": False,
        },
    )
    all_metrics: list[dict[str, Any]] = []
    all_checks: dict[str, Any] = {}
    outputs: dict[str, str] = {}
    for market, replay in ((Market.STOCK, stock), (Market.CRYPTO, crypto)):
        accounts = books(market)
        final_quotes = replay(accounts)
        for index, account in enumerate(accounts):
            result = account.result(final_quotes)
            check = reconcile(result, final_quotes, market)
            all_checks[account.key] = check
            all_metrics.append(metrics(result, market))
            name = f"{market.lower()}_{index:02d}.json.gz"
            with (
                (OUT / name).open("xb") as stream,
                gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as zipped,
            ):
                zipped.write(
                    json.dumps(
                        result, default=serialize, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                )
            outputs[name] = digest(OUT / name)
        LOG.info(
            json.dumps({"market": market, "accounts_saved": len(accounts), "ledger_checks": "OK"})
        )
    save("reconciliation.json", all_checks)
    save(
        "result.json",
        {
            "status": "EXPLANATORY_RETROSPECTIVE_COMPLETED_ADOPTION_UNVERIFIED",
            "code_commit": revision,
            "formal_pass": False,
            "independent_oos": 0,
            "accounts": all_metrics,
            "ledger_sha256": outputs,
        },
    )
    save(
        "ONE_SHOT_COMPLETED.json",
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "result_sha256": digest(OUT / "result.json"),
            "accounts": 64,
        },
    )
    LOG.info(json.dumps({"completed": True, "accounts": 64, "formal_pass": False}))


if __name__ == "__main__":
    main()
