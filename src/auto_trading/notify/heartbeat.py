"""텔레그램 봇 살아 있음 표시 · 서로 지켜보기 (사용자 2026-10-10 "봇이 멈추면 알려 줘").

* 봇(현황 2 분 · 정보 1 분)은 실행이 끝날 때마다 ``data/telegram/heartbeat.json`` 에 시각을 적는다.
* 두 봇이 서로의 시각을 본다. 상대가 15 분 넘게 조용하면 투자 봇으로 한 번 알리고,
  돌아오면 한 번 더.
  내가 직전 5 분 안에 돌았을 때만 판단한다 — PC 가 자다 깬 직후 헛알림을 막는다.
* 둘 다 멈추면 서로 못 알린다. 그때는 매일 21:20 점검(paper_watchdog)이
  1 시간 넘은 것을 심각으로 올린다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BOTS = {"telegram_bot": "현황 봇", "info_bot": "정보 봇"}
PEER = {"telegram_bot": "info_bot", "info_bot": "telegram_bot"}
STALE_MS = 15 * 60_000
SELF_FRESH_MS = 5 * 60_000
DAILY_STALE_MS = 60 * 60_000


def path(root: Path) -> Path:
    return root / "data" / "telegram" / "heartbeat.json"


def read(root: Path) -> dict[str, Any]:
    try:
        d = json.loads(path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write(root: Path, state: Mapping[str, Any]) -> None:
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(state)), encoding="utf-8")
    tmp.replace(p)


def decide(now_ms: int, my_prev_ms: int | None, peer_ms: int | None, alerted: bool) -> str | None:
    """'down' (처음 멈춤을 알림) · 'up' (돌아옴을 알림) · None (조용히). 순수."""
    if my_prev_ms is None or now_ms - my_prev_ms > SELF_FRESH_MS:
        return None  # 나도 막 깨어남 — 상대가 늦은 건지 PC 가 잔 건지 모른다
    stale = peer_ms is None or now_ms - peer_ms > STALE_MS
    if stale and not alerted:
        return "down"
    if not stale and alerted:
        return "up"
    return None


def beat(root: Path, me: str, now_ms: int) -> str | None:
    """내 시각을 적고 상대를 판단한다. 알릴 문장(없으면 None)을 돌려준다 — 보내기는 호출한 쪽."""
    from auto_trading.notify.inbox import _locked

    path(root).parent.mkdir(parents=True, exist_ok=True)
    with _locked(path(root)):
        return _beat(root, me, now_ms)


def _beat(root: Path, me: str, now_ms: int) -> str | None:
    from datetime import UTC, datetime, timedelta

    state = read(root)
    peer = PEER[me]
    prev = state.get(me)
    peer_ms = state.get(peer)
    alerted = bool(state.get(f"{peer}_alerted"))
    action = decide(
        now_ms,
        int(prev) if isinstance(prev, (int, float)) else None,
        int(peer_ms) if isinstance(peer_ms, (int, float)) else None,
        alerted,
    )
    state[me] = now_ms
    text = None
    if action == "down":
        state[f"{peer}_alerted"] = True
        when = (
            (datetime.fromtimestamp(int(peer_ms) / 1000, UTC) + timedelta(hours=9)).strftime(
                "%m/%d %H:%M"
            )
            if isinstance(peer_ms, (int, float))
            else "기록 없음"
        )
        text = (
            f"⚠ {BOTS[peer]} 멈춤 — 마지막 정상 {when}\n"
            f"   └ 예약 작업 AutoTrading_{'Info_Bot' if peer == 'info_bot' else 'Telegram_Bot'}"
            " · reports/tasklog 확인"
        )
    elif action == "up":
        state[f"{peer}_alerted"] = False
        text = f"✅ {BOTS[peer]} 다시 정상"
    write(root, state)
    return text


def daily_findings(root: Path, now_ms: int) -> list[tuple[str, str]]:
    """매일 점검용: 1 시간 넘게 조용한 봇 → (이름, 문장). 기록이 아예 없으면 빈 목록(아직 안 켬)."""
    state = read(root)
    out = []
    for key, label in BOTS.items():
        ms = state.get(key)
        if isinstance(ms, (int, float)) and now_ms - ms > DAILY_STALE_MS:
            out.append(
                (label, f"{label} {int((now_ms - ms) // 60_000)}분째 조용함 — 멈췄을 수 있음")
            )
    return out


def beat_and_alert(root: Path, me: str, now_ms: int) -> str | None:
    """``beat`` + 알릴 것이 있으면 **투자 봇**으로 보낸다 (정보 봇이 멈춰도 알림이 가게).

    보내기에 실패하면 그 이유를 돌려준다."""
    from auto_trading.config.secrets import load_telegram_credentials
    from auto_trading.notify.channels import NotifyError, TelegramNotifier

    text = beat(root, me, now_ms)
    creds = load_telegram_credentials(root / ".env")
    if text is None or creds is None:
        return None
    bot = TelegramNotifier(creds)
    try:
        bot.send(text)
    except NotifyError as exc:
        return f"봇 상태 알림 실패: {exc}"
    finally:
        bot.close()
    return None
