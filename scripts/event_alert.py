"""미국 주요 경제 일정 경고 — 텔레그램 (매매와 무관).

    python -m scripts.event_alert show    # 오늘 아침 · 프리마켓 문구와 다음 2 주 일정 (보내지 않음)
    python -m scripts.event_alert run     # 보낼 차례면 텔레그램으로 (같은 알림은 하루 한 번)

현황 봇(``telegram_bot run``, 2 분마다) 안에서 ``maybe_send`` 를 부른다 — 새 예약 작업 없음.
상태 ``data/event_alert/state.json``. 설정 '경제 일정 경고'(alerts.events)로 끈다.

실적 발표일은 Nasdaq의 오늘 밤·내일 밤 미국 세션 날짜를 조회한다. 비중 추천과 동일한
``data/event_calendar/earnings`` 캐시(5분)를 읽고 실패는 미확인으로 표시한다.
저장된 개별주 예약·보유 상태를 덧붙이지만 이 알림은 주문·tick을 호출하지 않는다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.ops import user_settings
from auto_trading.ops.entry_events import macro_events, on_session
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
from auto_trading.ops.event_store import EarningsDay, earnings_day

REPO = Path(__file__).resolve().parent.parent
CALENDAR = REPO / "config" / "event_calendar.yaml"
STATE = REPO / "data" / "event_alert" / "state.json"


def _load() -> tuple[list[Event], date | None]:
    return parse(yaml.safe_load(CALENDAR.read_text(encoding="utf-8")) or {})


def _calendar_rows(root: Path, today: date, now_ms: int) -> list[EarningsDay]:
    """오늘 밤·내일 밤의 미국 세션 날짜를 조회. 추천과 같은 저장소·유효성 규칙."""
    return [earnings_day(root, d, now_ms) for d in (today, today + timedelta(days=1))]


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
    return _earnings_status(root, today, int(time.time() * 1000))[0]


def _earnings_status(root: Path, today: date, now_ms: int) -> tuple[list[Earning], list[str]]:
    rows = _calendar_rows(root, today, now_ms)
    from scripts.market_brief import WATCH

    held = _held(root)
    out: list[Earning] = []
    warnings: list[str] = []
    for snapshot in rows:
        if not snapshot.ok:
            warnings.append(f"⚠️ {snapshot.day} {snapshot.error} — 발표 없음으로 보지 않아요")
        else:
            out += parse_earnings(snapshot.rows, snapshot.day, held, WATCH)
    return out, warnings


def _ticket_lines(root: Path, events: list[Event]) -> list[str]:
    """예약·보유 장부 읽기만. 알림에서 주문/취소/tick을 호출하지 않는다."""
    path = root / "data" / "size_guard" / "state.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        tickets = raw["tickets"]
        if not isinstance(tickets, list):
            raise ValueError("장부 형식")
        out = []
        for ticket in tickets:
            status = ticket["status"]
            symbol = str(ticket["symbol"])
            if status in ("reserved", "buying"):
                session = str(ticket.get("session") or "")
                label = f"⏳ {symbol} 예약 {session or '거래일 재확인 필요'}"
                if session.startswith("US:"):
                    risk = on_session(events, date.fromisoformat(session[3:]))
                    if risk.blocked:
                        label += " — 발표일 신규 매수 보류 대상"
                out.append(label)
            elif status in ("holding", "selling"):
                out.append(f"🛡 {symbol} 보유·손절 감시 — 발표만으로 자동 매도하지 않아요")
        if out:
            out += [
                "예약은 주문 직전에 일정·3거래일 실적·추세·현금을 재확인합니다.",
                "보류된 예약은 자동 이월하지 않아요. 다음 정규장에 새 추천·예약이 필요해요.",
                "−3%는 매도 시도 기준이며 갭·미체결·PC 꺼짐 때 손실 상한이 아닙니다.",
            ]
        return out
    except (OSError, ValueError, TypeError, KeyError):
        return ["⚠️ 개별주 예약·보유 장부 미확인 — 비어 있다고 보지 않아요"]


def _texts(now: datetime, root: Path = REPO) -> dict[str, str | None]:
    try:
        raw = yaml.safe_load(CALENDAR.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("거시 일정 형식 미확인")
        events, until = parse(raw)
        calendar_risk = macro_events(raw, now.date(), int(now.timestamp() * 1000))
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        events, until = [], None
        calendar_risk = macro_events({}, now.date(), int(now.timestamp() * 1000))
    earn, warnings = _earnings_status(root, now.date(), int(now.timestamp() * 1000))
    texts = {
        "morning": morning_text(now, events, until, earn),
        "premarket": premarket_text(now, events, until, earn),
    }
    # 일정 만료/손상을 일정 없는 조용한 날로 위장하지 않는다.
    warnings += [b for b in calendar_risk.blocked if "확인 불가" in b]
    extra = warnings + _ticket_lines(root, events)
    if extra:
        tail = "\n\n📐 개별주 일정·예약 확인\n" + "\n".join(extra)
        texts = {slot: (text or "🗓 미국 주요 일정 확인") + tail for slot, text in texts.items()}
    return texts


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
