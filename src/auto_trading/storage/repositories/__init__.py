"""저장소 계층 — 표마다 '읽기 · 쓰기' 창구 클래스 하나씩.

언제 쓰나: 백필 · 무결성 검사 · 스냅샷 · Paper 기록이 SQL 을 직접 쓰지 않고 이 클래스들을 부른다.
읽고 쓰는 것: 각 클래스가 맡은 표 하나 (candles · funding_rates · perp_metrics · paper_days …).

디자인 패턴: **Repository** — DB 접근은 전부 이 패키지 뒤에 숨긴다. 나중에 PostgreSQL 등으로
교체할 때 바꿀 곳이 여기로 한정된다 (03-data-model.md §1).
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
