"""💹 개별주 월간 복기 — 규칙대로 / 규칙 밖 나눠 성적 (가짜 장부)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from auto_trading.ops import stock_journal as m
from auto_trading.ops.size_order import Order, Ticket

KST = ZoneInfo("Asia/Seoul")


def _ms(y: int, mo: int, d: int) -> int:
    return int(datetime(y, mo, d, 12, tzinfo=KST).timestamp() * 1000)


def _ticket(tid: str, sym: str, override: bool, buy_px: float, sell_px: float | None) -> Ticket:
    t = Ticket(tid, sym, sym, "USD", 10, buy_px, _ms(2026, 11, 3), status="holding")
    t.override = override
    t.buys = [Order("b", "b", "BUY", 10, buy_px, 0, True, "FILLED", 10, 10 * buy_px, 0.0)]
    t.filled, t.avg = 10.0, buy_px
    if sell_px is not None:
        t.sells = [Order("s", "s", "SELL", 10, sell_px, 0, True, "FILLED", 10, 10 * sell_px, 0.0)]
        t.status, t.closed_ms = "closed", _ms(2026, 11, 20)
    return t


def test_review_splits_rule_and_override() -> None:
    tickets = [
        _ticket("1", "NVDA", False, 100.0, 97.0),  # 규칙대로 · 손절 −3%
        _ticket("2", "TSLA", True, 200.0, 220.0),  # 규칙 밖 · +10%
        _ticket("3", "AAPL", False, 50.0, None),  # 들고 있음
    ]
    recs = [
        {"at": _ms(2026, 11, 2), "ok": True},
        {"at": _ms(2026, 11, 3), "ok": False},
        {"at": _ms(2026, 10, 30), "ok": True},  # 지난달 — 안 셈
    ]
    text = m.review_text("2026-11", recs, tickets, {"AAPL": 55.0})
    assert "추천 2번 (규칙 통과 1 · 비추천 1)" in text
    assert "매수 3건 — 규칙대로 2 · 규칙 밖(그래도 매수) 1" in text
    assert "규칙대로: 1건 · 평균 -3.0% · 이긴 비율 0%" in text
    assert "규칙 밖: 1건 · 평균 +10.0% · 이긴 비율 100%" in text
    assert "AAPL 10주 · +10.0%" in text


def test_monthly_review_once_on_first_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import size_guard as sg

    monkeypatch.setattr(sg, "DATA", tmp_path)
    monkeypatch.setattr(sg, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(sg, "JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setattr(sg, "REVIEW", tmp_path / "review.json")
    sent: list[list[str]] = []
    monkeypatch.setattr(sg, "notify", sent.append)
    monkeypatch.setattr(sg, "review", lambda month=None: f"💹 개별주 복기 · {month}")
    first = int(datetime(2026, 12, 1, 9, 31, tzinfo=KST).timestamp() * 1000)
    assert sg.monthly_review(first) is None and not sent  # 11 월에 추천 · 매수 없음 → 안 보냄
    (tmp_path / "review.json").unlink()
    with (tmp_path / "journal.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"at": _ms(2026, 11, 5), "ok": True}) + "\n")
    assert sg.monthly_review(first) == "💹 개별주 복기 · 2026-11" and len(sent) == 1
    assert sg.monthly_review(first + 60_000) is None  # 한 번만
    second = int(datetime(2026, 12, 2, 10, 0, tzinfo=KST).timestamp() * 1000)
    assert sg.monthly_review(second) is None  # 1 일만
