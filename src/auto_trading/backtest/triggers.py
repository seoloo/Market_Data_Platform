"""연구용 트리거 호환 경로. A1 정의는 features.a1과 동일한 함수를 재사용한다."""

from __future__ import annotations

from auto_trading.features.a1 import (
    BARS_PER_HOUR,
    CONTRACTION_QUANTILE,
    RANGE_BARS,
    RANK_WINDOW_BARS,
    TREND_BARS,
    VOL_WINDOW_BARS,
    Trigger,
    breakout_triggers,
    contraction_flags,
    rolling_vol,
)

__all__ = [
    "BARS_PER_HOUR",
    "CASCADE_MOVE_SD",
    "CASCADE_OI_DROP",
    "CASCADE_WINDOW",
    "CONTRACTION_QUANTILE",
    "RANGE_BARS",
    "RANK_WINDOW_BARS",
    "TREND_BARS",
    "VOL_WINDOW_BARS",
    "Trigger",
    "breakout_triggers",
    "cascade_triggers",
    "contraction_flags",
    "rolling_vol",
]

CASCADE_MOVE_SD = 3.0
"""30분 가격 변화가 직전 변동성의 몇 배여야 급변인가."""
CASCADE_OI_DROP = 0.005
"""같은 30분에 미결제약정이 이 비율 이상 감소."""
CASCADE_WINDOW = 6
"""30분 = 5분봉 6개."""


def cascade_triggers(
    times: list[int],
    closes: list[float],
    vol: list[float | None],
    open_interest: dict[int, float],
) -> tuple[list[Trigger], int]:
    """후보 B — 급변과 **동시에** 미결제약정이 줄어든 시점. 방향은 급변의 반대.

    OI 가 줄지 않았다면 신규 투기이지 강제 청산이 아니다. 그 구분이 이 후보의
    전부이므로 두 조건을 모두 요구한다.

    돌려주는 두 번째 값은 **지표가 있어 판정 가능했던 봉 수**다. OI 스냅샷은
    2021 부터라 전체 봉 수로 빈도를 계산하면 과소평가된다.
    """
    out: list[Trigger] = []
    usable = 0
    blocked_until = 0
    for i in range(CASCADE_WINDOW, len(closes)):
        value = vol[i]
        if value is None or value <= 0.0:
            continue
        before = open_interest.get(times[i - CASCADE_WINDOW])
        now = open_interest.get(times[i])
        if before is None or now is None or before <= 0.0:
            continue
        usable += 1
        if i < blocked_until:
            continue
        prev_close = closes[i - CASCADE_WINDOW]
        if prev_close <= 0.0:
            continue
        change = (closes[i] - prev_close) / prev_close
        # 30분 변화를 5분 변동성과 견주려면 √6 배로 환산한다
        if abs(change) < CASCADE_MOVE_SD * value * CASCADE_WINDOW**0.5:
            continue
        if (now - before) / before > -CASCADE_OI_DROP:
            continue
        out.append(Trigger(index=i, direction=-1 if change > 0 else 1))
        blocked_until = i + CASCADE_WINDOW * 2
    return out, usable
