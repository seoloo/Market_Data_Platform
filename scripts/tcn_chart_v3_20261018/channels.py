"""V3 새 입력 채널: 4 시간봉 매수 체결량 비율 · 마감 때 알려진 마지막 펀딩비."""

from __future__ import annotations

from typing import Any

H4 = 14_400_000
BAR_MINUTES = 240
WINDOW = 96


def taker_imbalance(taker: Any, volume: Any) -> Any:
    """봉별 (매수 체결량 / 거래량) − 0.5. 거래량 0 이면 NaN."""
    import numpy as np  # type: ignore[import-not-found]

    taker, volume = np.asarray(taker, float), np.asarray(volume, float)
    out = np.full(len(volume), np.nan)
    ok = volume > 0
    out[ok] = taker[ok] / volume[ok] - 0.5
    return out


def funding_last(bar_end_ms: Any, funding: Any) -> Any:
    """정산 시각 ≤ 봉 마감 시각인 마지막 rate. funding 행 = [시각, rate, ...] 오름차순."""
    import numpy as np

    k = np.searchsorted(funding[:, 0], np.asarray(bar_end_ms, float), side="right") - 1
    return np.where(k >= 0, funding[np.maximum(k, 0), 1], np.nan)


def extra(times: Any, symbols: Any, per_symbol: list[Any], h4s: list[Any]) -> Any:
    """행마다 96 봉 × 2 채널. per_symbol[s] = 봉별 (taker_imbalance, funding_last) 배열 (n, 2)."""
    import numpy as np

    index = [{int(row[0]): k for k, row in enumerate(h)} for h in h4s]
    out = np.full((len(times), WINDOW, 2), np.nan, dtype=np.float64)
    for r, (t, s) in enumerate(zip(times, symbols, strict=True)):
        i = index[int(s)].get(int(t) - H4)
        if i is not None and i >= WINDOW - 1:
            out[r] = per_symbol[int(s)][i - WINDOW + 1 : i + 1]
    return out
