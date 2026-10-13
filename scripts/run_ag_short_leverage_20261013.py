"""Local bounded AG/RSI audit and approved one-shot pilot; no exchange APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "reports/wallet_grid_20261013"
OUT = ROOT / "reports/ag_short_leverage_corrected_20261014"
FIRST_OUT = ROOT / "reports/ag_short_leverage_20261013"
FIRST_RESULT_SHA = "8c50da541924739ffb3a154d3fb1562759f70a1fc193bd261d0279eea9ecd0e5"
START = int(datetime(2020, 6, 1, tzinfo=UTC).timestamp() * 1000)
END = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
INPUT_ID = "96dc97701d133ab3f4ec261534ed55c4129fd09507b535b884ff0e1c16107235"
FILES = (
    "docs/research/AG_SHORT_LEVERAGE_20261013.md",
    "docs/research/AG_SHORT_LEVERAGE_CORRECTION_20261014.md",
    "src/auto_trading/research/ag_short_leverage_20261013.py",
    "scripts/run_ag_short_leverage_20261013.py",
    "tests/research/test_ag_short_leverage_20261013.py",
    "src/auto_trading/research/wallet_grid_20261013.py",
    "scripts/wallet_grid_data_20261013.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(type(value).__name__)


def save(name: str, value: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, default=serialize, ensure_ascii=False, indent=2)


def source() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if digest(FIRST_OUT / "result.json") != FIRST_RESULT_SHA:
        raise DataIntegrityError("initial invalid result must be preserved")
    manifest = json.loads((SOURCE / "aligned_manifest.json").read_text(encoding="utf8"))
    if (
        manifest["input_id"] != INPUT_ID
        or data.payload_hash({k: v for k, v in manifest.items() if k != "input_id"}) != INPUT_ID
    ):
        raise DataIntegrityError("frozen AG input changed")
    if manifest["start_ms"] != START or manifest["end_ms"] != END:
        raise DataIntegrityError("AG data boundary")
    if digest(SOURCE / "features.json") != manifest["features_sha256"]:
        raise DataIntegrityError("frozen signals/funding changed")
    for entry in manifest["entries"]:
        if entry["month"] > "2026-08" or digest(ROOT / entry["raw_path"]) != entry["raw_sha256"]:
            raise DataIntegrityError("source mutation/cutoff")
    return manifest, json.loads((SOURCE / "features.json").read_text(encoding="utf8"))


def audit() -> None:
    manifest, features = source()
    updates = [f["at_ms"] for f in features if f["targets"] is not None]
    if (
        updates != list(range(START, END, p.DAY))
        or len(features) != (END - START) // p.H4
        or any(f["at_ms"] != START + i * p.H4 for i, f in enumerate(features))
    ):
        raise DataIntegrityError("daily event stream coverage/alignment")
    save(
        "audit.json",
        {
            "input_id": INPUT_ID,
            "quality": manifest["quality"],
            "cases": len(p.cases()),
            "performance_runs": 0,
            "actual_execution": "NOT_VERIFIED",
            "approved_retrospective_only": True,
            "initial_invalid_result_sha256": FIRST_RESULT_SHA,
            "goal_event_semantics": {
                "updates": len(updates),
                "no_update_blocks": len(features) - len(updates),
                "confirmed_target_valid_ms": p.DAY,
                "none_means": "NO_UPDATE_NOT_MISSING_CONFIRMED_DAILY_TARGET",
            },
        },
    )
    print(
        json.dumps({"audit_complete": True, "cases": len(p.cases()), "performance_runs": 0}),
        flush=True,
    )


def reconcile(run: dict[str, Any]) -> dict[str, Any]:
    """Independent signed-contract reconstruction from saved fills, not a replay."""
    cash = p.INITIAL
    spot = dict.fromkeys(p.SYMBOLS, Decimal(0))
    positions: dict[str, tuple[Decimal, Decimal]] = {}
    fees = slips = turnover = Decimal(0)
    for f in run["fills"]:
        qty, price, raw, fee = (Decimal(f[k]) for k in ("delta", "price", "raw_price", "fee"))
        symbol = f["symbol"]
        if f["asset"] == "SPOT":
            cash -= qty * price + fee
            spot[symbol] += qty
        else:
            old_qty, entry = positions.get(symbol, (Decimal(0), Decimal(0)))
            if old_qty * qty < 0:
                if abs(qty) > abs(old_qty):
                    raise DataIntegrityError("saved direction reversal")
                cash += abs(qty) * (price - entry) * (1 if old_qty > 0 else -1)
                remaining = old_qty + qty
                if remaining:
                    positions[symbol] = remaining, entry
                else:
                    positions.pop(symbol)
            else:
                total = old_qty + qty
                positions[symbol] = total, (entry * abs(old_qty) + price * abs(qty)) / abs(total)
            cash -= fee
        fees += fee
        slips += abs(qty) * abs(price - raw)
        turnover += abs(qty) * price
    fund = Decimal(0)
    for event in run["funding_events"]:
        amount = -Decimal(event["signed_qty"]) * Decimal(event["mark"]) * Decimal(event["rate"])
        if amount != Decimal(event["amount"]):
            raise DataIntegrityError("saved funding mismatch")
        fund += amount
    cash += fund
    equity = cash
    for i, symbol in enumerate(p.SYMBOLS):
        equity += spot[symbol] * Decimal(run["terminal_spot_prices"][i])
        qty, entry = positions.get(symbol, (Decimal(0), Decimal(0)))
        equity += qty * (Decimal(run["terminal_perp_prices"][i]) - entry)
    errors = [
        abs(cash - Decimal(run["terminal_cash"])),
        abs(equity - Decimal(run["terminal_equity"])),
        abs(fees - Decimal(run["fees"])),
        abs(slips - Decimal(run["slips"])),
        abs(fund - Decimal(run["funding"])),
        abs(turnover - Decimal(run["turnover"])),
    ]
    for i, symbol in enumerate(p.SYMBOLS):
        errors.append(abs(spot[symbol] - Decimal(run["terminal_spot_qty"][i])))
        saved = run["terminal_positions"].get(str(i))
        expected = Decimal(saved["qty"]) * int(saved["side"]) if saved else Decimal(0)
        errors.append(abs(positions.get(symbol, (Decimal(0), Decimal(0)))[0] - expected))
    if max(errors) > Decimal("0.0000000001"):
        raise DataIntegrityError(f"saved AG accounting errors: {errors}")
    return {"saved_fill_reconciliation": "MATCH", "max_error": max(errors), "strategy_replays": 0}


def metrics(run: dict[str, Any]) -> dict[str, Any]:
    trace = run["trace"]
    daily = [p.INITIAL, *(Decimal(x["equity"]) for x in trace if x["at_ms"] % p.DAY == 0)]
    daily[-1] -= Decimal(run["terminal_cost_reserve"])
    changes = [float(b / a - 1) for a, b in pairwise(daily) if a > 0]
    sd = statistics.stdev(changes) if len(changes) > 1 else 0
    monthly: dict[str, dict[str, Any]] = {}
    annual: dict[str, dict[str, Any]] = {}
    opening = year_open = p.INITIAL
    for index, point in enumerate(trace):
        month = datetime.fromtimestamp((point["at_ms"] - 1) / 1000, UTC).strftime("%Y-%m")
        ending = Decimal(point["equity"])
        if index == len(trace) - 1:
            ending -= Decimal(run["terminal_cost_reserve"])
        following = (
            datetime.fromtimestamp((trace[index + 1]["at_ms"] - 1) / 1000, UTC).strftime("%Y-%m")
            if index + 1 < len(trace)
            else None
        )
        if following != month:
            monthly[month] = {
                "pnl": ending - opening,
                "return": float(ending / opening - 1) if opening > 0 else None,
            }
            opening = ending
        if following is None or following[:4] != month[:4]:
            annual[month[:4]] = {
                "pnl": ending - year_open,
                "return": float(ending / year_open - 1) if year_open > 0 else None,
            }
            year_open = ending
    result = {
        k: v
        for k, v in run.items()
        if k not in ("trace", "fills", "trades", "funding_events", "sizing", "returns")
    }
    shorts = [t for t in run["trades"] if t["side"] == "SHORT"]
    final = p.INITIAL + Decimal(run["net"])
    sized = [f["sizing"] for f in run["fills"] if f.get("sizing") is not None]
    result.update(
        cagr=float(final / p.INITIAL) ** (365.25 * p.DAY / (END - START)) - 1
        if final > 0
        else None,
        sharpe=statistics.fmean(changes) / sd * math.sqrt(365.25) if sd > 0 else None,
        monthly=monthly,
        annual=annual,
        worst_month=min(
            monthly,
            key=lambda k: monthly[k]["return"] if monthly[k]["return"] is not None else -math.inf,
        ),
        short_roundtrips=len(shorts),
        short_losing_roundtrips=sum(Decimal(t["net"]) < 0 for t in shorts),
        short_loss_sum=sum((min(Decimal(0), Decimal(t["net"])) for t in shorts), Decimal(0)),
        worst_short_net=min((Decimal(t["net"]) for t in shorts), default=Decimal(0)),
        closed_short_net=sum((Decimal(t["net"]) for t in shorts), Decimal(0)),
        short_by_coin={
            s: sum((Decimal(t["net"]) for t in shorts if t["symbol"] == s), Decimal(0))
            for s in p.SYMBOLS
        },
        short_by_year={
            str(y): sum(
                (
                    Decimal(t["net"])
                    for t in shorts
                    if datetime.fromtimestamp(t["exit_ms"] / 1000, UTC).year == y
                ),
                Decimal(0),
            )
            for y in range(2020, 2027)
        },
        spot_fill_pieces=sum(f["asset"] == "SPOT" for f in run["fills"]),
        all_fill_pieces=len(run["fills"]),
        turnover_initial=float(Decimal(run["turnover"]) / p.INITIAL),
        first_short_halt_ms=next((t["at_ms"] for t in trace if t["short_halted"]), None),
        account_stop_ms=next((t["at_ms"] for t in trace if t["account_stopped"]), None),
        maximum_entry_short_margin_pct=max(
            (
                Decimal(s["margin_total_after"]) / Decimal(s["equity_after"])
                for s in sized
                if Decimal(s["equity_after"]) > 0
            ),
            default=Decimal(0),
        ),
        maximum_entry_short_risk_pct=max(
            (
                Decimal(s["risk"]) / Decimal(s["equity_after"])
                for s in sized
                if Decimal(s["equity_after"]) > 0
            ),
            default=Decimal(0),
        ),
        mean_entry_short_notional_pct=statistics.fmean(
            float(Decimal(s["notional"]) / Decimal(s["equity_before"])) for s in sized
        )
        if sized
        else None,
        return_requests=len(run["returns"]),
        return_first_buys=sum("first_buy_ms" in r for r in run["returns"]),
        mean_return_delay_min=statistics.fmean(
            (r["first_buy_ms"] - r["requested_ms"]) / p.MINUTE
            for r in run["returns"]
            if "first_buy_ms" in r
        )
        if any("first_buy_ms" in r for r in run["returns"])
        else None,
        independent_oos=0,
        adoption="UNVERIFIED",
        liquidation_tier="NOT_MODELED_NOT_CERTIFIED",
    )
    return result


def pilot() -> None:
    if any((OUT / f).exists() for f in ("run_started.json", "result.json")):
        raise DataIntegrityError("AG pilot already started; no rerun")
    if not (OUT / "audit.json").exists():
        raise DataIntegrityError("AG audit required")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True
    )
    if dirty:
        raise DataIntegrityError("AG code/contract must be committed")
    manifest, features = source()
    frozen = {f: digest(ROOT / f) for f in FILES}
    metadata = {
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "code_sha256": data.payload_hash(frozen),
        "files": frozen,
        "input_id": INPUT_ID,
        "started_utc": datetime.now(UTC).isoformat(),
        "cases": len(p.cases()),
        "approved_retrospective_only": True,
        "initial_invalid_result_sha256": FIRST_RESULT_SHA,
        "correction_scope": "DAILY_GOAL_EVENT_CARRY_ONLY_PARAMETERS_UNCHANGED",
    }
    save("run_started.json", metadata)
    runs = p.simulate(
        data.blocks(SOURCE / "aligned", manifest, features, progress=True), end_ms=END
    )
    summaries = {}
    for key, run in runs.items():
        name = "ledger_" + key.replace(":", "_") + ".json"
        save(name, run)
        saved = json.loads((OUT / name).read_text(encoding="utf8"))
        summaries[key] = {
            **metrics(saved),
            "independent_accounting": reconcile(saved),
            "ledger": name,
            "ledger_sha256": digest(OUT / name),
        }
    comparisons: list[dict[str, Any]] = []
    for exposure in (3, 4):
        for scenario in p.SCENARIOS:
            base, one, maximum = (
                summaries[f"AG{exposure}:{m}:{scenario.name}"]
                for m in ("BASE", "SHORT_1X", "SHORT_MAX")
            )
            comparisons.append(
                {
                    "exposure": exposure,
                    "scenario": scenario.name,
                    "1x_minus_base": Decimal(one["net"]) - Decimal(base["net"]),
                    "max_minus_base": Decimal(maximum["net"]) - Decimal(base["net"]),
                    "max_minus_1x": Decimal(maximum["net"]) - Decimal(one["net"]),
                    "max_mdd_minus_1x": Decimal(maximum["mdd"]) - Decimal(one["mdd"]),
                }
            )
    verdicts = {}
    for exposure in (3, 4):
        rows = [
            r
            for r in comparisons
            if r["exposure"] == exposure and r["scenario"] in ("BASE", "COST2")
        ]
        for mode in ("1x", "max"):
            deltas = [r[f"{mode}_minus_base"] for r in rows]
            label = "SHORT_1X" if mode == "1x" else "SHORT_MAX"
            observed = sum(
                summaries[f"AG{exposure}:{label}:{scenario}"]["short_roundtrips"]
                for scenario in ("BASE", "COST2")
            )
            verdicts[f"AG{exposure}:{mode}"] = (
                "판단불가_숏체결0"
                if observed == 0
                else "후향 기여 유리"
                if all(d > 0 for d in deltas)
                else "후향 기여 불리"
                if all(d <= 0 for d in deltas)
                else "혼재"
            )
    result = {
        **metadata,
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
                "cases": len(summaries),
                "verdicts": verdicts,
                "sha256": digest(OUT / "result.json"),
            },
            ensure_ascii=True,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("audit", "pilot"))
    args = parser.parse_args()
    if args.mode == "audit":
        audit()
    else:
        pilot()


if __name__ == "__main__":
    main()
