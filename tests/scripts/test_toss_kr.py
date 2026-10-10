"""국내 적립 실행기: 살 차례면 1 주 DAY 지정가 → 다음 날 체결 정리 · 원화 부족 · 정지 · 휴장."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import scripts.toss_kr as mod

from auto_trading.exchange.toss import TossOrderResult, TossOrderStatus
from auto_trading.ops import user_settings as us
from auto_trading.ops.kr_accumulate import Listed


@dataclass
class FakeCreds:
    trading_enabled: bool = True


@dataclass
class FakeToss:
    price: Decimal = Decimal(25_000)
    power: Decimal = Decimal(1_000_000)
    open_today: bool = True
    credentials: FakeCreds = field(default_factory=FakeCreds)
    sent: list[dict[str, Any]] = field(default_factory=list)
    statuses: dict[str, TossOrderStatus] = field(default_factory=dict)

    def kr_open_today(self, day: str) -> bool:
        return self.open_today

    def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        return dict.fromkeys(symbols, self.price)

    def buying_power(self, currency: str) -> Decimal:
        assert currency == "KRW"
        return self.power

    def order(self, symbol: str, side: str, qty: int, limit: Decimal, **kw: Any) -> TossOrderResult:
        self.sent.append({"symbol": symbol, "qty": qty, "limit": limit, **kw})
        return TossOrderResult(True, f"OID-{len(self.sent)}", "접수")

    def order_status(self, order_id: str) -> TossOrderStatus:
        return self.statuses[order_id]

    def close(self) -> None:
        pass


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeToss) -> list[str]:
    sent: list[str] = []
    for name, value in {
        "REPO": tmp_path,
        "DATA": tmp_path / "data" / "toss",
        "REPORTS": tmp_path / "reports" / "toss",
        "HALT": tmp_path / "data" / "HALT",
    }.items():
        monkeypatch.setattr(mod, name, value)
    monkeypatch.setattr(mod, "_trader", lambda symbols: fake)
    monkeypatch.setattr(mod, "_notify", lambda text: sent.append(text))
    v = us.load(tmp_path)
    form = {f.key: "on" for f in us.FIELDS if f.kind == "bool" and v[f.key]}
    form.update({"new.mkt": "KR", "new.query": "KODEX 200", "new.daily": "10,000"})
    new, err = us.validate(form, v, [Listed("069500", "KODEX 200", "KOSPI", "ETF")])
    assert not err
    us.apply(tmp_path, us.diff(v, new), datetime(2026, 10, 10, 11, tzinfo=UTC), new=new)
    return sent


def _day(monkeypatch: pytest.MonkeyPatch, day: int) -> None:
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, day, 12, 10, tzinfo=UTC))


def test_buys_when_accrued_then_settles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeToss()
    sent = _setup(tmp_path, monkeypatch, fake)
    _day(monkeypatch, 12)  # 11 · 12 = 20,000 < 1 주 25,125
    assert mod.run() == 0 and not fake.sent and not sent
    _day(monkeypatch, 13)  # 30,000 ≥ 25,125 × 1.0015
    assert mod.run() == 0
    assert fake.sent == [
        {
            "symbol": "069500",
            "qty": 1,
            "limit": Decimal(25_125),
            "client_order_id": "KR069500-2026-10-13",
            "time_in_force": "DAY",
        }
    ]
    assert "🛒 KODEX 200 1주 지정가 25,125원" in sent[-1]
    fake.statuses["OID-1"] = TossOrderStatus(
        "FILLED", Decimal(1), Decimal(25_050), Decimal(25_050), Decimal(4)
    )
    _day(monkeypatch, 14)  # 체결 정리 · 40,000 − 25,054 = 14,946 → 아직
    assert mod.run() == 0 and len(fake.sent) == 1
    assert "✅ KODEX 200 1주 체결 · 25,054원" in sent[-1]
    st = json.loads((tmp_path / "data/toss/KR_069500.json").read_text("utf-8"))
    assert st["last_qty"] == 1 and st["currency"] == "KRW" and st["market"] == "KOSPI"


def test_short_krw_halt_and_holiday(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeToss(power=Decimal(10_000))
    sent = _setup(tmp_path, monkeypatch, fake)
    _day(monkeypatch, 13)
    assert mod.run() == 0 and not fake.sent
    assert "원화가 모자라요" in sent[-1]
    fake.power = Decimal(1_000_000)
    (tmp_path / "data" / "HALT").write_text("시험", encoding="utf-8")
    _day(monkeypatch, 14)
    assert mod.run() == 0 and not fake.sent and "주문 안 함" in sent[-1]
    (tmp_path / "data" / "HALT").unlink()
    fake.open_today = False
    _day(monkeypatch, 15)
    assert mod.run() == 0 and not fake.sent


def test_no_items_is_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "REPO", tmp_path)
    assert mod.run() == 0


def test_no_order_outside_market_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeToss()
    _setup(tmp_path, monkeypatch, fake)
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 13, 16, 0, tzinfo=UTC))
    assert mod.run() == 0 and not fake.sent
