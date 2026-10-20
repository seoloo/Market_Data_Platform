"""🧭 Polaris Paper — 가짜 돈 회차 · 12 월 맞추기 · 하루 평가 (조회 없음)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ops import user_settings
from auto_trading.ops.polaris import ROUNDS, SYMBOLS, PolarisState, limit_price
from scripts import polaris_paper as pp

FX = 1400.0
CLOSES = {"SPYM": 70.0, "QQQM": 220.0, "IEF": 95.0, "GLDM": 80.0}


def _fresh() -> dict[str, Any]:
    return {
        "state": PolarisState(),
        "cash_krw": 0.0,
        "deposits_krw": 0.0,
        "last_close_day": "",
        "history": [],
    }


def test_nothing_before_november_and_same_close_once() -> None:
    paper = _fresh()
    assert pp.step(paper, "2026-10-30", CLOSES, FX, 1_000_000) == []
    assert paper["deposits_krw"] == 0 and paper["history"] == []
    assert paper["last_close_day"] == "2026-10-30"
    assert pp.step(paper, "2026-10-30", CLOSES, FX, 1_000_000) == []  # 같은 종가 두 번 없음


def test_four_rounds_then_december_rebalance() -> None:
    paper = _fresh()
    days = ["2026-11-02", "2026-11-03", "2026-12-01", "2027-01-04", "2027-02-01", "2027-03-01"]
    sent = []
    for day in days:
        sent += pp.step(paper, day, CLOSES, FX, 1_000_000)
    st = paper["state"]
    assert st.done_rounds == ROUNDS and len(sent) == ROUNDS  # 3 월엔 회차 없음
    assert "1/4 회차 1,000,000원" in sent[0] and "평가" in sent[0]
    assert paper["deposits_krw"] == 4_000_000
    spent = sum(o["usd"] + o["fee"] for o in st.orders) * FX
    assert paper["cash_krw"] == pytest.approx(4_000_000 - spent)
    assert 0 < paper["cash_krw"] < 4_000_000 * 0.1
    assert [h["date"] for h in paper["history"]] == days  # 첫 회차 날부터 매일
    # 주식 값이 크게 올라 12 월에 맞추기
    up = {**CLOSES, "QQQM": 400.0}
    texts = pp.step(paper, "2027-12-01", up, FX, 1_000_000)
    assert len(texts) == 1 and "12 월 맞추기" in texts[0] and "QQQM" in texts[0]
    assert st.rebal_for("2027") is not None and st.rebal_for("2027").status == "done"
    assert pp.step(paper, "2027-12-02", up, FX, 1_000_000) == []  # 그해 한 번


def test_buy_price_is_live_limit_rule() -> None:
    paper = _fresh()
    pp.step(paper, "2026-11-02", CLOSES, FX, 1_000_000)
    for order in paper["state"].orders:
        price = order["usd"] / order["qty"]
        assert price == pytest.approx(limit_price(CLOSES[order["symbol"]]))
    assert set(paper["state"].qty) == set(SYMBOLS)


def test_daily_morning_only_and_saves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pp, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(pp, "OUTBOX", tmp_path / "outbox.log")
    monkeypatch.setattr(pp, "load_telegram_credentials", lambda path: None)
    monkeypatch.setattr(pp, "_market_data", lambda today: ("2026-11-02", CLOSES, FX))
    monkeypatch.setattr(user_settings, "get", lambda root, key: 1_000_000)
    assert pp.daily(datetime(2026, 11, 3, 0, 20, tzinfo=pp.KST)) == 0
    assert not (tmp_path / "state.json").exists()  # 밤 실행은 건너뜀
    assert pp.daily(datetime(2026, 11, 3, 9, 0, tzinfo=pp.KST)) == 0
    saved = pp.load()
    assert saved["state"].done_rounds == 1 and saved["last_close_day"] == "2026-11-02"
    assert "1/4 회차" in (tmp_path / "outbox.log").read_text(encoding="utf-8")
