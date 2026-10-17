"""텔레그램 '현황' 명령 — 폰에서 "현황" 을 보내면 실거래 요약을 답장한다 (사용자 2026-10-10).

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
* '현황' 내용은 대시보드 실거래 홈과 같다: 장부 + 공개 시세(키 없음).
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
    DEPOSIT_WORDS,
    HELP_WORDS,
    LIQ_WORDS,
    PAPER_WORDS,
    UNHALT_WORDS,
    InboxMessage,
    InboxQueue,
    TelegramInbox,
    clear_stock_ask,
    is_command,
    is_liquidation,
    is_stock,
    is_stock_answer,
    liquidation_symbol,
    set_stock_ask,
    stock_ask_info,
)
from auto_trading.ops import user_settings
from auto_trading.ops.kill_switch import GROUPS
from auto_trading.ops.liquidate import SCOPES, Store
from auto_trading.ops.paper_summary import paper_text
from auto_trading.ui.live_home import build_books, fetch_quotes, needed, summary_text

REPO = Path(__file__).resolve().parent.parent


def inbox_path(root: Path = REPO) -> Path:
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
    coins, tickers = needed(root)
    q = fetch_quotes(coins, tickers)
    books = build_books(root, q)
    text = summary_text(books, q, int(time.time() * 1000))
    value = sum(b.value_krw for b in books)
    target = float(user_settings.get(root, "fire.target_krw"))
    text += f"\n🎯 파이어 목표 {target / 1e8:g}억까지 {value / target:.1%}"
    stock = _stock_summary()
    return text + ("\n\n" + "\n".join(stock) if stock else "")


def _stock_summary() -> list[str]:
    """💹 개별주 예약 · 보유 · 손절 거리 (2026-10-16 '현황에 개별주'). 실패해도 현황은 보낸다."""
    try:
        from scripts import size_guard as sg

        return sg.summary_lines()
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
  [전부 정리] [Vega 만] [Deneb 만] [자동 적립만] [개별주만] [Altair 만] [취소]
  버튼은 5분 안에 한 번만. 수동 보유(APH 등)는 안 팔아요.
  정리 NVDA (또는 정리 삼성전자) — 그 종목 하나만 (Deneb · 적립 몫 빼고 전부)

💹 개별주 NVDA (또는 개별주 삼성전자 · /stock)
  얼마나 살지 추천: 현금(원화 + 달러)의 0.3% 까지만 잃게, 손절 −3% · 한 종목 최대 10%
  실적 3거래일 안 · 추세 아님 · 지수 200일선 아래(절반)면 안 사거나 줄여요
  → [🛒 N주 매수] 누르면 정규장 시작 직후 지정가로 사고, −3% 면 자동으로 팔아요
  → [✏️ 수량 바꾸기] · 규칙에 걸리면 [✏️ 그래도 매수] — 몇 주인지 답장 → [✅ 예약]
     (한 종목 10% · 현금 안에서만, 손절 −3% 는 그대로)
  메뉴(/stock)나 '개별주' 만 보내면 "어떤 종목을 살까요?" 라고 물어요 → 종목 이름으로 답장
  (예약 · 손절 중인 종목이 있으면 현황 + [손절 해제] 버튼도)

💰 입금확인 (또는 /deposit)
  바이낸스에 입금했으면 바로 Vega · Altair 로 나눠요 (안 보내도 10분마다 자동 확인)

▶ 정지풀기 (또는 /unhalt)
  정리 뒤 멈춘 그룹 다시 켜기 → 다음 실행부터 규칙대로 다시 사요

💵 이번 달 입금 (세 곳 합계)
  매달 매수일 아침 질문에 합계로 답: 100만 · 1000000 · 0(쉬기)
  → Deneb 80 · Vega 10 · Altair 10 으로 나눠 어디에 얼마 넣을지 알려 줘요

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
    ("deposit", "입금 — 바이낸스 입금 확인 · Vega / Altair 로 나누기"),
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
        [b("Vega 만", "vega"), b("Deneb 만", "deneb")],
        [b("자동 적립만", "acc"), b("개별주만", "stock")],
        [b("Altair 만", "altair"), b("취소", "cancel")],
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
        if kind == "liq" and arg.startswith("s="):
            sym = arg[2:]
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🧯 {sym} 정리 시작 — 결과를 곧 보내요")
            liq.start(f"sym:{sym}", "텔레그램")
        elif kind == "liq" and arg in SCOPES:
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🧯 {SCOPES[arg]} 정리 시작 — 결과를 곧 보내요")
            liq.start(arg, "텔레그램")  # 결과는 start 가 보낸다
        elif kind == "unh" and arg in GROUPS:
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"▶ {GROUPS[arg]} 정지 풀기")
            liq.unhalt(arg, "텔레그램")
        return False
    query = liquidation_symbol(msg)
    if query:
        from scripts import size_guard as sg

        try:
            sym, name = sg.resolve(query)
        except SystemExit as exc:  # 여러 종목이 맞음
            bot.send(f"⚠ {exc}")
            return False
        nonce = liq.STORE.new_request(now, "liq")
        rows = [
            [{"text": f"🧯 {name} 정리", "callback_data": f"liq:{nonce}:s={sym}"}],
            [{"text": "취소", "callback_data": f"liq:{nonce}:cancel"}],
        ]
        bot.send_message(
            liq.preview_text(f"sym:{sym}") + "\n\n누르면 바로 시작해요 (5 분 안, 한 번만)",
            buttons=rows,
        )
        return True
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


STOCK_STORE = Store(REPO / "data" / "size_guard" / "telegram")
"""개별주 버튼 번호 (5 분 · 한 번) — 정리 버튼과 같은 방식."""
STOCK_WAIT_S = 120


def _stock_query(text: str) -> str:
    """'개별주 NVDA' · '/stock 삼성전자' · '개별주NVDA' → 'NVDA' · '삼성전자' (없으면 빈 값)."""
    from auto_trading.notify.inbox import STOCK_PREFIXES

    t = text.strip()
    low = t.lower()
    for p in sorted(STOCK_PREFIXES, key=len, reverse=True):
        if low.startswith(p):
            return t[len(p) :].strip()
    return t


def _polaris(bot: TelegramNotifier, msg: InboxMessage) -> bool:
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
    """'개별주 …' 글과 그 버튼. 버튼을 보냈으면 True (이 실행에서 답을 기다린다)."""
    from scripts import size_guard as sg

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
                    bot.edit(msg.message_id, "건너뛰었어요 — " + sg.skip_pead(nonce))
                return False
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🛒 PEAD {arg} 다시 확인하고 예약해요…")
            try:
                bot.send(sg.reserve_pead(nonce))
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
                bot.send(sg.reserve(arg))
            except SystemExit as exc:  # 다른 실행 중 · 종목 못 찾음
                bot.send(f"⚠ 예약 못 함 — {exc}")
        elif kind == "stkr":
            try:
                bot.send("🛡 손절 해제 — " + sg._set_status(arg, ("holding",), "released"))
            except SystemExit as exc:
                bot.send(f"⚠ 해제 못 함 — {exc}")
        elif kind == "stq":  # [수량 바꾸기] · [그래도 매수] → 몇 주?
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"✏️ {arg} 몇 주 살지 물어볼게요")
            return _ask_qty(bot, arg)
        elif kind == "sko":  # 수량 확인 → 예약
            sym, _, n = arg.rpartition(":")
            with contextlib.suppress(NotifyError):
                bot.edit(msg.message_id, f"🛒 {sym} {n}주 다시 확인하고 예약해요…")
            try:
                bot.send(sg.reserve(sym, qty=int(n)))
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
        act = [t for t in sg.load_tickets() if t.active]
        holding = [t for t in act if t.status == "holding"]
        if act:
            nonce = STOCK_STORE.new_request(now, "stkr")
            rows = [
                [{"text": f"{t.name} 손절 해제", "callback_data": f"stkr:{nonce}:{t.id}"}]
                for t in holding
            ]
            bot.send_message(
                "💹 지금 예약 · 손절 현황" + "\n\n" + sg.status(), buttons=rows or None
            )
        mid = bot.send_message(
            "💹 어떤 종목을 살까요?"
            + "\n\n"
            + "종목 이름이나 기호를 답장해 주세요 (예: 삼성전자 · 현대차 · NVDA)"
            + "\n"
            + "5분 안에 답하면 얼마나 살지 추천해 드려요",
            force_reply=True,
            placeholder="종목 이름 (예: 삼성전자)",
        )
        set_stock_ask(mid, int(time.time()))
        return True
    try:
        a = sg.advise(query)
    except SystemExit as exc:
        bot.send(f"⚠ {exc}")
        return False
    from auto_trading.ops.position_size import advice_text

    text = advice_text(a)
    if not a.ok and not a.can_override:
        bot.send(text)
        return False
    buy = STOCK_STORE.new_request(now, "stk")
    qty = STOCK_STORE.new_request(now, "stq")
    if a.ok:
        rows = [
            [
                {"text": f"🛒 {a.qty}주 매수", "callback_data": f"stk:{buy}:{a.symbol}"},
                {"text": "✏️ 수량 바꾸기", "callback_data": f"stq:{qty}:{a.symbol}"},
            ],
            [{"text": "취소", "callback_data": f"stk:{buy}:cancel"}],
        ]
    else:
        rows = [
            [{"text": "✏️ 그래도 매수 (수량 입력)", "callback_data": f"stq:{qty}:{a.symbol}"}],
            [{"text": "취소", "callback_data": f"stk:{buy}:cancel"}],
        ]
    bot.send_message(text + "\n\n누르면 다시 확인한 뒤 예약해요 (5 분 안, 한 번만)", buttons=rows)
    return True


def _ask_qty(bot: TelegramNotifier, symbol: str) -> bool:
    """'몇 주 살까요?' — 답장 입력칸을 열고 질문 기록(kind=qty)을 남긴다."""
    from auto_trading.ops.position_size import advice_text, qty_prompt_text
    from scripts import size_guard as sg

    try:
        a = sg.advise(symbol)
    except SystemExit as exc:
        bot.send(f"⚠ {exc}")
        return False
    if not a.can_override:
        bot.send(advice_text(a))
        return False
    mid = bot.send_message(qty_prompt_text(a), force_reply=True, placeholder="주 수 (예: 5)")
    set_stock_ask(mid, int(time.time()), kind="qty", symbol=a.symbol)
    return True


def _confirm_qty(bot: TelegramNotifier, symbol: str, text: str) -> bool:
    """'5' · '5주' 답 → 마지막 확인 카드 + [✅ N주 예약] [취소]."""
    from auto_trading.ops.position_size import CAP, confirm_text
    from scripts import size_guard as sg

    digits = text.strip().removesuffix("주").strip()
    if not digits.isdigit() or not symbol:
        bot.send("⚠ 주 수는 숫자로 답해 주세요 (예: 5) — 다시 '개별주' 부터 해 주세요")
        return False
    n = int(digits)
    try:
        a = sg.advise(symbol)
    except SystemExit as exc:
        bot.send(f"⚠ {exc}")
        return False
    if not a.can_override or not 1 <= n <= a.max_qty:
        bot.send(f"⚠ {a.name} 은 1 ~ {a.max_qty}주만 살 수 있어요 (한 종목 {CAP:.0%} · 현금)")
        return False
    nonce = STOCK_STORE.new_request(int(time.time() * 1000), "sko")
    rows = [
        [{"text": f"✅ {n}주 예약", "callback_data": f"sko:{nonce}:{a.symbol}:{n}"}],
        [{"text": "취소", "callback_data": f"sko:{nonce}:cancel"}],
    ]
    bot.send_message(confirm_text(a, n), buttons=rows)
    return True


def _stock(
    bot: TelegramNotifier, msgs: list[InboxMessage], queue: InboxQueue, inbox: TelegramInbox
) -> None:
    from auto_trading.core.errors import AutoTradingError

    try:
        waiting = False
        for m in msgs:
            waiting = handle_stock(bot, m) or waiting
        deadline = time.monotonic() + STOCK_WAIT_S
        while waiting and time.monotonic() < deadline:
            time.sleep(3)
            for m in queue.take(inbox, is_stock):
                more = handle_stock(bot, m)
                if more:  # 질문에 답해 추천 버튼을 보냄 → 버튼을 다시 기다린다
                    deadline = time.monotonic() + STOCK_WAIT_S
                elif m.data:
                    waiting = False
    except (AutoTradingError, KeyError, OSError, ValueError) as exc:
        bot.send(f"⚠ 개별주 처리 오류: {type(exc).__name__} {exc}"[:500])


def _size_guard(root: Path) -> None:
    """개별주 예약 매수 · −3% 손절 감시 — 2 분마다 (할 일 없으면 토스를 부르지 않는다)."""
    try:
        from scripts import size_guard as sg

        with sg._Lock():
            done = sg.tick()
        if done:
            print(f"개별주: {len(done)} 건 알림")
        if sg.monthly_review():
            print("개별주 월간 복기 보냄")
    except SystemExit:
        print("개별주: 다른 실행 중 — 다음에")
    except Exception as exc:  # 개별주 문제로 봇을 멈추지 않는다
        print(f"개별주 건너뜀: {type(exc).__name__}: {exc}")


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
    deposits = [
        m for m in cmds if not m.data and m.text.strip().lower().replace(" ", "") in DEPOSIT_WORDS
    ]
    stock_msgs = [m for m in cmds if is_stock(m)]
    status_cmds = [
        m
        for m in cmds
        if not is_liquidation(m)
        and not is_stock(m)
        and m not in helps
        and m not in papers
        and m not in deposits
    ]
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
        if stock_msgs:
            _stock(bot, stock_msgs, queue, inbox)
        if deposits:
            bot.send("💰 바이낸스 입금 확인 중…")
            _deposit_watch(root, force=True)
    finally:
        bot.close()
    _resume_liquidation()
    _size_guard(root)
    _event_alert(root)
    _namu_follow(root)
    _altair_demo(root)
    _paper_daily(root)
    _safety_daily()
    _deposit_watch(root)
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


DEPOSIT_EVERY_MS = 10 * 60_000


def _deposit_watch(root: Path, *, force: bool = False) -> None:
    """바이낸스 입금을 10 분마다(또는 '입금확인' 때 바로) 보고 Vega · Altair 로 나눈다."""
    state = root / "data" / "vega" / "deposit_watch.json"
    now = int(time.time() * 1000)
    try:
        last = int(json.loads(state.read_text(encoding="utf-8")).get("at", 0))
    except (OSError, ValueError):
        last = 0
    if not force and now - last < DEPOSIT_EVERY_MS:
        return
    try:
        from scripts import vega_live

        out = vega_live.deposit_check(now, force=force)
        if force and out and not out[0].startswith("💰"):
            creds = load_telegram_credentials(root / ".env")
            if creds is not None:
                bot = TelegramNotifier(creds)
                try:
                    bot.send("💰 " + " · ".join(out))
                finally:
                    bot.close()
    except Exception as exc:  # 입금 확인 문제로 봇을 멈추지 않는다
        print(f"입금 확인 건너뜀: {type(exc).__name__}: {exc}")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"at": now}), encoding="utf-8")


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


def _altair_demo(root: Path) -> None:
    """Altair(옛 AG4S2) 선물 데모(가짜 돈) — 하루 한 번 롱 맞추기 · 4 시간봉마다 숏 점검."""
    if not user_settings.get(root, "demo.altair"):
        return
    try:
        from scripts import altair_demo

        done = altair_demo.tick()
        if done:
            print(f"Altair 데모: {len(done)} 줄")
    except Exception as exc:  # 데모 문제로 봇을 멈추지 않는다
        print(f"Altair 데모 건너뜀: {type(exc).__name__}: {exc}")


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
