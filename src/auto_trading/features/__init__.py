"""지표 계층.

**측정만 한다.** 방향 해석은 ``strategy`` 가 담당한다 (features/base.py 참조).
모든 함수·클래스는 순수하며 I/O 가 없다 (AGENTS.md §3.1).
"""

from auto_trading.features.base import Feature, FeatureResult
from auto_trading.features.momentum import MomentumFeature, PullbackFeature
from auto_trading.features.trend import TrendFeature
from auto_trading.features.volatility import VolatilityRegimeFeature
from auto_trading.features.volume import VolumeConfirmFeature

__all__ = [
    "Feature",
    "FeatureResult",
    "MomentumFeature",
    "PullbackFeature",
    "TrendFeature",
    "VolatilityRegimeFeature",
    "VolumeConfirmFeature",
]
