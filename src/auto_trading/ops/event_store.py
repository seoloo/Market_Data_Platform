"""발표 알림·개별주 추천이 공유하는 날짜별 실적 자료. 주문/계좌 경로 없음."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

NASDAQ_EARNINGS = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
EARNINGS_TTL_MS = 5 * 60_000
ERROR_RETRY_MS = 60_000


@dataclass(frozen=True)
class EarningsDay:
    day: date
    rows: tuple[dict[str, Any], ...]
    fetched_at_ms: int
    source: str
    digest: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def fetch_earnings(day: date) -> Mapping[str, Any]:
    response = httpx.get(
        NASDAQ_EARNINGS.format(d=day.isoformat()),
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=5,
    )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("실적 응답이 객체가 아님")
    return value


def _rows(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    # data:null / rows 누락은 '발표 없음'의 증거가 아니다.
    status = payload.get("status")
    if isinstance(status, dict) and status.get("rCode", 200) != 200:
        raise ValueError("실적 공급자 오류")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise ValueError("실적 rows 미확인")
    rows = data["rows"]
    if any(not isinstance(r, dict) or not str(r.get("symbol") or "").strip() for r in rows):
        raise ValueError("실적 행 형식 오류")
    return tuple(dict(r) for r in rows)


def _digest(rows: tuple[dict[str, Any], ...]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def earnings_day(
    root: Path,
    day: date,
    now_ms: int,
    *,
    fetch: Callable[[date], Mapping[str, Any]] = fetch_earnings,
    force: bool = False,
) -> EarningsDay:
    """성공/확인 실패·출처·시각·digest 공유. 오래된 성공으로 실패를 가리지 않는다."""
    source = NASDAQ_EARNINGS.format(d=day.isoformat())
    path = root / "data" / "event_calendar" / "earnings" / f"{day.isoformat()}.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        age_ms = now_ms - int(cached["fetched_at_ms"])
        rows = tuple(cached["rows"])
        valid = (
            cached["schema"] == 1
            and cached["day"] == day.isoformat()
            and cached["source"] == source
            and cached["digest"] == _digest(rows)
            and all(isinstance(r, dict) and r.get("symbol") for r in rows)
        )
        if (
            valid
            and 0 <= age_ms < (ERROR_RETRY_MS if cached["error"] else EARNINGS_TTL_MS)
            and (not force or cached["error"])
        ):
            return EarningsDay(
                day, rows, cached["fetched_at_ms"], source, cached["digest"], cached["error"]
            )
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        rows, error = _rows(fetch(day)), ""
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        rows, error = (), f"실적 일정 확인 실패 ({type(exc).__name__})"
    result = EarningsDay(day, rows, now_ms, source, _digest(rows), error)
    data = {
        "schema": 1,
        "day": day.isoformat(),
        "rows": rows,
        "fetched_at_ms": now_ms,
        "source": source,
        "digest": result.digest,
        "error": error,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError:
        # 기록 실패를 정상 신규 진입으로 처리하지 않는다. 알림은 실패 문구를 표시할 수 있다.
        return EarningsDay(day, (), now_ms, source, "", "실적 일정 저장 실패")
    return result
