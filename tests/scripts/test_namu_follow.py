"""나무 모의 따라가기 실행 — 가짜 모의 계좌 (실제 호출 없음)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.namu import NamuOrder
from scripts import namu_follow

KST = namu_follow.KST


class FakeNamu:
    def __init__(self, fill_buys: bool = True) -> None:
        self.held: dict[str, int] = {}
        self.book: dict[int, NamuOrder] = {}
        self.canceled: list[int] = []
        self.fill_buys = fill_buys

    def balance(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return {}, [{"iem_cd": c, "rsdl_qty": q} for c, q in self.held.items()]

    def quote(self, code: str) -> dict[str, Any]:
        return {"askp1": 10_000, "bidp1": 9_990}

    def limit(self, side: str, code: str, qty: int, price: int) -> int:
        no = len(self.book) + 1
        filled = qty if (side == "sell" or self.fill_buys) else 0
        if filled:
            self.held[code] = self.held.get(code, 0) + (qty if side == "buy" else -qty)
        self.book[no] = NamuOrder(
            no, code, code, side, qty, price, filled, float(price), qty - filled, 0, ""
        )
        return no

    def orders(self, day: str) -> list[NamuOrder]:
        return list(self.book.values())

    def cancel(self, no: int, code: str) -> int:
        self.canceled.append(no)
        return no + 100

    def sleep(self, s: float) -> None:
        return None

    def close(self) -> None:
        return None


def _report(root: Path, paper: dict[str, Any]) -> None:
    p = root / "reports" / "kr_shadow" / "latest.json"
    p.parent.mkdir(parents=True)
    body = {"date": "2026-10-15", "kospi_above_200d": True, "paper": {"flow_rule": paper}}
    p.write_text(json.dumps(body), encoding="utf-8")


def test_follow_buys_paper_list_once_in_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _report(tmp_path, {"positions": [], "sell_next_close": {}, "buy_next_close": ["000001"]})
    fake = FakeNamu()
    monkeypatch.setattr(namu_follow, "_client", lambda root: fake)
    monkeypatch.setattr(namu_follow, "_open_today", lambda root, day: True)
    early = namu_follow.run(tmp_path, now=datetime(2026, 10, 16, 14, 59, tzinfo=KST))
    assert early == {"skip": "창 밖"}
    out = namu_follow.run(tmp_path, now=datetime(2026, 10, 16, 15, 2, tzinfo=KST))
    assert [(o["side"], o["code"], o["filled"]) for o in out["orders"]] == [
        ("buy", "000001", 70)  # 700 만 × 10% / 10,000 → 70 주
    ]
    assert fake.held == {"000001": 70} and out["equity"] < 7_000_000  # 비용 · 호가 차이만큼
    again = namu_follow.run(tmp_path, now=datetime(2026, 10, 16, 15, 10, tzinfo=KST))
    assert again == {"skip": "오늘 이미 함"}


def test_unfilled_buy_is_canceled_and_ledger_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _report(tmp_path, {"positions": [], "sell_next_close": {}, "buy_next_close": ["000001"]})
    fake = FakeNamu(fill_buys=False)
    monkeypatch.setattr(namu_follow, "_client", lambda root: fake)
    out = namu_follow.run(tmp_path, now=datetime(2026, 10, 16, 9, 0, tzinfo=KST), force=True)
    assert fake.canceled == [1] and out["orders"][0]["filled"] == 0
    assert out["cash"] == 7_000_000 and out["held"] == {}
