"""Vega 바이낸스 실행기 — 가짜 거래소 (실제 호출 없음)."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import ConfigError
from auto_trading.core.types import Candle
from auto_trading.exchange.binance_spot_live import BinanceSpotLive, load_live_spot
from auto_trading.exchange.spot import OrderStatus, SpotFilters, SpotOrderResult
from scripts import live_t2_binance as m

DAY = 86_400_000
NOW = 1_792_000_000_000 - 1_792_000_000_000 % DAY + 5 * 60_000  # 어떤 날 00:05 UTC


def _candles(dms: int, growth: float) -> list[Candle]:
    out = []
    for k in range(200, 0, -1):
        close_ms = dms - (k - 1) * DAY
        px = Decimal(str(round(100 * (1 + growth) ** (200 - k), 6)))
        out.append(
            Candle(
                open_ms=close_ms - DAY + 1,
                close_ms=close_ms,
                open=px,
                high=px,
                low=px,
                close=px,
                volume=Decimal(1),
                quote_volume=Decimal(1),
                trade_count=1,
            )
        )
    return out


class FakeBinance:
    def __init__(self, usdt: Decimal, fill: bool = True) -> None:
        self.bal = {"USDT": usdt}
        self.px = {"BTCUSDT": Decimal(100), "ETHUSDT": Decimal(10), "SOLUSDT": Decimal(1)}
        self.orders: dict[str, SpotOrderResult] = {}
        self.fill = fill
        self.sent: list[tuple[str, str, Decimal, Decimal]] = []
        self.trading_enabled = True
        self.fut = Decimal(0)
        self.moved: list[Decimal] = []
        self.transfer_error: Exception | None = None

    def futures_balance(self, asset: str = "USDT") -> Decimal:
        return self.fut

    def transfer(self, direction: Any, asset: str, amount: Decimal) -> str:
        if self.transfer_error is not None:
            raise self.transfer_error
        self.bal["USDT"] -= amount
        self.fut += amount
        self.moved.append(amount)
        return f"T{len(self.moved)}"

    def transfers(self, direction: Any, start_ms: int, end_ms: int) -> list[Any]:
        return []

    def balances(self) -> dict[str, Decimal]:
        return dict(self.bal)

    def last_price(self, symbol: str) -> Decimal:
        return self.px[symbol]

    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]:
        return _candles(as_of_ms, 0.003)

    def filters(self, symbol: str) -> SpotFilters:
        return SpotFilters(Decimal("0.0001"), Decimal("0.0001"), Decimal(5))

    def order_book(self, symbol: str, limit: int = 5) -> tuple[list[Any], list[Any]]:
        p = self.px[symbol]
        bids = [(p - i, Decimal(9)) for i in range(5)]
        asks = [(p + i, Decimal(9)) for i in range(5)]
        return bids, asks

    def get_order(self, symbol: str, ident: str) -> SpotOrderResult | None:
        return self.orders.get(ident)

    def place_limit_ioc(
        self, symbol: str, side: str, qty: Decimal, price: Decimal, ident: str
    ) -> SpotOrderResult:
        self.sent.append((symbol, side, qty, price))
        done = qty if self.fill else Decimal(0)
        coin = symbol.removesuffix("USDT")
        sign = 1 if side == "BUY" else -1
        self.bal[coin] = self.bal.get(coin, Decimal(0)) + sign * done
        self.bal["USDT"] -= sign * done * price
        status = OrderStatus("FILLED" if self.fill else "EXPIRED")
        r = SpotOrderResult(
            ident,
            str(len(self.orders)),
            symbol,
            side,
            status,
            qty,
            done,
            done * price,
            Decimal(0),
            "",
        )
        self.orders[ident] = r
        return r

    def fills(self, symbol: str, order_id: str) -> list[Any]:
        return []

    def close(self) -> None:
        return None


_SPLIT = m._split


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(m, "_split", lambda *a, **k: [])  # Altair 분배는 아래 시험에서만
    monkeypatch.setattr(m, "REPO", tmp_path)
    monkeypatch.setattr(m, "DATA", tmp_path / "data" / "binance_t2")
    monkeypatch.setattr(m, "STATE", tmp_path / "data" / "binance_t2" / "state.json")
    monkeypatch.setattr(m, "REPORTS", tmp_path / "reports" / "binance_t2")
    monkeypatch.setattr(m, "HALT", tmp_path / "data" / "HALT")
    monkeypatch.setattr(time, "time", lambda: NOW / 1000)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return tmp_path


def test_first_run_buys_three_coins_with_ioc_at_third_level(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeBinance(Decimal(1000))
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    assert m.run_cmd() == 0
    sides = {(s, side) for s, side, _q, _p in fake.sent}
    assert sides == {("BTCUSDT", "BUY"), ("ETHUSDT", "BUY"), ("SOLUSDT", "BUY")}
    assert all(p == fake.px[s] + 2 for s, _side, _q, p in fake.sent)  # 매도 호가 세 번째 칸
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert st["deposits"][0][1] == "1000" and st["last_decision"]
    assert m.run_cmd() == 0 and len(fake.sent) == 3  # 같은 날 다시: 주문 없음


def test_halt_cruise_blocks_orders(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (env / "data").mkdir(parents=True)
    (env / "data" / "HALT_cruise").write_text("전부 정리", encoding="utf-8")
    fake = FakeBinance(Decimal(1000))
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    assert m.run_cmd() == 0
    assert fake.sent == []
    outbox = (env / "reports" / "binance_t2" / "outbox.log").read_text(encoding="utf-8")
    assert "긴급 정지" in outbox


def test_unfilled_ioc_is_reported_and_ledger_unchanged(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeBinance(Decimal(1000), fill=False)
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    assert m.run_cmd() == 0
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert all("체결 없음" in o["line"] for o in st["orders"])
    assert st["expected"]["USDT"] == "1000"


def test_live_key_needed_and_orders_need_enable_flag(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_live_spot(tmp_path / ".env", environ={})
    _creds, on = load_live_spot(
        tmp_path / ".env",
        environ={"BINANCE_LIVE_API_KEY": "k", "BINANCE_LIVE_API_SECRET": "s"},
    )
    assert on is False
    c = BinanceSpotLive(
        BinanceCredentials("k", "s"),
        trading_enabled=False,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    with pytest.raises(ConfigError):
        c.place_limit_ioc("BTCUSDT", "BUY", Decimal(1), Decimal(1), "x")
    with pytest.raises(ConfigError):
        c.place_market_order("BTCUSDT", "BUY", Decimal(1), "x")


def test_order_posts_limit_ioc(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_000})
        seen.append(req)
        return httpx.Response(
            200,
            json={
                "clientOrderId": "t2b-x",
                "orderId": 7,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "status": "FILLED",
                "origQty": "0.001",
                "executedQty": "0.001",
                "cummulativeQuoteQty": "100",
            },
        )

    c = BinanceSpotLive(
        BinanceCredentials("k", "s"),
        trading_enabled=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: 1_000,
    )
    r = c.place_limit_ioc("BTCUSDT", "BUY", Decimal("0.001"), Decimal("100000"), "t2b-x")
    q = seen[0].url.params
    assert seen[0].method == "POST" and q["type"] == "LIMIT" and q["timeInForce"] == "IOC"
    assert q["price"] == "100000" and r.executed_qty == Decimal("0.001")


def _db(env: Path) -> None:
    import sqlite3

    sql = Path(m.__file__).resolve().parent.parent / "src" / "auto_trading" / "storage" / "sql"
    (env / "data").mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(env / "data" / "trading.db") as conn:
        conn.executescript((sql / "009_wallet_transfers.sql").read_text(encoding="utf-8"))


def test_falcon_split_moves_half_once_big_enough(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db(env)
    monkeypatch.setattr(m, "_split", _SPLIT)
    fake = FakeBinance(Decimal(1200))
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    assert m.run_cmd() == 0
    assert fake.moved == [Decimal("600.00")]  # 첫 분배: 선물 목표 50%
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert st["deposits"][0][1] == "600.00"  # Vega 는 남은 600 만 자기 돈으로 본다
    outbox = (env / "reports" / "binance_t2" / "outbox.log").read_text(encoding="utf-8")
    assert "Altair 몫 600.00 USDT 현물 → 선물 이체" in outbox


def test_falcon_split_small_account_and_rejected(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.core.errors import OrderRejectedError

    _db(env)
    monkeypatch.setattr(m, "_split", _SPLIT)
    small = FakeBinance(Decimal(218))
    monkeypatch.setattr(m, "_client", lambda **_: small)
    assert m.run_cmd() == 0
    assert small.moved == []  # 선물 목표 109 < 500 → 안 옮김
    big = FakeBinance(Decimal(1200))
    big.transfer_error = OrderRejectedError("code=-1002")
    monkeypatch.setattr(m, "_client", lambda **_: big)
    monkeypatch.setattr(time, "time", lambda: NOW / 1000 + 86_400)
    assert m.run_cmd() == 0  # 거부돼도 Vega 는 돈다
    outbox = (env / "reports" / "binance_t2" / "outbox.log").read_text(encoding="utf-8")
    assert "Altair 이체 거부" in outbox and "Universal Transfer" in outbox
