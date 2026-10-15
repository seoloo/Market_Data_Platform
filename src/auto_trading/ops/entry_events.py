"""개별주 신규 진입의 결정론적 발표 위험. 시계·네트워크·주문 없음."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from auto_trading.ops.event_calendar import HIGH, KST, Event, parse
from auto_trading.ops.event_store import EarningsDay

MACRO_MAX_AGE_DAYS = 30
EARN_BLOCK_SESSIONS = 3


@dataclass(frozen=True)
class EntryEvents:
    blocked: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    entry_day: date | None = None
    earnings_date: str | None = None
    earnings_in_days: int | None = None

    @property
    def ok(self) -> bool:
        return not self.blocked


def macro_events(raw: Mapping[str, Any], entry_day: date, now_ms: int) -> EntryEvents:
    """명시된 범위·검증일 내에서만 무일정을 정상으로 인정한다."""
    try:
        since = date.fromisoformat(str(raw["covered_from"]))
        until = date.fromisoformat(str(raw["covered_until"]))
        verified = date.fromisoformat(str(raw["verified_on"]))
        today = datetime.fromtimestamp(now_ms / 1000, KST).date()
        if (
            not since <= entry_day <= until
            or not 0 <= (today - verified).days <= MACRO_MAX_AGE_DAYS
        ):
            raise ValueError("달력 범위·검증일 미확인")
        events, _ = parse(raw)
        if not events:
            raise ValueError("달력 비어 있음")
    except (KeyError, TypeError, ValueError):
        return EntryEvents(
            ("경제일정 범위·최신성 확인 불가 — 신규 매수 보류",), entry_day=entry_day
        )
    return on_session(events, entry_day)


def on_session(events: Sequence[Event], entry_day: date) -> EntryEvents:
    blocked, lines = [], []
    for event in events:
        if event.day != entry_day:
            continue
        text = f"{event.name} ({event.at_kst:%m/%d %H:%M} KST)"
        if event.key in HIGH:
            blocked.append(f"{text} — 미국 {entry_day} 정규장 신규 매수 보류")
        else:
            lines.append(f"⚠️ {text} — 주의, 비중 자동 변경 없음")
    if blocked:
        lines.append("발표 다음 정규장에 재확인·새 예약 필요 (같은 장 자동 복귀 없음)")
    elif not lines:
        lines.append(f"✅ 미국 {entry_day} 주요 발표 없음 (등록 일정표 기준)")
    return EntryEvents(tuple(blocked), tuple(lines), entry_day)


def company_events(
    symbol: str, session_days: Sequence[date], days: Mapping[date, EarningsDay]
) -> EntryEvents:
    """진입 세션 + 다음 실제 정규장 세 번. 중간 휴장일 발표도 빠뜨리지 않는다."""
    if len(session_days) != EARN_BLOCK_SESSIONS + 1 or list(session_days) != sorted(
        set(session_days)
    ):
        return EntryEvents(("실제 거래일 달력 확인 불가 — 신규 매수 보류",))
    start, end = session_days[0], session_days[-1]
    cursor = start
    matches: list[date] = []
    while cursor <= end:
        snapshot = days.get(cursor)
        if snapshot is None or not snapshot.ok or snapshot.day != cursor:
            return EntryEvents((f"{cursor} 실적 일정 미확인 — 신규 매수 보류",), entry_day=start)
        if any(str(r["symbol"]).upper() == symbol.upper() for r in snapshot.rows):
            matches.append(cursor)
        cursor += timedelta(days=1)
    if not matches:
        return EntryEvents(
            lines=(f"✅ 실적: 진입일부터 3거래일 뒤({end})까지 명단에 없음 (확정 공시 보증 아님)",),
            entry_day=start,
            earnings_date="",
            earnings_in_days=EARN_BLOCK_SESSIONS + 1,
        )
    day = min(matches)
    distance = sum(start < s <= day for s in session_days)
    return EntryEvents(
        (f"실적 발표 {day} ({distance} 거래일 뒤) — 발표 다음 정규장에 새 추천 필요",),
        entry_day=start,
        earnings_date=day.isoformat(),
        earnings_in_days=distance,
    )
