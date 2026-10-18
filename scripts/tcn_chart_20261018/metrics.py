"""Prediction/account diagnostics, without any model-selection feedback."""

from __future__ import annotations

from typing import Any


def classification(y: Any, p: Any) -> dict[str, Any]:
    import numpy as np
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, log_loss

    pred = p.argmax(axis=1)
    conf = p.max(axis=1)
    bins = []
    for k in range(10):
        hit = (conf >= k / 10) & (conf <= 1 if k == 9 else conf < (k + 1) / 10)
        if hit.any():
            bins.append(
                {
                    "lo": k / 10,
                    "n": int(hit.sum()),
                    "confidence": float(conf[hit].mean()),
                    "accuracy": float(np.mean(pred[hit] == y[hit])),
                }
            )
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "report": classification_report(
            y, pred, labels=[0, 1, 2], output_dict=True, zero_division=0
        ),
        "confusion": confusion_matrix(y, pred, labels=[0, 1, 2]).tolist(),
        "log_loss": float(log_loss(y, p, labels=[0, 1, 2])),
        "brier": float(np.mean(np.sum((p - np.eye(3)[y]) ** 2, axis=1))),
        "calibration": bins,
        "ece": sum(b["n"] * abs(b["accuracy"] - b["confidence"]) for b in bins) / len(y),
    }


def paired(a: dict[str, float], b: dict[str, float]) -> dict[str, float | int]:
    import numpy as np

    keys = sorted(set(a) & set(b))
    d = np.asarray([a[k] - b[k] for k in keys])
    centered = d - d.mean()
    var = float(np.dot(centered, centered) / len(d))
    for lag in range(1, 4):
        var += 2 * (1 - lag / 4) * float(np.dot(centered[lag:], centered[:-lag]) / len(d))
    se = max(0, var / len(d)) ** 0.5
    return {
        "months": len(d),
        "mean": float(d.mean()),
        "se_hac3": se,
        "lower_2se": float(d.mean()) - 2 * se,
        "upper_2se": float(d.mean()) + 2 * se,
        "first_half": float(d[: len(d) // 2].mean()),
        "second_half": float(d[len(d) // 2 :].mean()),
    }
