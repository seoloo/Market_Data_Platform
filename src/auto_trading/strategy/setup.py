"""T1 되돌림을 **시간적 setup 조건**으로 해석한다 (D14).

왜 바꿨는가 — 2026-09-21 실데이터 2,184회 스윕에서 진입 16건 중 12건의 T1 신호가
정확히 0 이었다. T1 과 T2 를 **같은 시점의 가중합**에 넣으면 둘이 동시에 성립할 수
없기 때문이다. T1 은 RSI 가 눌림 구간에 있을 때 양수인데, T2 z 가 +2 를 넘으려면
가격이 방금 크게 튀어야 하고 그러면 RSI 는 이미 눌림 구간을 벗어나 있다.

가설 H1 이 말하는 것은 원래 **순서**였다: 되돌림이 *먼저* 오고, 그 다음 추세 방향
재개가 온다. 그래서 T1 은 점수가 아니라 **"최근에 되돌림이 있었는가"** 라는 조건이
되고, T2 가 그 뒤에 오는 방아쇠가 된다.

이 모듈은 순수하다 — I/O 없음, 시각을 직접 읽지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import FeatureConfig
from auto_trading.features.registry import float_param, int_param, zone_param

__all__ = ["SetupState", "detect_setup"]

_NOT_ARMED_REASONS = frozenset({"no_bias", "no_history", "no_pullback", "invalidated"})


@dataclass(frozen=True, slots=True)
class SetupState:
    """되돌림 setup 의 현재 상태.

    ``armed`` 가 False 면 T2 가 아무리 강해도 진입하지 않는다. 방아쇠는 setup 이
    걸려 있을 때만 의미가 있다.
    """

    armed: bool
    bars_since: int | None
    """되돌림이 마지막으로 성립한 이후 경과 봉. 0 이면 지금도 되돌림 중이다."""
    depth_rsi: Decimal | None
    """되돌림 구간에서 도달한 가장 깊은 RSI (롱=최저, 숏=최고). 기록용."""
    reason: str

    def __post_init__(self) -> None:
        if self.armed and self.bars_since is None:
            raise ValueError("armed setup 은 bars_since 를 가져야 한다")
        if not self.armed and self.reason not in _NOT_ARMED_REASONS:
            raise ValueError(f"미성립 사유가 정의되지 않았다: {self.reason}")

    def freshness(self, lookback_bars: int) -> float:
        """1.0(방금 되돌림) ~ 0 에 가까움(창 끝). 미성립이면 0.

        오래된 setup 일수록 "되돌림 후 재개" 라는 인과가 약해진다. 점수를 깎지는
        않고 ``confidence`` 에만 반영한다 — 진입 여부는 임계값이 정한다.
        """
        if not self.armed or self.bars_since is None or lookback_bars <= 0:
            return 0.0
        return max(0.0, 1.0 - self.bars_since / lookback_bars)


def detect_setup(
    rsi_history: Sequence[Decimal],
    bias_direction: int,
    cfg: FeatureConfig,
) -> SetupState:
    """최근 창에서 되돌림 setup 이 살아 있는지 판정한다.

    롱 기준 규칙 (숏은 완전 대칭):

    1. 최근 ``setup_lookback_bars`` 안에서 RSI 가 ``long_zone`` 에 들어온 **가장 최근**
       봉을 찾는다. 없으면 setup 없음.
    2. 그 봉 이후 지금까지 RSI 가 ``invalidate_below`` 아래로 내려간 적이 있으면
       무효다 — 그건 되돌림이 아니라 추세 이탈이다.
    3. 남으면 setup 성립. ``bars_since`` 는 그 봉으로부터 경과한 봉 수다.

    ``rsi_history`` 는 **오름차순**(과거→현재)이어야 한다.
    """
    if bias_direction == 0:
        return SetupState(False, None, None, "no_bias")

    lookback = int_param(cfg, "setup_lookback_bars", 12)
    if not rsi_history or lookback <= 0:
        return SetupState(False, None, None, "no_history")

    window = list(rsi_history[-lookback:])
    long_zone = zone_param(cfg, "long_zone", (35.0, 50.0))
    short_zone = zone_param(cfg, "short_zone", (50.0, 65.0))

    if bias_direction > 0:
        low, high = Decimal(str(long_zone[0])), Decimal(str(long_zone[1]))
        invalid_at = Decimal(str(float_param(cfg, "invalidate_below", 30.0)))
    else:
        low, high = Decimal(str(short_zone[0])), Decimal(str(short_zone[1]))
        invalid_at = Decimal(str(float_param(cfg, "invalidate_above", 70.0)))

    touch_index: int | None = None
    for offset, value in enumerate(reversed(window)):
        if low <= value <= high:
            touch_index = offset
            break
    if touch_index is None:
        return SetupState(False, None, None, "no_pullback")

    since_touch = window[len(window) - 1 - touch_index :]
    broken = (
        any(value < invalid_at for value in since_touch)
        if bias_direction > 0
        else any(value > invalid_at for value in since_touch)
    )
    if broken:
        return SetupState(False, None, None, "invalidated")

    depth = min(since_touch) if bias_direction > 0 else max(since_touch)
    return SetupState(armed=True, bars_since=touch_index, depth_rsi=depth, reason="armed")
