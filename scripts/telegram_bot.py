"""텔레그램 '현황' 명령 — 폰에서 "현황" 을 보내면 실거래 요약을 답장한다 (사용자 2026-10-10).

사용 예::

    python -m scripts.telegram_bot run    # 받은 명령이 있으면 답장 (예약: 2 분마다)
    python -m scripts.telegram_bot show   # 보내지 않고 화면에만

* 받은 글은 Glide 대화와 같은 대기열(``data/telegram/inbox.json``)로 나눠 읽는다.
  이 봇은 명령(현황 · 수익 · 잔고 · /status)만 가져가고, 금액 · 버튼 답은 Glide 몫으로 남긴다.
* 내용은 대시보드 실거래 홈과 같다: 장부 + 공개 시세(키 없음). 주문 · 계좌 조회 없음.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.notify.heartbeat import beat_and_alert
from auto_trading.notify.inbox import InboxQueue, TelegramInbox, is_command
from auto_trading.ops.fire import TARGET_KRW
from auto_trading.ui.live_home import build_books, fetch_quotes, needed, summary_text

REPO = Path(__file__).resolve().parent.parent


def inbox_path(root: Path = REPO) -> Path:
    return root / "data" / "telegram" / "inbox.json"


def _start_offset(root: Path) -> int:
    """대기열이 처음 생길 때 — Glide 가 쓰던 offset 에서 잇는다."""
    try:
        return int(json.loads((root / "data" / "toss" / "M3.json").read_text("utf-8"))["tg_offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def report(root: Path = REPO) -> str:
    coins, tickers = needed(root)
    q = fetch_quotes(coins, tickers)
    books = build_books(root, q)
    text = summary_text(books, q, int(time.time() * 1000))
    value = sum(b.value_krw for b in books)
    return f"{text}\n🎯 파이어 목표 {TARGET_KRW / 1e8:g}억까지 {value / TARGET_KRW:.1%}"


def run(root: Path = REPO) -> int:
    creds = load_telegram_credentials(root / ".env")
    if creds is None:
        print("텔레그램 설정 없음")
        return 0
    try:
        cmds = InboxQueue(inbox_path(root), _start_offset(root)).take(
            TelegramInbox(creds), is_command
        )
    except NotifyError as exc:
        print(f"텔레그램 받기 실패: {exc}")
        return 1
    if not cmds:
        print("현황 봇: 받은 명령 없음")
        beat_and_alert(root, "telegram_bot", int(time.time() * 1000))
        return 0
    text = report(root)  # 여러 번 보내도 답은 한 통
    bot = TelegramNotifier(creds)
    try:
        bot.send(text)
    finally:
        bot.close()
    print(text)
    beat_and_alert(root, "telegram_bot", int(time.time() * 1000))
    return 0


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        return run()
    if argv == ["show"]:
        sys.stdout.write(report() + "\n")
        return 0
    raise SystemExit("usage: python -m scripts.telegram_bot run|show")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
