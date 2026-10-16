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
from auto_trading.core.errors import ConfigError, ExchangeError
from auto_trading.core.types import Candle
from auto_trading.exchange.binance_spot_live import BinanceSpotLive, load_live_spot
from auto_trading.exchange.spot import OrderStatus, SpotFilters, SpotOrderResult
from scripts import vega_live as m

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
        self.earn = Decimal(0)
        self.earn_log: list[tuple[str, Decimal | None]] = []

    def earn_balance(self, asset: str = "USDT") -> Decimal:
        return self.earn

    def earn_subscribe(self, asset: str, amount: Decimal) -> str:
        self.bal["USDT"] -= amount
        self.earn += amount
        self.earn_log.append(("in", amount))
        return "P1"

    def earn_redeem(self, asset: str, amount: Decimal | None = None) -> str:
        got = self.earn if amount is None else amount
        self.earn -= got
        self.bal["USDT"] += got
        self.earn_log.append(("out", amount))
        return "R1"

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
    monkeypatch.setattr(m, "DATA", tmp_path / "data" / "vega")
    monkeypatch.setattr(m, "STATE", tmp_path / "data" / "vega" / "state.json")
    monkeypatch.setattr(m, "REPORTS", tmp_path / "reports" / "vega")
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


def test_halt_vega_blocks_orders(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (env / "data").mkdir(parents=True)
    (env / "data" / "HALT_vega").write_text("전부 정리", encoding="utf-8")
    fake = FakeBinance(Decimal(1000))
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    assert m.run_cmd() == 0
    assert fake.sent == []
    outbox = (env / "reports" / "vega" / "outbox.log").read_text(encoding="utf-8")
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


def test_altair_split_moves_half_once_big_enough(
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
    outbox = (env / "reports" / "vega" / "outbox.log").read_text(encoding="utf-8")
    assert "Altair 몫 600.00 USDT 현물 → 선물 이체" in outbox


def test_altair_split_small_account_and_rejected(
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
    outbox = (env / "reports" / "vega" / "outbox.log").read_text(encoding="utf-8")
    assert "Altair 이체 거부" in outbox and "Universal Transfer" in outbox


def test_deposit_check_splits_once_outside_quiet_window(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """저녁 입금 → 10 분 확인이 바로 Altair 몫을 옮기고, 다시 봐도 또 옮기지 않는다 (2026-10-16)."""
    _db(env)
    monkeypatch.setattr(m, "_split", _SPLIT)
    monkeypatch.setattr(m, "ALTAIR_STATE", env / "data" / "altair_live" / "state.json")
    fake = FakeBinance(Decimal(218))
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    m._save({"expected": {"USDT": "218"}, "units": "0.218", "peak": "1000"})
    evening = NOW + 12 * 3_600_000  # 00:05 UTC + 12 시간 = 21:05 KST
    assert m.deposit_check(evening) == []  # 입금 없음 — 조용히
    fake.bal["USDT"] = Decimal("1705.10")  # 200 만 원 입금
    alt = env / "data" / "altair_live" / "state.json"
    alt.parent.mkdir(parents=True)
    alt.write_text(json.dumps({"long_day": 1}), encoding="utf-8")
    out = m.deposit_check(evening)
    assert out[0].startswith("💰 입금 감지") and fake.moved == [Decimal("852.55")]
    assert "long_day" not in json.loads(alt.read_text(encoding="utf-8"))  # Altair 바로 맞추기
    assert m.deposit_check(evening + 600_000) == [] and fake.moved == [Decimal("852.55")]
    fake.bal["USDT"] += Decimal(
        500
    )  # 같은 날 또 입금 → 그 500 만 새로 보고 모자란 Altair 몫을 채움
    m.deposit_check(evening + 1_200_000)
    assert fake.moved == [Decimal("852.55"), Decimal("250.00")]
    quiet = NOW + 60_000  # 09:06 KST — Vega 실행이 나눈다
    assert m.deposit_check(quiet, force=True) == ["09:05 Vega 실행 시간대라 그 실행이 나눠요"]


def test_deposit_check_ignores_small_moves(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeBinance(Decimal(1010))  # 장부보다 10 USDT 많음 — 20 USDT 미만
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    m._save({"expected": {"USDT": "1000"}, "units": "1", "peak": "1000"})
    out = m.deposit_check(NOW + 12 * 3_600_000, force=True)
    assert out[0].startswith("새 입금 없음") and fake.moved == []


def _earn_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from auto_trading.ops import user_settings

    real = user_settings.get
    monkeypatch.setattr(
        user_settings, "get", lambda root, key: True if key == "vega.earn" else real(root, key)
    )


def test_earn_parks_rest_after_buying_and_ledger_counts_it(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-17 사용자: 남는 USDT 는 유동형에, 장부는 현물 + 유동형을 현금으로 센다."""
    _earn_on(monkeypatch)
    fake = FakeBinance(Decimal(1000), fill=False)  # 매수가 안 차 현금이 그대로 남은 날
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    assert m.run_cmd() == 0
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert fake.earn > 0 and fake.bal["USDT"] == m.EARN_KEEP  # 20 USDT 만 현물에
    assert Decimal(st["expected"]["USDT"]) == fake.earn + fake.bal["USDT"]
    outbox = (env / "reports" / "vega" / "outbox.log").read_text(encoding="utf-8")
    assert "유동형에 넣음" in outbox and "💤 유동형" in outbox
    # 장부와 같은 합계 → 입금으로 오해하지 않음
    assert m.deposit_check(NOW + 12 * 3_600_000, force=True)[0].startswith("새 입금 없음")


def test_earn_is_redeemed_before_buying(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeBinance(Decimal(100))
    fake.earn = Decimal(900)  # 설정이 꺼져 있어도 이미 넣어 둔 돈은 현금으로 세고 꺼내 쓴다
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    assert m.run_cmd() == 0
    assert fake.earn_log[0] == ("out", None) and len(fake.sent) == 3
    st = json.loads(m.STATE.read_text(encoding="utf-8"))
    assert st["deposits"][0][1] == "1000"  # 첫 입금 = 현물 100 + 유동형 900


def test_altair_split_redeems_from_earn_first(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db(env)
    monkeypatch.setattr(m, "_split", _SPLIT)
    monkeypatch.setattr(m, "ALTAIR_STATE", env / "data" / "altair_live" / "state.json")
    _earn_on(monkeypatch)
    fake = FakeBinance(Decimal(20))
    fake.earn = Decimal(198)
    monkeypatch.setattr(m, "_client", lambda **_: fake)
    m._save({"expected": {"USDT": "218"}, "units": "0.218", "peak": "1000"})
    fake.earn += Decimal("1487.10")  # 입금이 (예치된 채로) 늘었다고 가정
    out = m.deposit_check(NOW + 12 * 3_600_000)
    assert fake.moved == [Decimal("852.55")] and any("유동형에서" in x for x in out)
    assert fake.bal["USDT"] == m.EARN_KEEP  # 남은 Vega 몫은 다시 유동형에


def test_earn_client_needs_enable_flag_and_redeems_to_spot() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_000})
        seen.append(req)
        if req.url.path.endswith("/position"):
            return httpx.Response(
                200, json={"rows": [{"asset": "USDT", "productId": "USDT001", "totalAmount": "5"}]}
            )
        return httpx.Response(200, json={"redeemId": 9, "success": True})

    def client(on: bool) -> BinanceSpotLive:
        return BinanceSpotLive(
            BinanceCredentials("k", "s"),
            trading_enabled=on,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            clock=lambda: 1_000,
        )

    with pytest.raises(ConfigError):
        client(False).earn_subscribe("USDT", Decimal(1))
    c = client(True)
    assert c.earn_balance() == Decimal(5)
    assert c.earn_redeem("USDT") == "9"
    q = seen[-1].url.params
    assert seen[-1].method == "POST" and q["redeemAll"] == "true" and q["destAccount"] == "SPOT"


def test_redeem_waits_until_earn_balance_catches_up(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """유동형 잔고 조회가 늦으면 기다리고, 끝내 안 맞으면 장부를 쓰지 않고 멈춘다."""
    fake = FakeBinance(Decimal(10))
    fake.earn = Decimal(500)
    lag = {"n": 2}
    real = fake.earn_balance

    def slow(asset: str = "USDT") -> Decimal:
        if fake.earn_log and lag["n"] > 0:
            lag["n"] -= 1
            return Decimal(500)  # 아직 옛 값
        return real(asset)

    monkeypatch.setattr(fake, "earn_balance", slow)
    assert m._ensure_spot(fake, Decimal(100)) == ["💤 유동형에서 110.00 USDT 꺼냄 (살 돈)"]  # type: ignore[arg-type]
    lag["n"] = 10**6
    with pytest.raises(ExchangeError):
        m._ensure_spot(fake, Decimal(400))  # type: ignore[arg-type]
