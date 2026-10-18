"""✨ Sirius 실행기 — 가짜 토스 · 가짜 결정으로 첫 묶음 매수 · 관문 · 달러 부족 · 시간 밖."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.toss import TossOrderResult, TossOrderStatus
from auto_trading.ops import sirius_live as core
from auto_trading.ops import user_settings
from scripts import sirius_live as sl

NOW = 1_793_631_600_000  # 2026-11-02 15:00 UTC = 10:00 뉴욕 (정규장 안)
PICKS = [f"S{i}" for i in range(10)]


class _Creds:
    trading_enabled = True


class FakeToss:
    def __init__(self, power: float = 10_000.0, session: bool = True) -> None:
        self.credentials = _Creds()
        self.power = power
        self.session = session
        self.sent: list[tuple[str, str, Decimal]] = []
        self.status: dict[str, TossOrderStatus] = {}

    def checked_regular_session(self, day: str, *, kr: bool) -> tuple[str, str] | None:
        return ("2026-11-02T23:30:00+09:00", "2026-11-03T06:00:00+09:00") if self.session else None

    def us_regular_session(self, day: str) -> tuple[str, str] | None:
        return None  # 10-31 · 11-01 주말

    def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        return {s: Decimal(10) for s in symbols}

    def buying_power(self, currency: str) -> Decimal:
        return Decimal(str(self.power))

    def amount_buy(self, symbol: str, usd: Decimal, *, client_order_id: str) -> TossOrderResult:
        self.sent.append((symbol, "BUY", usd))
        oid = f"O-{symbol}"
        self.status[oid] = TossOrderStatus("FILLED", usd / 10, Decimal(10), usd, Decimal("0.01"))
        return TossOrderResult(True, oid, "접수")

    def fractional_sell(
        self, symbol: str, qty: Decimal, *, client_order_id: str
    ) -> TossOrderResult:
        self.sent.append((symbol, "SELL", qty))
        return TossOrderResult(True, f"S-{symbol}", "접수")

    def order_status(self, order_id: str) -> TossOrderStatus:
        return self.status[order_id]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fake = FakeToss()
    box: dict[str, Any] = {"fake": fake, "notes": [], "on": True}
    monkeypatch.setattr(sl, "ROOT", tmp_path / "sirius")
    monkeypatch.setattr(sl, "STATE", tmp_path / "sirius" / "state.json")
    monkeypatch.setattr(sl, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(sl, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(sl, "POLL", (0,))
    monkeypatch.setattr(sl, "_now_ms", lambda: NOW)
    monkeypatch.setattr(sl, "_trader", lambda syms, cap: box["fake"])
    monkeypatch.setattr(sl, "_notify", lambda text: box["notes"].append(text))
    monkeypatch.setattr(sl, "_others_usd", lambda: 0.0)
    monkeypatch.setattr(user_settings, "get", lambda root, key: box["on"])
    days = ["2026-10-28", "2026-10-29", "2026-10-30"]
    close = {s: [10.0, 10.0, 10.0] for s in PICKS}
    from scripts import leader_ml_paper as lp

    monkeypatch.setattr(lp, "load_panel", lambda: (days, close, close, [1.0] * 3, close))
    monkeypatch.setattr(
        lp, "make_decider", lambda d, c, v: lambda t: {"GBM": PICKS, "GBM_prob": [0.5] * 10}
    )
    st = core.new_state("2026-10-19")
    core.deposit(st, "2026-10-19", 840_000, 600.0, 1400.0)
    sl._save(st)
    return box


def _state() -> dict[str, Any]:
    out: dict[str, Any] = json.loads(sl.STATE.read_text(encoding="utf-8"))
    return out


def test_first_tranche_buys_ten_equal_amounts_and_books_fills(env: dict[str, Any]) -> None:
    assert sl.run() == 0
    fake = env["fake"]
    assert [s for s, side, _ in fake.sent] == PICKS
    assert all(usd == Decimal("10") for _, _, usd in fake.sent)  # 600 ÷ 6 ÷ 10
    st = _state()
    assert st["decisions"]["2026-10-30"]["bought"] is True
    assert len(st["lots"]) == 10 and all(lot["buy_day"] == "2026-11-02" for lot in st["lots"])
    assert st["cash_usd"] == pytest.approx(600 - 10 * 10.01)
    assert any("결정 상위 10" in n for n in env["notes"])
    # 같은 날 다시 돌려도 다시 사지 않는다
    fake.sent.clear()
    sl.run()
    assert fake.sent == []


def test_switch_off_sends_nothing(env: dict[str, Any]) -> None:
    env["on"] = False
    sl.run()
    assert env["fake"].sent == [] and _state()["decisions"] == {}


def test_holiday_or_outside_window_sends_nothing(env: dict[str, Any]) -> None:
    env["fake"].session = False
    sl.run()
    assert env["fake"].sent == []


def test_dollar_shortage_alerts_and_retries_later(env: dict[str, Any]) -> None:
    env["fake"].power = 5.0
    sl.run()
    st = _state()
    assert env["fake"].sent == [] and st["decisions"]["2026-10-30"]["bought"] is False
    assert any("달러 부족" in n for n in env["notes"])
    env["fake"].power = 10_000.0
    sl.run()  # 다음 실행: 달러가 생기면 같은 결정으로 산다
    assert len(env["fake"].sent) == 10 and _state()["decisions"]["2026-10-30"]["bought"] is True


def test_halt_file_blocks_orders(env: dict[str, Any]) -> None:
    sl.HALT.write_text("테스트 정지", encoding="utf-8")
    sl.run()
    assert env["fake"].sent == []
