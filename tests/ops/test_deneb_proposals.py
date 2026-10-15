"""Deneb 제안 (12 월 공제 실현 · 배당 단계) · 양도세 줄 — 가짜 텔레그램 · 가짜 토스로만."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.toss import TossOrderStatus
from auto_trading.notify.inbox import InboxMessage
from auto_trading.ops.deneb_live import DenebOrder, DenebState
from tests.ops.test_toss_accumulate import FakeToss, _FixedDT, _mod, _seed

TAX = {
    "summary": {"gain_krw": -2_518_746.0, "tax_krw": 0.0, "tax_free_room_krw": 100_000.0},
    "by_tag": {},
    "unrealized_total": 90_500.0,
}


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.pinned: list[int] = []
        self.unpinned: list[int] = []
        self.edits: list[tuple[int, str]] = []
        self.answered: list[str] = []

    def send_message(self, text: str, **kw: Any) -> int:
        self.sent.append({"text": text, **kw})
        return 100 + len(self.sent)

    def pin(self, mid: int) -> None:
        self.pinned.append(mid)

    def unpin(self, mid: int) -> None:
        self.unpinned.append(mid)

    def edit(self, mid: int, text: str, **kw: Any) -> None:
        self.edits.append((mid, text))

    def answer_callback(self, cid: str, text: str = "") -> None:
        self.answered.append(cid)


def _setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, deneb: DenebState
) -> tuple[Any, FakeToss, FakeBot, list[InboxMessage]]:
    from auto_trading.notify import inbox as inbox_mod

    fake = FakeToss(usd="5000")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "QS_LIVE", False)
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    bot = FakeBot()
    monkeypatch.setattr(mod, "_bot", lambda: bot)
    monkeypatch.setattr(mod, "_tax_summary", lambda reader, state: TAX)
    monkeypatch.setattr(mod, "load_telegram_credentials", lambda path: object())
    box: list[InboxMessage] = []

    class FakeInbox:
        def __init__(self, creds: object) -> None:
            pass

        def fetch(self, offset: int) -> tuple[list[InboxMessage], int]:
            out = list(box)
            box.clear()
            return out, offset + len(out)

    monkeypatch.setattr(inbox_mod, "TelegramInbox", FakeInbox)
    _seed(mod)
    mod._save_deneb(deneb)
    return mod, fake, bot, box


def _state(tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(
        (tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8")
    )
    return state


def _night(monkeypatch: pytest.MonkeyPatch, mod: Any, fake: FakeToss, d: str) -> None:
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime.fromisoformat(f"{d}T21:00:00+00:00"))
    for k in range(1, len(fake.sent) + 1):
        fake.statuses.setdefault(
            f"OID-{k}", TossOrderStatus("FILLED", Decimal(1), Decimal(1), Decimal(1), Decimal(0))
        )
    assert mod.run(telegram=True) == 0


def _deneb_state(**kw: Any) -> DenebState:
    deneb = DenebState("2026-10-01", cash_usd=0.0, qty={"TQQQ": 10.0, "SCHD": 5.0}, **kw)
    deneb.orders.append(
        DenebOrder(
            "2026-10-02", "TQQQ", "BUY", 10, 52.0, "c0", "OID-X", "FILLED", "", 10.0, 500.0, 1.0
        )
    )
    return deneb


def test_december_harvest_notice_input_qty_sell_then_buy_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, fake, bot, box = _setup(monkeypatch, tmp_path, _deneb_state(done_month="2026-12"))
    # 12-01(화) 09:00 아침 확인 → 메시지 + 아래 버튼 (2026-10-11 부터 공지 고정 안 함)
    # 주당 이익 (81.07 − 50.1) × 1350 ≈ 41,810원 → 100,000원 안에서 2 주
    now = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    mod._deneb_morning(now, True)
    notice = bot.sent[-1]
    assert "공제 남은 100,000원" in notice["text"] and "TQQQ 2주" in notice["text"]
    assert [b["text"] for b in notice["buttons"][0]] == ["전부 진행", "직접입력", "이번엔 안 함"]
    assert bot.pinned == []
    mod._deneb_morning(now, True)  # 다시 불러도 두 번 보내지 않는다
    assert len(bot.sent) == 1
    # [직접입력] → 입력칸 알림 → "1" → 접수 + [취소]
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 12, 1, 10, 0, tzinfo=UTC))
    box.append(InboxMessage(1, 0, "", data="h2026:input", callback_id="cb1", message_id=101))
    mod._deneb_poll("2026-12-01", True)
    assert bot.answered == ["cb1"] and bot.sent[-1]["force_reply"] is True
    assert "몇 주 팔까요" in bot.sent[-1]["text"]
    box.append(InboxMessage(2, 0, "1", reply_to=bot.sent[-1].get("reply_to", 0)))
    mod._deneb_poll("2026-12-01", True)
    assert "오늘 밤 TQQQ 1주" in bot.sent[-1]["text"]
    assert bot.sent[-1]["buttons"][0][0]["callback_data"] == "h2026:cancel"
    p = _state(tmp_path)["proposals"][0]
    assert p["status"] == "accepted" and p["qty"] == 1 and "예약" in bot.edits[-1][1]
    # 12-01 밤: 공제 실현 매도 1 주 (매달 매수는 이미 끝남)
    n0 = len(fake.sent)
    _night(monkeypatch, mod, fake, "2026-12-01")
    sells = [o for o in fake.sent[n0:] if o[0] == "TQQQ"]
    assert [(o[1], o[2]) for o in sells] == [("SELL", 1)]
    st = _state(tmp_path)
    sell_oid = next(o["order_id"] for o in st["orders"] if o["kind"] == "harvest_sell")
    # 12-02 밤: 매도 체결 → 같은 수량 되사기
    fake.statuses[sell_oid] = TossOrderStatus(
        "FILLED", Decimal(1), Decimal(81), Decimal(81), Decimal("0.08")
    )
    n1 = len(fake.sent)
    _night(monkeypatch, mod, fake, "2026-12-02")
    buys = [o for o in fake.sent[n1:] if o[0] == "TQQQ"]
    assert [(o[1], o[2]) for o in buys] == [("BUY", 1)]
    buy_oid = next(o["order_id"] for o in _state(tmp_path)["orders"] if o["kind"] == "harvest_buy")
    # 12-03 밤: 되사기 체결 → 끝, 고정 풀기
    fake.statuses[buy_oid] = TossOrderStatus(
        "FILLED", Decimal(1), Decimal(82), Decimal(82), Decimal("0.08")
    )
    _night(monkeypatch, mod, fake, "2026-12-03")
    p = _state(tmp_path)["proposals"][0]
    assert p["status"] == "done" and 101 in bot.unpinned
    assert _state(tmp_path)["qty"]["TQQQ"] == 10.0


def test_dividend_step_partial_then_cancel_restores_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deneb = _deneb_state(done_month="2026-10")
    deneb.dividends.append(
        ["2026-09-20", "SCHD", 100, 0.3, 900.0]
    )  # 900 × 1350 ÷ 12 ≈ 월 101,250원
    mod, _, bot, box = _setup(monkeypatch, tmp_path, deneb)
    mod._deneb_morning(datetime(2026, 10, 12, 9, 0, tzinfo=UTC), True)
    text = bot.sent[-1]["text"]
    # 전체 976.45 달러 × 50% = 488 → TQQQ 810.7 중 322 초과 → 4 주 팔고 SCHD 9 주
    assert "배당 단계 도달 (월 10만 원)" in text and "TQQQ 목표 60% → 50%" in text
    assert "TQQQ 4주 매도" in text and "SCHD 9주 매수" in text
    pid = _state(tmp_path)["proposals"][0]["pid"]
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 12, 10, 0, tzinfo=UTC))
    box.append(InboxMessage(1, 0, "", data=f"{pid}:input", callback_id="c", message_id=101))
    box.append(InboxMessage(2, 0, "2"))
    mod._deneb_poll("2026-10-12", True)
    st = _state(tmp_path)
    assert abs(st["lev_cap"] - 0.55) < 1e-12 and st["div_steps"] == 1  # 4 주 중 2 주 → 절반만 낮춤
    box.append(InboxMessage(3, 0, "", data=f"{pid}:cancel", callback_id="d", message_id=101))
    mod._deneb_poll("2026-10-12", True)
    st = _state(tmp_path)
    assert st["lev_cap"] == 0.6 and st["div_steps"] == 0 and st["proposals"][0]["status"] == "open"
    box.append(InboxMessage(4, 0, "", data=f"{pid}:no", callback_id="e", message_id=101))
    mod._deneb_poll("2026-10-12", True)
    st = _state(tmp_path)
    assert st["div_steps"] == 1 and st["lev_cap"] == 0.6 and 101 in bot.unpinned
    assert "TQQQ 60%를 그대로" in bot.sent[-1]["text"] and "30만 원" in bot.sent[-1]["text"]


def test_monthly_message_has_tax_lines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod, _, _, _ = _setup(monkeypatch, tmp_path, _deneb_state())
    text = mod._deneb_preview(datetime(2026, 11, 2, 9, 0, tzinfo=UTC))
    assert "🧾 올해 해외주식 양도" in text and "실현 손익 -2,518,746원" in text
    assert "세금 없이 더 실현할 수 있는 이익 100,000원" in text


def test_amount_answer_still_works_with_open_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deneb = _deneb_state(done_month="2026-11")
    mod, _, _, box = _setup(monkeypatch, tmp_path, deneb)
    mod._deneb_morning(datetime(2026, 12, 1, 9, 0, tzinfo=UTC), True)  # 공제 제안 열림
    st = mod._load_deneb()
    st.ask_month, st.ask_ts = "2026-12", 0
    mod._save_deneb(st)
    box.append(InboxMessage(1, 5, "50만"))  # 세 곳 합계 → Deneb 몫 40만 (시안 A)
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 12, 1, 9, 5, tzinfo=UTC))
    monkeypatch.setattr(mod, "_usdt_krw", lambda: None)
    mod._deneb_poll("2026-12-01", True)
    assert _state(tmp_path)["amounts"] == {"2026-12": 400_000.0}
    assert _state(tmp_path)["proposals"][0]["status"] == "open"
