"""AG4S2 데모 실행기 — 크기 계산 · 차례 판단 (가짜 값, 거래소 호출 없음)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.ops.ag_short_paper import DAY, H4
from scripts import ag4s2_testnet as m


def test_short_size_is_smaller_of_margin_cap_and_one_percent_risk() -> None:
    assert m.short_notional(1000.0, 0.0, 0.0) == pytest.approx(200.0)  # 10% × 2 배
    assert m.short_notional(1000.0, 150.0, 0.0) == pytest.approx(50.0)  # 이미 든 숏만큼 뺌
    risky = m.short_notional(1000.0, 0.0, -0.01)  # 음수 펀딩이 크면 위험 쪽이 작다
    assert risky == pytest.approx(0.01 * 1000 / (0.03 + 0.0039 + 0.06))


def test_qty_rounds_down_to_step_and_small_account_report() -> None:
    assert m.round_qty(459.0, 80_000.0, Decimal("0.001")) == Decimal("0.005")
    assert m.round_qty(0.0, 80_000.0, Decimal("0.001")) == 0
    lines = m.blocked_small([("BTC", 459.0), ("ETH", 330.0)], {"BTC": 100.0, "ETH": 20.0})
    assert lines == ["BTC 46 < 100 USDC"]  # 100 달러 장부면 BTC 만 막힘


def test_tick_does_nothing_between_bars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    day = 1_792_000_000_000 - 1_792_000_000_000 % DAY
    m._save({"long_day": day // DAY, "short_bar": (day + 60_000) // H4})

    def boom() -> None:
        raise AssertionError("차례가 아니면 거래소에 연결하지 않는다")

    monkeypatch.setattr(m, "_client", boom)
    assert m.tick(day + 60 * 60_000) == []
