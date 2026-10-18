"""V2 정답: 순수 24 시간 로그수익률. 체결 · 비용 · 손절은 넣지 않는다."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

H4 = 14_400_000
STEPS = 6  # 4 시간봉 6 개 = 24 시간


def log_return_24h(h4: Sequence[Sequence[float]], i: int) -> float:
    """봉 i(마감 = 결정 시각) 종가 → 봉 i+6 종가. 연속이 아니거나 가격 ≤ 0 이면 NaN."""
    j = i + STEPS
    if j >= len(h4) or h4[j][0] != h4[i][0] + STEPS * H4:
        return math.nan
    if h4[i][4] <= 0 or h4[j][4] <= 0:
        return math.nan
    return math.log(h4[j][4] / h4[i][4])


def targets(times: Any, symbols: Any, h4s: Sequence[Any]) -> Any:
    """행(결정 시각 t, 자산 번호)마다 y. t 는 봉 i 의 끝 = 시가 + 4h."""
    import numpy as np  # type: ignore[import-not-found]

    index = [{int(row[0]): k for k, row in enumerate(h)} for h in h4s]
    out = np.full(len(times), np.nan)
    for k, (t, s) in enumerate(zip(times, symbols, strict=True)):
        i = index[int(s)].get(int(t) - H4)
        if i is not None:
            out[k] = log_return_24h(h4s[int(s)], i)
    return out
