"""As-of macro features; index growth always uses a single historical vintage."""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import statistics
import urllib.parse
import zipfile
from datetime import date
from typing import Any

from scripts.deneb_macro_281.data_audit import (
    CUTOFF,
    DATA,
    OUT,
    REPO,
    SERIES,
    _request,
    observation_bounds,
    parse_initial,
    vintage_dates,
)
from scripts.deneb_macro_281.power_audit import load_prefix

FEATURE_NAMES = (
    "curve_level",
    "curve_change_3m",
    "credit_level",
    "credit_change_3m",
    "unemployment_gap",
    "claims_ratio",
    "production_3m",
    "core_pce_3m_annual",
    "policy_level",
    "policy_change_3m",
    "rv20",
    "drawdown252",
    "trend_score",
)


def parse_snapshot_zip(blob: bytes, series: str) -> dict[str, dict[str, float]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError("Expected one vintage CSV")
        reader = csv.DictReader(io.StringIO(archive.read(names[0]).decode("utf-8-sig")))
        fields = reader.fieldnames or []
        if not fields or fields[0] != "observation_date":
            raise ValueError(f"Unexpected vintage CSV header: {fields[:3]}")
        dates = {
            c: date.fromisoformat(c.removeprefix(series + "_")).isoformat() for c in fields[1:]
        }
        for d in dates.values():
            date.fromisoformat(d)
            if d > CUTOFF:
                raise ValueError("Vintage beyond sealed cutoff")
        result: dict[str, dict[str, float]] = {d: {} for d in dates.values()}
        for row in reader:
            observed = date.fromisoformat(row[fields[0]]).isoformat()
            if observed > CUTOFF:
                continue
            for column, vintage in dates.items():
                raw = row[column]
                if raw not in ("", "."):
                    if observed > vintage:
                        raise ValueError("Observation is newer than its historical vintage")
                    value = float(raw)
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError("Invalid historical index")
                    result[vintage][observed] = value
        return result


def index_snapshots(series: str, as_of_dates: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    folder = DATA / series
    html = (folder / "vintages.html").read_text(encoding="utf-8")
    available = vintage_dates(html)
    selected = sorted(
        {
            available[bisect.bisect_right(available, d) - 1]
            for d in as_of_dates
            if bisect.bisect_right(available, d) > 0
        }
    )
    _, end = observation_bounds(html)
    result, hashes = {}, {}
    for i in range(0, len(selected), 40):
        target = folder / f"snapshots_{i // 40:03d}.zip"
        if not target.exists():
            form: dict[str, Any] = {
                "form[units]": "lin",
                "form[obs_start_date]": "2012-01-01",
                "form[obs_end_date]": end,
                "form[file_type]": "2",
                "form[file_format]": "csv",
                "form[download_data]": "",
                "form[selected_vintage_dates][]": selected[i : i + 40],
            }
            blob = _request(
                "https://alfred.stlouisfed.org/series/downloaddata?seid=" + series,
                urllib.parse.urlencode(form, doseq=True).encode(),
            )
            if not blob.startswith(b"PK"):
                raise ValueError(f"Historical index download returned non-ZIP: {series}")
            # Only persist a structurally valid response, never replace an earlier one.
            parse_snapshot_zip(blob, series)
            target.write_bytes(blob)
        blob = target.read_bytes()
        hashes[target.relative_to(REPO).as_posix()] = hashlib.sha256(blob).hexdigest()
        result.update(parse_snapshot_zip(blob, series))
    return result, hashes


def released_rows(series: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    lag = SERIES[series][1]
    for p in sorted((DATA / series).glob("initial_[0-9][0-9][0-9].zip")):
        for r in parse_initial(p.read_bytes(), series):
            if 0 <= r["lag_days"] <= lag:
                rows[r["observed"]] = r
    return sorted(rows.values(), key=lambda r: r["observed"])


def months_before(day: str, n: int) -> str:
    number = int(day[:4]) * 12 + int(day[5:7]) - 1 - n
    return f"{number // 12:04d}-{number % 12 + 1:02d}-01"


def rate_pair(rows: list[dict[str, Any]], as_of: str, frequency: str) -> list[float | None]:
    current = [r for r in rows if r["released"] <= as_of]
    stale_days = {"daily": 20, "monthly": 95}[frequency]
    if (
        not current
        or (date.fromisoformat(as_of) - date.fromisoformat(current[-1]["observed"])).days
        > stale_days
    ):
        return [None, None]
    last = current[-1]
    previous = [r for r in current if r["observed"][:7] <= months_before(last["observed"], 3)[:7]]
    return [last["value"], last["value"] - previous[-1]["value"] if previous else None]


def index_growth(snapshots: dict[str, Any], as_of: str, annual: bool) -> float | None:
    known = [v for v in snapshots if v <= as_of]
    if not known:
        return None
    history = snapshots[max(known)]
    if not history:
        return None
    last = max(history)
    prior = months_before(last, 3)
    if prior not in history or (date.fromisoformat(as_of) - date.fromisoformat(last)).days > 95:
        return None
    ratio = history[last] / history[prior]
    return float(ratio ** (4 if annual else 1) - 1)


def build() -> dict[str, Any]:
    target = DATA / "panel.json"
    if target.exists():
        raise FileExistsError("Panel already frozen; do not overwrite")
    ndx, h = load_prefix(REPO / "data/research/tradfi/fred/NASDAQ100.csv", CUTOFF)
    days = sorted(ndx)
    ends = list({d[:7]: i for i, d in enumerate(days)}.values())
    ends = [i for i in ends if days[i] >= "2015-01-01" and i + 1 < len(days)]
    dates = [days[i - 2] for i in ends]
    histories, hashes = {}, {"NASDAQ100_filtered_prefix": h}
    for series in ("INDPRO", "PCEPILFE"):
        histories[series], extra = index_snapshots(series, dates)
        hashes.update(extra)
    initial = {s: released_rows(s) for s in SERIES if s not in histories}
    rows = []
    for i in ends:
        as_of, decision = days[i - 2], days[i]
        features: list[float | None] = []
        for series in ("T10Y3M", "BAA10Y"):
            features.extend(rate_pair(initial[series], as_of, "daily"))
        unemployment = [r for r in initial["UNRATE"] if r["released"] <= as_of]
        if (
            len(unemployment) >= 14
            and (date.fromisoformat(as_of) - date.fromisoformat(unemployment[-1]["observed"])).days
            <= 95
        ):
            values = [r["value"] for r in unemployment[-14:]]
            means = [statistics.fmean(values[j : j + 3]) for j in range(12)]
            features.append(means[-1] - min(means))
        else:
            features.append(None)
        claims = [r for r in initial["ICSA"] if r["released"] <= as_of]
        if (
            len(claims) >= 52
            and (date.fromisoformat(as_of) - date.fromisoformat(claims[-1]["observed"])).days <= 21
        ):
            features.append(
                statistics.fmean(r["value"] for r in claims[-4:])
                / statistics.fmean(r["value"] for r in claims[-52:])
                - 1
            )
        else:
            features.append(None)
        features.extend(
            index_growth(histories[s], as_of, s == "PCEPILFE") for s in ("INDPRO", "PCEPILFE")
        )
        features.extend(rate_pair(initial["FEDFUNDS"], as_of, "monthly"))
        j = i - 2
        returns = [ndx[days[k]] / ndx[days[k - 1]] - 1 for k in range(j - 19, j + 1)]
        features.extend(
            (
                statistics.pstdev(returns) * math.sqrt(252),
                ndx[days[j]] / max(ndx[d] for d in days[j - 251 : j + 1]) - 1,
                sum(1 if ndx[days[j]] > ndx[days[j - k]] else -1 for k in (20, 60, 120)) / 3,
            )
        )
        complete = i + 126 < len(days)
        label = (
            int(min(ndx[days[k]] for k in range(i + 1, i + 127)) <= ndx[decision] * 0.85)
            if complete
            else None
        )
        rows.append(
            {
                "decision": decision,
                "as_of": as_of,
                "execute": days[i + 1],
                "label_end": days[i + 126] if complete else None,
                "label": label,
                "features": features,
                "macro_missing": sum(x is None for x in features[:10]),
            }
        )
    result = {
        "feature_names": FEATURE_NAMES,
        "rows": rows,
        "source_sha256": hashes,
        "index_changes_use_one_vintage": True,
        "cutoff": CUTOFF,
    }
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "panel_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "rows": len(rows),
        "first_decision": rows[0]["decision"],
        "last_decision": rows[-1]["decision"],
        "source_sha256": hashes,
        "macro_missing_histogram": {
            str(n): sum(r["macro_missing"] == n for r in rows) for n in range(11)
        },
        "model_fits": 0,
        "candidate_pnl_calculations": 0,
    }
    (OUT / "panel_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    return result


if __name__ == "__main__":
    build()
