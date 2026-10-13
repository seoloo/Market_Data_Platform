"""Bounded local data audit and approved one-shot wallet/grid pilot. No APIs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import subprocess
import sys
import zipfile
from array import array
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import wallet_grid_20261013 as pure
from scripts import run_rsi_stoch_pilot_20261012 as base
from scripts import run_short_long_return_20261013 as prior
from scripts import run_short_validation_20261013 as previous
from scripts import wallet_grid_data_20261013 as data

ROOT = base.REPO
OUT = ROOT / "reports/wallet_grid_20261013"
DOC = "docs/research/LONG_WALLET_GRID_PILOT_20261013.md"
FILES = (
    DOC,
    "scripts/run_wallet_grid_20261013.py",
    "src/auto_trading/research/wallet_grid_20261013.py",
    "tests/research/test_wallet_grid_20261013.py",
    "scripts/wallet_grid_data_20261013.py",
    "tests/scripts/test_wallet_grid_data_20261013.py",
    "docs/research/LONG_WALLET_DATA_EXECUTION_ADDENDUM_20261013.md",
)
START, END = base.START, base.END
SCALE = 100_000_000
Columns = tuple[array[int], array[int], array[int], array[int], array[int]]
INVENTORY_ID = "4875010dc542af6e6d69110132afddc5495f56c4806c4b9ff4e6af80e20d8aab"


def code_hash() -> str:
    return data.payload_hash({f: digest(ROOT / f) for f in FILES})


def prepare() -> None:
    if any(
        (OUT / f).exists() for f in ("aligned_manifest.json", "run_started.json", "result.json")
    ):
        raise DataIntegrityError("prepared/run state exists; no overwrite")
    inventory_data = json.loads((OUT / "source_inventory.json").read_text(encoding="utf8"))
    if (
        inventory_data["source_sha256"] != INVENTORY_ID
        or data.payload_hash({k: v for k, v in inventory_data.items() if k != "source_sha256"})
        != INVENTORY_ID
    ):
        raise DataIntegrityError("source inventory changed")
    frames, legacy = prior.load()
    if legacy["source_sha256"] != prior.EXPECTED_INPUT:
        raise DataIntegrityError("frozen legacy features changed")
    features = json.loads((OUT / "features.json").read_text(encoding="utf8"))
    raw_fund = json.loads((previous.OUT / "funding_rest.json").read_text(encoding="utf8"))
    exact_flags = {
        s: {
            int(x["fundingTime"]): bool(x.get("markPrice") and Decimal(x["markPrice"]) > 0)
            for x in raw_fund[s]
        }
        for s in pure.SYMBOLS
    }
    for feature, frame in zip(features, frames["RSI"], strict=True):
        if feature["at_ms"] != frame.at_ms or feature["signals"] != sorted(frame.signals):
            raise DataIntegrityError("frozen signal alignment")
        weights = None if frame.targets is None else {s: str(w) for s, w in frame.targets.items()}
        if (
            feature["targets"] != weights
            or feature["target_as_of_ms"] != frame.target_as_of_ms
            or feature["signal_as_of_ms"] != frame.signal_as_of_ms
        ):
            raise DataIntegrityError("frozen target/time changed")
        for s in pure.SYMBOLS:
            if any(f["exact_mark"] != exact_flags[s][f["at_ms"]] for f in feature["funding"][s]):
                raise DataIntegrityError("funding mark provenance changed")
            actual = [
                {"at_ms": x.at_ms, "rate": str(x.rate), "mark": str(x.mark)}
                for x in frame.funding[s]
            ]
            if [
                {k: v for k, v in f.items() if k != "exact_mark"} for f in feature["funding"][s]
            ] != actual:
                raise DataIntegrityError("frozen funding changed")
    entries = []
    for i, entry in enumerate(inventory_data["entries"]):
        entries.append(data.cache_source(ROOT, OUT / "aligned", entry))
        if i % 4 == 3:
            print(
                json.dumps({"aligned_month": entry["month"], "new_performance_runs": 0}), flush=True
            )
    manifest = {
        "start_ms": START,
        "end_ms": END,
        "byteorder": sys.byteorder,
        "entries": entries,
        "features_sha256": digest(OUT / "features.json"),
        "inventory_id": INVENTORY_ID,
        "legacy_id": legacy["source_sha256"],
        "closed_windows": data.CLOSED,
        "legacy": legacy,
    }
    differences: dict[str, dict[str, Any]] = {
        k: {"bars": 0, "max_price_delta": "0", "examples": []} for k in ("spot", "perp")
    }
    count = closed_open = closed_close = 0
    for b, old in zip(data.blocks(OUT / "aligned", manifest, features), frames["RSI"], strict=True):
        count += 1
        closed_open += int(not all(x.observed for x in b.spot))
        closed_close += int(not all(b.spot_close_observed))
        for kind, bars, reference in (("spot", b.spot, old.spot), ("perp", b.perp, old.perp)):
            for s, bar in zip(pure.SYMBOLS, bars, strict=True):
                ref = reference[s]
                delta = max(abs(getattr(bar, f) - getattr(ref, f)) for f in ("o", "h", "low", "c"))
                if delta > Decimal("0.00000001"):
                    diff = differences[kind]
                    diff["bars"] += 1
                    diff["max_price_delta"] = str(max(Decimal(diff["max_price_delta"]), delta))
                    if len(diff["examples"]) < 12:
                        diff["examples"].append(
                            {"at_ms": b.at_ms, "symbol": s, "delta": str(delta)}
                        )
        if b.target_as_of_ms > b.at_ms or b.signal_as_of_ms > b.at_ms:
            raise DataIntegrityError("future frozen feature")
        for events in b.funding:
            if any(x.at_ms >= y.at_ms for x, y in pairwise(events)) or any(
                not b.at_ms <= x.at_ms < b.at_ms + pure.H4 for x in events
            ):
                raise DataIntegrityError("funding duplicate/boundary")
    # Futures signals must use the exact same canonical market-price source.
    if differences["perp"]["bars"]:
        save(
            "source_failure.json",
            {
                "status": "DATA_NOT_READY_CANONICAL_MISMATCH",
                "differences": differences,
                "performance_runs": 0,
            },
        )
        raise DataIntegrityError("canonical futures price mismatch; performance blocked")
    manifest["quality"] = {
        "blocks": count,
        "closed_open_blocks": closed_open,
        "closed_close_blocks": closed_close,
        "source_comparison": differences,
        "spot_reference": (
            "legacy 1h DB aggregation excluded from execution; fixed daily targets retained"
        ),
        "missing_rows": sum(e["missing_raw_rows"] for e in entries),
        "closed_observed_rows": sum(e["closed_observed_rows"] for e in entries),
        "funding_exact_counts": legacy["funding_exact_counts"],
        "funding_counts": legacy["funding_counts"],
        "actual_queue_ack_mark_minute": "MISSING_USER_APPROVED_EXPLANATORY_ONLY",
    }
    del raw_fund
    manifest["input_id"] = data.payload_hash(manifest)
    save("aligned_manifest.json", manifest)
    print(
        json.dumps(
            {
                "prepared": True,
                "input_id": manifest["input_id"],
                "quality": manifest["quality"],
                "new_performance_runs": 0,
            }
        ),
        flush=True,
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name: str, value: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("x", encoding="utf8") as target:
        target.write(json.dumps(value, default=str, ensure_ascii=False, indent=2) + "\n")


def scaled(text: str) -> int:
    value = Decimal(text)
    if not value.is_finite():
        raise DataIntegrityError("nonfinite source decimal")
    expanded = value * SCALE
    integer = int(expanded)
    if expanded != integer or abs(integer) >= 2**63:
        raise DataIntegrityError("source precision/range exceeds exact fixed-point cache")
    return integer


def stamp(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, UTC).isoformat()


def read_source(path: Path, lo_ms: int, hi_ms: int) -> tuple[Columns, dict[str, Any]]:
    if not lo_ms < hi_ms <= END or path.stem[-7:] > "2026-08":
        raise DataIntegrityError("preserved period forbidden")
    columns: Columns = (array("q"), array("q"), array("q"), array("q"), array("q"))
    expected, zeros = lo_ms, 0
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1 or not members[0].endswith(".csv"):
            raise DataIntegrityError("unexpected archive members")
        with archive.open(members[0]) as source:
            for row in csv.reader(io.TextIOWrapper(source, encoding="ascii")):
                if row and row[0] == "open_time":
                    continue
                if len(row) != 12 or not row[0].isdigit():
                    raise DataIntegrityError("unexpected source schema")
                raw = int(row[0])
                at = raw // 1000 if raw > 10**14 else raw
                if at >= END:
                    raise DataIntegrityError("source timestamp outside frozen cutoff")
                if not lo_ms <= at < hi_ms:
                    continue
                if at != expected:
                    raise DataIntegrityError(f"source gap/duplicate {path.name}:{stamp(expected)}")
                values = tuple(scaled(row[i]) for i in range(1, 6))
                o, h, low, close, volume = values
                if not 0 < low <= min(o, close) <= max(o, close) <= h or volume < 0:
                    raise DataIntegrityError("invalid exact source OHLCV")
                for column, value in zip(columns, values, strict=True):
                    column.append(value)
                zeros += volume == 0
                expected += pure.MINUTE
    if expected != hi_ms:
        raise DataIntegrityError(f"source missing tail {path.name}:{stamp(expected)}")
    return columns, {
        "rows": len(columns[0]),
        "first_ms": lo_ms,
        "end_ms": hi_ms,
        "zero_volume": zeros,
        "raw_sha256": digest(path),
    }


def write_cache(name: str, columns: Columns) -> str:
    path = OUT / name
    with path.open("xb") as output:
        for column in columns:
            column.tofile(output)
    return digest(path)


def cached(metadata: dict[str, Any]) -> Columns:
    path = OUT / metadata["cache"]
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != metadata["cache_sha256"]:
        raise DataIntegrityError("audited cache changed")
    count = int(metadata["rows"])
    if len(payload) != count * 5 * 8:
        raise DataIntegrityError("cache length/layout")
    all_values = array("q")
    all_values.frombytes(payload)
    return (
        all_values[:count],
        all_values[count : 2 * count],
        all_values[2 * count : 3 * count],
        all_values[3 * count : 4 * count],
        all_values[4 * count : 5 * count],
    )


def inspect_source(path: Path, lo_ms: int, hi_ms: int) -> dict[str, Any]:
    """Audit actual rows without constructing candles or evaluating any account."""
    if not lo_ms < hi_ms <= END or path.stem[-7:] > "2026-08":
        raise DataIntegrityError("preserved period forbidden")
    expected = lo_ms
    rows = zeros = 0
    gaps: list[dict[str, int]] = []
    duplicates: list[int] = []
    first: int | None = None
    last: int | None = None
    original_digest = digest(path)
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1 or not members[0].endswith(".csv"):
            raise DataIntegrityError("unexpected archive members")
        with archive.open(members[0]) as source:
            for row in csv.reader(io.TextIOWrapper(source, encoding="ascii")):
                if row and row[0] == "open_time":
                    continue
                if len(row) != 12 or not row[0].isdigit() or not row[8].isdigit():
                    raise DataIntegrityError("unexpected source schema")
                raw = int(row[0])
                if raw > 10**14 and raw % 1000:
                    raise DataIntegrityError("nonmillisecond microsecond timestamp")
                at = raw // 1000 if raw > 10**14 else raw
                if at >= END or at % pure.MINUTE:
                    raise DataIntegrityError("cutoff or nonminute source timestamp")
                if not lo_ms <= at < hi_ms:
                    continue
                if at > expected:
                    gaps.append(
                        {
                            "start_ms": expected,
                            "end_ms": at,
                            "missing_minutes": (at - expected) // pure.MINUTE,
                        }
                    )
                elif at < expected:
                    duplicates.append(at)
                o, h, low, close, volume = (scaled(row[i]) for i in range(1, 6))
                if not 0 < low <= min(o, close) <= max(o, close) <= h or volume < 0:
                    raise DataIntegrityError("invalid exact source OHLCV")
                if volume == 0 and int(row[8]) != 0:
                    raise DataIntegrityError("zero volume with positive trade count")
                rows += 1
                zeros += volume == 0
                first = at if first is None else first
                last = at
                expected = max(expected, at + pure.MINUTE)
    if expected < hi_ms:
        gaps.append(
            {
                "start_ms": expected,
                "end_ms": hi_ms,
                "missing_minutes": (hi_ms - expected) // pure.MINUTE,
            }
        )
    if digest(path) != original_digest:
        raise DataIntegrityError("source changed during audit")
    return {
        "rows": rows,
        "first_ms": first,
        "last_ms": last,
        "expected_end_ms": hi_ms,
        "gaps": gaps,
        "duplicate_or_out_of_order": duplicates,
        "zero_volume": zeros,
        "raw_sha256": original_digest,
        "prices_precision_valid": True,
    }


def inventory() -> None:
    """Complete source quality inventory; never treat a explained gap as a fill."""
    if (OUT / "source_inventory.json").exists() or (OUT / "run_started.json").exists():
        raise DataIntegrityError("inventory/run already exists; no overwrite")
    entries: list[dict[str, Any]] = []
    for month in ["2020-05", *previous.month_names()]:
        year, number = map(int, month.split("-"))
        lo = int(datetime(year, number, 1, tzinfo=UTC).timestamp() * 1000)
        ny, nm = (year + 1, 1) if number == 12 else (year, number + 1)
        hi = int(datetime(ny, nm, 1, tzinfo=UTC).timestamp() * 1000)
        if month == "2020-05":
            lo = START - pure.H4
        for kind in ("spot", "um"):
            for symbol in pure.SYMBOLS:
                path = (
                    ROOT
                    / "data/external/binance_vision"
                    / kind
                    / symbol
                    / "1m"
                    / f"{symbol}-1m-{month}.zip"
                )
                info = inspect_source(path, lo, hi)
                entries.append(
                    {
                        "month": month,
                        "kind": kind,
                        "symbol": symbol,
                        "raw_path": path.relative_to(ROOT).as_posix(),
                        **info,
                    }
                )
        print(json.dumps({"source_inventory_month": month, "new_performance_runs": 0}), flush=True)
    summary = {}
    for kind in ("spot", "um"):
        for symbol in pure.SYMBOLS:
            group = [e for e in entries if e["kind"] == kind and e["symbol"] == symbol]
            summary[f"{kind}:{symbol}"] = {
                "rows": sum(e["rows"] for e in group),
                "gap_segments": sum(len(e["gaps"]) for e in group),
                "missing_minutes": sum(g["missing_minutes"] for e in group for g in e["gaps"]),
                "duplicates": sum(len(e["duplicate_or_out_of_order"]) for e in group),
                "zero_volume": sum(e["zero_volume"] for e in group),
            }
    result = {
        "cutoff_ms": END,
        "start_ms": START,
        "entries": entries,
        "summary": summary,
        "status": "DATA_NOT_READY_AVAILABILITY_ADAPTER_PENDING",
        "performance_runs": 0,
        "gap_policy": "NO_INTERPOLATION_NO_FILLED_CANDLES",
        "preserved_partial_audit": sorted(p.name for p in OUT.glob("*.bin")),
    }
    result["source_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True).encode()
    ).hexdigest()
    save("source_inventory.json", result)
    print(
        json.dumps(
            {
                "source_inventory_complete": True,
                "summary": summary,
                "source_sha256": result["source_sha256"],
                "new_performance_runs": 0,
            }
        ),
        flush=True,
    )


def audit() -> None:
    if (OUT / "audit.json").exists() or (OUT / "run_started.json").exists():
        raise DataIntegrityError("audit already exists; no overwrite")
    if array("q").itemsize != 8:
        raise DataIntegrityError("unsupported fixed-point platform")
    frames, legacy = prior.load()
    if legacy["source_sha256"] != prior.EXPECTED_INPUT:
        raise DataIntegrityError("frozen legacy feature source changed")
    raw_fund = json.loads((previous.OUT / "funding_rest.json").read_text(encoding="utf8"))
    exact = {
        s: {
            int(x["fundingTime"]): bool(x.get("markPrice") and Decimal(x["markPrice"]) > 0)
            for x in raw_fund[s]
        }
        for s in pure.SYMBOLS
    }
    features = []
    for frame in frames["RSI"]:
        features.append(
            {
                "at_ms": frame.at_ms,
                "targets": frame.targets,
                "target_as_of_ms": frame.target_as_of_ms,
                "signals": sorted(frame.signals),
                "signal_as_of_ms": frame.signal_as_of_ms,
                "funding": {
                    s: [{**asdict(f), "exact_mark": exact[s][f.at_ms]} for f in frame.funding[s]]
                    for s in pure.SYMBOLS
                },
            }
        )
    save("features.json", features)
    entries: list[dict[str, Any]] = []
    months = previous.month_names()
    for month in ["2020-05", *months]:
        year, number = map(int, month.split("-"))
        lo = int(datetime(year, number, 1, tzinfo=UTC).timestamp() * 1000)
        ny, nm = (year + 1, 1) if number == 12 else (year, number + 1)
        hi = int(datetime(ny, nm, 1, tzinfo=UTC).timestamp() * 1000)
        if month == "2020-05":
            lo = START - pure.H4
        for kind in ("spot", "um"):
            for symbol in pure.SYMBOLS:
                path = (
                    ROOT
                    / "data/external/binance_vision"
                    / kind
                    / symbol
                    / "1m"
                    / f"{symbol}-1m-{month}.zip"
                )
                columns, info = read_source(path, lo, hi)
                name = f"{kind}_{symbol}_{month}.bin"
                entries.append(
                    {
                        "month": month,
                        "kind": kind,
                        "symbol": symbol,
                        "raw_path": path.relative_to(ROOT).as_posix(),
                        "cache": name,
                        "cache_sha256": write_cache(name, columns),
                        **info,
                    }
                )
        print(
            json.dumps({"audit_month": month, "new_performance_runs": 0}, ensure_ascii=False),
            flush=True,
        )
    result = {
        "cutoff_ms": END,
        "start_ms": START,
        "scale": SCALE,
        "byteorder": sys.byteorder,
        "entries": entries,
        "features_sha256": digest(OUT / "features.json"),
        "legacy": legacy,
        "historical_filters": "ASSUMED_NOT_VERIFIED",
        "actual_queue_ack_mark_minute": "MISSING",
        "G5_G6_G7": "NOT_VERIFIED_USER_APPROVED_EXPLANATORY_ONLY",
    }
    result["source_sha256"] = hashlib.sha256(
        json.dumps(result, default=str, sort_keys=True).encode()
    ).hexdigest()
    save("audit.json", result)
    print(
        json.dumps(
            {
                "audit_complete": True,
                "source_sha256": result["source_sha256"],
                "minute_rows": sum(int(x["rows"]) for x in entries),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def epoch_year(year: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)


def metrics(run: dict[str, Any]) -> dict[str, Any]:
    trace = run["trace"]
    days = [pure.INITIAL, *(Decimal(x["equity"]) for x in trace if x["at_ms"] % pure.DAY == 0)]
    days[-1] -= Decimal(run["terminal_cost_reserve"])
    changes = [float(b / a - 1) for a, b in pairwise(days) if a > 0]
    sd = statistics.stdev(changes) if len(changes) > 1 else 0
    monthly: dict[str, dict[str, Any]] = {}
    annual: dict[str, dict[str, Any]] = {}
    periods: dict[str, dict[str, Any]] = {}
    opening = yearly_open = pure.INITIAL
    yearly_coin = {s: Decimal(0) for s in pure.SYMBOLS}
    annual_coins = {}
    for i, point in enumerate(trace):
        month = stamp(point["at_ms"] - 1)[:7]
        year = month[:4]
        ending = Decimal(point["equity"])
        coin = {s: Decimal(v) for s, v in point["coin_net"].items()}
        if i == len(trace) - 1:
            ending -= Decimal(run["terminal_cost_reserve"])
            coin = {s: coin[s] - Decimal(run["coin_reserve"][s]) for s in pure.SYMBOLS}
        following = stamp(trace[i + 1]["at_ms"] - 1)[:7] if i + 1 < len(trace) else None
        if following != month:
            monthly[month] = {
                "pnl": ending - opening,
                "return": float(ending / opening - 1) if opening > 0 else None,
            }
            opening = ending
        if following is None or following[:4] != year:
            annual[year] = {
                "pnl": ending - yearly_open,
                "return": float(ending / yearly_open - 1) if yearly_open > 0 else None,
            }
            annual_coins[year] = {s: coin[s] - yearly_coin[s] for s in pure.SYMBOLS}
            yearly_open, yearly_coin = ending, coin
    for label, lo, hi in (
        ("2020-2023", START, epoch_year(2024)),
        ("2024-2025", epoch_year(2024), epoch_year(2026)),
        ("2026-01-08", epoch_year(2026), END),
    ):
        before = [x for x in trace if x["at_ms"] <= lo]
        part = [x for x in trace if lo < x["at_ms"] <= hi]
        begin = Decimal(before[-1]["equity"]) if before else pure.INITIAL
        ending = Decimal(part[-1]["equity"])
        if hi == END:
            ending -= Decimal(run["terminal_cost_reserve"])
        periods[label] = {
            "pnl": ending - begin,
            "return": float(ending / begin - 1),
            "independent_oos": False,
        }
    summary = {
        k: v
        for k, v in run.items()
        if k not in ("trace", "trades", "fills", "returns", "funding_events")
    }
    final = pure.INITIAL + Decimal(run["net"])
    summary.update(
        cagr=float(final / pure.INITIAL) ** (365.25 * pure.DAY / (END - START)) - 1
        if final > 0
        else None,
        sharpe=statistics.fmean(changes) / sd * math.sqrt(365.25) if sd > 0 else None,
        worst_month=min(monthly, key=lambda k: monthly[k]["return"]),
        monthly=monthly,
        annual=annual,
        annual_coin_pnl=annual_coins,
        periods=periods,
        short_roundtrips=len(run["trades"]),
        spot_fill_pieces=sum(f["leg"] in ("BUY_SPOT", "SELL_SPOT") for f in run["fills"]),
        all_fill_pieces=len(run["fills"]),
        turnover_initial=float(Decimal(run["turnover"]) / pure.INITIAL),
        closed_short_net=sum((Decimal(t["net"]) for t in run["trades"]), Decimal(0)),
        short_rebound_losses=sum(
            (
                -min(Decimal(0), Decimal(r["short_close_price_pnl_after_request"]))
                for r in run["returns"]
            ),
            Decimal(0),
        ),
        return_requests=len(run["returns"]),
        return_first_buys=sum(r["first_ms"] is not None for r in run["returns"]),
        return_completed=sum(r["full_ms"] is not None for r in run["returns"]),
        mean_first_buy_delay_min=statistics.fmean(
            (r["first_ms"] - r["requested_ms"]) / pure.MINUTE
            for r in run["returns"]
            if r["first_ms"] is not None
        )
        if any(r["first_ms"] is not None for r in run["returns"])
        else None,
        minimum_spot_cash=min(Decimal(x["spot_cash"]) for x in trace),
        minimum_futures_cash=min(Decimal(x["futures_cash"]) for x in trace),
        halted_at_ms=next((x["at_ms"] for x in trace if x["halted"]), None),
        stale_valuation_checkpoints=sum(not all(x["spot_close_observed"]) for x in trace),
        liquidation_tier="NOT_MODELED_NOT_CERTIFIED",
        independent_oos=0,
        adoption="UNVERIFIED",
    )
    return summary


def reconcile_ledger(run: dict[str, Any]) -> dict[str, Any]:
    """Recompute saved fills/events independently; no strategy rerun."""
    ratio = Decimal(run["case"]["spot_ratio"])
    cash_spot, cash_future = pure.INITIAL * ratio, pure.INITIAL * (1 - ratio)
    qty = {s: Decimal(0) for s in pure.SYMBOLS}
    positions: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    spot_fee = future_fee = spot_slip = future_slip = Decimal(0)
    for f in run["fills"]:
        s, q, price, raw, fee = (
            f["symbol"],
            Decimal(f["qty"]),
            Decimal(f["price"]),
            Decimal(f["raw_price"]),
            Decimal(f["fee"]),
        )
        if f["leg"] in ("BUY_SPOT", "SELL_SPOT"):
            sign = 1 if f["leg"] == "BUY_SPOT" else -1
            qty[s] += sign * q
            cash_spot -= sign * q * price + fee
            spot_fee += fee
            spot_slip += q * abs(price - raw)
        elif f["leg"] == "OPEN_SHORT":
            if s in positions:
                raise DataIntegrityError("saved short overlap")
            positions[s] = q, price, raw
            cash_future -= fee
            future_fee += fee
            future_slip += q * (raw - price)
        else:
            old, entry, raw_entry = positions[s]
            if q > old:
                raise DataIntegrityError("saved close exceeds position")
            cash_future += q * (entry - price) - fee
            future_fee += fee
            future_slip += q * (price - raw)
            if q == old:
                del positions[s]
            else:
                positions[s] = old - q, entry, raw_entry
    funding = sum((Decimal(e["amount"]) for e in run["funding_events"]), Decimal(0))
    for e in run["funding_events"]:
        expected = Decimal(e["qty"]) * Decimal(e["mark"]) * Decimal(e["rate"])
        if e["stop_order_unknown"] and e["at_ms"] % pure.MINUTE:
            expected = min(Decimal(0), expected)
        if Decimal(e["amount"]) != expected:
            raise DataIntegrityError("saved funding arithmetic")
    cash_future += funding
    spot_prices = {
        s: Decimal(v) for s, v in zip(pure.SYMBOLS, run["terminal_spot_prices"], strict=True)
    }
    perp_prices = {
        s: Decimal(v) for s, v in zip(pure.SYMBOLS, run["terminal_perp_prices"], strict=True)
    }
    equity = (
        cash_spot
        + cash_future
        + sum((qty[s] * spot_prices[s] for s in pure.SYMBOLS), Decimal(0))
        + sum((q * (entry - perp_prices[s]) for s, (q, entry, _) in positions.items()), Decimal(0))
    )
    values = {
        "terminal_spot_cash": cash_spot,
        "terminal_futures_cash": cash_future,
        "terminal_equity": equity,
        "spot_fee": spot_fee,
        "futures_fee": future_fee,
        "spot_slip": spot_slip,
        "futures_slip": future_slip,
        "funding": funding,
        "net": equity - pure.INITIAL - Decimal(run["terminal_cost_reserve"]),
    }
    errors = {
        k: str(v - Decimal(run[k]))
        for k, v in values.items()
        if abs(v - Decimal(run[k])) > Decimal("1e-10")
    }
    if errors or tuple(qty[s] for s in pure.SYMBOLS) != tuple(
        Decimal(q) for q in run["terminal_spot_qty"]
    ):
        raise DataIntegrityError(f"independent saved ledger mismatch: {errors}")
    if abs(
        sum((Decimal(x) for x in run["coin_net_before_reserve"].values()), Decimal(0))
        - (equity - pure.INITIAL)
    ) > Decimal("1e-10"):
        raise DataIntegrityError("coin contribution mismatch")
    return {
        "saved_fill_reconciliation": "MATCH",
        "max_error": max(abs(v - Decimal(run[k])) for k, v in values.items()),
        "strategy_replays": 0,
    }


def pilot() -> None:
    if any((OUT / f).exists() for f in ("run_started.json", "result.json")):
        raise DataIntegrityError("one-shot run exists; no rerun")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True
    )
    if dirty.strip():
        raise DataIntegrityError("registered code/docs/tests must be committed before performance")
    manifest = json.loads((OUT / "aligned_manifest.json").read_text(encoding="utf8"))
    if manifest["input_id"] != data.payload_hash(
        {k: v for k, v in manifest.items() if k != "input_id"}
    ):
        raise DataIntegrityError("prepared manifest changed")
    if digest(OUT / "features.json") != manifest["features_sha256"]:
        raise DataIntegrityError("frozen features changed")
    for e in manifest["entries"]:
        if digest(ROOT / e["raw_path"]) != e["raw_sha256"]:
            raise DataIntegrityError("frozen source changed")
    features = json.loads((OUT / "features.json").read_text(encoding="utf8"))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    start = {
        "code_commit": revision,
        "code_sha256": code_hash(),
        "input_id": manifest["input_id"],
        "started_utc": datetime.now(UTC).isoformat(),
        "cases": 36,
        "approved_retrospective_only": True,
    }
    save("run_started.json", start)
    runs = pure.simulate(
        data.blocks(OUT / "aligned", manifest, features, progress=True), end_ms=END
    )
    summaries = {}
    for key, run in runs.items():
        name = "ledger_" + key.replace(":", "_") + ".json"
        save(name, run)
        saved = json.loads((OUT / name).read_text(encoding="utf8"))
        reconciliation = reconcile_ledger(saved)
        summaries[key] = {
            **metrics(saved),
            "ledger": name,
            "ledger_sha256": digest(OUT / name),
            "independent_accounting": reconciliation,
        }
    comparisons: list[dict[str, Any]] = []
    for ratio in pure.RATIOS:
        for scenario in pure.SCENARIOS:
            base_run, slow, fast = (
                summaries[f"{ratio}:{m}:{scenario.name}"]
                for m in ("BASE_WALLET", "PRIORITY_4H", "PRIORITY_1M")
            )
            diff_month = [
                float(
                    Decimal(fast["monthly"][month]["pnl"]) - Decimal(slow["monthly"][month]["pnl"])
                )
                for month in fast["monthly"]
            ]
            comparisons.append(
                {
                    "spot_ratio": ratio,
                    "scenario": scenario.name,
                    "4h_minus_base": Decimal(slow["net"]) - Decimal(base_run["net"]),
                    "1m_minus_base": Decimal(fast["net"]) - Decimal(base_run["net"]),
                    "1m_minus_4h": Decimal(fast["net"]) - Decimal(slow["net"]),
                    "1m_mdd_minus_4h": Decimal(fast["mdd"]) - Decimal(slow["mdd"]),
                    "monthly_difference_sd_usdt": statistics.stdev(diff_month),
                    "independent_oos": 0,
                }
            )
    verdicts = {}
    for ratio in pure.RATIOS:
        rows = [r for r in comparisons if r["spot_ratio"] == ratio]
        verdicts[str(ratio)] = (
            "후향모형에서 유리"
            if all(r["1m_minus_4h"] > 0 and r["1m_mdd_minus_4h"] <= 0 for r in rows)
            else "후향모형에서 불리"
            if all(r["1m_minus_4h"] <= 0 for r in rows)
            else "혼재/판단불가"
        )
    result = {
        **start,
        "completed_utc": datetime.now(UTC).isoformat(),
        "performance_runs": 1,
        "status": "EXPLANATORY_RETROSPECTIVE_COMPLETED_ADOPTION_UNVERIFIED",
        "quality": manifest["quality"],
        "summaries": summaries,
        "comparisons": comparisons,
        "verdicts": verdicts,
        "pass": False,
        "independent_oos": 0,
        "paper_live_approved": False,
    }
    save("result.json", result)
    print(
        json.dumps(
            {
                "completed": True,
                "result_sha256": digest(OUT / "result.json"),
                "cases": len(summaries),
                "verdicts": verdicts,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inventory", "audit", "prepare", "pilot"))
    args = parser.parse_args()
    if args.mode == "inventory":
        inventory()
    elif args.mode == "audit":
        audit()
    elif args.mode == "prepare":
        prepare()
    else:
        pilot()


if __name__ == "__main__":
    main()
