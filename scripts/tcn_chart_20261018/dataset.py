"""One frozen supervised target from actual future paths, never input to features."""

from __future__ import annotations

from collections import Counter
from typing import Any

from scripts.tcn_chart_20261018 import data, execution, features


def build() -> dict[str, Any]:
    import numpy as np

    xs, ys, times, symbols, splits, rules, ranks = [], [], [], [], [], [], []
    forward_returns: list[float] = []
    audit: dict[str, Any] = {}
    for symbol in data.SYMBOLS:
        d = data.load(symbol)
        a, h4, funding = d["minutes"], d["h4"], d["funding"]
        f = np.asarray(features.calculate(h4.tolist()), dtype=np.float32)
        counts: Counter[str] = Counter()
        for i in range(features.WINDOW - 1, len(h4)):
            t = int(h4[i, 0]) + features.H4
            sp = features.split_id(t)
            if sp < 0:
                counts["outside_or_purged"] += 1
                continue
            window = f[i - 95 : i + 1]
            if not np.isfinite(window).all():
                counts["invalid_features"] += 1
                continue
            returns: list[float | None] = []
            valid = True
            for side in (1, -1):
                e = execution.simulate(a, funding, t, side, symbol)
                counts[e.status] += 1
                if e.status in ("DATA_INCOMPLETE", "UNRESOLVED"):
                    valid = False
                returns.append(float(e.net / (e.entry * e.qty)) if e.status == "CLOSED" else None)
            if not valid:
                counts["label_unknown"] += 1
            xs.append(window)
            # Keep the feature row for every prediction/account path. Only the supervised
            # target is missing; future execution eligibility must not filter the account.
            ys.append(features.classify(*returns) if valid else -1)
            times.append(t)
            symbols.append(data.SYMBOLS.index(symbol))
            splits.append(sp)
            rules.append(features.rule(f[i]))
            ranks.append(float(np.mean(h4[i - 19 : i + 1, 4] * h4[i - 19 : i + 1, 5])))
            forward_returns.append(
                float(h4[i + 6, 4] / h4[i, 4] - 1) if i + 6 < len(h4) else float("nan")
            )
        audit[symbol] = dict(counts)
        print({"dataset_asset": symbol, "counts": dict(counts)}, flush=True)
    result = {
        "x": np.asarray(xs),
        "y": np.asarray(ys, dtype=np.int64),
        "times": np.asarray(times, dtype=np.int64),
        "symbols": np.asarray(symbols),
        "splits": np.asarray(splits),
        "rules": np.asarray(rules),
        "ranks": np.asarray(ranks),
        "future_return_24h": np.asarray(forward_returns),
    }
    order = np.lexsort((result["symbols"], -result["ranks"], result["times"]))
    result = {k: v[order] for k, v in result.items()}
    np.savez_compressed(data.OUT / "dataset_v2.npz", **result)
    audit["split_labels"] = {
        str(s): np.bincount(
            result["y"][(result["splits"] == s) & (result["y"] >= 0)], minlength=3
        ).tolist()
        for s in (0, 1, 2)
    }
    audit["unknown_by_split"] = {
        str(s): int(np.count_nonzero((result["splits"] == s) & (result["y"] < 0)))
        for s in (0, 1, 2)
    }
    data.save("dataset_audit_v2.json", audit)
    return result
