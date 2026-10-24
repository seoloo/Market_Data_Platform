"""Historical availability adapter. Missing raw prices remain None, never fills."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import zipfile
from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeAlias

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import wallet_grid_20261013 as p

SCALE = 100_000_000
START, END = 1590969600000, 1788220800000
# 글자로 둔다 — 파이썬 3.11 은 array[int] 를 실행 중에 만들지 못한다(CI, README C-1)
Columns: TypeAlias = "tuple[array[int], array[int], array[int], array[int], array[int]]"
CLOSED_TEXT = (
    ("2020-06-28T02:00", "2020-06-28T05:30"),
    ("2020-11-30T06:00", "2020-11-30T07:00"),
    ("2020-12-21T13:19", "2020-12-21T18:00"),
    ("2020-12-25T02:00", "2020-12-25T03:00"),
    ("2021-02-11T02:19", "2021-02-11T05:00"),
    ("2021-03-06T02:00", "2021-03-06T03:30"),
    ("2021-04-20T02:00", "2021-04-20T04:30"),
    ("2021-04-25T04:00", "2021-04-25T08:45"),
    ("2021-08-13T02:00", "2021-08-13T06:30"),
    ("2021-09-29T07:00", "2021-09-29T09:00"),
    ("2023-03-24T11:27", "2023-03-24T14:00"),
)


def epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


CLOSED = tuple((epoch(a), epoch(b)) for a, b in CLOSED_TEXT)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()


def month_bounds(month: str) -> tuple[int, int]:
    if not "2020-05" <= month <= "2026-08":
        raise DataIntegrityError("unregistered/protected month")
    y, m = map(int, month.split("-"))
    lo = epoch(f"{y:04}-{m:02}-01T00:00")
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    hi = epoch(f"{ny:04}-{nm:02}-01T00:00")
    return (START - p.H4 if month == "2020-05" else lo), hi


def fixed(text: str) -> int:
    value = Decimal(text)
    if not value.is_finite():
        raise DataIntegrityError("nonfinite source")
    scaled = value * SCALE
    integer = int(scaled)
    if scaled != integer or abs(integer) >= 2**63:
        raise DataIntegrityError("source precision/range")
    return integer


def _write_or_verify(path: Path, payload: bytes) -> str:
    expected = hashlib.sha256(payload).hexdigest()
    if path.exists():
        if file_hash(path) != expected:
            raise DataIntegrityError("partial cache changed; no overwrite")
    else:
        with path.open("xb") as target:
            target.write(payload)
    return expected


def align_source(path: Path, lo_ms: int, hi_ms: int, *, spot: bool) -> tuple[Columns, array[int]]:
    if not START - p.H4 <= lo_ms < hi_ms <= END or path.stem[-7:] > "2026-08":
        raise DataIntegrityError("protected source boundary")
    count = (hi_ms - lo_ms) // p.MINUTE
    cols: Columns = tuple_arrays(count)
    present = array("b", [0]) * count
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise DataIntegrityError("source archive schema")
        with archive.open(names[0]) as source:
            for row in csv.reader(io.TextIOWrapper(source, encoding="ascii")):
                if row and row[0] == "open_time":
                    continue
                if len(row) != 12 or not row[0].isdigit():
                    raise DataIntegrityError("source row schema")
                raw = int(row[0])
                if raw > 10**14 and raw % 1000:
                    raise DataIntegrityError("source time precision")
                at = raw // 1000 if raw > 10**14 else raw
                if at >= END or at % p.MINUTE:
                    raise DataIntegrityError("protected/nonminute timestamp")
                if not lo_ms <= at < hi_ms:
                    continue
                i = (at - lo_ms) // p.MINUTE
                if present[i]:
                    raise DataIntegrityError("duplicate source row")
                values = tuple(fixed(row[j]) for j in range(1, 6))
                o, h, low, c, v = values
                if not 0 < low <= min(o, c) <= max(o, c) <= h or v < 0:
                    raise DataIntegrityError("source OHLCV")
                for column, value in zip(cols, values, strict=True):
                    column[i] = value
                present[i] = 1
    relevant = [(max(lo_ms, a), min(hi_ms, b)) for a, b in CLOSED if lo_ms < b and a < hi_ms]
    closed_indices: set[int] = set()
    if spot:
        for a, b in relevant:
            closed_indices.update(range((a - lo_ms) // p.MINUTE, (b - lo_ms) // p.MINUTE))
    for i, observed in enumerate(present):
        if not observed and i not in closed_indices:
            raise DataIntegrityError(f"unexplained source gap at {lo_ms + i * p.MINUTE}")
        if observed and i in closed_indices:
            present[i] = 2  # Real source row retained, explicitly NOT execution-available.
    return cols, present


def tuple_arrays(count: int = 0) -> Columns:
    return (
        array("q", [0]) * count,
        array("q", [0]) * count,
        array("q", [0]) * count,
        array("q", [0]) * count,
        array("q", [0]) * count,
    )


def cache_source(root: Path, out: Path, entry: dict[str, Any]) -> dict[str, Any]:
    kind, symbol, month = entry["kind"], entry["symbol"], entry["month"]
    if kind not in ("spot", "um") or symbol not in p.SYMBOLS:
        raise DataIntegrityError("source scope")
    lo, hi = month_bounds(month)
    path = root / "data/external/binance_vision" / kind / symbol / "1m" / f"{symbol}-1m-{month}.zip"
    if (
        path.relative_to(root).as_posix() != entry["raw_path"]
        or file_hash(path) != entry["raw_sha256"]
    ):
        raise DataIntegrityError("inventoried source changed")
    cols, state = align_source(path, lo, hi, spot=kind == "spot")
    if sum(x != 0 for x in state) != entry["rows"] or file_hash(path) != entry["raw_sha256"]:
        raise DataIntegrityError("source row count/version changed")
    out.mkdir(parents=True, exist_ok=True)
    name = f"{kind}_{symbol}_{month}"
    values_name, state_name = f"{name}.values.bin", f"{name}.state.bin"
    values_sha = _write_or_verify(out / values_name, b"".join(c.tobytes() for c in cols))
    state_sha = _write_or_verify(out / state_name, state.tobytes())
    return {
        **entry,
        "lo_ms": lo,
        "hi_ms": hi,
        "aligned_rows": len(state),
        "values": values_name,
        "values_sha256": values_sha,
        "state": state_name,
        "state_sha256": state_sha,
        "missing_raw_rows": state.count(0),
        "closed_observed_rows": state.count(2),
    }


@dataclass
class Series:
    lo_ms: int
    columns: Columns
    state: array[int]
    previous: p.Bar

    def __post_init__(self) -> None:
        last = -1
        self.last_observed = array("i")
        for i, state in enumerate(self.state):
            if state == 1:
                last = i
            self.last_observed.append(last)

    def raw(self, i: int) -> p.Bar | None:
        if self.state[i] == 0:
            return None
        o, h, low, c, v = (Decimal(column[i]) / SCALE for column in self.columns)
        return p.Bar(o, h, low, c, v, self.state[i] == 1)

    def bar(self, i: int) -> p.Bar:
        if i == -1:
            return self.previous
        raw = self.raw(i)
        if raw is not None and raw.observed:
            return raw
        j = self.last_observed[i - 1] if i > 0 else -1
        value = Decimal(self.columns[3][j]) / SCALE if j >= 0 else self.previous.c
        # A separate stale valuation, not an interpolated/executable candle.
        return p.Bar(value, value, value, value, Decimal(0), False)

    def aggregate(self, start: int, count: int = 240) -> p.Bar:
        first, end = self.bar(start), self.bar(start + count - 1)
        good = [i for i in range(start, start + count) if self.state[i] == 1]
        h = max([first.o, end.c, *(Decimal(self.columns[1][i]) / SCALE for i in good)])
        low = min([first.o, end.c, *(Decimal(self.columns[2][i]) / SCALE for i in good)])
        volume = Decimal(sum(self.columns[4][i] for i in good)) / SCALE
        return p.Bar(first.o, h, low, end.c, volume, first.observed)


def load_series(out: Path, metadata: dict[str, Any], previous: p.Bar) -> Series:
    payloads = []
    for key in ("values", "state"):
        payload = (out / metadata[key]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata[f"{key}_sha256"]:
            raise DataIntegrityError("aligned cache changed")
        payloads.append(payload)
    n = metadata["aligned_rows"]
    if len(payloads[0]) != n * 40 or len(payloads[1]) != n:
        raise DataIntegrityError("aligned cache layout")
    flat = array("q")
    flat.frombytes(payloads[0])
    cols: Columns = (
        flat[:n],
        flat[n : 2 * n],
        flat[2 * n : 3 * n],
        flat[3 * n : 4 * n],
        flat[4 * n : 5 * n],
    )
    state = array("b")
    state.frombytes(payloads[1])
    if any(x not in (0, 1, 2) for x in state):
        raise DataIntegrityError("availability state")
    return Series(metadata["lo_ms"], cols, state, previous)


@dataclass
class Provider:
    at_ms: int
    offset: int
    spot: tuple[Series, Series]
    perp: tuple[Series, Series]

    def get(self, index: int) -> p.Minute:
        i = self.offset + index
        return p.Minute(
            self.at_ms + index * p.MINUTE,
            (self.spot[0].bar(i), self.spot[1].bar(i)),
            (self.perp[0].bar(i), self.perp[1].bar(i)),
            (self.spot[0].bar(i - 1), self.spot[1].bar(i - 1)),
            (self.perp[0].bar(i - 1), self.perp[1].bar(i - 1)),
            self.at_ms + index * p.MINUTE,
        )


def blocks(
    out: Path, manifest: dict[str, Any], features: list[dict[str, Any]], *, progress: bool = False
) -> Iterator[p.Block]:
    if manifest["byteorder"] != sys.byteorder or manifest["end_ms"] != END:
        raise DataIntegrityError("cache platform/cutoff")
    index = {(e["month"], e["kind"], e["symbol"]): e for e in manifest["entries"]}
    seed = p.Bar(Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(0), False)
    previous = dict.fromkeys([(k, s) for k in ("spot", "um") for s in p.SYMBOLS], seed)
    cursor = 0
    for month in sorted({e["month"] for e in manifest["entries"]}):
        series = {
            (k, s): load_series(out, index[month, k, s], previous[k, s])
            for k in ("spot", "um")
            for s in p.SYMBOLS
        }
        spot, perp = (
            (series["spot", p.SYMBOLS[0]], series["spot", p.SYMBOLS[1]]),
            (series["um", p.SYMBOLS[0]], series["um", p.SYMBOLS[1]]),
        )
        lo = series["spot", p.SYMBOLS[0]].lo_ms
        count = len(spot[0].state)
        if any(s.lo_ms != lo or len(s.state) != count for s in series.values()):
            raise DataIntegrityError("series alignment")
        if month != "2020-05":
            if progress:
                print(json.dumps({"replay_month": month, "one_shot": True}), flush=True)
            for offset in range(0, count, p.H4 // p.MINUTE):
                at = lo + offset * p.MINUTE
                feature = features[cursor]
                if feature["at_ms"] != at or at >= END:
                    raise DataIntegrityError("feature/price alignment")
                cursor += 1
                weights = feature["targets"]
                targets = (
                    None
                    if weights is None
                    else (Decimal(weights[p.SYMBOLS[0]]), Decimal(weights[p.SYMBOLS[1]]))
                )
                groups = tuple(
                    tuple(
                        p.Funding(
                            int(x["at_ms"]), Decimal(x["rate"]), Decimal(x["mark"]), x["exact_mark"]
                        )
                        for x in feature["funding"][s]
                    )
                    for s in p.SYMBOLS
                )
                yield p.Block(
                    at,
                    (spot[0].aggregate(offset), spot[1].aggregate(offset)),
                    (perp[0].aggregate(offset), perp[1].aggregate(offset)),
                    (spot[0].bar(offset - 1), spot[1].bar(offset - 1)),
                    (perp[0].bar(offset - 1), perp[1].bar(offset - 1)),
                    targets,
                    feature["target_as_of_ms"],
                    (p.SYMBOLS[0] in feature["signals"], p.SYMBOLS[1] in feature["signals"]),
                    feature["signal_as_of_ms"],
                    (groups[0], groups[1]),
                    Provider(at, offset, spot, perp),
                    (spot[0].bar(offset + 239).observed, spot[1].bar(offset + 239).observed),
                )
        previous = {key: s.bar(count - 1) for key, s in series.items()}
    if cursor != len(features):
        raise DataIntegrityError("unused/missing feature rows")
