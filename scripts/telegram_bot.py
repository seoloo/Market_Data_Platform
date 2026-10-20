"""텔레그램 투자 봇 — 현황 · Paper · 정리 · 정지풀기 · 적립 · 개별주 명령에 답한다 (2026-10-10 ~).

언제 실행: 2 분마다 ``AutoTrading_Telegram_Bot`` (``run``). 같은 실행에서 정리 이어 하기 ·
           개별주 감시 · 경제 일정 · 나무 모의 · 그림자 기록 · 매일 Paper 요약 ·
           매일 안전 점검도 돈다.
읽고 쓰는 것: 텔레그램(받은 글 대기열 ``data/telegram/inbox.json``) · 장부들(현황) ·
              버튼 번호 ``data/telegram/acc_buttons`` · ``data/size_guard/telegram`` · 설정.

사용 예::

    python -m scripts.telegram_bot run    # 받은 명령이 있으면 답장 (예약: 2 분마다)
    python -m scripts.telegram_bot show   # 보내지 않고 화면에만

* 받은 글은 Deneb 대화와 같은 대기열(``data/telegram/inbox.json``)로 나눠 읽는다.
  이 봇은 명령(현황 · 수익 · 잔고 · /status)과 '전부 정리'(정리 · 정지풀기 · 그 버튼)만 가져가고,
  금액 · 버튼 답은 Deneb 몫으로 남긴다.
* **'정리'** (2026-10-11): 미리 보기 + 범위 버튼(전부 · Vega · Deneb · 적립 · 취소, 5 분 · 한 번).
  누르면 ``scripts/liquidate.py`` 가 그룹 정지 → 토스 매수 취소 → 지정가 매도. 이 실행 안에서
  2 분 동안 버튼을 기다린다. 매 실행마다 진행 중 정리를 이어 한다(토스 장 시간 · 미체결 다시 내기).
* **'개별주 NVDA'** (2026-10-16): 비중 추천(scripts/size_guard) + [🛒 이대로 매수] [취소] 버튼
  (5 분 · 한 번). 누르면 다시 확인한 뒤 예약 → 정규장 시작 직후 지정가.
  매 실행(2 분)마다 예약 매수 · −3% 손절 감시를 돌린다.
  주문은 버튼을 눌렀을 때만 · 설정 ``stock.orders`` · 토스 주문 허용일 때만.
* '현황' 내용은 대시보드 실거래 홈과 같다: 장부 + 공개 시세(키 없음) + 실제 계좌 동기화.

디자인 패턴: **Command + 목록표** — 받은 명령을 종류별로 나누는 표(:data:`COMMAND_KINDS`)와
매 실행 부가 작업 표(:data:`BACKGROUND_JOBS`) 하나씩 (리팩토링 5 단계, 2026-10-21).
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from auto_trading.app import live_accounts
from auto_trading.config.secrets import load_telegram_credentials
from auto_trading.notify.channels import NotifyError, TelegramNotifier
from auto_trading.notify.heartbeat import beat_and_alert
from auto_trading.notify.inbox import (
    DEPOSIT_WORDS,
    HELP_WORDS,
    LIQ_WORDS,
    PAPER_WORDS,
    UNHALT_WORDS,
    InboxMessage,
    InboxQueue,
    TelegramInbox,
    acc_ask_info,
    clear_acc_ask,
    clear_stock_ask,
    is_acc,
    is_acc_answer,
    is_command,
    is_liquidation,
    is_stock,
    is_stock_answer,
    liquidation_symbol,
    set_acc_ask,
    set_stock_ask,
    stock_ask_info,
)
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import GROUPS
from auto_trading.ops.liquidate import SCOPES, Store
from auto_trading.ops.paper_summary import paper_text
from auto_trading.ui.live_home import build_books, fetch_quotes, needed, summary_text

# 현황을 그 순간 실제 계좌로 맞출 조회 창구 (ui 는 거래소를 직접 부르지 않는다, 2026-10-21)
live_accounts.install()

REPO = Path(__file__).resolve().parent.parent


def inbox_path(root: Path = REPO) -> Path:
    """[쓰임] 받은 글 대기열 파일 (Deneb 대화와 나눠 읽는다)."""
    return root / "data" / "telegram" / "inbox.json"


def _start_offset(root: Path) -> int:
    """대기열이 처음 생길 때 — Deneb 이 쓰던 offset 에서 잇는다."""
    try:
        return int(
            json.loads((root / "data" / "toss" / "DENEB.json").read_text("utf-8"))["tg_offset"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def report(root: Path = REPO) -> str:
    """[쓰임] '현황' 답 — 실거래 요약(대시보드와 같은 계산) + 파이어 목표 % + 💹 개별주 현황."""
    coins, tickers = needed(root)
    quotes = fetch_quotes(coins, tickers)
    books = build_books(root, quotes)
    text = summary_text(books, quotes, int(time.time() * 1000))
    value = sum(book.value_krw for book in books)
    target = float(user_settings.get(root, "fire.target_krw"))
    text += f"\n🎯 파이어 목표 {target / 1e8:g}억까지 {value / target:.1%}"
    stock = _stock_summary()
    return text + ("\n\n" + "\n".join(stock) if stock else "")


def _stock_summary() -> list[str]:
    """💹 개별주 예약 · 보유 · 손절 거리 (2026-10-16 '현황에 개별주'). 실패해도 현황은 보낸다."""
    try:
        from scripts import size_guard

        return size_guard.summary_lines()
    except Exception as exc:
        print(f"개별주 현황 건너뜀: {type(exc).__name__}: {exc}")
        return []


HELP = """📌 투자 봇 사용법 — 이렇게 보내 보세요

📊 현황 (또는 /status · 수익 · 잔고)
  실거래 요약: Deneb · Vega · 자동 적립 + 파이어 목표까지 %

📒 Paper (또는 /paper · 페이퍼)
  가상 장부 요약: 코인 · AG/AGS · 미국 주식 · 국장 주도주 (매일 21:30 자동으로도 와요)

🧯 정리 (또는 /liquidate · 청산 · 전부매도)
  보유를 지정가로 팔기 — 미리 보기 뒤 버튼:
  [전부 정리] [Vega 만] [Deneb 만] [자동 적립 (종목 고르기)] [개별주만]
  [Altair 만] [Sirius 만] [취소]
  자동 적립은 종목을 골라 그 종목 적립 몫만 팔고, 그 종목 적립도 멈춰요
  버튼은 5분 안에 한 번만. 수동 보유(APH 등)는 안 팔아요.
  정리 NVDA (또는 정리 삼성전자) — 그 종목 하나만 (Deneb · 적립 몫 빼고 전부)

💰 적립 (또는 /acc)
  자동 적립 종목 보기 · [➕ 종목 추가] · [✏️ 금액] · [➖ 빼기] — 내일 적립분부터, 보유는 그대로
  추가는 답장으로 '기호 금액' (예: PLTR 3 · 삼성전자 10000) → [✅ 추가]
  [✏️ 금액] 누르면 새 하루 금액을 답장 → [✅ 바꾸기] (미국 $0.5 ~ 20 · 국내 ₩500 ~ 30,000)
  한 줄로도: 적립 추가 PLTR 3 · 적립 금액 BTQ 3 · 적립 빼기 BTQ

💹 개별주 NVDA (또는 개별주 삼성전자 · /stock)
  얼마나 살지 추천: 현금(원화 + 달러)의 0.3% 까지만 잃게, 손절 −3% · 한 종목 최대 10%
  실적 3거래일 안 · 추세 아님 · 지수 200일선 아래(절반)면 안 사거나 줄여요
  → [🛒 N주 매수] 누르면 정규장 시작 직후 지정가로 사고, −3% 면 자동으로 팔아요
  → [✏️ 수량 바꾸기] · 규칙에 걸리면 [✏️ 그래도 매수] — 몇 주인지 답장 → [✅ 예약]
     (한 종목 10% · 현금 안에서만, 손절 −3% 는 그대로)
  메뉴(/stock)나 '개별주' 만 보내면 "어떤 종목을 살까요?" 라고 물어요 → 종목 이름으로 답장
  (예약 · 손절 중인 종목이 있으면 현황 + [손절 해제] 버튼도)

▶ 정지풀기 (또는 /unhalt)
  정리 뒤 멈춘 그룹 다시 켜기 → 다음 실행부터 규칙대로 다시 사요

💵 이번 달 입금 (두 곳 합계)
  매달 매수일 아침 질문에 합계로 답: 100만 · 1000000 · 0(쉬기)
  → Deneb 90 (토스) · Vega 10 (업비트 원화) 으로 나눠 어디에 얼마 넣을지 알려 줘요

🔘 제안 버튼 (배당 단계 · 12월 공제 실현)
  메시지 아래 버튼으로 답해요

❓ 도움 (또는 /help) — 이 안내

이 방으로 오는 알림: Deneb · Vega · 토스 매매 결과, 환전 요청, 위험 알림,
경제 일정 · 실적 경고(CPI · FOMC · 보유 종목 실적 등 07:35 · 16:30),
매일 21:30 Paper 요약
종목 뉴스는 정보 봇에 물어보세요."""
COMMANDS_MENU = [
    ("status", "현황 — 실거래 요약"),
    ("paper", "Paper — 가상 장부 요약"),
    ("stock", "개별주 — 비중 추천 · 장 시작 매수 · −3% 손절"),
    # 2026-10-19 사용자 "입금 같은 경우는 바이낸스에 돈을 넣기 위해서 넣었는데 그냥 주석쳐서
    # 메뉴에서 지워줘" — Vega 를 업비트로 되돌리고 Altair 를 정리해 바이낸스 입금 확인이 필요 없다.
    # ("deposit", "입금 — 바이낸스 입금 확인 · Vega / Altair 로 나누기"),
    ("acc", "적립 — 자동 적립 종목 추가 · 빼기"),
    ("liquidate", "정리 — 보유 팔기 (미리 보기 + 버튼)"),
    ("unhalt", "정지풀기 — 멈춘 그룹 다시 켜기"),
    ("help", "사용법"),
]
"""입력칸 '/' 메뉴 (텔레그램 명령은 영문만)."""


def pin_help(root: Path = REPO) -> str:
    """사용법을 보내고 대화방 공지로 고정한다 (예전 고정은 모두 푼다) + '/' 메뉴."""
    credentials = load_telegram_credentials(root / ".env")
    if credentials is None:
        return "텔레그램 설정 없음"
    bot = TelegramNotifier(credentials)
    try:
        message_id = bot.send_message(HELP)
        bot.unpin_all()
        bot.pin(message_id)
        bot.set_commands(COMMANDS_MENU)
    finally:
        bot.close()
    return f"투자 봇 공지 고정 (메시지 {message_id})"


LIQ_WAIT_S = 120
"""'정리' 범위 버튼을 이 실행 안에서 기다리는 시간 (다음 예약 실행을 기다리지 않게)."""


def _liq_buttons(nonce: str) -> list[list[dict[str, str]]]:
    """[쓰임] '정리' 범위 버튼들 (버튼 값 ``liq:<번호>:<범위>``)."""

    def button(text: str, arg: str) -> dict[str, str]:
        return {"text": text, "callback_data": f"liq:{nonce}:{arg}"}

    return [
        [button("🧯 전부 정리", "all")],
        [button("Vega 만", "vega"), button("Deneb 만", "deneb")],
        [button("자동 적립 (종목 고르기)", "accmenu"), button("개별주만", "stock")],
        [button("Altair 만", "altair"), button("Sirius 만", "sirius")],
        [button("취소", "cancel")],
    ]


def handle_liquidation(bot: TelegramNotifier, msg: InboxMessage) -> bool:
    """[쓰임] '정리' · '정지풀기' 글과 그 버튼. 버튼을 보냈으면 True (이 실행에서 답을 기다린다).
    [이유] 버튼 번호는 5 분 · 한 번만 — 오래된 버튼이나 두 번 누름으로 정리가 다시 시작되지 않게."""
    from scripts import liquidate

    now = int(time.time() * 1000)
    if msg.data:
        if msg.callback_id:
            with contextlib.suppress(NotifyError):
                bot.answer_callback(msg.callback_id, "접수했어요")
        parts = msg.data.split(":")
        if len(parts) != 3:
            return False
        kind, nonce, arg = parts
        if not liquidate.STORE.use_request(nonce, now, kind):
            bot.send("⌛ 지난 버튼이에요 (5 분 지남 · 이미 누름) — 다시 보내 주세요")
            return False
        if arg == "cancel":
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, "취소했어요 — 아무것도 바꾸지 않았어요")
            return False
        if kind == "liq" and arg == "accmenu":  # 자동 적립 — 종목을 골라 정리 (2026-10-18)
            return _acc_liq_menu(bot, liquidate, now, msg.message_id)
        if kind == "liq" and arg.startswith("a="):
            sym = arg[2:]
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🧯 자동 적립 {sym} 정리 시작 — 결과를 곧 보내요")
            liquidate.start(f"acc:{sym}", "텔레그램")
            return False
        if kind == "liq" and arg.startswith("s="):
            sym = arg[2:]
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🧯 {sym} 정리 시작 — 결과를 곧 보내요")
            liquidate.start(f"sym:{sym}", "텔레그램")
        elif kind == "liq" and arg in SCOPES:
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🧯 {SCOPES[arg]} 정리 시작 — 결과를 곧 보내요")
            liquidate.start(arg, "텔레그램")  # 결과는 start 가 보낸다
        elif kind == "unh" and arg in GROUPS:
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"▶ {GROUPS[arg]} 정지 풀기")
            liquidate.unhalt(arg, "텔레그램")
        return False
    query = liquidation_symbol(msg)
    if query:
        from scripts import size_guard

        try:
            symbol, name = size_guard.resolve(query)
        except SystemExit as exc:  # 여러 종목이 맞음
            bot.send(f"⚠ {exc}")
            return False
        nonce = liquidate.STORE.new_request(now, "liq")
        rows = [
            [{"text": f"🧯 {name} 정리", "callback_data": f"liq:{nonce}:s={symbol}"}],
            [{"text": "취소", "callback_data": f"liq:{nonce}:cancel"}],
        ]
        bot.send_message(
            liquidate.preview_text(f"sym:{symbol}") + "\n\n누르면 바로 시작해요 (5 분 안, 한 번만)",
            buttons=rows,
        )
        return True
    word = msg.text.strip().lower().replace(" ", "")
    if word in LIQ_WORDS:
        nonce = liquidate.STORE.new_request(now, "liq")
        text = liquidate.preview_text("all")
        bot.send_message(
            text + "\n\n범위를 누르면 바로 시작해요 (5 분 안, 한 번만)",
            buttons=_liq_buttons(nonce),
        )
        return True
    if word in UNHALT_WORDS:
        halted = {group: reason for group, reason in liquidate.halts().items() if reason}
        if not halted:
            extra = " (전체 비상 정지는 설정 화면에서)" if root_halt().exists() else ""
            bot.send("정지 중인 그룹이 없어요" + extra)
            return False
        nonce = liquidate.STORE.new_request(now, "unh")
        rows = [
            [{"text": f"{GROUPS[group]} 풀기", "callback_data": f"unh:{nonce}:{group}"}]
            for group in halted
        ]
        rows.append([{"text": "취소", "callback_data": f"unh:{nonce}:cancel"}])
        lines = [f"⛔ {GROUPS[group]}: {reason}" for group, reason in halted.items()]
        bot.send_message(
            "\n".join([*lines, "", "풀면 다음 실행부터 그 전략이 자기 규칙대로 다시 사요"]),
            buttons=rows,
        )
        return True
    return False


def _acc_liq_menu(bot: TelegramNotifier, liquidate: Any, now: int, message_id: int) -> bool:
    """[쓰임] 정리 → 자동 적립: 적립 몫이 있는 종목마다 버튼 + 적립 전부 + 취소."""
    items, notes = liquidate.plan("acc")
    nonce = liquidate.STORE.new_request(now, "liq")
    rows = [
        [
            {
                "text": f"🧯 {item.symbol} {item.want:f}주",
                "callback_data": f"liq:{nonce}:a={item.symbol}",
            }
        ]
        for item in items
    ]
    if items:
        rows.append([{"text": "🧯 적립 전부 정리", "callback_data": f"liq:{nonce}:acc"}])
    rows.append([{"text": "취소", "callback_data": f"liq:{nonce}:cancel"}])
    lines = ["🧯 자동 적립 — 어느 종목을 정리할까요? (적립 몫만 · 지정가)"]
    lines += [f"· {item.symbol} 적립 몫 {item.want:f}주" for item in items] or [
        "팔 적립 몫이 없어요"
    ]
    lines += [f"· {note}" for note in notes]
    lines.append("종목을 고르면 그 종목 적립도 멈춰요(목록에서 빼기) — 다시 하려면 '적립' → 추가")
    with contextlib.suppress(NotifyError):
        bot.edit(message_id, "자동 적립 종목 고르기 ↓")
    bot.send_message("\n".join(lines), buttons=rows)
    return True


# ------------------------------------------------------------------ 💰 자동 적립 추가 · 빼기

ACC_STORE = Store(REPO / "data" / "telegram" / "acc_buttons")
"""'적립' 버튼 번호 (5 분 · 한 번) — 정리 버튼과 같은 방식."""
ACC_PENDING = REPO / "data" / "telegram" / "acc_pending.json"
"""확인 버튼이 가리키는 할 일 (번호 → 추가 · 빼기 · 금액 내용)."""
ACC_WAIT_S = 120
"""'적립' 버튼 · 답장을 이 실행 안에서 기다리는 시간."""


def _acc_parse(text: str) -> tuple[str, str, str]:
    """'PLTR 3' · '삼성전자 10000' · '005930 5000원' → (찾을 말, US · KR, 하루 금액 글)."""
    import re

    parts = text.strip().split()
    daily = ""
    if len(parts) >= 2:
        last = parts[-1].replace(",", "").replace("$", "").replace("원", "").replace("₩", "")
        try:
            float(last)
            daily, parts = last, parts[:-1]
        except ValueError:
            pass
    query = " ".join(parts)
    korean = re.fullmatch(r"\d{6}", query) is not None or re.search("[가-힣]", query) is not None
    return query, "KR" if korean else "US", daily


def _acc_pending(nonce: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """확인 버튼이 가리키는 할 일 (번호 → 추가 · 빼기 내용). 10 분 지난 것은 지운다."""
    try:
        pending: dict[str, Any] = json.loads(ACC_PENDING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pending = {}
    if nonce is not None and payload is not None:
        pending = {
            key: value
            for key, value in pending.items()
            if time.time() - float(value.get("at", 0)) < 600
        }
        pending[nonce] = {**payload, "at": time.time()}
        ACC_PENDING.parent.mkdir(parents=True, exist_ok=True)
        ACC_PENDING.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    return pending


def _acc_unit(item: dict[str, Any]) -> str:
    """[쓰임] 하루 금액 글 — 국내 '₩5,000' · 미국 '$1'."""
    return f"₩{item['daily']:,.0f}" if item["mkt"] == "KR" else f"${item['daily']:g}"


def _acc_menu(bot: TelegramNotifier, root: Path) -> bool:
    """[쓰임] '적립' 메뉴 — 지금 종목 목록 + [➕ 추가] · 종목마다 [✏️ 금액] [➖ 빼기] · [닫기]."""
    items = user_settings.acc_items(user_settings.load(root))
    nonce = ACC_STORE.new_request(int(time.time() * 1000), "acc")
    lines = ["💰 자동 적립 종목 — 바꾸면 내일 적립분부터 (보유는 그대로)"]
    lines += [
        f"· {item['name']}({item['sym']}) 하루 {_acc_unit(item)}{'' if item['on'] else ' · 꺼짐'}"
        for item in items
    ] or ["· 적립 종목 없음"]
    lines.append(
        f"최대 {user_settings.MAX_ITEMS} 개 · QNDX 는 QS 전략과 얽혀 여기서 못 바꿔요 · "
        "보유를 팔려면 '정리' → 자동 적립"
    )
    rows = [[{"text": "➕ 종목 추가", "callback_data": f"acc:{nonce}:add"}]]
    rows += [
        [
            {"text": f"✏️ {item['sym']} 금액", "callback_data": f"acc:{nonce}:amt={item['key']}"},
            {"text": f"➖ {item['sym']} 빼기", "callback_data": f"acc:{nonce}:rm={item['key']}"},
        ]
        for item in items
    ]
    rows.append([{"text": "닫기", "callback_data": f"acc:{nonce}:cancel"}])
    bot.send_message("\n".join(lines), buttons=rows)
    return True


def _acc_confirm_add(bot: TelegramNotifier, root: Path, text: str) -> bool:
    """[쓰임] '기호 금액' 답 → 설정 화면과 같은 검사 → 확인 카드 [✅ 추가] [취소].
    [이유] 바로 저장하지 않고 한 번 더 묻는다 — 잘못 찾은 종목에 매일 돈이 들어가지 않게."""
    from auto_trading.ops.kr_accumulate import read_universe

    query, mkt, daily = _acc_parse(text)
    if not query:
        bot.send("⚠ 종목을 적어 주세요 — 예: PLTR 3 · 삼성전자 10000")
        return False
    current = user_settings.load(root)
    universe = read_universe(root / "data" / "toss" / "universe.json")
    new, errors = user_settings.add_item(current, query, mkt, daily, universe)
    if errors:
        bot.send("⚠ " + " · ".join(errors.values()) + " — 다시 '적립' 부터 해 주세요")
        return False
    changes = user_settings.diff(current, new)
    nonce = ACC_STORE.new_request(int(time.time() * 1000), "acc")
    _acc_pending(nonce, {"action": "add", "query": query, "mkt": mkt, "daily": daily})
    rows = [
        [{"text": "✅ 추가", "callback_data": f"acc:{nonce}:ok"}],
        [{"text": "취소", "callback_data": f"acc:{nonce}:cancel"}],
    ]
    what = changes[0].label if changes else query
    bot.send_message(f"💰 {what} — 내일 적립분부터 사요. 맞으면 [✅ 추가]", buttons=rows)
    return True


def _acc_confirm_rm(bot: TelegramNotifier, root: Path, key: str) -> bool:
    """[쓰임] 빼기 확인 카드 [✅ 빼기] [취소] (내일 적립분부터, 보유는 그대로)."""
    items = {item["key"]: item for item in user_settings.acc_items(user_settings.load(root))}
    item = items.get(key)
    if item is None:
        bot.send("⚠ 이미 목록에 없는 종목이에요")
        return False
    nonce = ACC_STORE.new_request(int(time.time() * 1000), "acc")
    _acc_pending(nonce, {"action": "rm", "key": key})
    rows = [
        [{"text": f"✅ {item['sym']} 빼기", "callback_data": f"acc:{nonce}:ok"}],
        [{"text": "취소", "callback_data": f"acc:{nonce}:cancel"}],
    ]
    bot.send_message(
        f"💰 {item['name']}({item['sym']}) 하루 {_acc_unit(item)} 적립을 목록에서 뺄까요?\n"
        "내일 적립분부터 안 사요 · 보유는 그대로 (팔려면 '정리' → 자동 적립)",
        buttons=rows,
    )
    return True


def _acc_ask_amount(bot: TelegramNotifier, root: Path, key: str) -> bool:
    """[쓰임] [✏️ 금액] → '새 하루 금액을 답장해 주세요' (답장 입력칸을 연다)."""
    items = {item["key"]: item for item in user_settings.acc_items(user_settings.load(root))}
    item = items.get(key)
    if item is None:
        bot.send("⚠ 이미 목록에 없는 종목이에요")
        return False
    allowed_range = "₩500 ~ ₩30,000" if item["mkt"] == "KR" else "$0.5 ~ $20"
    message_id = bot.send_message(
        f"✏️ {item['name']}({item['sym']}) 지금 하루 {_acc_unit(item)}"
        " — 새 하루 금액을 답장해 주세요"
        f" ({allowed_range})",
        force_reply=True,
        placeholder="예: 3" if item["mkt"] == "US" else "예: 2000",
    )
    set_acc_ask(message_id, int(time.time()), kind="amt", key=key)
    return True


def _acc_confirm_amount(bot: TelegramNotifier, root: Path, key: str, text: str) -> bool:
    """[쓰임] 새 하루 금액 답 → 설정 화면과 같은 범위 검사 → 확인 카드 [✅ 바꾸기] [취소]."""
    amount = text.strip().replace(",", "").replace("$", "").replace("원", "").replace("₩", "")
    current = user_settings.load(root)
    new, errors = user_settings.set_daily(current, key, amount)
    if errors:
        bot.send("⚠ " + " · ".join(errors.values()) + " — 다시 '적립' 부터 해 주세요")
        return False
    changes = user_settings.diff(current, new)
    if not changes:
        bot.send("지금 금액과 같아요 — 바꾸지 않았어요")
        return False
    change = changes[0]
    nonce = ACC_STORE.new_request(int(time.time() * 1000), "acc")
    _acc_pending(nonce, {"action": "amt", "key": key, "daily": amount})
    rows = [
        [{"text": "✅ 바꾸기", "callback_data": f"acc:{nonce}:ok"}],
        [{"text": "취소", "callback_data": f"acc:{nonce}:cancel"}],
    ]
    bot.send_message(
        f"✏️ {change.label} {user_settings.show(change.key, change.before)} → "
        f"{user_settings.show(change.key, change.after)} — 내일 적립분부터. 맞으면 [✅ 바꾸기]",
        buttons=rows,
    )
    return True


def _acc_apply(bot: TelegramNotifier, root: Path, payload: dict[str, Any]) -> None:
    """[쓰임] [✅] 누름 → 그때 설정으로 다시 검사해 저장(추가 · 빼기 · 금액) → 결과 한 통.
    [이유] 확인 카드를 보낸 뒤 설정이 바뀌었을 수 있어, 저장 직전에 다시 검사한다."""
    from datetime import UTC, datetime

    from auto_trading.ops.kr_accumulate import read_universe

    current = user_settings.load(root)
    if payload.get("action") == "rm":
        try:
            new = user_settings.remove_item(current, str(payload["key"]))
        except ValueError as exc:
            bot.send(f"⚠ {exc}")
            return
    elif payload.get("action") == "amt":
        new, errors = user_settings.set_daily(current, str(payload["key"]), str(payload["daily"]))
        if errors:
            bot.send("⚠ " + " · ".join(errors.values()))
            return
    else:
        universe = read_universe(root / "data" / "toss" / "universe.json")
        new, errors = user_settings.add_item(
            current, str(payload["query"]), str(payload["mkt"]), str(payload["daily"]), universe
        )
        if errors:
            bot.send("⚠ " + " · ".join(errors.values()))
            return
    changes = user_settings.diff(current, new)
    user_settings.apply(root, changes, datetime.now(UTC), by="텔레그램", new=new)

    def line(change: user_settings.Change) -> str:
        if change.key.endswith((".usd", ".daily")):
            before = user_settings.show(change.key, change.before)
            after = user_settings.show(change.key, change.after)
            return f"{change.label} {before} → {after}"
        return change.label

    bot.send("✅ " + " · ".join(line(change) for change in changes) + " — 내일 적립분부터")


def handle_acc(bot: TelegramNotifier, msg: InboxMessage, root: Path = REPO) -> bool:
    """[쓰임] '적립' 메뉴 · 버튼 · 추가 답장 · '적립 추가 PLTR 3' · '적립 금액 BTQ 3' ·
    '적립 빼기 BTQ'. 버튼 · 답장을 기다려야 하면 True."""
    if msg.data:
        if msg.callback_id:
            with contextlib.suppress(NotifyError):
                bot.answer_callback(msg.callback_id, "접수했어요")
        parts = msg.data.split(":", 2)
        if len(parts) != 3:
            return False
        _kind, nonce, arg = parts
        if not ACC_STORE.use_request(nonce, int(time.time() * 1000), "acc"):
            bot.send("⌛ 지난 버튼이에요 (5 분 지남 · 이미 누름) — '적립' 을 다시 보내 주세요")
            return False
        if arg == "cancel":
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, "닫았어요 — 아무것도 바꾸지 않았어요")
            return False
        if arg == "add":
            message_id = bot.send_message(
                "➕ 추가할 종목과 하루 금액을 답장해 주세요\n"
                "미국: 기호 + 달러 (예: PLTR 3) · 국내: 이름이나 6 자리 코드 + 원"
                " (예: 삼성전자 10000)\n금액을 빼면 기본(미국 $1 · 국내 5,000원)",
                force_reply=True,
                placeholder="예: PLTR 3",
            )
            set_acc_ask(message_id, int(time.time()))
            return True
        if arg.startswith("rm="):
            return _acc_confirm_rm(bot, root, arg[3:])
        if arg.startswith("amt="):
            return _acc_ask_amount(bot, root, arg[4:])
        if arg == "ok":
            payload = _acc_pending().get(nonce)
            if payload is None:
                bot.send("⌛ 확인할 내용이 사라졌어요 — '적립' 부터 다시 해 주세요")
                return False
            _acc_apply(bot, root, payload)
        return False
    if is_acc_answer(msg):
        ask = acc_ask_info()
        clear_acc_ask()
        if ask.get("kind") == "amt":
            return _acc_confirm_amount(bot, root, str(ask.get("key")), msg.text)
        return _acc_confirm_add(bot, root, msg.text)
    parts = msg.text.strip().split(maxsplit=2)
    if len(parts) >= 3 and parts[1] in ("추가", "더하기"):
        return _acc_confirm_add(bot, root, parts[2])
    if len(parts) >= 3 and parts[1] in ("금액", "변경", "바꾸기"):
        words = parts[2].split()
        hit = [
            item
            for item in user_settings.acc_items(user_settings.load(root))
            if words and item["sym"] == words[0].upper()
        ]
        if len(words) != 2 or not hit:
            bot.send("⚠ '적립 금액 BTQ 3' 처럼 목록에 있는 종목과 새 하루 금액을 적어 주세요")
            return False
        return _acc_confirm_amount(bot, root, hit[0]["key"], words[1])
    if len(parts) >= 3 and parts[1] in ("빼기", "삭제", "제거"):
        symbol = parts[2].strip().upper()
        hit = [
            item
            for item in user_settings.acc_items(user_settings.load(root))
            if item["sym"] == symbol
        ]
        if not hit:
            bot.send(f"⚠ {symbol} 는 적립 목록에 없어요")
            return False
        return _acc_confirm_rm(bot, root, hit[0]["key"])
    return _acc_menu(bot, root)


def _acc(
    bot: TelegramNotifier, msgs: list[InboxMessage], queue: InboxQueue, inbox: TelegramInbox
) -> None:
    """[쓰임] '적립' 글들을 처리하고, 버튼 · 답장을 기다려야 하면 이 실행 안에서 2 분까지."""
    from auto_trading.core.errors import AutoTradingError

    try:
        waiting = False
        for message in msgs:
            waiting = handle_acc(bot, message) or waiting
        deadline = time.monotonic() + ACC_WAIT_S
        while waiting and time.monotonic() < deadline:
            time.sleep(3)
            for message in queue.take(inbox, is_acc):
                if handle_acc(bot, message):
                    deadline = time.monotonic() + ACC_WAIT_S
    except (AutoTradingError, KeyError, OSError, ValueError) as exc:
        bot.send(f"⚠ 적립 처리 오류: {type(exc).__name__} {exc}"[:500])


STOCK_STORE = Store(REPO / "data" / "size_guard" / "telegram")
"""개별주 버튼 번호 (5 분 · 한 번) — 정리 버튼과 같은 방식."""
STOCK_WAIT_S = 120


def _stock_query(text: str) -> str:
    """'개별주 NVDA' · '/stock 삼성전자' · '개별주NVDA' → 'NVDA' · '삼성전자' (없으면 빈 값)."""
    from auto_trading.notify.inbox import STOCK_PREFIXES

    stripped = text.strip()
    lowered = stripped.lower()
    for prefix in sorted(STOCK_PREFIXES, key=len, reverse=True):
        if lowered.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return stripped


def _polaris(bot: TelegramNotifier, msg: InboxMessage) -> bool:
    """[쓰임] 🧭 Polaris 회차 버튼 ([🛒 매수] · [이번 달 건너뛰기]) → Polaris 실행기에 넘긴다."""
    from scripts import polaris

    if msg.callback_id:
        with contextlib.suppress(NotifyError):
            bot.answer_callback(msg.callback_id, "접수했어요")
    parts = msg.data.split(":", 2)
    if len(parts) != 3 or parts[2] not in ("buy", "skip"):
        return False
    reply = polaris.on_buy(parts[1]) if parts[2] == "buy" else polaris.on_skip(parts[1])
    with contextlib.suppress(NotifyError):
        bot.edit(msg.message_id, reply)
    return False


def handle_stock(bot: TelegramNotifier, msg: InboxMessage, root: Path = REPO) -> bool:
    """[쓰임] '개별주 …' 글과 그 버튼 (추천 · 예약 · 수량 · 손절 해제 · 월간 추천 · PEAD).
    버튼을 보냈으면 True (이 실행에서 답을 기다린다).
    [이유] 주문은 버튼을 눌렀을 때만 · 설정 ``stock.orders`` 가 켜졌을 때만."""
    from scripts import size_guard

    if msg.data.startswith("pol:"):  # 🧭 Polaris 회차 버튼 — 나무, 토스 개별주 설정과 별개
        return _polaris(bot, msg)
    now = int(time.time() * 1000)
    if not user_settings.get(root, "stock.orders"):
        bot.send("💹 개별주 주문이 설정에서 꺼져 있어요 (오르빗 설정 '개별주 비중 주문')")
        return False
    if msg.data:
        if msg.callback_id:
            with contextlib.suppress(NotifyError):
                bot.answer_callback(msg.callback_id, "접수했어요")
        parts = msg.data.split(":", 2)
        if len(parts) != 3:
            return False
        kind, nonce, arg = parts
        if kind in (
            "rcb",
            "rcm",
            "rcs",
            "rsl",
            "rsk",
        ):  # 월간 추천 · 매도 신호 — 5 일 유효(추천 기록이 지킴)
            from scripts import stock_reco as sr

            word = {
                "rcb": "🛒 예약 확인 중…",
                "rcm": "📋 보내는 중…",
                "rcs": "건너뜀",
                "rsl": "🧹 매도 예약 중…",
                "rsk": "계속 보유",
            }
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"{word[kind]} ({arg})" if arg != "x" else word[kind])
            try:
                reply = {
                    "rcb": lambda: sr.on_buy(nonce, arg),
                    "rcm": lambda: sr.on_more(nonce),
                    "rcs": lambda: sr.on_skip(nonce),
                    "rsl": lambda: sr.on_sell(nonce, arg),
                    "rsk": lambda: sr.on_keep(nonce),
                }[kind]()
            except (SystemExit, ValueError) as exc:
                reply = f"⚠ 처리 못 함 — {exc}"
            if reply:
                bot.send(reply)
            return False
        if kind == "stp":  # PEAD 알림 버튼 — 다음 정규장이 끝날 때까지 유효(알림 기록이 지킴)
            if arg == "cancel":
                with contextlib.suppress(NotifyError):
                    bot.edit(msg.message_id, "건너뛰었어요 — " + size_guard.skip_pead(nonce))
                return False
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🛒 PEAD {arg} 다시 확인하고 예약해요…")
            try:
                bot.send(size_guard.reserve_pead(nonce))
            except (SystemExit, ValueError) as exc:
                bot.send(f"⚠ PEAD 예약 못 함 — {exc}")
            return False
        if not STOCK_STORE.use_request(nonce, now, kind):
            bot.send("⌛ 지난 버튼이에요 (5 분 지남 · 이미 누름) — 다시 보내 주세요")
            return False
        if arg == "cancel":
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, "취소했어요 — 예약하지 않았어요")
            return False
        if kind == "stk":
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🛒 {arg} 다시 확인하고 예약해요…")
            try:
                bot.send(size_guard.reserve(arg))
            except SystemExit as exc:  # 다른 실행 중 · 종목 못 찾음
                bot.send(f"⚠ 예약 못 함 — {exc}")
        elif kind == "stkr":
            try:
                bot.send("🛡 손절 해제 — " + size_guard._set_status(arg, ("holding",), "released"))
            except SystemExit as exc:
                bot.send(f"⚠ 해제 못 함 — {exc}")
        elif kind == "stq":  # [수량 바꾸기] · [그래도 매수] → 몇 주?
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"✏️ {arg} 몇 주 살지 물어볼게요")
            return _ask_qty(bot, arg)
        elif kind == "sko":  # 수량 확인 → 예약
            symbol, _, qty_text = arg.rpartition(":")
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🛒 {symbol} {qty_text}주 다시 확인하고 예약해요…")
            try:
                bot.send(size_guard.reserve(symbol, qty=int(qty_text)))
            except (SystemExit, ValueError) as exc:
                bot.send(f"⚠ 예약 못 함 — {exc}")
        return False
    ask = stock_ask_info() if is_stock_answer(msg) else None
    if ask is not None:  # '어떤 종목?' · '몇 주?' 에 대한 답
        clear_stock_ask()
        if ask.get("kind") == "qty":
            return _confirm_qty(bot, str(ask.get("symbol") or ""), msg.text)
        query = msg.text.strip()
    else:
        query = _stock_query(msg.text)
    if not query:
        active = [ticket for ticket in size_guard.load_tickets() if ticket.active]
        holding = [ticket for ticket in active if ticket.status == "holding"]
        if active:
            nonce = STOCK_STORE.new_request(now, "stkr")
            rows = [
                [
                    {
                        "text": f"{ticket.name} 손절 해제",
                        "callback_data": f"stkr:{nonce}:{ticket.id}",
                    }
                ]
                for ticket in holding
            ]
            bot.send_message(
                "💹 지금 예약 · 손절 현황" + "\n\n" + size_guard.status(), buttons=rows or None
            )
        message_id = bot.send_message(
            "💹 어떤 종목을 살까요?"
            + "\n\n"
            + "종목 이름이나 기호를 답장해 주세요 (예: 삼성전자 · 현대차 · NVDA)"
            + "\n"
            + "5분 안에 답하면 얼마나 살지 추천해 드려요",
            force_reply=True,
            placeholder="종목 이름 (예: 삼성전자)",
        )
        set_stock_ask(message_id, int(time.time()))
        return True
    try:
        advice = size_guard.advise(query)
    except SystemExit as exc:
        bot.send(f"⚠ {exc}")
        return False
    from auto_trading.ops.position_size import advice_text

    text = advice_text(advice)
    if not advice.ok and not advice.can_override:
        bot.send(text)
        return False
    buy_nonce = STOCK_STORE.new_request(now, "stk")
    qty_nonce = STOCK_STORE.new_request(now, "stq")
    if advice.ok:
        rows = [
            [
                {
                    "text": f"🛒 {advice.qty}주 매수",
                    "callback_data": f"stk:{buy_nonce}:{advice.symbol}",
                },
                {"text": "✏️ 수량 바꾸기", "callback_data": f"stq:{qty_nonce}:{advice.symbol}"},
            ],
            [{"text": "취소", "callback_data": f"stk:{buy_nonce}:cancel"}],
        ]
    else:
        rows = [
            [
                {
                    "text": "✏️ 그래도 매수 (수량 입력)",
                    "callback_data": f"stq:{qty_nonce}:{advice.symbol}",
                }
            ],
            [{"text": "취소", "callback_data": f"stk:{buy_nonce}:cancel"}],
        ]
    bot.send_message(text + "\n\n누르면 다시 확인한 뒤 예약해요 (5 분 안, 한 번만)", buttons=rows)
    return True


def _ask_qty(bot: TelegramNotifier, symbol: str) -> bool:
    """'몇 주 살까요?' — 답장 입력칸을 열고 질문 기록(kind=qty)을 남긴다."""
    from auto_trading.ops.position_size import advice_text, qty_prompt_text
    from scripts import size_guard

    try:
        advice = size_guard.advise(symbol)
    except SystemExit as exc:
        bot.send(f"⚠ {exc}")
        return False
    if not advice.can_override:
        bot.send(advice_text(advice))
        return False
    message_id = bot.send_message(
        qty_prompt_text(advice), force_reply=True, placeholder="주 수 (예: 5)"
    )
    set_stock_ask(message_id, int(time.time()), kind="qty", symbol=advice.symbol)
    return True


def _confirm_qty(bot: TelegramNotifier, symbol: str, text: str) -> bool:
    """'5' · '5주' 답 → 마지막 확인 카드 + [✅ N주 예약] [취소]."""
    from auto_trading.ops.position_size import CAP, confirm_text
    from scripts import size_guard

    digits = text.strip().removesuffix("주").strip()
    if not digits.isdigit() or not symbol:
        bot.send("⚠ 주 수는 숫자로 답해 주세요 (예: 5) — 다시 '개별주' 부터 해 주세요")
        return False
    qty = int(digits)
    try:
        advice = size_guard.advise(symbol)
    except SystemExit as exc:
        bot.send(f"⚠ {exc}")
        return False
    if not advice.can_override or not 1 <= qty <= advice.max_qty:
        bot.send(
            f"⚠ {advice.name} 은 1 ~ {advice.max_qty}주만 살 수 있어요 (한 종목 {CAP:.0%} · 현금)"
        )
        return False
    nonce = STOCK_STORE.new_request(int(time.time() * 1000), "sko")
    rows = [
        [{"text": f"✅ {qty}주 예약", "callback_data": f"sko:{nonce}:{advice.symbol}:{qty}"}],
        [{"text": "취소", "callback_data": f"sko:{nonce}:cancel"}],
    ]
    bot.send_message(confirm_text(advice, qty), buttons=rows)
    return True


def _stock(
    bot: TelegramNotifier, msgs: list[InboxMessage], queue: InboxQueue, inbox: TelegramInbox
) -> None:
    """[쓰임] '개별주' 글들을 처리하고, 버튼 · 답장을 기다려야 하면 이 실행 안에서 2 분까지."""
    from auto_trading.core.errors import AutoTradingError

    try:
        waiting = False
        for message in msgs:
            waiting = handle_stock(bot, message) or waiting
        deadline = time.monotonic() + STOCK_WAIT_S
        while waiting and time.monotonic() < deadline:
            time.sleep(3)
            for message in queue.take(inbox, is_stock):
                more = handle_stock(bot, message)
                if more:  # 질문에 답해 추천 버튼을 보냄 → 버튼을 다시 기다린다
                    deadline = time.monotonic() + STOCK_WAIT_S
                elif message.data:
                    waiting = False
    except (AutoTradingError, KeyError, OSError, ValueError) as exc:
        bot.send(f"⚠ 개별주 처리 오류: {type(exc).__name__} {exc}"[:500])


def _size_guard(root: Path) -> None:
    """개별주 예약 매수 · −3% 손절 감시 — 2 분마다 (할 일 없으면 토스를 부르지 않는다)."""
    try:
        from scripts import size_guard

        with size_guard._Lock():
            done = size_guard.tick()
        if done:
            print(f"개별주: {len(done)} 건 알림")
        if size_guard.monthly_review():
            print("개별주 월간 복기 보냄")
    except SystemExit:
        print("개별주: 다른 실행 중 — 다음에")
    except Exception as exc:  # 개별주 문제로 봇을 멈추지 않는다
        print(f"개별주 건너뜀: {type(exc).__name__}: {exc}")


def root_halt(root: Path = REPO) -> Path:
    """[쓰임] 전체 비상 정지 파일 ``data/HALT``."""
    return root / "data" / "HALT"


def _liquidation(
    bot: TelegramNotifier, msgs: list[InboxMessage], queue: InboxQueue, inbox: TelegramInbox
) -> None:
    """[쓰임] '정리' · '정지풀기' 글들을 처리하고, 범위 버튼을 이 실행 안에서 2 분까지 기다린다."""
    from auto_trading.core.errors import AutoTradingError

    try:
        waiting = False
        for message in msgs:
            waiting = handle_liquidation(bot, message) or waiting
        deadline = time.monotonic() + LIQ_WAIT_S
        while waiting and time.monotonic() < deadline:
            time.sleep(3)
            for message in queue.take(inbox, is_liquidation):
                handle_liquidation(bot, message)
                if message.data:
                    waiting = False
    except (AutoTradingError, KeyError, OSError) as exc:
        bot.send(f"⚠ 정리 처리 오류: {type(exc).__name__} {exc}"[:500])


def _word(message: InboxMessage) -> str:
    """[쓰임] 비교용 글 — 앞뒤 공백 · 대소문자 · 띄어쓰기를 무시."""
    return message.text.strip().lower().replace(" ", "")


def _sort_commands(commands: list[InboxMessage]) -> dict[str, list[InboxMessage]]:
    """[쓰임] 받은 명령을 종류별로 나눈다 (:data:`COMMAND_KINDS` 순서, 한 글은 한 종류에만).
    [이유] 종류마다 처리 함수가 다르고, '현황' 은 다른 어느 종류에도 안 맞는 명령 모두다."""
    kinds: dict[str, list[InboxMessage]] = {kind: [] for kind in COMMAND_KINDS}
    for message in commands:
        if not message.data and message.text.strip().lower() in HELP_WORDS:
            kinds["help"].append(message)
        if not message.data and _word(message) in PAPER_WORDS:
            kinds["paper"].append(message)
        if not message.data and _word(message) in DEPOSIT_WORDS:
            kinds["deposit"].append(message)
        if is_acc(message):
            kinds["acc"].append(message)
        elif is_stock(message):
            kinds["stock"].append(message)
        if is_liquidation(message):
            kinds["liquidation"].append(message)
    for message in commands:
        already = any(message in kinds[kind] for kind in ("acc", "help", "paper", "deposit"))
        if not is_liquidation(message) and not is_stock(message) and not already:
            kinds["status"].append(message)
    return kinds


COMMAND_KINDS = ("help", "paper", "status", "liquidation", "stock", "acc", "deposit")
"""명령 종류 — 이 순서로 처리한다 (도움 · Paper · 현황은 여러 번 와도 답은 한 통)."""


def run(root: Path = REPO) -> int:
    """[쓰임] 2 분마다 한 번 — 받은 명령에 답하고, 부가 작업(:data:`BACKGROUND_JOBS`)을 돈다."""
    credentials = load_telegram_credentials(root / ".env")
    if credentials is None:
        print("텔레그램 설정 없음")
        return 0
    queue = InboxQueue(inbox_path(root), _start_offset(root))
    inbox = TelegramInbox(credentials)
    try:
        commands = queue.take(inbox, is_command)
    except NotifyError as exc:
        print(f"텔레그램 받기 실패: {exc}")
        return 1
    kinds = _sort_commands(commands)
    bot = TelegramNotifier(credentials)
    try:
        # 명령 종류 → 처리 (Command + 목록표)
        if kinds["help"]:
            bot.send(HELP)
        if kinds["paper"]:
            bot.send(paper_text(root))  # 여러 번 보내도 한 통
        if kinds["status"]:
            text = report(root)  # 여러 번 보내도 답은 한 통
            bot.send(text)
            print(text)
        if kinds["liquidation"]:
            _liquidation(bot, kinds["liquidation"], queue, inbox)
        if kinds["stock"]:
            _stock(bot, kinds["stock"], queue, inbox)
        if kinds["acc"]:
            _acc(bot, kinds["acc"], queue, inbox)
        if kinds["deposit"]:  # 2026-10-19 바이낸스 입금 확인 · 분배 멈춤 (Vega 업비트로)
            bot.send("💰 바이낸스 입금 확인은 멈췄어요 — Vega 는 업비트 원화로 넣으면 돼요")
    finally:
        bot.close()
    for _name, job in BACKGROUND_JOBS:
        job(root)
    if not commands:
        print("현황 봇: 받은 명령 없음")
    beat_and_alert(
        root,
        "telegram_bot",
        int(time.time() * 1000),
        alert=bool(user_settings.get(root, "alerts.bot_down")),
    )
    return 0


BACKGROUND_JOBS: tuple[tuple[str, Callable[[Path], None]], ...] = (
    ("정리 이어 하기", lambda root: _resume_liquidation()),
    ("개별주 예약 · 손절 감시", lambda root: _size_guard(root)),
    ("경제 일정 경고", lambda root: _event_alert(root)),
    ("나무 모의 따라가기", lambda root: _namu_follow(root)),
    ("그림자 기록 272", lambda root: _shadow_272()),
    ("매일 Paper 요약", lambda root: _paper_daily(root)),
    ("매일 안전 점검", lambda root: _safety_daily()),
)
"""매 실행(2 분)마다 명령 처리 뒤 이 순서로 도는 부가 작업 — 하나가 실패해도 다음은 돈다
(각 함수가 스스로 실패를 삼킨다). 함수는 이 표가 부를 때 찾는다(시험이 바꿔 끼울 수 있게)."""


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
    credentials = load_telegram_credentials(root / ".env")
    if credentials is None:
        return
    bot = TelegramNotifier(credentials)
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


def _safety_daily() -> None:
    """매일 안전 점검 (09:20 KST 뒤 한 번, 이상 · 바뀐 것 있을 때만 알림). 실패해도 봇은 끝난다."""
    try:
        from scripts import safety_check

        sent = safety_check.run()
        if sent:
            print("안전 점검 알림 보냄")
    except Exception as exc:  # 점검 문제로 봇을 멈추지 않는다
        print(f"안전 점검 건너뜀: {type(exc).__name__}: {exc}")


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


def _shadow_272() -> None:
    """H1 숏 · Altair 펀딩비 끄기 그림자 기록(문서 272) — 4 시간마다 다시 계산, 주문 · 알림 없음."""
    try:
        from scripts import shadow_272

        out = shadow_272.run()
        if out:
            print(f"그림자 272: H1 {out['h1_filled']} 건 · 펀딩 {out['funding_days']} 일")
    except Exception as exc:  # 그림자 기록 문제로 봇을 멈추지 않는다
        print(f"그림자 272 건너뜀: {type(exc).__name__}: {exc}")


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
    from scripts import liquidate

    try:
        liquidate.resume()
    except (AutoTradingError, KeyError, OSError) as exc:
        print(f"정리 이어 하기 실패: {exc}")


def main(argv: list[str]) -> int:
    """[쓰임] 명령 이름 → 함수 (예약은 ``run``)."""
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
