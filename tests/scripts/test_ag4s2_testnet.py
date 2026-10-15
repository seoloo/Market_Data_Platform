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
    lines = m.blocked_small([("BTC", 918.0), ("ETH", 661.0)], {"BTC": 100.0, "ETH": 20.0})
    assert lines == ["BTC 46 < 100 USDC"]  # 2,000 장부 주문을 100 달러로 줄이면 BTC 만 막힘


def test_tick_does_nothing_between_bars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    day = 1_792_000_000_000 - 1_792_000_000_000 % DAY
    m._save({"long_day": day // DAY, "short_bar": (day + 60_000) // H4})

    def boom() -> None:
        raise AssertionError("차례가 아니면 거래소에 연결하지 않는다")

    monkeypatch.setattr(m, "_client", boom)
    assert m.tick(day + 60 * 60_000) == []


INFO = {"parts": [-0.032, 0.295, 0.244], "vol": 0.44, "cap": 0.344}


def test_long_block_shows_what_and_why() -> None:
    d = {
        "before": 0.005,
        "after": 0.011,
        "q": 0.006,
        "p": 80839.2,
        "side": "BUY",
        "target": 918.0,
        "cur": 404.0,
    }
    out = m.long_block("BTC", d, INFO, 0.115, "🛡 BTC 스톱 64,635.70 (롱 −20%)")
    assert out[0] == "📈 BTC 롱 늘림  0.005 → 0.011 BTC  (+0.006 @ 80,839.20)"
    assert out[1] == " 왜? 추세 점수 1/3"
    assert "20일 🔻-3.2% · 60일 🔺+29.5% · 120일 🔺+24.4%" in out[2]
    assert "× 최대 비중 34.4% (변동성 44%) = 11.5%" in out[3]
    assert out[4].endswith("× AG4 4배 = 46% → 목표 918 USDC (전 404)")
    assert out[5] == " 🛡 비상 스톱 64,635.70 (평균가 -20%)"


def test_long_close_says_short_is_now_possible() -> None:
    d = {
        "before": 0.28,
        "after": 0.0,
        "q": 0.28,
        "p": 2150.0,
        "side": "SELL",
        "target": 0.0,
        "cur": 600.0,
    }
    info = {"parts": [-0.141, -0.023, 0.18], "vol": 0.6, "cap": 0.25}
    out = m.long_block("ETH", d, info, 0.0, "")
    assert out[0].startswith("📉 ETH 롱 정리  0.28 → 0")
    assert "   → 롱만이라 0%" in out and " 👀 이제 ETH 숏 대기" in out


def test_short_block_lists_three_reasons_and_size() -> None:
    k = {"rsi_prev": 74.2, "rsi": 66.8, "adx": 30.4, "pdi": 16.5, "mdi": 24.6}
    out = m.short_block("ETH", 0.186, 2150.0, 2000.0, k, 0.0, 400.0, 2214.5, 1_792_000_000_000)
    text = "\n".join(out)
    assert "🔻 ETH 숏 진입  0.186 ETH @ 2,150.00  (약 400 USDC = 장부 20%)" in text
    assert "RSI7 74.2 → 66.8" in text and "ADX 30.4 > 25 · -DI 24.6 > +DI 16.5" in text
    assert "min(장부 10%×2배 400, 위험 1%÷3.4% 590) → 400" in text
    assert "🛡 손절 2,214.50 (+3%)" in text


def test_alert_text_has_position_block_and_no_short_reason() -> None:
    rows = [("BTC", 0.011, 80794.7, 80800.0), ("ETH", 0.28, 2355.0, 2355.0)]
    text = m.alert_text(
        0, ["📈 BTC 롱 늘림", ""], [], {"BTC": 0.11, "ETH": 0.08}, False, rows, 2000.6, 2000.0
    )
    assert "🚫 숏 없음 — BTC·ETH 모두 롱 목표가 있어 규칙상 숏 안 함" in text
    assert " BTC 롱 0.011 · 889 USDC (+0.0%)" in text
    assert " 현금 452 · 장부 2,000.6 USDC (시작 2,000)" in text
    skipped = m.alert_text(0, ["x"], ["BTC(롱 목표 있음)"], {}, False, [], 2000.0, 2000.0)
    assert "🚫 숏 신호 났지만 안 함: BTC(롱 목표 있음)" in skipped
