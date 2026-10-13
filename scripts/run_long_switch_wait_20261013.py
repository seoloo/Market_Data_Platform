"""Bounded local-data audit and one-shot conditional 0/60-minute pilot."""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_trading.backtest.overlay import T2_ALONE, next_targets
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.money import floor_to_step
from auto_trading.research import long_switch_wait_20261013 as pure
from scripts import audit_h1_o_ag_20261012 as old

ROOT = old.REPO
OUT = ROOT / "reports/long_switch_wait_20261013"
PREVIOUS = ROOT / "reports/short_long_return_20261013"
AUDIT_SOURCE = ROOT / "reports/long_capture_audit_20261013/audit_existing.json"
FILES = (
    "docs/research/LONG_SWITCH_WAIT_PILOT_20261013.md",
    "scripts/run_long_switch_wait_20261013.py",
    "src/auto_trading/research/long_switch_wait_20261013.py",
    "tests/research/test_long_switch_wait_20261013.py",
)
EXPECTED_RESULT = "74089c37884595daecd9981207e6c3d6ef9190d7eb14f6cc7b99c8b449650d52"


def stamp(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, UTC).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name: str, value: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("x", encoding="utf8") as output:
        output.write(json.dumps(value, default=str, ensure_ascii=False, indent=2) + "\n")


def months(start_ms: int, end_ms: int) -> list[str]:
    start, end = stamp(start_ms)[:7], stamp(end_ms - 1)[:7]
    return [
        f"{year:04d}-{month:02d}"
        for year in range(2020, 2027)
        for month in range(1, 13)
        if start <= f"{year:04d}-{month:02d}" <= end
    ]


def load_minutes(
    kind: str, symbol: str, windows: list[tuple[int, int]], hashes: dict[str, str]
) -> dict[int, pure.Bar]:
    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(windows):
        if not old.FROM_MS <= lo < hi <= old.END_MS:
            raise DataIntegrityError("unregistered archive window/cutoff")
        if merged and lo <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(hi, merged[-1][1])
        else:
            merged.append((lo, hi))
    starts = [lo for lo, _ in merged]
    selected_months = sorted({m for lo, hi in merged for m in months(lo, hi)})
    bars: dict[int, pure.Bar] = {}
    for month in selected_months:
        if month > "2026-08":
            raise DataIntegrityError("preserved period forbidden")
        path = (
            ROOT
            / "data/external/binance_vision"
            / kind
            / symbol
            / "1m"
            / (f"{symbol}-1m-{month}.zip")
        )
        if not path.is_file():
            raise DataIntegrityError(f"missing local minute archive: {kind}/{path.name}")
        hashes[f"{kind}/{symbol}/{path.name}"] = digest(path)
        with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as source:
            for row in csv.reader(io.TextIOWrapper(source, encoding="ascii")):
                if not row[0].isdigit():
                    if row[0] == "open_time":
                        continue
                    raise DataIntegrityError("unexpected archive header")
                raw_ms = int(row[0])
                at = raw_ms // 1000 if raw_ms > 10**14 else raw_ms
                if at >= old.END_MS:
                    raise DataIntegrityError("archive cutoff violation")
                index = bisect.bisect_right(starts, at) - 1
                if index < 0 or at >= merged[index][1]:
                    continue
                if at in bars or at % pure.MINUTE:
                    raise DataIntegrityError("duplicate/unrounded minute")
                bar = pure.Bar(*(Decimal(x) for x in (row[1], row[2], row[3], row[4], row[5])))
                pure._valid_bar(bar)
                bars[at] = bar
    for lo, hi in merged:
        if any(t not in bars for t in range(lo, hi, pure.MINUTE)):
            raise DataIntegrityError(f"minute gap: {kind}/{symbol}/{stamp(lo)}")
    return bars


def load() -> tuple[list[tuple[str, pure.Event, list[pure.Frame]]], dict[str, Any]]:
    if digest(PREVIOUS / "result.json") != EXPECTED_RESULT:
        raise DataIntegrityError("previous result changed")
    audit = json.loads(AUDIT_SOURCE.read_text(encoding="utf8"))
    data = old.load()
    if old.input_digest(data) != old.EXPECTED_INPUT_SHA256:
        raise DataIntegrityError("previous bounded market inputs changed")
    daily = {s: sorted(data.spot[s]) for s in old.SYMBOLS}
    closes = {s: [data.spot[s][t].close for t in daily[s]] for s in old.SYMBOLS}
    goals = {
        t: next_targets(
            {s: closes[s][: bisect.bisect_right(daily[s], t - 2 * pure.DAY)] for s in old.SYMBOLS},
            T2_ALONE,
        )
        for t in range(old.FROM_MS, old.END_MS, pure.DAY)
    }
    sources = audit["diagnostics"]["RSI_PRIORITY"]["events"]
    if len(sources) != 9:
        raise DataIntegrityError("frozen request population changed")
    details = {
        rule: json.loads((PREVIOUS / f"{rule}_PRIORITY_base_all.json").read_text(encoding="utf8"))
        for rule in ("RSI", "KELTNER")
    }
    plans: list[dict[str, Any]] = []
    for source in sources:
        at, symbol = int(source["request_ms"]), source["symbol"]
        ends = [t for t in goals if t > at and goals[t][symbol] == 0]
        if not ends:
            raise DataIntegrityError("long cycle extends beyond frozen cutoff")
        off = min(ends)
        for rule, detail in details.items():
            matches = [
                trade
                for trade in detail["trades"]
                if trade["symbol"] == symbol and trade["entry_ms"] <= at <= trade["exit_ms"]
            ]
            if len(matches) != 1 or matches[0]["exit_ms"] != at:
                raise DataIntegrityError("short/cooldown event attribution mismatch")
            trade = matches[0]
            plans.append(
                {
                    "rule": rule,
                    "symbol": symbol,
                    "start_ms": at,
                    "off_ms": off,
                    "end_ms": min(off + pure.DAY, old.END_MS),
                    "short_entry_ms": int(trade["entry_ms"]),
                    "qty": trade["qty"],
                    "already_flat": trade["reason"] == "STOP",
                }
            )
    hashes = {"previous_result": EXPECTED_RESULT, "chronology_audit": digest(AUDIT_SOURCE)}
    for rule in details:
        path = PREVIOUS / f"{rule}_PRIORITY_base_all.json"
        hashes[path.name] = digest(path)
    archive: dict[tuple[str, str], dict[int, pure.Bar]] = {}
    for symbol in old.SYMBOLS:
        selected = [p for p in plans if p["symbol"] == symbol]
        windows = [(p["start_ms"] - pure.MINUTE, p["end_ms"]) for p in selected]
        archive["spot", symbol] = load_minutes("spot", symbol, windows, hashes)
        archive["um", symbol] = load_minutes(
            "um",
            symbol,
            [
                *windows,
                *((p["short_entry_ms"], p["short_entry_ms"] + pure.MINUTE) for p in selected),
            ],
            hashes,
        )

    funding_path = ROOT / "reports/short_validation_laya_20261013/funding_rest.json"
    raw_funds = json.loads(funding_path.read_text(encoding="utf8"))
    hashes["funding_rest"] = digest(funding_path)
    fund_map: dict[str, dict[int, pure.Funding]] = {}
    mark_proxy = 0
    for symbol in old.SYMBOLS:
        selected = [p for p in plans if p["symbol"] == symbol]
        fund_map[symbol] = {}
        for raw in raw_funds[symbol]:
            at = int(raw["fundingTime"])
            if not any(p["start_ms"] <= at < p["end_ms"] for p in selected):
                continue
            exact = bool(raw.get("markPrice") and Decimal(raw["markPrice"]) > 0)
            if exact:
                mark = Decimal(raw["markPrice"])
            else:
                month = stamp(at)[:7]
                path = (
                    ROOT
                    / "data/external/binance_vision/um_mark"
                    / symbol
                    / "1h"
                    / (f"{symbol}-1h-{month}.zip")
                )
                hashes[f"mark/{symbol}/{month}"] = digest(path)
                mark = None
                with zipfile.ZipFile(path) as zipped:
                    for row in csv.reader(io.StringIO(zipped.read(zipped.namelist()[0]).decode())):
                        if row[0].isdigit() and int(row[0]) == at // 3_600_000 * 3_600_000:
                            mark = Decimal(row[1])
                            break
                if mark is None:
                    raise DataIntegrityError("funding mark fallback missing")
                mark_proxy += 1
            if mark is None:
                raise DataIntegrityError("funding mark unavailable")
            minute_ms = at // pure.MINUTE * pure.MINUTE
            if minute_ms in fund_map[symbol]:
                raise DataIntegrityError("multiple settlements in one minute")
            fund_map[symbol][minute_ms] = pure.Funding(at, Decimal(raw["fundingRate"]), mark, exact)
        expected = {
            t
            for t, _ in data.funding[symbol]
            if any(p["start_ms"] <= t < p["end_ms"] for p in selected)
        }
        if expected != {f.at_ms for f in fund_map[symbol].values()}:
            raise DataIntegrityError("funding cash schedule mismatch")

    loaded = []
    h = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode())
    for p in plans:
        s, at = p["symbol"], p["start_ms"]
        spot, perp = archive["spot", s], archive["um", s]
        reference = spot[at - pure.MINUTE].c
        qty = floor_to_step(pure.INITIAL * Decimal(str(goals[at][s])) / reference, pure.SPOT_STEP)
        event = pure.Event(
            s,
            at,
            p["end_ms"],
            Decimal(0) if p["already_flat"] else Decimal(p["qty"]),
            perp[at - pure.MINUTE].c,
            perp[p["short_entry_ms"]].o * Decimal("0.99955") * Decimal("1.03"),
            qty,
            reference,
            p["already_flat"],
        )
        frames = [
            pure.Frame(
                t,
                spot[t],
                perp[t],
                spot[t - pure.MINUTE],
                perp[t - pure.MINUTE],
                Decimal(str(goals[t // pure.DAY * pure.DAY][s])),
                t // pure.DAY * pure.DAY - pure.DAY,
                (fund_map[s][t],) if t in fund_map[s] else (),
            )
            for t in range(at, p["end_ms"], pure.MINUTE)
        ]
        h.update(json.dumps(asdict(event), sort_keys=True, default=str).encode())
        for frame in frames:
            h.update(json.dumps(asdict(frame), sort_keys=True, default=str).encode())
        loaded.append((p["rule"], event, frames))
    unique_plans = [p for p in plans if p["rule"] == "RSI"]
    clusters: list[tuple[int, int]] = []
    for p in sorted(unique_plans, key=lambda x: x["start_ms"]):
        if clusters and p["start_ms"] <= clusters[-1][1]:
            clusters[-1] = clusters[-1][0], max(p["end_ms"], clusters[-1][1])
        else:
            clusters.append((p["start_ms"], p["end_ms"]))
    return loaded, {
        "status": "DATA_AUDIT_ONLY",
        "new_pnl_runs": 0,
        "source_sha256": h.hexdigest(),
        "file_hashes": hashes,
        "local_archive_count": len([k for k in hashes if "/" in k]),
        "plans": plans,
        "events": 9,
        "rule_event_conditions": len(loaded),
        "overlap_clusters": len(clusters),
        "minute_rows": {f"{kind}:{symbol}": len(bars) for (kind, symbol), bars in archive.items()},
        "funding_mark_proxy_rows": mark_proxy,
        "gaps": 0,
        "cutoff_exclusive_ms": old.END_MS,
        "signal_contract": "unchanged d-2 daily close; d 00:00 effective",
        "historical_first_seen_order_book_ack_filters": "NOT_AVAILABLE",
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    pairs = []
    for rule in ("RSI", "KELTNER"):
        for scenario in pure.SCENARIOS:
            selected = [r for r in results if r["rule"] == rule and r["scenario"] == scenario.name]
            arms: dict[str, Any] = {}
            for wait in (0, 60):
                group = [r for r in selected if r["wait_min"] == wait]
                sums = {
                    key: sum((r[key] for r in group), Decimal(0))
                    for key in (
                        "net",
                        "long_net",
                        "short_net",
                        "spot_fee",
                        "spot_slip",
                        "short_fee",
                        "short_slip",
                        "funding",
                        "turnover",
                        "spot_residual",
                        "short_residual",
                    )
                    if key not in ("spot_residual", "short_residual")
                }
                arms[str(wait)] = {
                    **sums,
                    "first_buy_events": sum(r["first_buy_ms"] is not None for r in group),
                    "full_buy_events": sum(r["full_buy_ms"] is not None for r in group),
                    "spot_residual_events": sum(r["spot_residual"] > 0 for r in group),
                    "short_residual_events": sum(r["short_residual"] > 0 for r in group),
                    "max_event_mdd": max(r["mdd"] for r in group),
                    "average_exposure": sum(r["average_exposure"] for r in group) / 9,
                }
            delta = arms["0"]["net"] - arms["60"]["net"]
            summaries[f"{rule}:{scenario.name}"] = {
                "arms": arms,
                "delta_0_minus_60": delta,
                "description_only": "0_MIN_FAVORABLE"
                if delta > 0
                else ("60_MIN_FAVORABLE" if delta < 0 else "SAME"),
                "adoption": "UNVERIFIED",
            }
            for before in [r for r in selected if r["wait_min"] == 0]:
                after = next(
                    r
                    for r in selected
                    if r["wait_min"] == 60
                    and r["start_ms"] == before["start_ms"]
                    and r["symbol"] == before["symbol"]
                )
                if before["short_net"] != after["short_net"] or before["ack_ms"] != after["ack_ms"]:
                    raise DataIntegrityError("paired common short path mismatch")
                pairs.append(
                    {
                        "rule": rule,
                        "scenario": scenario.name,
                        "symbol": before["symbol"],
                        "request_utc": stamp(before["start_ms"]),
                        "delta": before["net"] - after["net"],
                        "cost_delta": before["spot_fee"]
                        + before["spot_slip"]
                        - after["spot_fee"]
                        - after["spot_slip"],
                        "bought_qty_delta": before["bought_qty"] - after["bought_qty"],
                        "checkpoints_delta": {
                            k: before["checkpoints"][k] - after["checkpoints"][k]
                            for k in before["checkpoints"]
                        },
                    }
                )
    return {
        "summary": summaries,
        "pairs": pairs,
        "max_accounting_residual": max(abs(r["residual"]) for r in results),
    }


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("audit", "pilot"):
        raise DataIntegrityError("choose audit or frozen one-shot pilot")
    loaded, meta = load()
    if sys.argv[1] == "audit":
        save("audit.json", meta)
        print(
            json.dumps(
                {k: meta[k] for k in ("events", "overlap_clusters", "minute_rows", "gaps")},
                ensure_ascii=False,
            )
        )
        return
    audit = json.loads((OUT / "audit.json").read_text(encoding="utf8"))
    if audit["source_sha256"] != meta["source_sha256"]:
        raise DataIntegrityError("audited input changed")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True
    )
    tracked = subprocess.check_output(["git", "ls-files", "--", *FILES], cwd=ROOT, text=True)
    if dirty.strip() or len(tracked.splitlines()) != len(FILES):
        raise DataIntegrityError("registration/implementation must be committed before PnL")
    code = {f: digest(ROOT / f) for f in FILES}
    save(
        "run_started.json",
        {
            "input_sha256": meta["source_sha256"],
            "code": code,
            "head": old.git("rev-parse", "HEAD"),
            "runs": 180,
        },
    )
    results = []
    for rule, event, frames in loaded:
        for scenario in pure.SCENARIOS:
            for wait in (0, 60):
                result = pure.simulate(event, frames, wait_min=wait, scenario=scenario)
                result["rule"] = rule
                results.append(result)
    if len(results) != 180:
        raise DataIntegrityError("run population mismatch")
    save("detail.json", results)
    save(
        "result.json",
        {
            "status": "EXPLANATORY_RETROSPECTIVE_ONLY",
            "runs": 180,
            "input_sha256": meta["source_sha256"],
            "code": code,
            "adoption": "UNVERIFIED",
            "independent_oos": 0,
            **summarize(results),
        },
    )
    print(json.dumps({"runs": 180, "result_sha256": digest(OUT / "result.json")}, default=str))


if __name__ == "__main__":
    main()
