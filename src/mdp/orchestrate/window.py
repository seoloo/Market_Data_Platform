"""실행할 달 정하기 — 예약 실행은 데이터 구간에서, 수동 실행은 매개변수에서.

언제 쓰나: DAG 템플릿이 ``{{ mdp_month(params, data_interval_start) }}`` 처럼 부른다
          (Airflow 사용자 매크로).
읽고 쓰는 것: 없음.

규칙:
- 매개변수 ``month``(``YYYY-MM``)가 있으면 그 달 — 수동 실행 · 특정 달 다시 돌리기.
- 없으면 데이터 구간 시작의 달 — ``@monthly`` 예약은 3 월 구간 [3/1, 4/1)을 4/1 에
  돌린다(달이 끝난 뒤).
- 둘 다 없으면 멈춘다(어느 달을 돌릴지 모르는 채로 돌리지 않는다).

[이유] Airflow 3 의 수동 실행은 데이터 구간이 없을 수 있다. '어느 달인가' 를 한 함수가 정하게 해서
       작업마다 다른 달을 돌리는 실수를 막는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

__all__ = ["mdp_month", "mdp_month_end", "mdp_month_start"]


def _month(params: Mapping[str, Any], interval_start: datetime | None) -> date:
    """[쓰임] 위 규칙으로 그 달 1 일."""
    text = str(params.get("month") or "").strip()
    if text:
        year_text, month_text = text.split("-")
        return date(int(year_text), int(month_text), 1)
    if interval_start is None:
        raise ValueError("돌릴 달을 모른다 — 매개변수 month(YYYY-MM)를 주거나 예약 실행으로")
    return date(interval_start.year, interval_start.month, 1)


def mdp_month(params: Mapping[str, Any], interval_start: datetime | None) -> str:
    """[쓰임] ``YYYY-MM`` — Silver · Gold · 복사 명령의 ``--month``."""
    return f"{_month(params, interval_start):%Y-%m}"


def mdp_month_start(params: Mapping[str, Any], interval_start: datetime | None) -> str:
    """[쓰임] 그 달 첫날 ``YYYY-MM-DD`` — 받기 명령의 ``--start``."""
    return f"{_month(params, interval_start):%Y-%m-%d}"


def mdp_month_end(params: Mapping[str, Any], interval_start: datetime | None) -> str:
    """[쓰임] 그 달 마지막 날 ``YYYY-MM-DD`` — 받기 명령의 ``--end``."""
    first = _month(params, interval_start)
    next_first = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return f"{next_first - timedelta(days=1):%Y-%m-%d}"
