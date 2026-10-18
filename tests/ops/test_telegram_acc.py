"""텔레그램 '적립' — 자동 적립 종목 추가 · 빼기 · 정리에서 종목 고르기 (2026-10-18 사용자 요청)."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.notify import inbox
from auto_trading.notify.inbox import InboxMessage, is_acc, is_command
from auto_trading.ops import user_settings
from auto_trading.ops.liquidate import Item, Store, build_items
from scripts import liquidate as liq
from scripts import telegram_bot as tb


class FakeBot:
    def __init__(self) -> None:
        self.out: list[tuple[str, Any]] = []

    def send(self, text: str) -> None:
        self.out.append(("send", text))

    def send_message(self, text: str, *, buttons: Any = None, **kw: Any) -> int:
        self.out.append(("buttons", (text, buttons, kw)))
        return 7

    def edit(self, message_id: int, text: str, **_: Any) -> None:
        self.out.append(("edit", text))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.out.append(("answer", text))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tb, "ACC_STORE", Store(tmp_path / "acc_buttons"))
    monkeypatch.setattr(tb, "ACC_PENDING", tmp_path / "acc_pending.json")
    monkeypatch.setattr(inbox, "acc_ask_path", lambda: tmp_path / "acc_ask.json")
    uni = tmp_path / "data" / "toss" / "universe.json"
    uni.parent.mkdir(parents=True)
    uni.write_text(
        json.dumps(
            {
                "markets": {
                    "NASDAQ": [{"symbol": "PLTR", "name": "팔란티어", "securityType": "STOCK"}]
                }
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _text(t: str, reply_to: int = 0) -> InboxMessage:
    return InboxMessage(1, int(time.time()), t, reply_to=reply_to)


def _press(data: str) -> InboxMessage:
    return InboxMessage(2, int(time.time()), "", data=data, callback_id="cb", message_id=7)


def _buttons(bot: FakeBot) -> list[str]:
    kind, (_t, rows, _kw) = bot.out[-1]
    assert kind == "buttons"
    return [b["callback_data"] for row in rows for b in row]


def test_routing() -> None:
    for t in ("적립", "/acc", "적립 추가 PLTR 3", "자동적립"):
        assert is_acc(_text(t)) and is_command(_text(t))
    assert not is_acc(_text("적립금 얼마"))
    assert is_acc(InboxMessage(1, 0, "", data="acc:ab:add"))


def test_parse() -> None:
    assert tb._acc_parse("PLTR 3") == ("PLTR", "US", "3")
    assert tb._acc_parse("삼성전자 10,000원") == ("삼성전자", "KR", "10000")
    assert tb._acc_parse("005930") == ("005930", "KR", "")
    assert tb._acc_parse("$PLTR") == ("$PLTR", "US", "")


def test_menu_lists_items_with_add_and_remove(root: Path) -> None:
    bot = FakeBot()
    assert tb.handle_acc(bot, _text("적립"), root)  # type: ignore[arg-type]
    data = _buttons(bot)
    assert data[0].endswith(":add")
    assert {d.split(":", 2)[2] for d in data[1:-1]} == {"rm=acc.BTQ", "rm=acc.WQTM", "rm=acc.QS"}
    assert "BTQ" in bot.out[-1][1][0]


def test_add_flow_asks_then_confirms_then_saves(root: Path) -> None:
    bot = FakeBot()
    tb.handle_acc(bot, _text("적립"), root)  # type: ignore[arg-type]
    add = _buttons(bot)[0]
    assert tb.handle_acc(bot, _press(add), root)  # type: ignore[arg-type]
    assert bot.out[-1][1][2].get("force_reply")  # 답장 입력칸
    assert tb.handle_acc(bot, _text("PLTR 3", reply_to=7), root)  # type: ignore[arg-type]
    ok = _buttons(bot)[0]
    assert ok.endswith(":ok") and "팔란티어(PLTR) 추가 · 하루 $3" in bot.out[-1][1][0]
    assert user_settings.acc_items(user_settings.load(root))[-1]["sym"] != "PLTR"  # 아직 안 바뀜
    tb.handle_acc(bot, _press(ok), root)  # type: ignore[arg-type]
    items = user_settings.acc_items(user_settings.load(root))
    assert items[-1]["sym"] == "PLTR" and items[-1]["daily"] == 3.0
    assert bot.out[-1][0] == "send" and "✅" in bot.out[-1][1]
    tb.handle_acc(bot, _press(ok), root)  # type: ignore[arg-type]
    assert "지난 버튼" in bot.out[-1][1]  # 두 번 누르면 안 됨


def test_one_line_add_and_unknown_symbol(root: Path) -> None:
    bot = FakeBot()
    assert tb.handle_acc(bot, _text("적립 추가 PLTR 2"), root)  # type: ignore[arg-type]
    assert _buttons(bot)[0].endswith(":ok")
    assert not tb.handle_acc(bot, _text("적립 추가 ZZZZ 2"), root)  # type: ignore[arg-type]
    assert bot.out[-1][0] == "send" and "찾지 못했어요" in bot.out[-1][1]


def test_remove_flow(root: Path) -> None:
    bot = FakeBot()
    assert tb.handle_acc(bot, _text("적립 빼기 btq"), root)  # type: ignore[arg-type]
    ok = _buttons(bot)[0]
    tb.handle_acc(bot, _press(ok), root)  # type: ignore[arg-type]
    values = user_settings.load(root)
    assert values["acc.BTQ.removed"] is True
    assert [x["sym"] for x in user_settings.acc_items(values)] == ["WQTM", "QS"]


def test_liquidation_acc_menu_picks_one_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[str] = []
    monkeypatch.setattr(liq, "STORE", Store(tmp_path / "liq"))
    monkeypatch.setattr(liq, "preview_text", lambda scope="all": "🧯 미리 보기")
    monkeypatch.setattr(
        liq, "plan", lambda scope: ([Item("toss_us", "BTQ", ["acc"], Decimal(3))], ["메모"])
    )

    def start(scope: str, src: str) -> str:
        started.append(scope)
        return "ok"

    monkeypatch.setattr(liq, "start", start)
    bot = FakeBot()
    tb.handle_liquidation(bot, _text("정리"))  # type: ignore[arg-type]
    menu = next(d for d in _buttons(bot) if d.endswith(":accmenu"))
    assert tb.handle_liquidation(bot, _press(menu))  # type: ignore[arg-type]
    picks = _buttons(bot)
    assert picks[0].endswith(":a=BTQ") and picks[1].endswith(":acc")
    tb.handle_liquidation(bot, _press(picks[0]))  # type: ignore[arg-type]
    assert started == ["acc:BTQ"]


def test_build_items_acc_only_sells_accumulate_share() -> None:
    items, notes = build_items(
        (),
        upbit_balances={}, upbit_prices={}, vega_coins=(),
        toss_holdings={"BTQ": Decimal(5), "TQQQ": Decimal(3)},
        toss_sellable={"BTQ": Decimal(5), "TQQQ": Decimal(3)},
        deneb_qty={}, acc_qty={"BTQ": Decimal(3)}, is_kr=lambda s: False,
        only_acc="BTQ",
    )  # fmt: skip
    assert [(it.symbol, it.want, it.groups) for it in items] == [("BTQ", Decimal(3), ["acc"])]
    assert any("수동 보유" in n for n in notes)


def test_stop_acc_removes_symbol(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(liq, "REPO", root)
    line = liq._stop_acc("QS", "텔레그램")
    assert "멈춤" in line and user_settings.load(root)["acc.QS.removed"] is True
    assert "목록에 없음" in liq._stop_acc("QS", "텔레그램")
