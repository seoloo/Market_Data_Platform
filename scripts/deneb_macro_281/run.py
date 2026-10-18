"""Single approved retrospective run. Never opens an exchange or operation state."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Mapping
from decimal import Decimal
from importlib.metadata import version
from typing import Any

from scripts.deneb_macro_281.account import simulate
from scripts.deneb_macro_281.data_audit import DATA, OUT, REPO
from scripts.deneb_macro_281.models import metrics, predict
from scripts.deneb_macro_281.power_audit import load_prefix

PERIODS = {
    "validation": ("2017-01-01", "2019-12-31"),
    "historical_confirmation": ("2020-01-01", "2026-08-31"),
}


def prices() -> dict[str, Any]:
    from scripts.run_brake_183 import signals

    result: dict[str, Any] = {}
    for name, path in (
        ("lev", "lev_div_181/TQQQ.csv"),
        ("div", "div_mix_180/SCHD.csv"),
        ("qqq", "div_mix_180/QQQ.csv"),
    ):
        result[name], _ = load_prefix(REPO / "data/research" / path, "2026-08-31")
    result["days"] = sorted(set(result["lev"]) & set(result["div"]) & set(result["qqq"]))
    result["vol"], result["trend"] = signals(sorted(result["qqq"]), result["qqq"])
    return result


def compact(account: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in account.items()
        if k not in ("ledger", "orders", "events", "navs", "nav_days")
    }


def attribution(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    gains = {"L": Decimal(0), "D": Decimal(0)}
    avoided = missed = Decimal(0)
    saved_down = missed_up = other = Decimal(0)
    for a, c in zip(base["ledger"], candidate["ledger"], strict=True):
        if a["day"] != c["day"]:
            raise ValueError("Mismatched ledger days")
        for s in gains:
            delta = Decimal(c["price_gain_" + s]) - Decimal(a["price_gain_" + s])
            gains[s] += delta
            if s == "L":
                avoided += max(delta, Decimal(0))
                missed += min(delta, Decimal(0))
                if Decimal(a["price_gain_L"]) < 0 and delta > 0:
                    saved_down += delta
                elif Decimal(a["price_gain_L"]) > 0 and delta < 0:
                    missed_up -= delta
                else:
                    other += delta
    extra_fee = Decimal(candidate["fees_krw"]) - Decimal(base["fees_krw"])
    extra_tax = sum(
        Decimal(candidate[k]) - Decimal(base[k])
        for k in ("taxes_paid_krw", "combined_final_tax_krw")
    )
    delta = Decimal(candidate["after_tax_liquidation_krw"]) - Decimal(
        base["after_tax_liquidation_krw"]
    )
    error = delta - (sum(gains.values()) - extra_fee - extra_tax)
    if abs(error) > Decimal("0.00001"):
        raise ValueError(f"Incremental contribution reconciliation: {error}")
    return {
        "increment_krw": str(delta),
        "TQQQ_price_contribution_krw": str(gains["L"]),
        "SCHD_price_contribution_krw": str(gains["D"]),
        "positive_TQQQ_difference_krw": str(avoided),
        "negative_TQQQ_difference_krw": str(missed),
        "saved_down_price_path_krw": str(saved_down),
        "missed_up_price_path_krw": str(missed_up),
        "other_TQQQ_price_path_difference_krw": str(other),
        "extra_fee_krw": str(extra_fee),
        "extra_tax_krw": str(extra_tax),
        "reconciliation_error_krw": str(error),
        "positive_negative_are_position_path_differences_not_pure_causal_saved_losses": True,
    }


def main(*, resume_saved: bool = False) -> dict[str, Any]:
    target = OUT / "pilot_result.json"
    marker = OUT / "pilot_attempt.json"
    if target.exists() or (marker.exists() and not resume_saved):
        raise FileExistsError("This fixed pilot already started; do not silently rerun")
    code = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "scripts/deneb_macro_281",
            "docs/research/281-deneb-macro-risk-pilot.md",
        ],
        cwd=REPO,
        check=True,
    )
    panel_path = DATA / "panel.json"
    panel = json.loads(panel_path.read_text(encoding="utf-8"))
    attempt = {
        "code_commit": code,
        "panel_sha256": hashlib.sha256(panel_path.read_bytes()).hexdigest(),
        "prereg_sha256": hashlib.sha256(
            (REPO / "docs/research/281-deneb-macro-risk-pilot.md").read_bytes()
        ).hexdigest(),
    }
    if resume_saved:
        if (OUT / "pilot_resume.json").exists():
            raise FileExistsError("Saved-model recovery has already started")
        checkpoint = json.loads((OUT / "saved_checkpoint.json").read_text(encoding="utf-8"))
        for key in ("panel_sha256", "prereg_sha256"):
            if checkpoint["original_attempt"][key] != attempt[key]:
                raise ValueError("Resume would change frozen inputs or preregistration")
        for name, digest in checkpoint["sha256"].items():
            if hashlib.sha256((REPO / name).read_bytes()).hexdigest() != digest:
                raise ValueError("Saved model changed before resume")
        if checkpoint["model_count"] != 30 or (DATA / "pilot_ledgers.json").exists():
            raise ValueError("Resume is permitted only from complete pre-PnL checkpoint")
        (OUT / "pilot_resume.json").write_text(
            json.dumps(
                {
                    **attempt,
                    "model_fits_this_run": 0,
                    "original_training_code": checkpoint["original_attempt"]["code_commit"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        marker.write_text(
            json.dumps(
                {
                    "code_commit": code,
                    "started_ms": time.time_ns() // 1_000_000,
                    "panel_sha256": hashlib.sha256(panel_path.read_bytes()).hexdigest(),
                    "prereg_sha256": hashlib.sha256(
                        (REPO / "docs/research/281-deneb-macro-risk-pilot.md").read_bytes()
                    ).hexdigest(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    data = prices()
    started = time.monotonic()
    predictions, fits = predict(panel["rows"], replay_saved=resume_saved)
    fits_seconds = time.monotonic() - started
    if fits_seconds > 900:
        raise TimeoutError("Fixed fifteen-minute fitting budget exceeded")
    constant = 1 - 0.5 * predictions[0]["base"]
    all_accounts: dict[str, Any] = {}
    comparisons: dict[str, Any] = {}
    reports: dict[str, Any] = {}
    for period, (start, end) in PERIODS.items():
        all_accounts[period], comparisons[period], reports[period] = {}, {}, {}
        for sensitivity, fee, delay in (
            ("base", "0.0015", 0),
            ("cost2", "0.0030", 0),
            ("delay1", "0.0015", 1),
        ):
            books = {}
            for name, mode in (("A_DENEB", "NONE"), ("B_STATIC", "STATIC"), ("C_MACRO", "MACRO")):
                book = simulate(
                    data,
                    predictions,
                    start=start,
                    end=end,
                    mode=mode,
                    constant=constant,
                    fee=fee,
                    delay=delay if mode == "MACRO" else 0,
                )
                books[name] = book
            all_accounts[period][sensitivity] = books
            reports[period][sensitivity] = {k: compact(v) for k, v in books.items()}
            comparisons[period][sensitivity] = {
                "C_minus_A": attribution(books["A_DENEB"], books["C_MACRO"]),
                "C_minus_B": attribution(books["B_STATIC"], books["C_MACRO"]),
            }
    h = comparisons["historical_confirmation"]
    regular = all_accounts["historical_confirmation"]["base"]
    positive = all(Decimal(h[s]["C_minus_A"]["increment_krw"]) > 0 for s in h)
    favorable = (
        positive
        and Decimal(h["base"]["C_minus_B"]["increment_krw"]) > 0
        and (
            regular["C_MACRO"]["tax_payment_nav"]["mdd"]
            <= regular["A_DENEB"]["tax_payment_nav"]["mdd"]
        )
    )
    verdict = (
        "후향 파일럿에서 유리"
        if favorable
        else (
            "후향 파일럿에서 불리"
            if Decimal(h["base"]["C_minus_A"]["increment_krw"]) <= 0
            else "판단 불가"
        )
    )
    result = {
        "research": 281,
        "code_commit": code,
        "resumed_saved_models": resume_saved,
        "model_fits_this_run": 0 if resume_saved else 30,
        "status": "EXPLANATORY_RETROSPECTIVE_ONLY",
        "adoption": "UNVERIFIED",
        "verdict": verdict,
        "fits_seconds": fits_seconds,
        "constant_multiplier": constant,
        "libraries": {name: version(name) for name in ("numpy", "scikit-learn", "joblib")},
        "monthly_contribution_krw": 100_000,
        "predictions": predictions,
        "fits": fits,
        "accounts": reports,
        "comparisons": comparisons,
        "prediction_metrics": {k: metrics(predictions, a, b) for k, (a, b) in PERIODS.items()},
        "limitations": [
            "18 or fewer initial labeled training months; 80% power not established",
            "Historical data reused; not independent OOS",
            "Fractional adjusted-price accounting, not live execution",
            "Fixed FX and dividend cash/withholding approximation",
            "Proportional tax payment approximation",
            "No Paper/Live approval, operational risk and HALT parity unverified",
        ],
    }
    # Save full ledgers once, without recomputing outcomes for presentation.
    ledger_path = DATA / "pilot_ledgers.json"
    ledger_path.write_text(
        json.dumps(all_accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result["ledgers_sha256"] = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
    result["ledgers_path"] = ledger_path.relative_to(REPO).as_posix()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": verdict,
                "accounts": reports["historical_confirmation"]["base"],
                "comparison": h,
                "prediction_metrics": result["prediction_metrics"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return result


if __name__ == "__main__":
    import sys

    main(resume_saved="--resume-saved" in sys.argv)
