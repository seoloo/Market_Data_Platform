"""Bounded local data audit and approved one-shot wallet/grid pilot. No APIs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from array import array
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import wallet_grid_20261013 as pure
from scripts import run_rsi_stoch_pilot_20261012 as base
from scripts import run_short_long_return_20261013 as prior
from scripts import run_short_validation_20261013 as previous

ROOT = base.REPO
OUT = ROOT / "reports/wallet_grid_20261013"
DOC = "docs/research/LONG_WALLET_GRID_PILOT_20261013.md"
FILES = (
    DOC,
    "scripts/run_wallet_grid_20261013.py",
    "src/auto_trading/research/wallet_grid_20261013.py",
    "tests/research/test_wallet_grid_20261013.py",
)
START, END = base.START, base.END
SCALE = 100_000_000
Columns = tuple[array[int], array[int], array[int], array[int], array[int]]


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inventory", "audit", "pilot"))
    args = parser.parse_args()
    if args.mode == "inventory":
        inventory()
    elif args.mode == "audit":
        audit()
    else:
        raise DataIntegrityError("pilot implementation not frozen yet")


if __name__ == "__main__":
    main()
