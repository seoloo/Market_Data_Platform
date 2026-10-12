"""One-shot approved fixed-short / long-return pilot; never operating orders."""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import statistics
import subprocess
import sys
import zipfile
from dataclasses import asdict, replace
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.backtest.overlay import T2_ALONE, next_targets
from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import short_long_return_20261013 as pure
from scripts import audit_h1_o_ag_20261012 as old
from scripts import run_complement_pilot_20261013 as complement
from scripts import run_rsi_stoch_pilot_20261012 as base
from scripts import run_short_validation_20261013 as previous

OUT = base.REPO / "reports/short_long_return_20261013"
DOC = "docs/research/SHORT_LONG_RETURN_PILOT_20261013.md"
NEW_FILES = (
    DOC,
    "scripts/run_short_long_return_20261013.py",
    "src/auto_trading/research/short_long_return_20261013.py",
    "tests/research/test_short_long_return_20261013.py",
)
EXPECTED_INPUT = "a38055d7b3797c2c94c21db0f9f9c3444fd8412062ec75e1967047f853d4cc67"
EXPECTED_LEGACY = "f8c3e7666f2d10bfa37ab3349306543aa99cd79d2fd4add823b44c92413e16c9"
EXPECTED_CANONICAL = "3a27e3091012641fcae1cb78aa232433d38cd28e52fcdc76bb3932e9914a7e6a"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name: str, value: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("x", encoding="utf8") as target:
        target.write(json.dumps(value, default=str, ensure_ascii=False, indent=2) + "\n")


def load() -> tuple[dict[str, list[pure.Frame]], dict[str, Any]]:
    data = old.load()
    coins, quality = previous.load_canonical()
    if old.input_digest(data) != EXPECTED_LEGACY or quality["source_sha256"] != EXPECTED_CANONICAL:
        raise DataIntegrityError("prior input source changed")
    raw_funding = json.loads((previous.OUT / "funding_rest.json").read_text(encoding="utf8"))
    supplements = json.loads(
        (previous.OUT / "mark_hour_supplement.json").read_text(encoding="utf8")
    )
    funding: dict[str, list[pure.CashEvent]] = {}
    file_hashes: dict[str, str] = {}
    exact_counts: dict[str, int] = {}
    engine = complement.Engine(coins)
    ready: dict[str, dict[int, set[str]]] = {r: {} for r in ("RSI", "KELTNER")}
    for c in coins:
        for rule in ready:
            cfg = previous.frozen(rule)
            for i in engine.signals(c, cfg):
                if c.regime[cfg.regime][i]:
                    ready[rule].setdefault(c.t[i] + pure.H4, set()).add(c.name)
        marks: dict[int, Decimal] = {}
        for month in previous.month_names():
            path = (
                base.REPO
                / "data/external/binance_vision/um_mark"
                / c.name
                / "1h"
                / f"{c.name}-1h-{month}.zip"
            )
            file_hashes[f"{c.name}:{month}"] = digest(path)
            with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as source:
                for row in csv.reader(io.TextIOWrapper(source, encoding="ascii")):
                    if row[0].isdigit():
                        at = int(row[0])
                        if not base.START <= at < base.END:
                            raise DataIntegrityError("mark archive cutoff")
                        marks[at] = Decimal(row[1])
        raw_by_time = {int(r["fundingTime"]): r for r in raw_funding[c.name]}
        events, exact = [], 0
        for at, rate in zip(c.fund_t, c.fund_r, strict=True):
            if not base.START <= at < base.END:
                continue
            raw = raw_by_time[at]
            if Decimal(str(rate)) != Decimal(raw["fundingRate"]):
                raise DataIntegrityError("funding rate changed")
            if raw.get("markPrice") and Decimal(raw["markPrice"]) > 0:
                mark = Decimal(raw["markPrice"])
                exact += 1
            else:
                hour = at // 3600000 * 3600000
                mark = marks[hour] if hour in marks else Decimal(supplements[f"{c.name}:{hour}"][1])
            events.append(pure.CashEvent(at, Decimal(str(rate)), mark))
        funding[c.name], exact_counts[c.name] = events, exact
    by = {c.name: c for c in coins}
    index = {s: {t: i for i, t in enumerate(c.t)} for s, c in by.items()}
    daily = {s: sorted(data.spot[s]) for s in base.SYMBOLS}
    daily_closes = {s: [data.spot[s][t].close for t in daily[s]] for s in base.SYMBOLS}

    def spot_bar(s: str, t: int) -> pure.Bar:
        b = data.spot4[s][t]
        return pure.Bar(*(Decimal(str(v)) for v in (b.o, b.h, b.low, b.c, b.v)))

    def perp_bar(s: str, t: int) -> pure.Bar:
        c, i = by[s], index[s][t]
        return pure.Bar(*(Decimal(str(v[i])) for v in (c.o, c.h, c.lo, c.c, c.v)))

    result: dict[str, list[pure.Frame]] = {r: [] for r in ready}
    fund_times = {s: [e.at_ms for e in events] for s, events in funding.items()}
    for t in range(base.START, base.END, pure.H4):
        targets = None
        if t % pure.DAY == 0:
            history = {
                s: daily_closes[s][: bisect.bisect_right(daily[s], t - 2 * pure.DAY)]
                for s in base.SYMBOLS
            }
            targets = {s: Decimal(str(w)) for s, w in next_targets(history, T2_ALONE).items()}
        bar_events = {
            s: tuple(
                funding[s][
                    bisect.bisect_left(fund_times[s], t) : bisect.bisect_left(
                        fund_times[s], t + pure.H4
                    )
                ]
            )
            for s in base.SYMBOLS
        }
        for rule in result:
            result[rule].append(
                pure.Frame(
                    t,
                    {s: spot_bar(s, t) for s in base.SYMBOLS},
                    {s: perp_bar(s, t) for s in base.SYMBOLS},
                    {s: spot_bar(s, t - pure.H4) for s in base.SYMBOLS},
                    {s: perp_bar(s, t - pure.H4) for s in base.SYMBOLS},
                    targets,
                    t - pure.DAY if targets is not None else -1,
                    frozenset(ready[rule].get(t, set())),
                    t,
                    bar_events,
                )
            )
    meta = {
        "legacy_sha256": EXPECTED_LEGACY,
        "canonical_source_sha256": quality["source_sha256"],
        "funding_rest_sha256": digest(previous.OUT / "funding_rest.json"),
        "supplement_sha256": digest(previous.OUT / "mark_hour_supplement.json"),
        "mark_archives_sha256": file_hashes,
        "funding_exact_counts": exact_counts,
        "funding_counts": {s: len(e) for s, e in funding.items()},
        "quality": data.quality,
        "signals": {r: sum(len(f.signals) for f in frames) for r, frames in result.items()},
    }
    h = hashlib.sha256(json.dumps(meta, sort_keys=True).encode())
    for rule, frames in result.items():
        h.update(rule.encode())
        for frame in frames:
            h.update(
                json.dumps(
                    asdict(frame),
                    default=lambda v: sorted(v) if isinstance(v, frozenset) else str(v),
                    sort_keys=True,
                ).encode()
            )
    meta["source_sha256"] = h.hexdigest()
    return result, meta


def metrics(run: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    eq = [pure.INITIAL, *(p["equity"] for p in run["trace"])]
    peak, mdd = pure.INITIAL, Decimal(0)
    for value in eq:
        peak = max(peak, value)
        mdd = max(mdd, 1 - value / peak)
    days = [
        pure.INITIAL,
        *(p["equity"] for p in run["trace"] if (p["at_ms"] + pure.H4) % pure.DAY == 0),
    ]
    changes = [float(b / a - 1) for a, b in pairwise(days) if a > 0]
    monthly: dict[str, float] = {}
    from datetime import UTC, datetime

    opening = pure.INITIAL
    for i, p in enumerate(run["trace"]):
        key = datetime.fromtimestamp(p["at_ms"] / 1000, UTC).strftime("%Y-%m")
        if (
            i + 1 == len(run["trace"])
            or datetime.fromtimestamp(run["trace"][i + 1]["at_ms"] / 1000, UTC).strftime("%Y-%m")
            != key
        ):
            monthly[key] = float(p["equity"] / opening - 1)
            opening = p["equity"]
    years = (end - start) / (365.25 * pure.DAY)
    result = {k: v for k, v in run.items() if k not in ("trace", "trades")}
    result.update(
        cagr=float(eq[-1] / pure.INITIAL) ** (1 / years) - 1,
        sharpe=statistics.fmean(changes) / statistics.stdev(changes) * math.sqrt(365.25)
        if len(changes) > 1 and statistics.stdev(changes) > 0
        else 0,
        mdd=float(mdd),
        worst_month=min(monthly.values()),
        monthly=monthly,
        mean_exposure=statistics.fmean(float(p["exposure"]) for p in run["trace"]),
        max_exposure=max(float(p["exposure"]) for p in run["trace"]),
    )
    return result


def paired(
    reference: dict[str, Any], run: dict[str, Any], frames: list[pure.Frame]
) -> dict[str, Any]:
    lost = avoided = down = up = flat = Decimal(0)
    previous_ref = previous_run = pure.INITIAL
    for i, (a, b, f) in enumerate(zip(reference["trace"], run["trace"], frames, strict=True)):
        incremental = (b["equity"] - previous_run) - (a["equity"] - previous_ref)
        if a["equity"] < previous_ref:
            down += incremental
        elif a["equity"] > previous_ref:
            up += incremental
        else:
            flat += incremental
        for s in f.spot:
            opportunity = Decimal(0)
            if b["blocked"][s]:
                opportunity += a["spot_qty"][s] * (f.spot[s].c - f.spot[s].o)
            if i and run["trace"][i - 1]["blocked"][s]:
                opportunity += reference["trace"][i - 1]["spot_qty"][s] * (
                    f.spot[s].o - f.previous_spot[s].c
                )
            lost += max(Decimal(0), opportunity)
            avoided -= min(Decimal(0), opportunity)
        previous_ref, previous_run = a["equity"], b["equity"]
    pieces = {
        "spot_market_change": run["spot_market"] - reference["spot_market"],
        "short_market": run["short_market"],
        "funding": run["funding"],
        "additional_fees": run["spot_fee"] + run["short_fee"] - reference["spot_fee"],
        "additional_slip": run["spot_slip"] + run["short_slip"] - reference["spot_slip"],
        "fee_reserve_change": run["fee_reserve"] - reference["fee_reserve"],
    }
    delta = run["net"] - reference["net"]
    reconstructed = (
        pieces["spot_market_change"]
        + pieces["short_market"]
        + pieces["funding"]
        - pieces["additional_fees"]
        - pieces["additional_slip"]
        - pieces["fee_reserve_change"]
    )
    return {
        "net_increment": delta,
        "pieces": pieces,
        "residual": delta - reconstructed,
        "blocked_long_missed_profit": lost,
        "blocked_long_avoided_loss": avoided,
        "blocked_long_reference_net": lost - avoided,
        "base_down_increment": down,
        "base_up_increment": up,
        "base_flat_increment": flat,
        "rebound_trade_losses": sum(
            (t["net"] for t in run["trades"] if t["market_gross"] < 0), Decimal(0)
        ),
        "rebound_trade_count": sum(t["market_gross"] < 0 for t in run["trades"]),
        "trade_by_symbol": {
            s: sum((t["net"] for t in run["trades"] if t["symbol"] == s), Decimal(0))
            for s in base.SYMBOLS
        },
    }


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if command not in ("audit", "run"):
        raise DataIntegrityError("audit or run")
    if command == "run":
        for file in NEW_FILES:
            subprocess.run(
                ["git", "ls-files", "--error-unmatch", file],
                cwd=base.REPO,
                check=True,
                capture_output=True,
            )
        if (
            subprocess.check_output(["git", "diff", "HEAD", "--", *NEW_FILES], cwd=base.REPO)
            or (OUT / "result.json").exists()
        ):
            raise DataIntegrityError("commit first / completed result cannot rerun")
    sources, meta = load()
    if command == "audit":
        save("audit.json", meta)
        print(
            json.dumps(
                {
                    k: meta[k]
                    for k in ("source_sha256", "signals", "funding_counts", "funding_exact_counts")
                },
                ensure_ascii=False,
            )
        )
        return
    if meta["source_sha256"] != EXPECTED_INPUT:
        raise DataIntegrityError("registered data digest changed")
    summary: dict[str, Any] = {}
    for label, cost, delay in (("base", 1, 0), ("cost2", 2, 0), ("delay4h", 1, 1)):
        for period, (start, end) in base.PERIODS.items():
            frames_by: dict[str, list[pure.Frame]] = {}
            for rule, whole in sources.items():
                signal_at = {f.at_ms + delay * pure.H4: f.signals for f in whole}
                frames_by[rule] = [
                    replace(
                        f,
                        signals=signal_at.get(f.at_ms, frozenset())
                        if f.at_ms < end - base.PURGE
                        else frozenset(),
                        signal_as_of_ms=f.at_ms - delay * pure.H4,
                    )
                    for f in whole
                    if start <= f.at_ms < end
                ]
            reference = pure.simulate(
                frames_by["RSI"], end_ms=end, mode="BASE", cost=cost, delay=delay
            )
            summary[f"BASE:{label}:{period}"] = metrics(reference, start, end)
            for rule, frames in frames_by.items():
                for mode in ("PRIORITY", "WAIT"):
                    key = f"{rule}_{mode}:{label}:{period}"
                    checkpoint = OUT / (key.replace(":", "_") + ".json")
                    if checkpoint.exists():
                        raise DataIntegrityError("partial run exists; do not overwrite")
                    run = pure.simulate(frames, end_ms=end, mode=mode, cost=cost, delay=delay)
                    report = {
                        "account": metrics(run, start, end),
                        "paired": paired(reference, run, frames),
                    }
                    summary[key] = report
                    save(
                        checkpoint.name, {**report, "trades": run["trades"], "trace": run["trace"]}
                    )
            print(json.dumps({"phase": label, "period": period}), flush=True)
    save(
        "result.json",
        {
            "status": "EXPLANATORY_RETROSPECTIVE_UNVERIFIED",
            "new_oos": 0,
            "inputs": meta,
            "new_code_sha256": {file: digest(base.REPO / file) for file in NEW_FILES[1:]},
            "registration_doc_sha256": digest(base.REPO / DOC),
            "reference_overlay_sha256": digest(base.REPO / "src/auto_trading/backtest/overlay.py"),
            "reference_trend_sha256": digest(base.REPO / "src/auto_trading/backtest/trend.py"),
            "previous_code_sha256": previous.code_hash(),
            "registration_git": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=base.REPO, text=True
            ).strip(),
            "summary": summary,
        },
    )


if __name__ == "__main__":
    main()
