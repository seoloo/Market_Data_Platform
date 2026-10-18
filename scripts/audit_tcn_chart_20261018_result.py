"""Audit saved artifacts and diagnose labels; never re-train or select a new model."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from collections import Counter
from decimal import Decimal
from typing import Any

from scripts.tcn_chart_20261018 import data, execution, features, metrics, models


def main() -> None:
    import numpy as np
    import torch

    source = data.OUT / "result.json"
    result_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    result = json.loads(source.read_text(encoding="utf-8"))
    for name, digest in result["artifact_sha256"].items():
        if hashlib.sha256((data.OUT / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"artifact changed: {name}")
    with np.load(data.OUT / "dataset_v2.npz", allow_pickle=False) as f:
        ds = dict(f)
    with np.load(data.OUT / "predictions.npz", allow_pickle=False) as f:
        probabilities = dict(f)
    with np.load(data.OUT / "scaler.npz", allow_pickle=False) as f:
        mean, sd = f["mean"], f["sd"]
    expected_mu, expected_sd = models.scaler(ds["x"][ds["splits"] == 0])
    np.testing.assert_array_equal(mean, expected_mu)
    np.testing.assert_array_equal(sd, expected_sd)
    torch.set_num_threads(4)
    model = models.network().eval()
    model.load_state_dict(torch.load(data.OUT / "tcn.pt", map_location="cpu", weights_only=True))
    x = models.normalize(ds["x"][:256], mean, sd)
    cpu = models.predict(model, x, "cpu")
    error = float(np.max(np.abs(cpu - probabilities["TCN"][:256])))
    if error > 1e-6:
        raise ValueError("saved CUDA/CPU inference mismatch")
    out: dict[str, Any] = {
        "result_sha256": result_hash,
        "checkpoint_cpu_gpu_max_error": error,
        "scaler_train_only_exact": True,
        "prediction_counts": {},
        "label_coverage": {},
        "account_checks": {},
        "posthoc_label_diagnostic": {},
        "environment": {
            p: importlib.metadata.version(p)
            for p in ("numpy", "torch", "scikit-learn", "matplotlib", "scipy")
        },
    }
    for period, name in enumerate(("train", "validation", "holdout")):
        mask = ds["splits"] == period
        out["prediction_counts"][name] = {
            k: np.bincount(metrics.decisions(p)[mask], minlength=3).tolist()
            for k, p in probabilities.items()
        }
        out["label_coverage"][name] = {
            "all_predictions": int(mask.sum()),
            "known_labels": int(np.count_nonzero(mask & (ds["y"] >= 0))),
            "unknown_labels_retained": int(np.count_nonzero(mask & (ds["y"] < 0))),
        }
    for key, r in result["accounts"].items():
        period = ("train", "validation", "holdout").index(key.split("/")[0])
        if r["n_trades"] + sum(r["veto"].values()) != int(np.count_nonzero(ds["splits"] == period)):
            raise ValueError("account opportunity omitted")
        trade_net = math.fsum(t["net"] for t in r["trades"])
        errors = [
            abs(trade_net - r["net"]),
            abs(r["gross"] - r["fee"] + r["funding"] - r["net"]),
            abs(r["daily_equity"][-1] - r["final"]),
            abs(10000 * math.prod(1 + x for x in r["monthly"].values()) - r["final"]),
        ]
        if max(errors) > 1e-6:
            raise ValueError(f"cash/fee/funding/monthly mismatch: {key}")
        before = 10000.0
        previous_exit = -3600000
        failed = False
        for t in r["trades"]:
            if t["entry_notional"] > before * 0.1 + 1e-6:
                raise ValueError("entry margin cap")
            if t["entry_ms"] < previous_exit + 3600000 or failed:
                raise ValueError("overlap, cooldown or unresolved halt violated")
            if Decimal(t["qty"]) % Decimal("0.001"):
                raise ValueError("quantity step")
            previous_exit = t["exit_ms"]
            before += t["net"]
            failed = t["was_unresolved"]
        out["account_checks"][key] = {
            "max_reconciliation_error_usdt": max(errors),
            "all_feature_rows_accounted_for": True,
            "margin_overlap_halt_checked": True,
            "unresolved": [t for t in r["trades"] if t["was_unresolved"]],
        }
    # Explanatory diagnosis only: fixed original 1,000-USDT label episodes, not a
    # new candidate or a second account-performance evaluation.
    for s, symbol in enumerate(data.SYMBOLS):
        d = data.load(symbol)
        counts: Counter[str] = Counter()
        for k in np.flatnonzero((ds["splits"] == 2) & (ds["symbols"] == s)):
            e = [
                execution.simulate(d["minutes"], d["funding"], int(ds["times"][k]), side, symbol)
                for side in (1, -1)
            ]
            if any(x.status in ("UNRESOLVED", "DATA_INCOMPLETE") for x in e):
                counts["unknown"] += 1
                if ds["y"][k] != -1:
                    raise ValueError("unknown label was encoded as an action")
                continue
            returns = [
                float(x.net / (x.entry * x.qty)) if x.status == "CLOSED" else None for x in e
            ]
            y = features.classify(*returns)
            if y != ds["y"][k]:
                raise ValueError("saved label does not match frozen path")
            if y == 0:
                counts[
                    "flat_no_entry_either_side"
                    if all(x.qty == 0 for x in e)
                    else "flat_filled_but_no_20bp_edge"
                ] += 1
            else:
                counts["long" if y == 1 else "short"] += 1
        out["posthoc_label_diagnostic"][symbol] = dict(counts)
    if hashlib.sha256(source.read_bytes()).hexdigest() != result_hash:
        raise ValueError("original result changed")
    data.save("posthoc_audit.json", out)
    print(
        json.dumps(
            {
                "audit": "OK",
                "original_result_unchanged": True,
                "cpu_gpu_error": error,
                "labels": out["posthoc_label_diagnostic"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
