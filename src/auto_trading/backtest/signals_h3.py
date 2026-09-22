"""H3 교차 심볼 후보 신호 5종 (23-h3-preregistration.md §2).

H2 와 **정보원은 같다** — 미결제약정·funding·테이커 플로우·계좌 롱숏비.
바뀐 것은 질문이다. "이 심볼이 오를까" 가 아니라 **"심볼들 중 어느 게 상대적으로
셀까"** 를 묻는다. 그래서 신호는 심볼마다 계산하고, 순위는 ``crosssection`` 이 매긴다.

신호 자체는 횡단면 정보를 쓰지 않는다. 한 심볼의 과거만 보고 값을 만들고,
그 값을 같은 시각의 다른 심볼들과 **비교**하는 것은 다음 단계의 일이다.
이렇게 나눠야 신호 계산이 유니버스 구성에 의존하지 않는다.

정규화는 전부 causal 이다.
"""

from __future__ import annotations

import bisect
import math

from auto_trading.features.normalize import causal_zscore_series

__all__ = [
    "SIGNAL_NAMES",
    "Z_LOOKBACK",
    "build_cross_signals",
]

SIGNAL_NAMES = (
    "X1_rel_funding",
    "X2_rel_oi_change",
    "X3_rel_taker",
    "X4_rel_crowd_gap",
    "X5_rel_reversal",
)

Z_LOOKBACK = 7 * 24 * 12
"""7일 causal 창 (5분 스냅샷 기준). 지표의 수준이 아니라 최근 대비 이례성을 본다."""

BARS_PER_HOUR = 12
REVERSAL_BARS = 72
"""단기 역전 신호가 보는 과거 구간 (6시간)."""

Series = dict[int, float]
Metrics = dict[int, tuple[float, float, float, float]]
"""``{시각: (미결제약정, 테이커비, 군중 롱숏비, 상위 트레이더 롱숏비)}``."""


def build_cross_signals(closes: Series, metrics: Metrics, funding: Series) -> dict[str, Series]:
    """한 심볼의 신호 5종. warm-up 미충족 시각은 **아예 넣지 않는다**.

    0 으로 채우지 않는 이유: 0 은 "중립" 이라는 값이 되어 횡단면 순위에 끼어든다.
    없는 값은 없는 채로 두고, 그 심볼은 그 시각의 단면에서 빠진다.
    """
    times = sorted(metrics)
    out: dict[str, Series] = {name: {} for name in SIGNAL_NAMES}
    if len(times) < Z_LOOKBACK + BARS_PER_HOUR + 1:
        return out

    oi = [metrics[t][0] for t in times]
    taker = [metrics[t][1] for t in times]
    crowd = [metrics[t][2] for t in times]
    top = [metrics[t][3] for t in times]

    oi_z = causal_zscore_series(_log_change(oi, BARS_PER_HOUR), Z_LOOKBACK)
    taker_z = causal_zscore_series(taker, Z_LOOKBACK)
    crowd_z = causal_zscore_series(crowd, Z_LOOKBACK)
    top_z = causal_zscore_series(top, Z_LOOKBACK)

    funding_times = sorted(funding)
    step_ms = times[1] - times[0] if len(times) > 1 else 300_000

    for index, at_ms in enumerate(times):
        settled = _last_settled(funding_times, at_ms)
        if settled is not None:
            out["X1_rel_funding"][at_ms] = funding[settled]

        oi_value, taker_value = oi_z[index], taker_z[index]
        if oi_value is not None:
            out["X2_rel_oi_change"][at_ms] = oi_value
        if taker_value is not None:
            out["X3_rel_taker"][at_ms] = taker_value

        crowd_value, top_value = crowd_z[index], top_z[index]
        if crowd_value is not None and top_value is not None:
            out["X4_rel_crowd_gap"][at_ms] = crowd_value - top_value

        past = closes.get(at_ms - REVERSAL_BARS * step_ms)
        current = closes.get(at_ms)
        if past and current:
            out["X5_rel_reversal"][at_ms] = (current - past) / past * 10_000
    return out


def _log_change(values: list[float], bars: int) -> list[float]:
    """``bars`` 전 대비 로그 변화율. 앞쪽은 0 으로 둔다.

    로그를 쓰는 이유: 미결제약정은 수년에 걸쳐 수십 배로 늘어난다. 절대 증분을 쓰면
    최근 구간이 과거를 압도해 causal z 가 의미를 잃는다.
    """
    out = [0.0] * len(values)
    for index in range(bars, len(values)):
        before, current = values[index - bars], values[index]
        if before > 0.0 and current > 0.0:
            out[index] = math.log(current / before)
    return out


def _last_settled(funding_times: list[int], at_ms: int) -> int | None:
    """``at_ms`` 시점에 **이미 정산된** 가장 최근 funding 시각.

    정산 전 요율을 당겨 쓰면 미래를 보는 것이다.
    """
    index = bisect.bisect_right(funding_times, at_ms)
    return funding_times[index - 1] if index else None
