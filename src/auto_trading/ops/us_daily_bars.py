"""미국 주식 일봉 CSV 이어 붙이기 — 끝난 날만 쓰고, 최근 줄은 새 값으로 바로잡는다.

한국투자증권 일봉은 미국 장중 · 장 전에도 **그날 진행 중인 봉** 을 준다.
한국 21시대(미국 장 전)에 받으면 거래량 수십만 주짜리 미완성 봉이 그날 종가로 들어가고,
새 날짜만 덧붙이던 예전 방식은 그 줄을 다시 고치지 않았다
(2026-10-12 발견: SPY 10/06 ~ 10/09 거래량 14만 ~ 32만 주). 여기서는

* 뉴욕 시각으로 정규장 마감(16:00) 30 분 뒤가 지나지 않은 날의 봉은 버린다.
* 겹쳐 받은 최근 날짜는 새 값으로 덮어쓴다 (예전에 들어간 미완성 줄을 바로잡는다).
* 최근 ``RECENT_DAYS`` 보다 오래된 겹친 날의 종가가 ``MISMATCH`` 넘게 다르면
  수정주가가 바뀐 것 → 전체 다시 받기.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from auto_trading.core.types import Candle

NY = ZoneInfo("America/New_York")
SETTLED = time(16, 30)
MISMATCH = 0.005
RECENT_DAYS = 10
FIELDS = ("date", "open", "high", "low", "close", "volume")

Row = dict[str, str]


def last_settled_day(now: datetime | None = None) -> str:
    """지금 정규장이 확실히 끝난 마지막 미국 날짜 (거래일인지는 따지지 않는다)."""
    ny = (now or datetime.now(UTC)).astimezone(NY)
    d = ny.date() if ny.time() >= SETTLED else ny.date() - timedelta(days=1)
    return d.isoformat()


def candle_day(b: Candle) -> str:
    return datetime.fromtimestamp(b.open_ms / 1000, UTC).strftime("%Y-%m-%d")


def to_row(day: str, b: Candle) -> Row:
    return {
        "date": day,
        "open": str(b.open),
        "high": str(b.high),
        "low": str(b.low),
        "close": str(b.close),
        "volume": str(b.volume),
    }


def settled(bars: Iterable[Candle], last_ok: str) -> dict[str, Candle]:
    """끝난 날의 봉만 {날짜: 봉}."""
    return {d: b for b in bars if (d := candle_day(b)) <= last_ok}


@dataclass(frozen=True, slots=True)
class Merge:
    rows: list[Row]
    appended: int
    repaired: int
    dropped_unsettled: int
    needs_full: bool


def merge(rows: list[Row], fresh: Mapping[str, Candle], last_ok: str) -> Merge:
    """저장된 줄 ``rows`` 에 끝난 날의 새 봉 ``fresh`` 를 합친다.

    ``needs_full`` 이면 수정주가가 바뀐 것이라 전체를 다시 받아야 한다.
    """
    recent_from = (date.fromisoformat(last_ok) - timedelta(days=RECENT_DAYS)).isoformat()
    old = {r["date"]: r for r in rows}
    for d, b in fresh.items():
        if d < recent_from and d in old:
            before = float(old[d]["close"])
            if before > 0 and abs(float(b.close) / before - 1) > MISMATCH:
                return Merge(rows, 0, 0, 0, True)
    unsettled = [d for d in old if d > last_ok]
    for d in unsettled:
        del old[d]
    appended = repaired = 0
    for d, b in fresh.items():
        new = to_row(d, b)
        if d not in old:
            appended += 1
        elif any(old[d][k] != new[k] for k in FIELDS):
            repaired += 1
        old[d] = new
    return Merge([old[d] for d in sorted(old)], appended, repaired, len(unsettled), False)
