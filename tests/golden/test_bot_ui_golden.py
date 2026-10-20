"""리팩토링 5 단계 전후 비교 기준 — 텔레그램 봇 흐름 · 현황 글 · 화면 HTML.

[쓰임] 텔레그램 '적립' (메뉴 · 추가 · 빼기 · 금액) · '정리' · '정지풀기' 대화, 현황 글, 실거래 홈 ·
       설정 · 정리 칸 HTML 을 가짜 봇 · 고정 장부로 돌려 기준 파일과 글자까지 비교한다.
[이유] 5 단계에서 ``scripts/telegram_bot.py`` · ``ui/*`` 를 손대서,
       바꾸기 전 코드로 기준을 찍어 둔다.
       흐름은 기존 시험(tests/ops/test_telegram_* · tests/ui/test_live_home)을 따른다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from auto_trading.notify.inbox import InboxMessage
from tests.golden.test_live_golden import _check_golden
from tests.ops.test_telegram_acc import root as root  # 가짜 적립 준비 (pytest fixture)
from tests.ops.test_telegram_liquidation import fake as fake  # 가짜 정리 실행기 (fixture)


def _text(text: str, reply_to: int = 0) -> InboxMessage:
    return InboxMessage(1, int(time.time()), text, reply_to=reply_to)


def _press(data: str) -> InboxMessage:
    return InboxMessage(2, int(time.time()), "", data=data, callback_id="cb", message_id=7)


def _last_buttons(bot: Any) -> list[str]:
    _kind, payload = bot.out[-1]
    rows = payload[1]
    return [button["callback_data"] for row in rows for button in row]


def test_golden_telegram_acc_flows(root: Path) -> None:
    from scripts import telegram_bot as tb
    from tests.ops.test_telegram_acc import FakeBot

    bot = FakeBot()
    results = [tb.handle_acc(bot, _text("적립"), root)]  # type: ignore[arg-type]
    add = _last_buttons(bot)[0]
    results.append(tb.handle_acc(bot, _press(add), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _text("PLTR 3", reply_to=7), root))  # type: ignore[arg-type]
    ok = _last_buttons(bot)[0]
    results.append(tb.handle_acc(bot, _press(ok), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _press(ok), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _text("적립 추가 ZZZZ 2"), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _text("적립 빼기 btq"), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _press(_last_buttons(bot)[0]), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _text("적립"), root))  # type: ignore[arg-type]
    amount = next(data for data in _last_buttons(bot) if data.endswith(":amt=acc.WQTM"))
    results.append(tb.handle_acc(bot, _press(amount), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _text("25", reply_to=7), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _text("적립 금액 wqtm 2"), root))  # type: ignore[arg-type]
    results.append(tb.handle_acc(bot, _press(_last_buttons(bot)[0]), root))  # type: ignore[arg-type]
    out = ["## 돌려준 값", repr(results), "## 봇", *(repr(row) for row in bot.out)]
    _check_golden("telegram_acc", "\n".join(out), root)


def test_golden_telegram_liquidation_flows(fake: dict[str, list[Any]], tmp_path: Path) -> None:
    from scripts import telegram_bot as tb
    from tests.ops.test_telegram_liquidation import FakeBot

    bot = FakeBot()
    results = [tb.handle_liquidation(bot, InboxMessage(1, 0, "정리"))]  # type: ignore[arg-type]
    deneb = next(data for data in _last_buttons(bot) if data.endswith(":deneb"))
    results.append(tb.handle_liquidation(bot, _press(deneb)))  # type: ignore[arg-type]
    results.append(tb.handle_liquidation(bot, _press(deneb)))  # type: ignore[arg-type]
    results.append(tb.handle_liquidation(bot, InboxMessage(1, 0, "전부매도")))  # type: ignore[arg-type]
    cancel = next(data for data in _last_buttons(bot) if data.endswith(":cancel"))
    results.append(tb.handle_liquidation(bot, _press(cancel)))  # type: ignore[arg-type]
    results.append(tb.handle_liquidation(bot, _press("liq:deadbeef:all")))  # type: ignore[arg-type]
    results.append(tb.handle_liquidation(bot, InboxMessage(1, 0, "정지풀기")))  # type: ignore[arg-type]
    results.append(tb.handle_liquidation(bot, _press(_last_buttons(bot)[0])))  # type: ignore[arg-type]
    out = ["## 돌려준 값", repr(results), "## 봇", *(repr(row) for row in bot.out)]
    out += ["## 실행기에 넘긴 것", repr(fake)]
    _check_golden("telegram_liquidation", "\n".join(out), tmp_path)


def test_golden_live_home_summary_and_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.ui import live_home
    from auto_trading.ui.data import load_dashboard
    from auto_trading.ui.live_home import build_books, render_live, summary_text
    from auto_trading.ui.render import render_page
    from tests.ui.test_live_home import _NOW, _Q, _seed

    monkeypatch.setattr(live_home, "SHOW_UPBIT", True)
    _seed(tmp_path)
    # 화면에 '장부 업데이트' 로 파일 수정 시각이 찍혀서, 장부 파일 시각을 고정한다
    fixed = _NOW / 1000 - 3600
    for path in (tmp_path / "data").rglob("*"):
        os.utime(path, (fixed, fixed))
    books = build_books(tmp_path, _Q)
    out = ["## 현황 글", summary_text(books, _Q, _NOW)]
    out += ["## 실거래 홈", render_live(books, _Q, _NOW)]
    out += ["## 가린 홈", render_live(books, _Q, _NOW, hide=True)]
    monkeypatch.setattr(live_home, "quote_source", lambda coins, tickers: _Q)
    out += ["## 페이지 dark", render_page(load_dashboard(tmp_path, _NOW), "live", theme="dark")]
    _check_golden("live_home", "\n".join(out), tmp_path)


def test_golden_settings_and_liquidate_panel(tmp_path: Path) -> None:
    from auto_trading.ops import user_settings as us
    from auto_trading.ui.liquidate_panel import liquidate_panel
    from auto_trading.ui.settings_page import render_remote, render_review, render_settings

    values = us.load(tmp_path)
    panel = liquidate_panel("T", {"vega": "전부 정리", "deneb": None, "acc": None}, "마지막 작업")
    new = {**values, "deneb.monthly_krw": 400_000, "acc.BTQ.usd": 3.0}
    out = [
        "## 설정",
        render_settings(values, token="T", errors={"fire.target_krw": "숫자로"}, liquidate=panel),
        "## 확인",
        render_review(us.diff(values, new), {"deneb.monthly_krw": "400000"}, token="T"),
        "## 원격",
        render_remote(),
        "## 정리 칸",
        *panel,
    ]
    _check_golden("settings_page", "\n".join(out), tmp_path)
