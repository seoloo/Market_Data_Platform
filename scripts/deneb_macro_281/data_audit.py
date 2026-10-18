"""Public ALFRED first-release audit; never substitute today's revised history."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data/research/deneb_macro_281"
OUT = REPO / "reports/deneb_macro_281"
CUTOFF = "2026-08-31"
SERIES = {
    "T10Y3M": ("daily", 7),
    "BAA10Y": ("daily", 7),
    "UNRATE": ("monthly", 62),
    "ICSA": ("weekly", 14),
    "INDPRO": ("monthly", 62),
    "PCEPILFE": ("monthly", 62),
    "FEDFUNDS": ("monthly", 62),
}


def vintage_dates(html: str, cutoff: str = CUTOFF) -> list[str]:
    """Read only the vintage-date select, excluding dates after the sealed cutoff."""
    match = re.search(
        r'<select\b[^>]*id="form_selected_vintage_dates"[^>]*>(.*?)</select>',
        html,
        flags=re.S,
    )
    if match is None:
        raise ValueError("ALFRED vintage selector missing; not a valid data response")
    return sorted(
        {
            d
            for d in re.findall(r'<option\b[^>]*value="(\d{4}-\d{2}-\d{2})"', match[1])
            if d <= cutoff
        }
    )


def observation_bounds(html: str) -> tuple[str, str]:
    """Clamp to the provider's stated observation range, not a fabricated history."""
    bounds = []
    for suffix in ("start", "end"):
        match = re.search(
            rf'<input\b[^>]*id="form_obs_{suffix}_date"[^>]*value="(\d{{4}}-\d{{2}}-\d{{2}})"',
            html,
        )
        if match is None:
            raise ValueError("ALFRED observation bounds missing")
        bounds.append(match[1])
    return max("1960-01-01", bounds[0]), min(CUTOFF, bounds[1])


def parse_initial(blob: bytes, series: str, cutoff: str = CUTOFF) -> list[dict[str, Any]]:
    """Strictly parse original releases with their public availability date."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError("Expected exactly one original-release CSV")
        content = archive.read(names[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    expected = {"period_start_date", series, "realtime_start_date"}
    if set(reader.fieldnames or []) != expected:
        raise ValueError(f"Unexpected initial-release columns for {series}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in reader:
        observed, released = row["period_start_date"], row["realtime_start_date"]
        if observed > cutoff or released > cutoff:
            continue
        obs, rel = date.fromisoformat(observed), date.fromisoformat(released)
        if observed in seen:
            raise ValueError("Duplicate observation in initial-release file")
        seen.add(observed)
        raw = row[series]
        if raw in ("", ".", "NA"):
            continue
        value = float(raw)
        if not (-1e12 < value < 1e12):
            raise ValueError("Non-finite/out-of-range macro value")
        rows.append(
            {
                "observed": observed,
                "released": released,
                "value": value,
                "lag_days": (rel - obs).days,
            }
        )
    return sorted(rows, key=lambda r: r["observed"])


def assess(rows: list[dict[str, Any]], max_lag_days: int) -> dict[str, Any]:
    """Reject archive backfills as timely history; negative lags are also invalid."""
    timely = [r for r in rows if 0 <= r["lag_days"] <= max_lag_days]
    late = [r for r in rows if r["lag_days"] > max_lag_days]
    lags = sorted(r["lag_days"] for r in rows)
    return {
        "original_rows": len(rows),
        "timely_rows": len(timely),
        "first_observation": rows[0]["observed"] if rows else None,
        "first_timely_observation": timely[0]["observed"] if timely else None,
        "first_timely_release": timely[0]["released"] if timely else None,
        "last_timely_release": timely[-1]["released"] if timely else None,
        "archive_backfill_rows": len(late),
        "negative_lag_rows": sum(r["lag_days"] < 0 for r in rows),
        "release_lag_median_days": statistics.median(lags) if lags else None,
        "release_lag_max_days": max(lags) if lags else None,
        "lag_limit_days": max_lag_days,
    }


def _request(url: str, body: bytes | None = None) -> bytes:
    request = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(request, timeout=20) as response:
        blob: bytes = response.read(32_000_001)
    if len(blob) > 32_000_000:
        raise ValueError("Response exceeds the fixed 32 MB budget")
    return blob


def fetch_series(series: str) -> dict[str, Any]:
    """Cache original responses outside Git. Never overwrite an existing raw response."""
    frequency, lag = SERIES[series]
    folder = DATA / series
    folder.mkdir(parents=True, exist_ok=True)
    page = folder / "vintages.html"
    url = "https://alfred.stlouisfed.org/series/downloaddata?seid=" + series
    if not page.exists():
        page.write_bytes(_request(url))
    html = page.read_text(encoding="utf-8")
    dates = vintage_dates(html)
    observed_start, observed_end = observation_bounds(html)
    if not dates:
        raise ValueError(f"No permitted vintage dates: {series}")
    all_rows: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    # Keep form fields ahead of the date array and stay below common form item limits.
    # This is a transport correction, not a change of observations or vintage cutoffs.
    for start in range(0, len(dates), 400):
        target = folder / f"initial_{start // 400:03d}.zip"
        form: dict[str, Any] = {
            "form[units]": "lin",
            "form[obs_start_date]": observed_start,
            "form[obs_end_date]": observed_end,
            "form[entered_vintage_dates]": "",
            "form[file_type]": "4",
            "form[file_format]": "csv",
            "form[download_data]": "",
            "form[selected_vintage_dates][]": dates[start : start + 400],
        }
        if not target.exists():
            blob = _request(url, urllib.parse.urlencode(form, doseq=True).encode())
            if not blob.startswith(b"PK"):
                failure = folder / f"initial_failure_{start // 400:03d}.html"
                if not failure.exists():
                    failure.write_bytes(blob)
                raise ValueError("ALFRED returned HTML instead of an original-release ZIP")
            parse_initial(blob, series)
            target.write_bytes(blob)
        blob = target.read_bytes()
        hashes[target.name] = hashlib.sha256(blob).hexdigest()
        for row in parse_initial(blob, series):
            old = all_rows.get(row["observed"])
            if old is not None and old != row:
                raise ValueError("Conflicting first-release values across source batches")
            all_rows[row["observed"]] = row
    rows = sorted(all_rows.values(), key=lambda r: r["observed"])
    return {
        "series": series,
        "frequency": frequency,
        "source_url": url,
        "format": "initial_release_only",
        "cutoff": CUTOFF,
        "requested_observation_range": [observed_start, observed_end],
        "first_vintage": dates[0],
        "last_vintage": dates[-1],
        "vintage_count": len(dates),
        "raw_sha256": hashes,
        "raw_path": folder.relative_to(REPO).as_posix(),
        **assess(rows, lag),
    }


def run(*, recovered: bool = False, completed: bool = False) -> dict[str, Any]:
    """Finish every public source check, recording source failure without fallback."""
    OUT.mkdir(parents=True, exist_ok=True)
    filename = (
        "data_audit_completed.json"
        if completed
        else ("data_audit_recovered.json" if recovered else "data_audit.json")
    )
    target = OUT / filename
    if target.exists():
        raise FileExistsError("Audit result exists; inspect it instead of silently rerunning")
    result: dict[str, Any] = {
        "research": 281,
        "stage": "G0_DATA_AUDIT",
        "as_of_ms": int(datetime.now(UTC).timestamp() * 1000),
        "cutoff": CUTOFF,
        "model_fits": 0,
        "candidate_pnl_calculations": 0,
        "series": {},
    }
    for series in SERIES:
        try:
            row = fetch_series(series)
        except (urllib.error.URLError, ValueError, OSError, zipfile.BadZipFile) as exc:
            row = {"series": series, "status": "UNAVAILABLE", "error": str(exc)}
        result["series"][series] = row
        print(json.dumps(row, ensure_ascii=False), flush=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    import sys

    run(recovered="--recovered" in sys.argv, completed="--completed" in sys.argv)
