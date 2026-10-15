"""미국 주요 경제 일정 경고 — 텔레그램 (매매와 무관).

    python -m scripts.event_alert show    # 오늘 아침 · 프리마켓 문구와 다음 2 주 일정 (보내지 않음)
    python -m scripts.event_alert run     # 보낼 차례면 텔레그램으로 (같은 알림은 하루 한 번)

현황 봇(``telegram_bot run``, 2 분마다) 안에서 ``maybe_send`` 를 부른다 — 새 예약 작업 없음.
상태 ``data/event_alert/state.json``. 설정 '경제 일정 경고'(alerts.events)로 끈다.

실적 발표일: Nasdaq 공식 실적 캘린더(키 없음)에서 오늘 · 내일(미국 날짜)을 받아
토스 보유 · 관심 종목 · 초대형주만 고른다. 하루 한 번 받아 ``data/event_alert/earnings.json`` 에
둔다. 받지 못하면 실적 줄만 빠지고 경제 일정 알림은 그대로 간다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import yaml

from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.ops import user_settings
from auto_trading.ops.event_calendar import (
    KST,
    Earning,
    Event,
    due,
    morning_text,
    parse,
    parse_earnings,
    premarket_text,
)

REPO = Path(__file__).resolve().parent.parent
CALENDAR = REPO / "config" / "event_calendar.yaml"
STATE = REPO / "data" / "event_alert" / "state.json"
EARNINGS = REPO / "data" / "event_alert" / "earnings.json"
NASDAQ_EARNINGS = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def _load() -> tuple[list[Event], date | None]:
    return parse(yaml.safe_load(CALENDAR.read_text(encoding="utf-8")) or {})


def _calendar_rows(today: date) -> dict[str, list[dict[str, object]]]:
    """오늘 · 내일(미국 날짜) 실적 캘린더 행 — 하루 한 번 받고 나머지는 저장본."""
    try:
        cached = json.loads(EARNINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if cached.get("fetched") == today.isoformat():
        return dict(cached.get("days") or {})
    days: dict[str, list[dict[str, object]]] = {}
    for d in (today, today + timedelta(days=1)):
        r = httpx.get(NASDAQ_EARNINGS.format(d=d.isoformat()), headers=BROWSER, timeout=30)
        r.raise_for_status()
        days[d.isoformat()] = list(((r.json().get("data") or {}).get("rows")) or [])
    EARNINGS.parent.mkdir(parents=True, exist_ok=True)
    EARNINGS.write_text(
        json.dumps({"fetched": today.isoformat(), "days": days}, ensure_ascii=False),
        encoding="utf-8",
    )
    return days


def _held(root: Path) -> list[str]:
    """토스 미국 보유 종목 (조회만). 실패하면 빈 목록."""
    try:
        from auto_trading.config.secrets import load_toss_credentials
        from auto_trading.exchange.toss import TossReader, TossTokenCache, is_kr_symbol

        r = TossReader(
            load_toss_credentials(root / ".env"), TossTokenCache(root / "data" / "toss_token.json")
        )
        try:
            return [s for s in r.holdings() if not is_kr_symbol(s)]
        finally:
            r.close()
    except Exception as exc:  # 보유 조회 실패 — 관심 · 대형주만 본다
        print(f"토스 보유 조회 건너뜀: {type(exc).__name__}")
        return []


def _earnings(root: Path, today: date) -> list[Earning]:
    """알릴 실적 발표 (보유 · 관심 · 초대형주). 받지 못하면 빈 목록."""
    try:
        rows = _calendar_rows(today)
    except Exception as exc:  # 캘린더 실패로 경제 일정 알림까지 막지 않는다
        print(f"실적 캘린더 건너뜀: {type(exc).__name__}: {exc}")
        return []
    from scripts.market_brief import WATCH

    held = _held(root)
    out: list[Earning] = []
    for d, day_rows in sorted(rows.items()):
        out += parse_earnings(day_rows, date.fromisoformat(d), held, WATCH)
    return out


def _texts(now: datetime, root: Path = REPO) -> dict[str, str | None]:
    events, until = _load()
    earn = _earnings(root, now.date())
    return {
        "morning": morning_text(now, events, until, earn),
        "premarket": premarket_text(now, events, until, earn),
    }


def maybe_send(root: Path = REPO, now: datetime | None = None) -> list[str]:
    """보낼 차례인 알림을 보내고 보낸 이름을 돌려준다.

    일정이 없는 날은 보내지 않고 '본 것' 으로만 남긴다.
    """
    if not user_settings.get(root, "alerts.events"):
        return []
    now = now or datetime.now(KST)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"sent": []}
    todo = due(now, state.get("sent", []))
    if not todo:
        return []
    texts = _texts(now, root)
    creds = load_telegram_credentials(root / ".env")
    sent = []
    for name in todo:
        text = texts[name.split(":", 1)[1]]
        if text and creds is not None:
            bot = TelegramNotifier(creds)
            try:
                bot.send(text)
            except NotifyError as exc:
                print(f"일정 알림 실패: {exc}")
                continue  # 다음 실행에서 다시
            finally:
                bot.close()
            sent.append(name)
        state.setdefault("sent", []).append(name)
    state["sent"] = state["sent"][-120:]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return sent


def show() -> str:
    now = datetime.now(KST)
    events, until = _load()
    texts = _texts(now)
    soon = [e for e in events if now.date() <= e.day <= now.date() + timedelta(days=14)]
    earn = "\n".join(
        f"{e.day:%m-%d} {e.symbol} ({e.timing} · {e.why}) {e.name}"
        for e in _earnings(REPO, now.date())
    )
    upcoming = (
        "\n".join(f"{e.at_kst:%m-%d %H:%M} KST (미국 {e.day:%m-%d} 장) · {e.name}" for e in soon)
        or "없음"
    )
    return (
        f"[아침 알림]\n{texts['morning'] or '(오늘은 보낼 것 없음)'}\n\n"
        f"[프리마켓 알림]\n{texts['premarket'] or '(오늘은 보낼 것 없음)'}\n\n"
        f"[다음 2 주]\n{upcoming}\n\n"
        f"[오늘 · 내일 실적]\n{earn or '없음'}\n\n일정표 끝: {until}"
    )


def main(argv: list[str]) -> int:
    if argv == ["show"]:
        sys.stdout.write(show() + "\n")
    elif argv == ["run"]:
        print(maybe_send() or "보낼 알림 없음")
    else:
        raise SystemExit("usage: python -m scripts.event_alert show | run")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
