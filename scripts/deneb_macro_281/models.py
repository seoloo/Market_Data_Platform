"""Fixed expanding-window prediction; all transforms fit on matured train rows only."""

from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Sequence
from importlib import import_module
from typing import Any


def training_rows(rows: list[dict[str, Any]], cutoff: str) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r["label_end"] is not None and r["label_end"] < cutoff and r["label"] is not None
    ]


def fit_transform(
    train: Sequence[Sequence[float | None]],
    future: Sequence[Sequence[float | None]],
) -> tuple[list[list[float]], list[list[float]], dict[str, Any]]:
    if not train:
        raise ValueError("Empty preprocessing train data")
    medians: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    for j in range(len(train[0])):
        valid = sorted(float(v) for r in train if (v := r[j]) is not None and math.isfinite(v))
        medians.append(statistics.median(valid) if valid else 0.0)
        for q, destination in ((0.01, lower), (0.99, upper)):
            rank = (len(valid) - 1) * q
            lo, hi = math.floor(rank), math.ceil(rank)
            destination.append(
                valid[lo] * (hi - rank) + valid[hi] * (rank - lo)
                if valid and hi != lo
                else valid[lo]
                if valid
                else 0.0
            )

    def replace(rows: Sequence[Sequence[float | None]]) -> list[list[float]]:
        output = []
        for row in rows:
            missing = [v is None or not math.isfinite(v) for v in row]
            values = [
                medians[j] if v is None or not math.isfinite(v) else min(upper[j], max(lower[j], v))
                for j, v in enumerate(row)
            ]
            output.append(values + [float(v) for v in missing])
        return output

    at, bt = replace(train), replace(future)
    mean = [statistics.fmean(c) for c in zip(*at, strict=True)]
    sd = [statistics.pstdev(c) or 1.0 for c in zip(*at, strict=True)]

    def standardize(rows: list[list[float]]) -> list[list[float]]:
        return [[(v - mean[j]) / sd[j] for j, v in enumerate(r)] for r in rows]

    return (
        standardize(at),
        standardize(bt),
        {
            "median": medians,
            "lower": lower,
            "upper": upper,
            "mean": mean,
            "sd": sd,
        },
    )


def predict(
    rows: list[dict[str, Any]],
    *,
    replay_saved: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from scripts.deneb_macro_281.data_audit import DATA, REPO

    np = import_module("numpy")
    gbm_class = import_module("sklearn.ensemble").HistGradientBoostingClassifier
    lr_class = import_module("sklearn.linear_model").LogisticRegression
    threadpool_limits = import_module("threadpoolctl").threadpool_limits
    joblib = import_module("joblib")

    predictions, fits = [], []
    for year in range(2017, 2027):
        future = [r for r in rows if int(r["execute"][:4]) == year]
        if not future:
            continue
        cutoff = future[0]["as_of"]
        train = training_rows(rows, cutoff)
        if not train:
            raise ValueError("No mature historical training rows")
        y = np.asarray([r["label"] for r in train], dtype=int)
        base = (int(y.sum()) + 1) / (len(y) + 2)
        fit: dict[str, Any] = {
            "year": year,
            "cutoff": cutoff,
            "train_rows": len(train),
            "positive_labels": int(y.sum()),
            "base_probability": base,
            "last_train_label_end": max(r["label_end"] for r in train),
            "models": {},
        }
        probs = {"CONST": [base] * len(future)}
        with threadpool_limits(limits=4):
            for name, columns in (
                ("LR", list(range(13))),
                ("GBM", list(range(13))),
                ("PRICE", [10, 11, 12]),
            ):
                a = [[r["features"][j] for j in columns] for r in train]
                b = [[r["features"][j] for j in columns] for r in future]
                at, bt, preprocessing = fit_transform(a, b)
                if len(set(y.tolist())) < 2:
                    probs[name] = [base] * len(future)
                    fit["models"][name] = {
                        "status": "SINGLE_CLASS_CONSTANT",
                        "preprocessing": preprocessing,
                    }
                    continue
                model: Any
                if name == "GBM":
                    model = gbm_class(
                        max_iter=50,
                        max_depth=2,
                        learning_rate=0.05,
                        min_samples_leaf=10,
                        l2_regularization=10,
                        early_stopping=False,
                        random_state=281,
                    )
                else:
                    model = lr_class(C=0.1, max_iter=2000, class_weight=None, random_state=281)
                folder = DATA / "models"
                folder.mkdir(exist_ok=True)
                artifact = folder / f"{year}_{name}.joblib"
                if replay_saved:
                    model = joblib.load(artifact)
                else:
                    if artifact.exists():
                        raise FileExistsError("A frozen model artifact already exists")
                    model.fit(at, y)
                    joblib.dump(model, artifact)
                probs[name] = model.predict_proba(bt)[:, 1].tolist()
                replay = joblib.load(artifact).predict_proba(bt)[:, 1]
                if not np.allclose(replay, probs[name], rtol=0, atol=1e-12):
                    raise ValueError("Saved-model replay mismatch")
                fit["models"][name] = {
                    "status": "FITTED",
                    "preprocessing": preprocessing,
                    "coefficients": model.coef_.tolist() if name != "GBM" else None,
                    "intercept": model.intercept_.tolist() if name != "GBM" else None,
                    "artifact": artifact.relative_to(REPO).as_posix(),
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    "saved_replay_max_error": float(np.max(np.abs(replay - probs[name]))),
                }
        for i, row in enumerate(future):
            predictions.append(
                {
                    **{
                        k: row[k]
                        for k in (
                            "decision",
                            "as_of",
                            "execute",
                            "label",
                            "label_end",
                            "macro_missing",
                        )
                    },
                    "base": base,
                    "probabilities": {k: p[i] for k, p in probs.items()},
                }
            )
        fits.append(fit)
    return predictions, fits


def metrics(predictions: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    np = import_module("numpy")
    average_precision_score = import_module("sklearn.metrics").average_precision_score
    roc_auc_score = import_module("sklearn.metrics").roc_auc_score

    eligible = [r for r in predictions if start <= r["execute"] <= end and r["label"] is not None]
    y = np.asarray([r["label"] for r in eligible], dtype=int)
    result: dict[str, Any] = {"rows": len(y), "positive": int(y.sum()), "models": {}}
    for name in ("CONST", "LR", "GBM", "PRICE"):
        p = np.asarray([r["probabilities"][name] for r in eligible])
        q = np.clip(p, 1e-12, 1 - 1e-12)
        warning = p >= np.asarray([2 * r["base"] for r in eligible])
        tp = int(((y == 1) & warning).sum())
        result["models"][name] = {
            "brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))),
            "roc_auc": float(roc_auc_score(y, p)) if len(set(y.tolist())) > 1 else None,
            "pr_auc": float(average_precision_score(y, p)) if y.sum() else None,
            "warnings": int(warning.sum()),
            "true_positive": tp,
            "precision": tp / int(warning.sum()) if warning.sum() else None,
            "recall": tp / int(y.sum()) if y.sum() else None,
            "calibration": [
                {
                    "lo": a,
                    "hi": b,
                    "count": int(mask.sum()),
                    "mean_probability": float(p[mask].mean()) if mask.sum() else None,
                    "event_rate": float(y[mask].mean()) if mask.sum() else None,
                }
                for a, b in (
                    (0, 0.1),
                    (0.1, 0.2),
                    (0.2, 0.4),
                    (0.4, 0.6),
                    (0.6, 0.8),
                    (0.8, 1.000001),
                )
                if (mask := (p >= a) & (p < b)) is not None
            ],
        }
    if any(not math.isfinite(m["brier"]) for m in result["models"].values()):
        raise ValueError("Non-finite prediction metric")
    return result
