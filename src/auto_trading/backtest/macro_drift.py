"""거시 지표 발표 직후 반응의 지속·반전 — 이벤트 탐지 (docs/research/49). 순수 함수, I/O 없음.

발표 시각 ``t`` (CPI·NFP·PCE 08:30 ET, FOMC 성명 14:00 ET)에서:

1. 첫 반응 = ``t`` 부터 ``window_bars`` 개 5분봉(기본 15분)의 수익 — ``t`` 봉 시가 → 마지막 봉 종가.
2. 반응이 0 이 아니면 이벤트. ``side`` = 반응 방향(+1 상승 / −1 하락).
3. 진입은 반응 창 다음 봉 시가(``t + 15분``).
   거래 방향(지속 = ``side``, 반전 = ``−side``)은 판정 규칙이 정한다.
4. 구조 극값 = 반응 창의 최고·최저가 — 롱의 스톱은 최저가, 숏의 스톱은 최고가
   (:func:`auto_trading.backtest.perp_events.settle_trade` 가 그대로 쓴다).

미래 자료 없음: 이벤트는 반응 창 마지막 봉 마감까지의 자료로 정하고 다음 봉 시가에 들어간다.
발표 시각이 5분 격자에 없거나 앞뒤 자료가 모자라면 그 발표는 건너뛴다(결측을 채우지 않는다).
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence

from auto_trading.backtest.perp_events import BAR_MS, Bars, PerpEvent

__all__ = ["reaction_events"]


def reaction_events(
    bars: Bars,
    release_ms: Sequence[int],
    *,
    window_bars: int = 3,
    hold_bars: int = 48,
) -> list[PerpEvent]:
    """발표마다 첫 반응 이벤트. 앞 거래(진입 + ``hold_bars``)와 겹치는 발표는 건너뛴다."""
    if window_bars < 1:
        raise ValueError(f"window_bars 는 1 이상이어야 한다: {window_bars}")
    events: list[PerpEvent] = []
    busy_until = -1
    for t in sorted(release_ms):
        if t % BAR_MS:
            continue
        i = bisect.bisect_left(bars.open_ms, t)
        if i >= len(bars.open_ms) or bars.open_ms[i] != t:
            continue
        last = i + window_bars - 1
        entry = last + 1
        if entry + hold_bars > len(bars.open_ms) or i <= busy_until:
            continue
        move = bars.close[last] / bars.open[i] - 1
        if move == 0:
            continue
        side = 1 if move > 0 else -1
        window = slice(i, last + 1)
        events.append(
            PerpEvent("MD", last, entry, side, max(bars.high[window]), min(bars.low[window]))
        )
        busy_until = entry + hold_bars - 1
    return events
