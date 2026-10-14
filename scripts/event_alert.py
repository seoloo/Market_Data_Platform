"""미국 주요 경제 일정 경고 — 텔레그램 (매매와 무관).

    python -m scripts.event_alert show    # 오늘 아침 · 프리마켓 문구와 다음 2 주 일정 (보내지 않음)
    python -m scripts.event_alert run     # 보낼 차례면 텔레그램으로 (같은 알림은 하루 한 번)

현황 봇(``telegram_bot run``, 2 분마다) 안에서 ``maybe_send`` 를 부른다 — 새 예약 작업 없음.
상태 ``data/event_alert/state.json``. 설정 '경제 일정 경고'(alerts.events)로 끈다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.ops import user_settings
from auto_trading.ops.event_calendar import KST, Event, due, morning_text, parse, premarket_text

REPO = Path(__file__).resolve().parent.parent
CALENDAR = REPO / "config" / "event_calendar.yaml"
STATE = REPO / "data" / "event_alert" / "state.json"


def _load() -> tuple[list[Event], date | None]:
    return parse(yaml.safe_load(CALENDAR.read_text(encoding="utf-8")) or {})


def _texts(now: datetime) -> dict[str, str | None]:
    events, until = _load()
    return {
        "morning": morning_text(now, events, until),
        "premarket": premarket_text(now, events, until),
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
    texts = _texts(now)
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
    upcoming = (
        "\n".join(f"{e.at_kst:%m-%d %H:%M} KST (미국 {e.day:%m-%d} 장) · {e.name}" for e in soon)
        or "없음"
    )
    return (
        f"[아침 알림]\n{texts['morning'] or '(오늘은 보낼 것 없음)'}\n\n"
        f"[프리마켓 알림]\n{texts['premarket'] or '(오늘은 보낼 것 없음)'}\n\n"
        f"[다음 2 주]\n{upcoming}\n\n일정표 끝: {until}"
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
