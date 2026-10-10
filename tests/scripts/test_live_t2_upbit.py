from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import scripts.live_t2_upbit as mod

from auto_trading.core.types import Candle
from auto_trading.exchange.upbit import UpbitOrder

DAY = 86_400_000


def _up(n: int, last_close_ms: int) -> list[Candle]:
    out, px = [], 100.0
    first = (last_close_ms + 1) // DAY - n
    for k in range(first, first + n):
        nxt = px * 1.01 * (1.01 if k % 2 else 0.99)
        out.append(
            Candle(
                k * DAY,
                k * DAY + DAY - 1,
                Decimal(str(px)),
                Decimal(str(max(px, nxt) * 1.001)),
                Decimal(str(min(px, nxt) * 0.999)),
                Decimal(str(nxt)),
                Decimal(1),
            )
        )
        px = nxt
    return out


class FakeUpbit:
    def __init__(self) -> None:
        self.krw = Decimal(300_000)
        self.hold = {"BTC": Decimal(0), "ETH": Decimal(0), "SOL": Decimal(0)}
        self.px = {"BTC": Decimal(100_000), "ETH": Decimal(10_000), "SOL": Decimal(1_000)}
        self.orders: dict[str, UpbitOrder] = {}
        self.sent: list[tuple[str, str, Decimal, Decimal, str]] = []

    def balances(self) -> dict[str, Decimal]:
        return {"KRW": self.krw, **self.hold}

    def tickers(self, markets: list[str]) -> dict[str, Decimal]:
        return {m: self.px[m.split("-")[1]] for m in markets}

    def orderbook(self, market: str) -> tuple[list[Decimal], list[Decimal]]:
        p = self.px[market.split("-")[1]]
        return [p * Decimal("1.001") * (1 + Decimal(k) / 1000) for k in range(5)], [
            p * Decimal("0.999") * (1 - Decimal(k) / 1000) for k in range(5)
        ]

    def closed_daily(self, market: str, as_of_ms: int, count: int = 200) -> list[Candle]:
        return _up(200, as_of_ms)

    def get_order(self, identifier: str) -> UpbitOrder | None:
        return self.orders.get(identifier)

    def place_limit_ioc(
        self, market: str, side: str, volume: Decimal, price: Decimal, identifier: str
    ) -> UpbitOrder:
        coin = market.split("-")[1]
        self.sent.append((coin, side, volume, price, identifier))
        funds = volume * self.px[coin]
        fee = funds * Decimal("0.0005")
        if side == "bid":
            self.krw -= funds + fee
            self.hold[coin] += volume
        else:
            self.krw += funds - fee
            self.hold[coin] -= volume
        o = UpbitOrder(identifier, "done", side, volume, volume, fee, funds)
        self.orders[identifier] = o
        return o

    def close(self) -> None:
        pass


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Any, FakeUpbit, list[str]]:
    fake = FakeUpbit()
    sent: list[str] = []
    monkeypatch.setattr(mod, "DATA", tmp_path / "data")
    monkeypatch.setattr(mod, "STATE", tmp_path / "data" / "state.json")
    monkeypatch.setattr(mod, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(mod, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(mod, "_client", lambda: fake)
    monkeypatch.setattr(mod, "_notify", sent.append)
    monkeypatch.setattr("scripts.live_t2_upbit.time.sleep", lambda s: None)
    monkeypatch.setattr("scripts.live_t2_upbit.time.time", lambda: 300 * DAY / 1000 + 3600)
    return mod, fake, sent


def test_run_buys_with_limit_ioc_once_per_day(env: tuple[Any, FakeUpbit, list[str]]) -> None:
    m, fake, sent = env
    assert m.main(["run"]) == 0
    sides = {(c, s) for c, s, *_ in fake.sent}
    assert sides == {("BTC", "bid"), ("ETH", "bid"), ("SOL", "bid")}
    assert fake.krw > 0  # 원화 안에서만 샀다
    assert sent and "Cruise" in sent[0]
    n = len(fake.sent)
    assert m.main(["run"]) == 0  # 같은 날 다시 → 주문 없음
    assert len(fake.sent) == n
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert st["last_decision"] and Decimal(st["units"]) > 0


def test_halt_blocks_orders(env: tuple[Any, FakeUpbit, list[str]]) -> None:
    m, fake, sent = env
    m.HALT.write_text("test", encoding="utf-8")
    assert m.main(["run"]) == 0
    assert fake.sent == []
    assert any("긴급 정지" in x for x in sent)


def test_deposit_detected_without_nav_jump(
    env: tuple[Any, FakeUpbit, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    m, fake, sent = env
    m.main(["run"])
    fake.krw += Decimal(300_000)  # 다음 달 입금
    monkeypatch.setattr("scripts.live_t2_upbit.time.time", lambda: 301 * DAY / 1000 + 3600)
    m.main(["run"])
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert Decimal(st["last_nav"]) == pytest.approx(Decimal(1000), rel=Decimal("0.01"))
    assert any("입금" in x for x in sent)


def test_alert_has_deposit_and_pnl(env: tuple[Any, FakeUpbit, list[str]]) -> None:
    m, _fake, sent = env
    m.main(["run"])
    assert "총 입금 300,000 원" in sent[0]
    assert "원가" in sent[0]


def test_stop_reminds_at_30_days_and_release_clears(
    env: tuple[Any, FakeUpbit, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    m, _fake, sent = env
    m.main(["run"])
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    day0 = st["last_decision"]
    st.update(stopped=True, stopped_since=day0, stop_reminded=[])
    m.STATE.write_text(json.dumps(st), encoding="utf-8")
    sent.clear()
    monkeypatch.setattr("scripts.live_t2_upbit.time.time", lambda: 330 * DAY / 1000 + 3600)
    m.main(["run"])
    assert any("정지 30일째" in x for x in sent)
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert st["stop_reminded"] == [30]
    m.main(["release-dd", "시험"])
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert not st["stopped"] and "stopped_since" not in st
