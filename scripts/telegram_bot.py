"""텔레그램 '현황' 명령 — 폰에서 "현황" 을 보내면 실거래 요약을 답장한다 (사용자 2026-10-10).

사용 예::

    python -m scripts.telegram_bot run    # 받은 명령이 있으면 답장 (예약: 2 분마다)
    python -m scripts.telegram_bot show   # 보내지 않고 화면에만

* 받은 글은 Glide 대화와 같은 대기열(``data/telegram/inbox.json``)로 나눠 읽는다.
  이 봇은 명령(현황 · 수익 · 잔고 · /status)과 '전부 정리'(정리 · 정지풀기 · 그 버튼)만 가져가고,
  금액 · 버튼 답은 Glide 몫으로 남긴다.
* **'정리'** (2026-10-11): 미리 보기 + 범위 버튼(전부 · Cruise · Glide · 적립 · 취소, 5 분 · 한 번).
  누르면 ``scripts/liquidate.py`` 가 그룹 정지 → 토스 매수 취소 → 지정가 매도. 이 실행 안에서
  2 분 동안 버튼을 기다린다. 매 실행마다 진행 중 정리를 이어 한다(토스 장 시간 · 미체결 다시 내기).
* 내용은 대시보드 실거래 홈과 같다: 장부 + 공개 시세(키 없음). 주문 · 계좌 조회 없음.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.notify.heartbeat import beat_and_alert
from auto_trading.notify.inbox import (
    HELP_WORDS,
    LIQ_WORDS,
    PAPER_WORDS,
    UNHALT_WORDS,
    InboxMessage,
    InboxQueue,
    TelegramInbox,
    is_command,
    is_liquidation,
)
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import GROUPS
from auto_trading.ops.liquidate import SCOPES
from auto_trading.ops.paper_summary import paper_text
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
    target = float(user_settings.get(root, "fire.target_krw"))
    return f"{text}\n🎯 파이어 목표 {target / 1e8:g}억까지 {value / target:.1%}"


HELP = """📌 투자 봇 사용법 — 이렇게 보내 보세요

📊 현황 (또는 /status · 수익 · 잔고)
  실거래 요약: Glide · Cruise · 자동 적립 + 파이어 목표까지 %

📒 Paper (또는 /paper · 페이퍼)
  가상 장부 요약: 코인 · AG/AGS · 미국 주식 · 국장 주도주 (매일 21:30 자동으로도 와요)

🧯 정리 (또는 /liquidate · 청산 · 전부매도)
  보유를 지정가로 팔기 — 미리 보기 뒤 버튼:
  [전부 정리] [Cruise 만] [Glide 만] [자동 적립만] [취소]
  버튼은 5분 안에 한 번만. 수동 보유(APH 등)는 안 팔아요.

▶ 정지풀기 (또는 /unhalt)
  정리 뒤 멈춘 그룹 다시 켜기 → 다음 실행부터 규칙대로 다시 사요

💵 Glide 이번 달 금액
  매달 매수일 아침 질문에 숫자로 답: 30만 · 500000 · 0(쉬기)

🔘 제안 버튼 (배당 단계 · 12월 공제 실현)
  메시지 아래 버튼으로 답해요

❓ 도움 (또는 /help) — 이 안내

이 방으로 오는 알림: Glide · Cruise · 토스 매매 결과, 환전 요청, 위험 알림,
경제 일정 · 실적 경고(CPI · FOMC · 보유 종목 실적 등 07:35 · 16:30),
매일 21:30 Paper 요약
종목 뉴스는 정보 봇에 물어보세요."""
COMMANDS_MENU = [
    ("status", "현황 — 실거래 요약"),
    ("paper", "Paper — 가상 장부 요약"),
    ("liquidate", "정리 — 보유 팔기 (미리 보기 + 버튼)"),
    ("unhalt", "정지풀기 — 멈춘 그룹 다시 켜기"),
    ("help", "사용법"),
]
"""입력칸 '/' 메뉴 (텔레그램 명령은 영문만)."""


def pin_help(root: Path = REPO) -> str:
    """사용법을 보내고 대화방 공지로 고정한다 (예전 고정은 모두 푼다) + '/' 메뉴."""
    creds = load_telegram_credentials(root / ".env")
    if creds is None:
        return "텔레그램 설정 없음"
    bot = TelegramNotifier(creds)
    try:
        mid = bot.send_message(HELP)
        bot.unpin_all()
        bot.pin(mid)
        bot.set_commands(COMMANDS_MENU)
    finally:
        bot.close()
    return f"투자 봇 공지 고정 (메시지 {mid})"


LIQ_WAIT_S = 120
"""'정리' 범위 버튼을 이 실행 안에서 기다리는 시간 (다음 예약 실행을 기다리지 않게)."""


def _liq_buttons(nonce: str) -> list[list[dict[str, str]]]:
    def b(text: str, arg: str) -> dict[str, str]:
        return {"text": text, "callback_data": f"liq:{nonce}:{arg}"}

    return [
        [b("🧯 전부 정리", "all")],
        [b("Cruise 만", "cruise"), b("Glide 만", "glide")],
        [b("자동 적립만", "acc")],
        [b("취소", "cancel")],
    ]


def handle_liquidation(bot: TelegramNotifier, msg: InboxMessage) -> bool:
    """'정리' · '정지풀기' 글과 그 버튼. 버튼을 보냈으면 True (이 실행에서 답을 기다린다)."""
    from scripts import liquidate as liq

    now = int(time.time() * 1000)
    if msg.data:
        if msg.callback_id:
            with contextlib.suppress(NotifyError):
                bot.answer_callback(msg.callback_id, "접수했어요")
        parts = msg.data.split(":")
        if len(parts) != 3:
            return False
        kind, nonce, arg = parts
        if not liq.STORE.use_request(nonce, now, kind):
            bot.send("⌛ 지난 버튼이에요 (5 분 지남 · 이미 누름) — 다시 보내 주세요")
            return False
        if arg == "cancel":
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, "취소했어요 — 아무것도 바꾸지 않았어요")
            return False
        if kind == "liq" and arg in SCOPES:
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🧯 {SCOPES[arg]} 정리 시작 — 결과를 곧 보내요")
            liq.start(arg, "텔레그램")  # 결과는 start 가 보낸다
        elif kind == "unh" and arg in GROUPS:
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"▶ {GROUPS[arg]} 정지 풀기")
            liq.unhalt(arg, "텔레그램")
        return False
    word = msg.text.strip().lower().replace(" ", "")
    if word in LIQ_WORDS:
        nonce = liq.STORE.new_request(now, "liq")
        text = liq.preview_text("all")
        bot.send_message(
            text + "\n\n범위를 누르면 바로 시작해요 (5 분 안, 한 번만)",
            buttons=_liq_buttons(nonce),
        )
        return True
    if word in UNHALT_WORDS:
        on = {g: r for g, r in liq.halts().items() if r}
        if not on:
            extra = " (전체 비상 정지는 설정 화면에서)" if root_halt().exists() else ""
            bot.send("정지 중인 그룹이 없어요" + extra)
            return False
        nonce = liq.STORE.new_request(now, "unh")
        rows = [[{"text": f"{GROUPS[g]} 풀기", "callback_data": f"unh:{nonce}:{g}"}] for g in on]
        rows.append([{"text": "취소", "callback_data": f"unh:{nonce}:cancel"}])
        lines = [f"⛔ {GROUPS[g]}: {r}" for g, r in on.items()]
        bot.send_message(
            "\n".join([*lines, "", "풀면 다음 실행부터 그 전략이 자기 규칙대로 다시 사요"]),
            buttons=rows,
        )
        return True
    return False


def root_halt(root: Path = REPO) -> Path:
    return root / "data" / "HALT"


def _liquidation(
    bot: TelegramNotifier, msgs: list[InboxMessage], queue: InboxQueue, inbox: TelegramInbox
) -> None:
    from auto_trading.core.errors import AutoTradingError

    try:
        waiting = False
        for m in msgs:
            waiting = handle_liquidation(bot, m) or waiting
        deadline = time.monotonic() + LIQ_WAIT_S
        while waiting and time.monotonic() < deadline:
            time.sleep(3)
            for m in queue.take(inbox, is_liquidation):
                handle_liquidation(bot, m)
                if m.data:
                    waiting = False
    except (AutoTradingError, KeyError, OSError) as exc:
        bot.send(f"⚠ 정리 처리 오류: {type(exc).__name__} {exc}"[:500])


def run(root: Path = REPO) -> int:
    creds = load_telegram_credentials(root / ".env")
    if creds is None:
        print("텔레그램 설정 없음")
        return 0
    queue = InboxQueue(inbox_path(root), _start_offset(root))
    inbox = TelegramInbox(creds)
    try:
        cmds = queue.take(inbox, is_command)
    except NotifyError as exc:
        print(f"텔레그램 받기 실패: {exc}")
        return 1
    helps = [m for m in cmds if not m.data and m.text.strip().lower() in HELP_WORDS]
    papers = [
        m for m in cmds if not m.data and m.text.strip().lower().replace(" ", "") in PAPER_WORDS
    ]
    status_cmds = [m for m in cmds if not is_liquidation(m) and m not in helps and m not in papers]
    liq_msgs = [m for m in cmds if is_liquidation(m)]
    bot = TelegramNotifier(creds)
    try:
        if helps:
            bot.send(HELP)
        if papers:
            bot.send(paper_text(root))  # 여러 번 보내도 한 통
        if status_cmds:
            text = report(root)  # 여러 번 보내도 답은 한 통
            bot.send(text)
            print(text)
        if liq_msgs:
            _liquidation(bot, liq_msgs, queue, inbox)
    finally:
        bot.close()
    _resume_liquidation()
    _event_alert(root)
    _namu_follow(root)
    _ag4s2_demo(root)
    _paper_daily(root)
    if not cmds:
        print("현황 봇: 받은 명령 없음")
    beat_and_alert(
        root,
        "telegram_bot",
        int(time.time() * 1000),
        alert=bool(user_settings.get(root, "alerts.bot_down")),
    )
    return 0


def _paper_daily(root: Path) -> None:
    """매일 21:30(KST) 뒤 한 번 — 모든 Paper 요약 (사용자 2026-10-15 '모든 페이퍼들 전부 알림')."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from auto_trading.ops.paper_summary import daily_due

    if not user_settings.get(root, "alerts.paper"):
        return
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    state_path = root / "data" / "telegram" / "paper_daily.json"
    try:
        last = json.loads(state_path.read_text(encoding="utf-8")).get("sent")
    except (OSError, ValueError):
        last = None
    today = now.date().isoformat()
    if not daily_due((now.hour, now.minute), today, last):
        return
    creds = load_telegram_credentials(root / ".env")
    if creds is None:
        return
    bot = TelegramNotifier(creds)
    try:
        bot.send(paper_text(root).replace("📒 Paper 요약", f"📒 오늘의 Paper ({today})", 1))
    except NotifyError as exc:
        print(f"Paper 요약 실패: {exc}")  # 다음 실행(2 분 뒤)에서 다시
        return
    finally:
        bot.close()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"sent": today}), encoding="utf-8")
    print("Paper 요약 보냄")


def _namu_follow(root: Path) -> None:
    """나무 모의 계좌로 국장 수급 Paper 따라가기 — 거래일 15:00 ~ 15:20 하루 한 번 (가상 700 만)."""
    if not user_settings.get(root, "namu.follow"):
        return
    try:
        from scripts import namu_follow

        out = namu_follow.run(root)
        if "skip" not in out:
            print(f"나무 모의 따라가기: 주문 {len(out['orders'])} 건, 평가 {out['equity']:,}원")
    except Exception as exc:  # 모의 따라가기 문제로 봇을 멈추지 않는다
        print(f"나무 모의 따라가기 건너뜀: {type(exc).__name__}: {exc}")


def _ag4s2_demo(root: Path) -> None:
    """AG4S2 바이낸스 선물 데모(가짜 돈) — 하루 한 번 롱 맞추기 · 4 시간봉마다 숏 점검."""
    if not user_settings.get(root, "demo.ag4s2"):
        return
    try:
        from scripts import ag4s2_testnet

        done = ag4s2_testnet.tick()
        if done:
            print(f"AG4S2 데모: {len(done)} 줄")
    except Exception as exc:  # 데모 문제로 봇을 멈추지 않는다
        print(f"AG4S2 데모 건너뜀: {type(exc).__name__}: {exc}")


def _event_alert(root: Path) -> None:
    """미국 주요 경제 일정 경고(아침 · 프리마켓 전). 실패해도 현황 봇은 그대로 끝난다."""
    try:
        from scripts import event_alert

        sent = event_alert.maybe_send(root)
        if sent:
            print(f"일정 알림 보냄: {sent}")
    except Exception as exc:  # 일정 알림 문제로 봇을 멈추지 않는다
        print(f"일정 알림 건너뜀: {type(exc).__name__}: {exc}")


def _resume_liquidation() -> None:
    """진행 중인 '전부 정리' 가 있으면 이어 한다 (토스는 장 열린 뒤 · 10 분 미체결 다시 내기)."""
    from auto_trading.core.errors import AutoTradingError
    from scripts import liquidate as liq

    try:
        liq.resume()
    except (AutoTradingError, KeyError, OSError) as exc:
        print(f"정리 이어 하기 실패: {exc}")


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        return run()
    if argv == ["pin-help"]:
        print(pin_help())
        return 0
    if argv == ["show"]:
        sys.stdout.write(report() + "\n")
        return 0
    raise SystemExit("usage: python -m scripts.telegram_bot run|show|pin-help")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
