"""Baseline-only planning variance. No macro model, signal or candidate PnL."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.deneb_macro_281.data_audit import CUTOFF, OUT, REPO, SERIES

from auto_trading.backtest.power import plan

TRAIN_END = "2016-06-30"
SIMS = 300
ANNUAL_EFFECT = 0.03
SCENARIOS = ((0.1, 63), (0.2, 126))


def load_prefix(path: Path, end: str) -> tuple[dict[str, float], str]:
    """Stop at the first later date, before parsing any later price value."""
    out: dict[str, float] = {}
    digest = hashlib.sha256()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        header = stream.readline()
        digest.update(header.encode())
        columns = next(csv.reader([header]))
        if columns not in (["date", "adjclose"], ["observation_date", "NASDAQ100"]):
            raise ValueError("Unexpected price schema")
        previous = ""
        for line in stream:
            day = line.partition(",")[0]
            if day > end:
                break
            if day <= previous:
                raise ValueError("Non-increasing price dates")
            previous = day
            digest.update(line.encode())
            raw = next(csv.reader([line]))[1]
            if raw in ("", "."):
                continue
            value = float(raw)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid historical price")
            out[day] = value
    return out, digest.hexdigest()


def long_run_sd(xs: Sequence[float], lag: int = 6) -> float:
    if len(xs) <= lag + 1:
        raise ValueError("Not enough observations for planning variance")
    mean = statistics.fmean(xs)
    centered = [x - mean for x in xs]
    n = len(xs)
    variance = sum(x * x for x in centered) / n
    for k in range(1, lag + 1):
        cov = sum(centered[i] * centered[i - k] for i in range(k, n)) / n
        variance += 2 * (1 - k / (lag + 1)) * cov
    return math.sqrt(max(variance, 0.0))


def month_differences(
    days: Sequence[str],
    returns: Sequence[float],
    occupancy: float,
    duration: int,
    rng: random.Random,
) -> list[float]:
    """Random attenuation of the baseline: return only centered-risk planning inputs."""
    state = rng.random() < occupancy
    by_month: dict[str, float] = {}
    for day, ret in zip(days, returns, strict=True):
        old = state
        if state:
            state = rng.random() >= 1 / duration
        elif rng.random() < occupancy / ((1 - occupancy) * duration):
            state = True
        delta = (-0.5 * ret if state else 0.0) - (0.00075 if state != old else 0.0)
        by_month[day[:7]] = by_month.get(day[:7], 0.0) + delta
    return list(by_month.values())


def power_row(sd: float, n: int, months_per_sample: int) -> dict[str, Any]:
    if n < 1 or sd <= 0:
        return {"status": "UNDETERMINED", "samples": n}
    p = plan(sd * 10_000, n, ANNUAL_EFFECT / 12 * months_per_sample * 10_000, 0.0)
    return {
        "sd_pct": sd * 100,
        "samples": n,
        "months_per_sample": months_per_sample,
        "required_samples": p.required_samples,
        "achieved_power": p.achieved_power,
        "annual_mde_pct_points": p.detectable_effect_bps / 10_000 * 12 / months_per_sample * 100,
        "decidable_proxy_only": p.decidable,
    }


def label_summary(
    days: Sequence[str],
    prices: Mapping[str, float],
    indices: Sequence[int],
    horizon: int = 126,
) -> dict[str, int]:
    """Describe targets only; merge overlapping positive label windows, not rows."""
    positives, groups, last_end = 0, 0, -1
    for i in indices:
        if i + horizon >= len(days):
            raise ValueError("Incomplete future label window")
        if min(prices[days[j]] for j in range(i + 1, i + horizon + 1)) <= prices[days[i]] * 0.85:
            positives += 1
            if i > last_end:
                groups += 1
            last_end = max(last_end, i + horizon)
    return {
        "eligible_months_upper_bound": len(indices),
        "positive_months": positives,
        "negative_months": len(indices) - positives,
        "overlapping_positive_window_groups": groups,
    }


def baseline() -> tuple[list[str], list[float], dict[str, str]]:
    """Reuse 184's unchanged pure baseline, with training-only, bounded input files."""
    from scripts.run_brake_183 import signals
    from scripts.run_brake_184 import simulate

    maps, hashes = {}, {}
    for key, source in (
        ("lev", "data/research/lev_div_181/TQQQ.csv"),
        ("div", "data/research/div_mix_180/SCHD.csv"),
        ("qqq", "data/research/div_mix_180/QQQ.csv"),
    ):
        maps[key], hashes[source] = load_prefix(REPO / source, TRAIN_END)
    days = sorted(set(maps["lev"]) & set(maps["div"]) & set(maps["qqq"]))
    vol, trend = signals(sorted(maps["qqq"]), maps["qqq"])
    data = {
        "days": days,
        "lev": maps["lev"],
        "div": maps["div"],
        "vol": vol,
        "trend": trend,
        "start": "2012-01-01",
    }
    sim = simulate(data, maps["qqq"], ref="T", park="cash")
    navs = sim["navs"]
    returns = [0.0] + [b / a - 1 for a, b in pairwise(navs)]
    return sim["nav_days"], returns, hashes


def main() -> dict[str, Any]:
    target = OUT / "power_audit.json"
    if target.exists():
        raise FileExistsError("Planning audit exists; do not rerun to select a path")
    data_file = OUT / "data_audit_completed.json"
    if not data_file.exists():
        data_file = OUT / "data_audit_recovered.json"
    if not data_file.exists():
        data_file = OUT / "data_audit.json"
    source = json.loads(data_file.read_text(encoding="utf-8"))
    complete = all(source["series"][s].get("timely_rows", 0) for s in SERIES)
    first_releases = [source["series"][s].get("first_timely_release") for s in SERIES]
    common_start = max(d for d in first_releases if d) if complete else None
    # Add the 12-month feature history conservatively; data completeness is separate.
    if common_start:
        feature_start = f"{int(common_start[:4]) + 1:04d}-{common_start[5:7]}-01"
    else:
        feature_start = None
    ndx, ndx_hash = load_prefix(REPO / "data/research/tradfi/fred/NASDAQ100.csv", CUTOFF)
    days = sorted(ndx)
    month_ends = {d[:7]: i for i, d in enumerate(days)}
    decision_months = [
        m
        for m, i in month_ends.items()
        if i + 126 < len(days) and feature_start is not None and days[i] >= feature_start
    ]
    intervals = {
        "train": ("0000-01", "2016-06"),
        "validation": ("2017-01", "2019-06"),
        "historical_confirmation": ("2020-01", "2026-08"),
    }
    counts = {k: sum(a <= m <= b for m in decision_months) for k, (a, b) in intervals.items()}
    labels = {
        k: label_summary(days, ndx, [month_ends[m] for m in decision_months if a <= m <= b])
        for k, (a, b) in intervals.items()
    }
    base_days, base_returns, hashes = baseline()
    hashes["NASDAQ100_filtered_prefix"] = ndx_hash
    rng = random.Random(281)
    scenarios: dict[str, Any] = {}
    for occupancy, duration in SCENARIOS:
        monthly_sds, half_year_sds = [], []
        for _ in range(SIMS):
            monthly = month_differences(base_days, base_returns, occupancy, duration, rng)
            blocks = [sum(monthly[i : i + 6]) for i in range(0, len(monthly) - 5, 6)]
            monthly_sds.append(long_run_sd(monthly))
            half_year_sds.append(statistics.stdev(blocks))
        m90 = sorted(monthly_sds)[math.ceil(0.9 * SIMS) - 1]
        h90 = sorted(half_year_sds)[math.ceil(0.9 * SIMS) - 1]
        scenarios[f"occupancy_{occupancy}_duration_{duration}"] = {
            "monthly_sd_median_pct": statistics.median(monthly_sds) * 100,
            "half_year_sd_median_pct": statistics.median(half_year_sds) * 100,
            "monthly": {k: power_row(m90, n, 1) for k, n in counts.items()},
            "nonoverlapping_half_year": {k: power_row(h90, n // 6, 6) for k, n in counts.items()},
        }
    proxy_checks = [
        scenario[unit][period].get("decidable_proxy_only", False)
        for scenario in scenarios.values()
        for unit in ("monthly", "nonoverlapping_half_year")
        for period in ("train", "historical_confirmation")
    ]
    result = {
        "research": 281,
        "status": "G0_NOT_READY_ADOPTION_UNVERIFIED",
        "model_fits": 0,
        "candidate_pnl_calculations": 0,
        "common_first_timely_release": common_start,
        "conservative_feature_start": feature_start,
        "common_coverage_is_only_upper_bound": True,
        "label_eligible_month_counts_upper_bound": counts,
        "label_distribution_not_model_performance": labels,
        "last_label_eligible_month": decision_months[-1] if decision_months else None,
        "baseline_variance_only_period": [base_days[0], base_days[-1]],
        "target_annual_increment_pct_points": ANNUAL_EFFECT * 100,
        "simulations_per_assumption": SIMS,
        "seed": 281,
        "all_conservative_proxy_checks_passed": all(proxy_checks),
        "formal_power_gate": "NOT_ESTABLISHED",
        "scenarios": scenarios,
        "filtered_source_sha256": hashes,
        "source_audit_sha256": hashlib.sha256(data_file.read_bytes()).hexdigest(),
        "limitations": [
            "Actual TQQQ-only overlay variance unknown; whole-baseline attenuation is a proxy",
            "126-trading-day labels overlap; six-month blocks remain dependent across crises",
            "First timely release does not prove a complete point-in-time feature panel",
            "INDPRO/PCEPILFE index rebasing requires same-vintage history, not stitched levels",
            "Baseline 184 is not full live accounting parity or tax-inclusive differential PnL",
            "Past research exposure means historical confirmation is not independent OOS",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    main()
