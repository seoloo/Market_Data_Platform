"""H2 후보 신호 5종 (16-h2-proposal.md §4.1).

**전부 가격에서 파생되지 않은 값**을 쓴다 — 미결제약정, funding, 테이커 플로우,
계좌 롱숏비. H1 의 5개 feature 가 전부 가격 파생값이었고 아무 신호도 내지 못했기
때문이다 (15-h1-postmortem.md §6).

F3 만 가격을 쓰는데, 가격 자체를 예측에 쓰는 것이 아니라 **"포지션이 늘었는데 가격이
안 움직였다"** 는 괴리를 재기 위해서다.

정규화는 전부 causal 이다. 전체 표본 통계를 쓰면 미래를 본 것이 된다.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass

from auto_trading.core.types import Candle, FundingRate, PerpMetrics
from auto_trading.features.normalize import causal_percentile_rank_series, causal_zscore_series

__all__ = ["SIGNAL_NAMES", "SignalInputs", "build_signals"]

SIGNAL_NAMES = (
    "F1_funding_extreme",
    "F2_oi_surge",
    "F3_oi_price_divergence",
    "F4_taker_imbalance",
    "F5_crowd_divergence",
)

_BARS_PER_HOUR = 12
"""5분 스냅샷 기준 1시간."""

_Z_LOOKBACK = 7 * 24 * _BARS_PER_HOUR
"""7일 causal 창. 지표의 수준이 아니라 **최근 대비 이례성**을 본다."""

_FUNDING_LOOKBACK = 90
"""30일 (8시간 주기 × 3 × 30). funding 은 느린 변수라 창이 길어야 한다."""


@dataclass(frozen=True, slots=True)
class SignalInputs:
    """정렬·정합이 끝난 입력.

    세 계열의 시각이 정확히 맞아야 한다. 어긋난 채로 계산하면 한 칸 미래를 보는
    누출이 되므로, 맞추는 책임을 이 타입에 모은다.
    """

    at_ms: tuple[int, ...]
    metrics: tuple[PerpMetrics, ...]
    closes: tuple[float, ...]
    funding_rates: tuple[float, ...]

    def __post_init__(self) -> None:
        lengths = {
            len(self.at_ms),
            len(self.metrics),
            len(self.closes),
            len(self.funding_rates),
        }
        if len(lengths) != 1:
            raise ValueError(f"입력 계열 길이가 다르다: {lengths}")


def align_inputs(
    metrics: Sequence[PerpMetrics],
    candles: Sequence[Candle],
    funding: Sequence[FundingRate],
) -> SignalInputs:
    """지표 시각을 기준으로 5분봉 종가와 **직전에 정산된** funding 을 붙인다.

    funding 은 8시간마다만 갱신되므로 직전 값을 이어 쓴다. ``funding_ms <= at_ms``
    조건이 인과성을 보장한다 — 아직 정산되지 않은 요율을 당겨쓰지 않는다.
    """
    closes_by_ms = {c.open_ms: float(c.close) for c in candles}
    funding_ms = [f.funding_ms for f in funding]
    kept_ms: list[int] = []
    kept_metrics: list[PerpMetrics] = []
    kept_closes: list[float] = []
    kept_funding: list[float] = []
    for entry in metrics:
        close = closes_by_ms.get(entry.at_ms)
        if close is None:
            continue  # 봉이 없는 시각은 만들지 않는다
        index = bisect.bisect_right(funding_ms, entry.at_ms) - 1
        if index < 0:
            continue  # 아직 정산 이력이 없다
        kept_ms.append(entry.at_ms)
        kept_metrics.append(entry)
        kept_closes.append(close)
        kept_funding.append(float(funding[index].rate))
    return SignalInputs(
        at_ms=tuple(kept_ms),
        metrics=tuple(kept_metrics),
        closes=tuple(kept_closes),
        funding_rates=tuple(kept_funding),
    )


def build_signals(inputs: SignalInputs) -> dict[str, list[float | None]]:
    """신호 5종을 계산한다. warm-up 미충족 구간은 ``None`` 이다.

    ``None`` 을 0 으로 대체하지 않는다 — 0 은 "중립" 이라는 뜻이 되어버려
    warm-up 구간이 신호 분포에 섞인다 (AGENTS.md §3.6).
    """
    metrics = inputs.metrics
    oi = [float(m.open_interest) for m in metrics]
    taker = [float(m.taker_buy_sell_ratio) for m in metrics]
    crowd = [float(m.account_long_short_ratio) for m in metrics]
    top = [float(m.toptrader_position_ratio) for m in metrics]

    oi_change = _rate_of_change(oi, _BARS_PER_HOUR)
    price_move = [abs(v) for v in _rate_of_change(inputs.closes, _BARS_PER_HOUR)]

    oi_z = causal_zscore_series(oi_change, _Z_LOOKBACK)
    move_z = causal_zscore_series(price_move, _Z_LOOKBACK)
    taker_z = causal_zscore_series(taker, _Z_LOOKBACK)
    crowd_z = causal_zscore_series(crowd, _Z_LOOKBACK)
    top_z = causal_zscore_series(top, _Z_LOOKBACK)

    return {
        "F1_funding_extreme": causal_percentile_rank_series(
            list(inputs.funding_rates), _FUNDING_LOOKBACK
        ),
        "F2_oi_surge": oi_z,
        "F3_oi_price_divergence": _subtract(oi_z, move_z),
        "F4_taker_imbalance": taker_z,
        "F5_crowd_divergence": _subtract(crowd_z, top_z),
    }


def _rate_of_change(values: Sequence[float], bars: int) -> list[float]:
    """``bars`` 전 대비 로그 변화율. 앞쪽 ``bars`` 개는 0 으로 둔다.

    로그를 쓰는 이유: 미결제약정은 수년에 걸쳐 수십 배로 늘어난다. 절대 증분을 쓰면
    최근 구간이 과거를 압도해 causal z 가 의미를 잃는다.
    """
    out = [0.0] * len(values)
    for index in range(bars, len(values)):
        previous, current = values[index - bars], values[index]
        if previous > 0.0 and current > 0.0:
            out[index] = math.log(current / previous)
    return out


def _subtract(left: list[float | None], right: list[float | None]) -> list[float | None]:
    return [None if a is None or b is None else a - b for a, b in zip(left, right, strict=True)]
