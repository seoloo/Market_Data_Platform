"""미국 주요 경제 일정 경고 — "오늘 밤 CPI 가 있으니 새로 사는 건 보류" (I/O 없음, 매매와 무관).

사용자 (2026-10-15): "어제 프리마켓에서 구매해서 일어나보니까 -8퍼였어 방법없나
오늘은 어떠한 일정이 있으니 들어 가는 건 보류 해라 뭐 이런식으로".

* 일정표는 ``config/event_calendar.yaml``(공식 출처 · 미국 동부 시간). 미국 정규장 날짜 D 의 일정은
  한국 날짜 D 의 밤(프리마켓 17:00 KST ~)에 해당한다.
* 알림 두 번: 아침(07:35 KST ~) "오늘 밤 · 내일 밤 일정",
  프리마켓 전(16:30 KST ~) "오늘 밤 일정 — 새 매수 보류".
* 실적 발표(사용자 2026-10-15 승인 '④ 실적 발표일 알림'): 보유 · 관심 종목과 지수를 흔드는
  초대형주(시총 $500B 이상)의 실적 발표일을 같은 알림에 붙인다. 미국 날짜 D 의 장 전 발표는
  한국 D 의 밤, 장 마감 뒤 발표는 한국 D+1 새벽이라 둘 다 '한국 D 에 사면 맞는 발표' 다.
* 방향을 맞히는 알림이 아니다. 발표 직후 반응을 따라가는 매매는 연구 49 에서 비용을 넘지 못했다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

__all__ = [
    "ET",
    "KST",
    "Earning",
    "Event",
    "due",
    "morning_text",
    "parse",
    "parse_earnings",
    "premarket_text",
]

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
HIGH = frozenset({"CPI", "NFP", "FOMC", "PCE"})
"""나스닥을 크게 흔드는 일정 — '새 매수 보류'. 나머지(PPI · GDP)는 '크기 주의'."""
MORNING = time(7, 35)
PREMARKET = time(16, 30)
LATE = time(23, 30)
_WEEKDAY = "월화수목금토일"


@dataclass(frozen=True)
class Event:
    day: date
    """미국 동부 날짜."""
    time_et: time
    key: str
    name: str

    @property
    def at_kst(self) -> datetime:
        return datetime.combine(self.day, self.time_et, tzinfo=ET).astimezone(KST)

    @property
    def high(self) -> bool:
        return self.key in HIGH


BIG_CAP_USD = 500e9
"""이 시총 이상이면 보유하지 않아도 알린다 — 나스닥 · S&P 지수 자체를 흔든다."""
_TIMING = {"time-pre-market": "장 전", "time-after-hours": "장 마감 뒤"}


@dataclass(frozen=True)
class Earning:
    day: date
    """미국 동부 날짜 (발표가 장 전이든 마감 뒤든 그날)."""
    symbol: str
    name: str
    timing: str
    """'장 전' · '장 마감 뒤' · '시각 미정'."""
    why: str
    """'보유' · '관심' · '대형주'."""

    @property
    def high(self) -> bool:
        """보유 · 관심 종목은 '새 매수 보류', 대형주는 '크기 주의'."""
        return self.why != "대형주"


def _cap(text: Any) -> float | None:
    try:
        return float(str(text).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def parse_earnings(
    rows: Iterable[Mapping[str, Any]],
    day: date,
    held: Iterable[str],
    watch: Iterable[str],
) -> list[Earning]:
    """Nasdaq 실적 캘린더 한 날치 행 → 알릴 실적 (보유 → 관심 → 대형주 시총 순)."""
    held_s, watch_s = {s.upper() for s in held}, {s.upper() for s in watch}
    out: list[tuple[int, float, Earning]] = []
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        cap = _cap(r.get("marketCap")) or 0.0
        if sym in held_s:
            why, rank = "보유", 0
        elif sym in watch_s:
            why, rank = "관심", 1
        elif cap >= BIG_CAP_USD:
            why, rank = "대형주", 2
        else:
            continue
        timing = _TIMING.get(str(r.get("time") or ""), "시각 미정")
        out.append((rank, -cap, Earning(day, sym, str(r.get("name") or sym), timing, why)))
    return [e for _, _, e in sorted(out, key=lambda x: (x[0], x[1], x[2].symbol))]


def _earn_line(e: Earning) -> str:
    return f"• {e.symbol} 실적 ({e.timing}) · {e.why}"


def parse(raw: Mapping[str, Any]) -> tuple[list[Event], date | None]:
    """YAML 내용 → (날짜 순 일정, 일정표가 덮는 마지막 날)."""
    events = []
    for e in raw.get("events") or []:
        hh, mm = (int(x) for x in str(e["time_et"]).split(":"))
        events.append(
            Event(date.fromisoformat(str(e["date"])), time(hh, mm), str(e["key"]), str(e["name"]))
        )
    until = raw.get("covered_until")
    return sorted(events, key=lambda e: (e.day, e.time_et)), (
        date.fromisoformat(str(until)) if until else None
    )


def _on(events: Iterable[Event], day: date) -> list[Event]:
    return [e for e in events if e.day == day]


def _md(d: date) -> str:
    return f"{d.month}/{d.day}({_WEEKDAY[d.weekday()]})"


def _line(e: Event, today_kst: date) -> str:
    k = e.at_kst
    when = f"{k:%H:%M}" if k.date() == today_kst else f"{k.month}/{k.day} {k:%H:%M}"
    return f"• {when} KST · {e.name}"


def _stale(until: date | None, today: date) -> str:
    if until is None or today <= until:
        return ""
    return (
        "\n\n⚠️ 일정표(config/event_calendar.yaml)가 "
        f"{until} 까지만 있어요 — 새 일정 갱신이 필요해요."
    )


def morning_text(
    now_kst: datetime,
    events: Sequence[Event],
    until: date | None,
    earnings: Sequence[Earning] = (),
) -> str | None:
    """오늘 밤 일정 · 실적이 있거나 내일 밤 큰 일정 · 보유 실적이 있으면 아침 알림, 없으면 None."""
    today = now_kst.date()
    nxt = today + timedelta(days=1)
    tonight = _on(events, today)
    earn_tonight = [e for e in earnings if e.day == today]
    tomorrow = [e for e in _on(events, nxt) if e.high]
    earn_tomorrow = [e for e in earnings if e.day == nxt and e.high]
    if not (tonight or earn_tonight or tomorrow or earn_tomorrow):
        return None
    lines = ["🗓 미국 주요 일정 알림"]
    if tonight or earn_tonight:
        lines += [
            "",
            f"오늘 밤 ({_md(today)} 미국 장)",
            *(_line(e, today) for e in tonight),
            *(_earn_line(e) for e in earn_tonight),
        ]
        if any(e.high for e in tonight) or any(e.high for e in earn_tonight):
            lines.append(
                "👉 발표 전후로 크게 흔들릴 수 있어요. "
                "오늘 프리마켓 · 장 초반에 새로 사는 건 보류를 권해요."
            )
        else:
            lines.append("👉 흔들릴 수 있어요. 새로 사면 크기를 줄여 주세요.")
    if tomorrow or earn_tomorrow:
        lines += [
            "",
            f"내일 밤 ({_md(nxt)} 미국 장) — 큰 일정",
            *(_line(e, today) for e in tomorrow),
            *(_earn_line(e) for e in earn_tomorrow),
        ]
    if any(e.why == "보유" for e in [*earn_tonight, *earn_tomorrow]):
        lines += [
            "",
            "💼 보유 종목 실적 — 발표 뒤 하루에 ±10% 넘게 움직이기도 해요. "
            "들고 넘길지 크기를 미리 정해 두세요.",
        ]
    lines += [
        "",
        "자동 매매 장부(Deneb · Vega · 자동 적립)는 원래 규칙대로 돌아요 — 직접 매매용 경고예요.",
    ]
    return "\n".join(lines) + _stale(until, today)


def premarket_text(
    now_kst: datetime,
    events: Sequence[Event],
    until: date | None,
    earnings: Sequence[Earning] = (),
) -> str | None:
    """프리마켓(17:00 KST) 전 — 오늘 밤 일정 · 실적이 있으면 한 번 더."""
    today = now_kst.date()
    tonight = _on(events, today)
    earn = [e for e in earnings if e.day == today]
    if not tonight and not earn:
        return None
    big = any(e.high for e in tonight) or any(e.high for e in earn)
    head = "⛔ 오늘 밤 큰 발표 — 새 매수 보류 권장" if big else "⚠️ 오늘 밤 발표 — 크기 주의"
    lines = [head, *(_line(e, today) for e in tonight), *(_earn_line(e) for e in earn), ""]
    if tonight:
        first = min(e.at_kst for e in tonight)
        lines.append(
            f"프리마켓에서 사면 {first:%H:%M} 발표를 그대로 맞아요. "
            "발표를 보고 하루 지나서 판단해도 늦지 않아요."
        )
    if earn:
        mine = ", ".join(e.symbol for e in earn if e.high)
        tail = (
            f"오늘 {mine} 를 사면 발표를 들고 넘기게 돼요."
            if mine
            else "대형주 실적은 나스닥 전체를 흔들 수 있어요."
        )
        lines.append(
            "실적은 장 전(한국 밤 8 ~ 10 시) · 장 마감 뒤(한국 새벽 5 ~ 7 시)에 나와요. " + tail
        )
    return "\n".join(lines) + _stale(until, today)


def due(now_kst: datetime, sent: Iterable[str]) -> list[str]:
    """지금 보낼 차례인 알림 이름(``YYYY-MM-DD:morning`` · ``:premarket``).

    늦게 켜져도 그날 안에는 보낸다.
    """
    done = set(sent)
    day = now_kst.date().isoformat()
    t = now_kst.time()
    out = []
    if MORNING <= t < PREMARKET and f"{day}:morning" not in done:
        out.append(f"{day}:morning")
    if PREMARKET <= t < LATE and f"{day}:premarket" not in done:
        out.append(f"{day}:premarket")
    return out
