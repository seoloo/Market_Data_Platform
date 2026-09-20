"""저장소 계층.

DB 접근은 전부 이 패키지 뒤에 숨긴다. 나중에 PostgreSQL 로 교체할 때
바꿀 곳이 여기로 한정된다 (03-data-model.md §1).
"""

from auto_trading.storage.repositories.candles import CandleGap, CandleRepository
from auto_trading.storage.repositories.meta import CodeVersion, ConfigVersion, MetaRepository

__all__ = [
    "CandleGap",
    "CandleRepository",
    "CodeVersion",
    "ConfigVersion",
    "MetaRepository",
]
