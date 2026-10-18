"""고정 정책 P(τ): ŷ > τ 롱(1), ŷ < −τ 숏(2), 그 밖 관망(0). V1 account.replay 의 신호 형식."""

from __future__ import annotations

from typing import Any

TAU = 0.0019  # V1 통상 편도 0.095% × 2 (사전 고정)


def decide(pred: Any, tau: float = TAU) -> Any:
    import numpy as np  # type: ignore[import-not-found]

    p = np.asarray(pred, dtype=float)
    out = np.where(p > tau, 1, np.where(p < -tau, 2, 0))
    return np.where(np.isfinite(p), out, 0).astype(np.int64)


def selection(pred: Any, y: Any, mask: Any, tau: float = TAU) -> dict[str, Any]:
    """정책이 고른 시점의 방향 × y (총 24h 로그수익, 체결 · 비용 없음)."""
    import numpy as np

    d = decide(pred, tau)
    sign = np.where(d == 1, 1.0, np.where(d == 2, -1.0, 0.0))
    pick = mask & (d != 0) & np.isfinite(y)
    gross = sign[pick] * y[pick]
    return {
        "signals": int(pick.sum()),
        "long": int((pick & (d == 1)).sum()),
        "short": int((pick & (d == 2)).sum()),
        "mean_directional_log_return": float(gross.mean()) if pick.any() else None,
        "hit_rate": float(np.mean(gross > 0)) if pick.any() else None,
    }
