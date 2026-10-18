"""회귀 예측 지표 · 손실 차 Newey-West 검정 (numpy 만, 모델 선택에 쓰지 않음)."""

from __future__ import annotations

from typing import Any

NW_LAG = 6


def rank(x: Any) -> Any:
    import numpy as np  # type: ignore[import-not-found]

    r = np.empty(len(x))
    r[np.argsort(x, kind="mergesort")] = np.arange(len(x))
    return r


def spearman(a: Any, b: Any) -> float | None:
    import numpy as np

    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(rank(a), rank(b))[0, 1])


def regression(y: Any, p: Any, mean: float) -> dict[str, Any]:
    import numpy as np

    ok = np.isfinite(y)
    y, p = y[ok], p[ok]
    e = y - p
    mse = float(np.mean(e**2))
    nz = y != 0
    return {
        "n": len(y),
        "mse": mse,
        "mae": float(np.mean(np.abs(e))),
        "r2_vs_mean": 1 - mse / float(np.mean((y - mean) ** 2)),
        "r2_vs_zero": 1 - mse / float(np.mean(y**2)),
        "direction_hit": float(np.mean(np.sign(p[nz]) == np.sign(y[nz]))),
        "ic": spearman(p, y),
        "pred_sd": float(np.std(p)),
        "pred_quantiles": [float(v) for v in np.quantile(p, [0.01, 0.1, 0.5, 0.9, 0.99])],
    }


def nw_t(d: Any, lag: int = NW_LAG) -> dict[str, float]:
    """평균 > 0 이면 TCN 손실이 작다. Bartlett 가중 HAC."""
    import numpy as np

    d = np.asarray(d, dtype=float)
    c = d - d.mean()
    var = float(np.dot(c, c) / len(d))
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * float(np.dot(c[k:], c[:-k]) / len(d))
    se = max(var, 0.0) ** 0.5 / len(d) ** 0.5
    return {"mean": float(d.mean()), "se": se, "t": float(d.mean()) / se if se > 0 else 0.0}


def loss_diff_by_time(times: Any, y: Any, base: Any, model: Any) -> Any:
    """시각별 자산 평균 (e_base² − e_model²), 시각 순서."""
    import numpy as np

    ok = np.isfinite(y)
    t, d = times[ok], (y[ok] - base[ok]) ** 2 - (y[ok] - model[ok]) ** 2
    _uniq, inv = np.unique(t, return_inverse=True)
    return np.bincount(inv, weights=d) / np.bincount(inv)


def calibration(y: Any, p: Any, bins: int = 10) -> list[dict[str, float]]:
    import numpy as np

    ok = np.isfinite(y)
    y, p = y[ok], p[ok]
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for k in range(bins):
        hit = (p >= edges[k]) & ((p <= edges[k + 1]) if k == bins - 1 else (p < edges[k + 1]))
        if hit.any():
            out.append(
                {"n": int(hit.sum()), "pred": float(p[hit].mean()), "actual": float(y[hit].mean())}
            )
    return out


def halves(times: Any) -> tuple[Any, Any]:
    """고유 시각을 반으로 나눈 앞/뒤 행 마스크."""
    import numpy as np

    uniq = np.unique(times)
    cut = uniq[len(uniq) // 2]
    return times < cut, times >= cut
