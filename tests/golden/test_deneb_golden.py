"""리팩토링 3 단계 전후 비교 기준 — Deneb 의 덜 다니는 길 (docs/REFACTOR_PLAN.md).

[쓰임] 금액 묻기 → 답 → 밤 매수, 재시도, 12 월 공제 실현, 배당 단계 제안,
       금 섞기 · 배당이 든 둘째 달을 가짜 토스 · 가짜 텔레그램으로 돌려
       알림 · 버튼 · 주문 · 장부를 기준 파일과 글자까지 비교한다.
[이유] 3 단계는 2 천 줄 넘는 ``scripts/toss_accumulate.py`` 를 손대서, 기존 기준(첫 달 · 하락 방어)
       밖의 길도 바꾸기 전 코드로 찍어 둔다. 흐름은 기존 시험(tests/ops)을 그대로 따른다.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.toss import TossOrderStatus
from tests.golden.test_live_golden import _check_golden, _json_text


def _deneb_json(tmp_path: Path) -> str:
    """Deneb 장부 — 금액을 물은 시각(``ask_ts``, 실제 시계)만 가린다."""
    text = _json_text(tmp_path / "data" / "DENEB.json")
    return re.sub(r'"ask_ts": [1-9]\d*', '"ask_ts": <NOW>', text)


def _fill_pending(fake: Any) -> None:
    for k in range(1, len(fake.sent) + 1):
        fake.statuses.setdefault(
            f"OID-{k}", TossOrderStatus("FILLED", Decimal(1), Decimal(1), Decimal(1), Decimal(0))
        )


# ------------------------------------------ 1. 금액 묻기 → 답 → 밤 매수


def test_golden_deneb_chat_amount_then_night(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from auto_trading.notify import inbox as inbox_mod
    from auto_trading.notify.inbox import InboxMessage
    from tests.ops.test_toss_accumulate import NOW_KST, FakeToss, _mod, _seed

    sent: list[str] = []
    fake = FakeToss(usd="2000")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    monkeypatch.setattr(mod, "load_telegram_credentials", lambda path: object())
    inbox: list[InboxMessage] = []

    class FakeInbox:
        def __init__(self, creds: object) -> None:
            pass

        def fetch(self, offset: int) -> tuple[list[InboxMessage], int]:
            out = list(inbox)
            inbox.clear()
            return out, offset + len(out)

    monkeypatch.setattr(inbox_mod, "TelegramInbox", FakeInbox)
    _seed(mod)
    out: list[str] = []
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 6, 9, 0, tzinfo=UTC))
    assert mod.fx(telegram=True) == 0
    out += ["## 09:00 fx", capsys.readouterr().out, *sent]
    sent.clear()
    monkeypatch.setattr(mod, "_usdt_krw", lambda: 1_400.0)
    inbox.append(InboxMessage(1, 9_999_999_999, "50만"))
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 6, 9, 5, tzinfo=UTC))
    assert mod.deneb_chat(telegram=True) == 0
    out += ["## 09:05 답 50만", capsys.readouterr().out, *sent]
    sent.clear()
    inbox.append(InboxMessage(2, 9_999_999_999, "0"))  # 다시 0 → 이번 달 쉬기 답
    inbox.append(InboxMessage(3, 9_999_999_999, "40만"))
    assert mod.deneb_chat(telegram=True) == 0
    out += ["## 09:05 답 0 · 40만", capsys.readouterr().out, *sent]
    sent.clear()
    monkeypatch.setattr(mod, "_kst_now", lambda: NOW_KST)
    assert mod.run(telegram=True) == 0
    out += ["## 21:00 run", capsys.readouterr().out, *sent, repr(fake.sent)]
    sent.clear()
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 9, 0, tzinfo=UTC))
    _fill_pending(fake)
    assert mod.fx(telegram=True) == 0
    assert mod.deneb_chat(telegram=True) == 0
    out += ["## 10-07 아침", capsys.readouterr().out, *sent, "## DENEB.json", _deneb_json(tmp_path)]
    _check_golden("deneb_chat", "\n".join(out), tmp_path)


# ------------------------------------------ 2. 재시도 (달러 부족 → 23:40 · 03:30)


def test_golden_deneb_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.ops.test_toss_accumulate import FakeToss, _mod, _seed

    sent: list[str] = []
    fake = FakeToss(outcome="insufficient-buying-power")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    _seed(mod)
    assert mod.run(telegram=True) == 0
    out = ["## 21:00 거절", capsys.readouterr().out, *sent, repr(fake.sent)]
    sent.clear()
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 6, 23, 40, tzinfo=UTC))
    assert mod.deneb_only(telegram=True) == 0
    out += ["## 23:40 또 거절", capsys.readouterr().out, *sent]
    sent.clear()
    fake.outcome = "ok"
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 3, 30, tzinfo=UTC))
    assert mod.deneb_only(telegram=True) == 0
    assert mod.deneb_only(telegram=True) == 0
    out += ["## 03:30 접수 · 한 번 더", capsys.readouterr().out, *sent, repr(fake.sent)]
    out += ["## DENEB.json", _deneb_json(tmp_path)]
    _check_golden("deneb_retry", "\n".join(out), tmp_path)


# ------------------------------------------ 3. 12 월 공제 실현 · 4. 배당 단계


def _bot_text(bot: Any) -> list[str]:
    return [
        *(repr(message) for message in bot.sent),
        f"pinned={bot.pinned} unpinned={bot.unpinned} answered={bot.answered}",
        *(repr(edit) for edit in bot.edits),
    ]


def test_golden_deneb_december_harvest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from auto_trading.notify.inbox import InboxMessage
    from tests.ops.test_deneb_proposals import _deneb_state, _night, _setup, _state

    mod, fake, bot, box = _setup(monkeypatch, tmp_path, _deneb_state(done_month="2026-12"))
    now = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    mod._deneb_morning(now, True)
    mod._deneb_morning(now, True)
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 12, 1, 10, 0, tzinfo=UTC))
    box.append(InboxMessage(1, 0, "", data="h2026:input", callback_id="cb1", message_id=101))
    mod._deneb_poll("2026-12-01", True)
    box.append(InboxMessage(2, 0, "1", reply_to=bot.sent[-1].get("reply_to", 0)))
    mod._deneb_poll("2026-12-01", True)
    _night(monkeypatch, mod, fake, "2026-12-01")
    sell_oid = next(
        o["order_id"] for o in _state(tmp_path)["orders"] if o["kind"] == "harvest_sell"
    )
    fake.statuses[sell_oid] = TossOrderStatus(
        "FILLED", Decimal(1), Decimal(81), Decimal(81), Decimal("0.08")
    )
    _night(monkeypatch, mod, fake, "2026-12-02")
    buy_oid = next(o["order_id"] for o in _state(tmp_path)["orders"] if o["kind"] == "harvest_buy")
    fake.statuses[buy_oid] = TossOrderStatus(
        "FILLED", Decimal(1), Decimal(82), Decimal(82), Decimal("0.08")
    )
    _night(monkeypatch, mod, fake, "2026-12-03")
    out = ["## 출력", capsys.readouterr().out, "## 봇", *_bot_text(bot), "## 주문", repr(fake.sent)]
    out += ["## DENEB.json", _deneb_json(tmp_path)]
    _check_golden("deneb_harvest", "\n".join(out), tmp_path)


def test_golden_deneb_dividend_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from auto_trading.notify.inbox import InboxMessage
    from tests.ops.test_deneb_proposals import _deneb_state, _setup, _state

    deneb = _deneb_state(done_month="2026-10")
    deneb.dividends.append(["2026-09-20", "SCHD", 100, 0.3, 900.0])
    mod, _fake, bot, box = _setup(monkeypatch, tmp_path, deneb)
    mod._deneb_morning(datetime(2026, 10, 12, 9, 0, tzinfo=UTC), True)
    pid = _state(tmp_path)["proposals"][0]["pid"]
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 12, 10, 0, tzinfo=UTC))
    box.append(InboxMessage(1, 0, "", data=f"{pid}:input", callback_id="c", message_id=101))
    box.append(InboxMessage(2, 0, "2"))
    mod._deneb_poll("2026-10-12", True)
    box.append(InboxMessage(3, 0, "", data=f"{pid}:cancel", callback_id="d", message_id=101))
    mod._deneb_poll("2026-10-12", True)
    box.append(InboxMessage(4, 0, "", data=f"{pid}:no", callback_id="e", message_id=101))
    mod._deneb_poll("2026-10-12", True)
    out = ["## 출력", capsys.readouterr().out, "## 봇", *_bot_text(bot)]
    out += ["## DENEB.json", _deneb_json(tmp_path)]
    _check_golden("deneb_dividend_step", "\n".join(out), tmp_path)


# ------------------------------------------ 5. 금 섞기 · 배당이 든 둘째 달


def test_golden_deneb_gold_and_dividend_second_month(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.ops.test_toss_accumulate import FakeToss, _fill_all, _mod, _seed

    class LaterCloses(FakeToss):
        """종가가 ``end`` 날까지 이어지는 가짜.

        기본 가짜는 10-05 에서 끝나 11 월엔 '오래된 종가' 로 멈춘다."""

        end = "2026-10-05"

        def daily_closes(self, symbol: str, count: int = 200) -> list[tuple[str, Decimal]]:
            from datetime import date, timedelta

            last = date.fromisoformat(self.end)
            return [
                ((last - timedelta(days=149 - k)).isoformat(), Decimal(500 + k)) for k in range(150)
            ]

    sent: list[str] = []
    fake = LaterCloses(usd="3000")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    _seed(mod)
    assert mod.run(telegram=True) == 0  # 10-06 첫 달 (금 없음)
    _fill_all(fake, tmp_path)
    out = ["## 10-06", capsys.readouterr().out, *sent]
    sent.clear()
    monkeypatch.setattr(mod, "_deneb_gold", lambda: 0.5)
    monkeypatch.setattr(mod, "_dividend_events", lambda: [("2026-10-08", "SCHD", 0.26)])
    for day, last_close in (
        ("2026-10-07", "2026-10-06"),
        ("2026-11-02", "2026-10-30"),
        ("2026-11-03", "2026-11-02"),
    ):
        fake.end = last_close
        monkeypatch.setattr(
            mod, "_kst_now", lambda d=day: datetime.fromisoformat(f"{d}T21:00:00+00:00")
        )
        assert mod.run(telegram=True) == 0
        _fill_all(fake, tmp_path)
        out += [f"## {day} (금 섞기 켜짐)", capsys.readouterr().out, *sent]
        sent.clear()
    assert mod.preview() == 0
    assert mod.status() == 0
    out += ["## preview · status", capsys.readouterr().out, "## 주문", repr(fake.sent)]
    out += ["## DENEB.json", _deneb_json(tmp_path)]
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["dividends"], "배당이 장부에 들어가야 기준이 뜻이 있다"
    _check_golden("deneb_gold_dividend", "\n".join(out), tmp_path)
