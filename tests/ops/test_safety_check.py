"""매일 안전 점검 — 판단 함수 (가짜 값)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from auto_trading.ops import safety_check as m

D = Decimal


def test_switch_and_halt_changes_only_after_first_day() -> None:
    assert m.state_changes({}, {"TOSS": "켜짐"}, "스위치") == []
    out = m.state_changes(
        {"TOSS": "꺼짐", "UPBIT": "꺼짐"}, {"TOSS": "켜짐", "UPBIT": "꺼짐"}, "스위치"
    )
    assert [f.text for f in out] == ["스위치 TOSS: 꺼짐 → 켜짐"] and out[0].level == "info"


def test_key_permissions() -> None:
    assert m.key_issues({"enableWithdrawals": False, "ipRestrict": True}) == []
    texts = [f.text for f in m.key_issues({"enableWithdrawals": True, "ipRestrict": False})]
    assert any("출금" in t for t in texts) and any("IP" in t for t in texts)


def test_futures_mode_and_stray_positions() -> None:
    assert m.futures_issues(True, False, {}, False) == []
    texts = [f.text for f in m.futures_issues(False, True, {"BTCUSDC": D("0.01")}, False)]
    assert len(texts) == 3 and "단일 자산" in texts[0] and "BTCUSDC 0.01" in texts[2]
    assert m.futures_issues(True, False, {"BTCUSDC": D("0.01")}, True) == []  # Altair 켜짐


def test_tasks_duplicates_and_required() -> None:
    ok = {
        "AutoTrading_Telegram_Bot": "Ready",
        "AutoTrading_Vega": "Ready",
        "AutoTrading_Binance_Cruise": "Disabled",
        "AutoTrading_Deneb_Chat": "Disabled",
        "AutoTrading_Toss_M3_Chat": "Ready",
    }
    assert m.task_issues(ok) == []
    bad = {**ok, "AutoTrading_Deneb_Chat": "Ready", "AutoTrading_Telegram_Bot": "Disabled"}
    texts = [f.text for f in m.task_issues(bad)]
    assert any("Toss_M3_Chat" in t and "Deneb_Chat" in t for t in texts)
    assert any("Telegram_Bot" in t for t in texts)


def test_vega_sold_outside_ledger_and_holdings() -> None:
    exp = {"KRW": "300000", "BTC": "0.00030375", "ETH": "0.00732129"}
    got = {"KRW": D("1000"), "BTC": D("0.00000375"), "ETH": D("0.00732")}  # ETH 수수료만큼은 괜찮음
    texts = [f.text for f in m.vega_issues(exp, got)]
    assert len(texts) == 1 and "Vega BTC" in texts[0]
    assert m.holding_issues("Deneb", {"TQQQ": D(0)}, {}) == []
    out = m.holding_issues("💹 개별주", {"NVDA": D(10)}, {"NVDA": D(4)})
    assert "NVDA 장부 10 > 토스 보유 4" in out[0].text


def test_report_text_silent_when_clean() -> None:
    assert m.report_text([], []) is None
    text = m.report_text(
        [m.Finding("warn", "출금 켜짐"), m.Finding("info", "스위치 바뀜")], ["토스 보유 (Timeout)"]
    )
    assert text is not None
    assert text.index("⚠️ 확인 필요") < text.index("ℹ️ 어제와") < text.index("❔ 확인 못 한 것")


def test_run_once_a_day_after_0920(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import safety_check as s

    monkeypatch.setattr(s, "STATE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(s, "_send", sent.append)
    monkeypatch.setattr(
        s, "check", lambda: ([m.Finding("warn", "출금 켜짐")], [], {"switches": {}, "halts": {}})
    )
    kst = ZoneInfo("Asia/Seoul")
    assert s.run(datetime(2026, 10, 17, 9, 10, tzinfo=kst)) is None  # 09:20 전
    assert s.run(datetime(2026, 10, 17, 9, 21, tzinfo=kst)) is not None and len(sent) == 1
    assert s.run(datetime(2026, 10, 17, 15, 0, tzinfo=kst)) is None  # 하루 한 번
