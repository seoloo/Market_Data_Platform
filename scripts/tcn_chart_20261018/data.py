"""Bounded archive adapter: protected months are never opened."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/tcn_chart_20261018"
VISION = ROOT / "data/external/binance_vision"
START = 1590969600000
END = 1788220800000
MINUTE = 60_000
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def months() -> list[str]:
    return [
        f"{y:04d}-{m:02d}"
        for y in range(2020, 2027)
        for m in range(1, 13)
        if "2020-06" <= f"{y:04d}-{m:02d}" <= "2026-08"
    ]


def save(name: str, value: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )


def archive(path: Path, cols: tuple[int, ...]) -> Any:
    import numpy as np

    if path.stem[-7:] not in months():
        raise ValueError("protected archive month")
    with zipfile.ZipFile(path) as zf:
        raw = zf.read(zf.namelist()[0])  # CRC checked by ZipFile
    first = raw.split(b"\n", 1)[0].split(b",", 1)[0]
    a = np.loadtxt(
        io.BytesIO(raw),
        delimiter=",",
        usecols=cols,
        skiprows=0 if first.isdigit() else 1,
        dtype=np.float64,
    )
    if not np.isfinite(a).all() or np.any(a[:, 0] < START) or np.any(a[:, 0] >= END):
        raise ValueError("bad/out-of-bound archive data")
    return a


def prepare() -> dict[str, Any]:
    import numpy as np

    if (OUT / "data_manifest.json").exists():
        return json.loads((OUT / "data_manifest.json").read_text(encoding="utf-8"))
    old = ROOT / "reports/short_validation_laya_20261013"
    raw_f = json.loads((old / "funding_rest.json").read_text(encoding="utf-8"))
    supplement = json.loads((old / "mark_hour_supplement.json").read_text(encoding="utf-8"))
    manifest: dict[str, Any] = {"sources": {}, "assets": {}, "cutoff_exclusive_ms": END}
    for f in ("funding_rest.json", "mark_hour_supplement.json"):
        manifest["sources"][str((old / f).relative_to(ROOT))] = hashlib.sha256(
            (old / f).read_bytes()
        ).hexdigest()
    for symbol in SYMBOLS:
        parts, mark_parts = [], []
        for kind, tf, cols, target in (
            ("um", "1m", (0, 1, 2, 3, 4, 5), parts),
            ("um_mark", "1h", (0, 1, 2, 3, 4), mark_parts),
        ):
            for month in months():
                p = VISION / kind / symbol / tf / f"{symbol}-{tf}-{month}.zip"
                manifest["sources"][str(p.relative_to(ROOT))] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                target.append(archive(p, cols))
        a, marks = np.concatenate(parts), np.concatenate(mark_parts)
        valid = (
            (a[:, 1:5] > 0).all(axis=1)
            & (a[:, 2] >= np.maximum(a[:, 1], a[:, 4]))
            & (a[:, 3] <= np.minimum(a[:, 1], a[:, 4]))
            & (a[:, 5] >= 0)
        )
        gaps = int(np.count_nonzero(np.diff(a[:, 0]) != MINUTE))
        if gaps or not valid.all() or len(a) != (END - START) // MINUTE:
            raise ValueError(f"{symbol}: invalid minute coverage, no imputation")
        b = a.reshape(-1, 240, 6)
        h4 = np.column_stack(
            (
                b[:, 0, 0],
                b[:, 0, 1],
                b[:, :, 2].max(axis=1),
                b[:, :, 3].min(axis=1),
                b[:, -1, 4],
                b[:, :, 5].sum(axis=1),
            )
        )
        md = {int(row[0]): row for row in marks}
        funds, exact = [], 0
        for r in raw_f[symbol]:
            t, rate = int(r["fundingTime"]), float(r["fundingRate"])
            if not START <= t < END:
                raise ValueError("funding file crosses protected boundary")
            price = float(r.get("markPrice") or 0)
            if price > 0:
                low = high = price
                exact += 1
            else:
                hour = t // 3_600_000 * 3_600_000
                if hour in md:
                    mark = md[hour]
                    price, high, low = mark[1], mark[2], mark[3]
                else:
                    v = supplement[f"{symbol}:{hour}"]
                    price, low, high = float(v[1]), float(v[3]), float(v[2])
            funds.append([t, rate, price, low, high])
        f = np.asarray(funds)
        if np.any(np.diff(f[:, 0]) <= 0) or np.any(np.diff(f[:, 0]) > 9 * 3_600_000):
            raise ValueError("funding missing or unordered")
        np.savez(OUT / f"{symbol}.npz", minutes=a, h4=h4, funding=f)
        manifest["assets"][symbol] = {
            "minutes": len(a),
            "gaps": gaps,
            "h4": len(h4),
            "funding": len(f),
            "exact_mark": exact,
            "proxy_mark": len(f) - exact,
            "cache_sha256": hashlib.sha256((OUT / f"{symbol}.npz").read_bytes()).hexdigest(),
        }
        print(json.dumps({"prepared": symbol, **manifest["assets"][symbol]}), flush=True)
    save("data_manifest.json", manifest)
    return manifest


def load(symbol: str) -> dict[str, Any]:
    import numpy as np

    if symbol not in SYMBOLS:
        raise ValueError("unregistered asset")
    meta = json.loads((OUT / "data_manifest.json").read_text(encoding="utf-8"))
    path = OUT / f"{symbol}.npz"
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta["assets"][symbol]["cache_sha256"]:
        raise ValueError("cache digest mismatch")
    with np.load(path, allow_pickle=False) as d:
        return {k: d[k] for k in d.files}
