"""⚠️ 은퇴 — 실행하지 않음 (2026-10-19, retired/README.md).

Altair 하루 롱 맞추기 메이커(post-only) 주문 — 문서 269 · 사용자 2026-10-18 "그렇게 변경해줘".

가짜 거래소만 쓴다. 걸어 두기 · 다 체결 · 30 분 뒤 남은 양 IOC · 정지면 취소 ·
바로 체결될 자리면 IOC.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from auto_trading.core.errors import OrderRejectedError
from auto_trading.exchange.futures import FuturesOrderResult, FuturesOrderType
from auto_trading.exchange.spot import OrderStatus
from scripts import altair_demo as core

NOW = 1_792_000_000_000 - 1_792_000_000_000 % 86_400_000 + 5 * 60_000


def _res(
    ident: str, side: str, status: OrderStatus, qty: str, filled: str, price: str = "80000"
) -> FuturesOrderResult:
    return FuturesOrderResult(
        ident, "1", "BTCUSDC", side, FuturesOrderType.LIMIT, status, Decimal(qty),
        Decimal(filled), Decimal(price) if Decimal(filled) > 0 else Decimal(0), False, False,
        Decimal(0), Decimal(0), "USDC", Decimal(price),
    )  # fmt: skip


class _F:
    min_notional = Decimal(20)
    step_size = Decimal("0.001")
    tick_size = Decimal("0.1")


class _Pos:
    def __init__(self, q: str) -> None:
        self.quantity = Decimal(q)
        self.entry_price = Decimal("80000")


class _Acct:
    def __init__(self, pos: dict[str, _Pos]) -> None:
        self.positions = pos


class Fake:
    def __init__(self, gtx: OrderStatus = OrderStatus.NEW) -> None:
        self.gtx = gtx
        self.orders: dict[str, FuturesOrderResult] = {}
        self.post_only: list[tuple[str, str, Decimal, Decimal, bool]] = []
        self.ioc: list[tuple[str, str, Decimal]] = []
        self.cancelled: list[str] = []
        self.stops: list[str] = []
        self.pos: dict[str, _Pos] = {}

    def asset_balance(self, asset: str) -> Decimal:
        return Decimal(1000)

    def account(self) -> _Acct:
        return _Acct(self.pos)

    def filters(self, sym: str) -> _F:
        return _F()

    def mark_price(self, sym: str) -> Decimal:
        return Decimal(80000)

    def book_ticker(self, sym: str) -> tuple[Decimal, Decimal]:
        return Decimal("79990.0"), Decimal("80010.0")

    def place_limit_order(
        self, sym: str, side: str, qty: Decimal, price: Decimal, ident: str, *, reduce_only: bool
    ) -> FuturesOrderResult:
        self.post_only.append((sym, side, qty, price, reduce_only))
        r = _res(ident, side, self.gtx, str(qty), "0")
        self.orders[ident] = r
        return r

    def get_order(self, sym: str, ident: str) -> FuturesOrderResult | None:
        return self.orders.get(ident)

    def cancel_order(self, sym: str, ident: str) -> FuturesOrderResult | None:
        self.cancelled.append(ident)
        o = self.orders[ident]
        self.orders[ident] = _res(ident, o.side, OrderStatus.CANCELED, str(o.orig_qty),
                                  str(o.executed_qty))  # fmt: skip
        return self.orders[ident]

    def place_limit_ioc(
        self, sym: str, side: str, qty: Decimal, price: Decimal, ident: str, *, reduce_only: bool
    ) -> FuturesOrderResult:
        self.ioc.append((sym, side, qty))
        return _res(ident, side, OrderStatus.FILLED, str(qty), str(qty))

    def place_stop_market(self, sym: str, side: str, trig: Decimal, ident: str) -> None:
        self.stops.append(ident)

    def cancel_stop(self, sym: str, ident: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "long_targets", lambda now: ({"BTC": 0.1, "ETH": 0.0}, {}))


def test_rebalance_places_post_only_at_best_bid_not_ioc() -> None:
    c = Fake()
    st: dict[str, Any] = {"live": True}
    alert: list[str] = []
    lines = core.rebalance_long(c, st, NOW, alert=alert, maker=True)  # type: ignore[arg-type]
    assert c.ioc == []  # 즉시 체결 주문 없음
    sym, side, qty, price, reduce = c.post_only[0]
    assert (sym, side, price, reduce) == ("BTCUSDC", "BUY", Decimal("79990.0"), False)
    assert qty == Decimal("0.005")  # 1000 × 0.1 × 4 = 400 USDC ÷ 80,000
    assert st["maker"]["BTC"]["qty"] == "0.005"
    assert any("호가에 걸어 둠" in x for x in lines)


def test_filled_maker_is_recorded_and_stop_placed() -> None:
    c = Fake()
    st: dict[str, Any] = {"live": True}
    core.rebalance_long(c, st, NOW, alert=[], maker=True)  # type: ignore[arg-type]
    ident = st["maker"]["BTC"]["id"]
    c.orders[ident] = _res(ident, "BUY", OrderStatus.FILLED, "0.005", "0.005", "79990")
    c.pos = {"BTCUSDC": _Pos("0.005")}
    alert: list[str] = []
    core.maker_check(c, st, NOW + 5 * 60_000, halted=None, alert=alert)  # type: ignore[arg-type]
    assert "maker" not in st and c.ioc == [] and c.cancelled == []
    assert any("메이커 · 수수료 0" in x for x in alert) and c.stops


def test_waits_then_cancels_and_sends_rest_as_ioc_after_30_minutes() -> None:
    c = Fake()
    st: dict[str, Any] = {"live": True}
    core.rebalance_long(c, st, NOW, alert=[], maker=True)  # type: ignore[arg-type]
    ident = st["maker"]["BTC"]["id"]
    c.orders[ident] = _res(ident, "BUY", OrderStatus.PARTIALLY_FILLED, "0.005", "0.002", "79990")
    core.maker_check(c, st, NOW + 10 * 60_000, halted=None, alert=[])  # type: ignore[arg-type]
    assert "BTC" in st["maker"] and c.ioc == []  # 아직 30 분 안 — 기다림
    alert: list[str] = []
    core.maker_check(c, st, NOW + 30 * 60_000, halted=None, alert=alert)  # type: ignore[arg-type]
    assert c.cancelled == [ident]
    assert c.ioc == [("BTCUSDC", "BUY", Decimal("0.003"))]  # 남은 양만
    assert "maker" not in st and any("안 걸린 양" in x for x in alert)


def test_halt_cancels_pending_and_sends_nothing() -> None:
    c = Fake()
    st: dict[str, Any] = {"live": True}
    core.rebalance_long(c, st, NOW, alert=[], maker=True)  # type: ignore[arg-type]
    alert: list[str] = []
    core.maker_check(c, st, NOW + 60_000, halted="전부 정리", alert=alert)  # type: ignore[arg-type]
    assert c.cancelled and c.ioc == [] and "maker" not in st
    assert any("취소" in x for x in alert)


def test_drawdown_halt_cancels_pending_buy() -> None:
    c = Fake()
    st: dict[str, Any] = {"live": True}
    core.rebalance_long(c, st, NOW, alert=[], maker=True)  # type: ignore[arg-type]
    st["no_new"] = "계좌 낙폭 31% (30% 정지)"
    core.maker_check(c, st, NOW + 60_000, halted=None, alert=[])  # type: ignore[arg-type]
    assert c.cancelled and c.ioc == []


def test_post_only_would_take_so_falls_back_to_ioc() -> None:
    c = Fake(gtx=OrderStatus.EXPIRED)
    st: dict[str, Any] = {"live": True}
    lines = core.rebalance_long(c, st, NOW, alert=[], maker=True)  # type: ignore[arg-type]
    assert c.ioc == [("BTCUSDC", "BUY", Decimal("0.005"))] and "maker" not in st
    assert any("메이커 대신 IOC" in x for x in lines)


def test_rejected_post_only_falls_back_to_ioc(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Fake()

    def boom(*a: Any, **k: Any) -> None:
        raise OrderRejectedError("-5022 post only")

    monkeypatch.setattr(c, "place_limit_order", boom)
    st: dict[str, Any] = {"live": True}
    core.rebalance_long(c, st, NOW, alert=[], maker=True)  # type: ignore[arg-type]
    assert c.ioc and "maker" not in st


def test_demo_keeps_ioc() -> None:
    c = Fake()
    st: dict[str, Any] = {"live": True}
    core.rebalance_long(c, st, NOW, alert=[])  # type: ignore[arg-type]
    assert c.post_only == [] and c.ioc


def test_liquidation_forgets_pending_maker(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from scripts import liquidate

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"maker": {"BTC": {"id": "x"}}, "stops": {}}), encoding="utf-8")
    monkeypatch.setattr(liquidate, "ALTAIR_STATE", path)
    liquidate._altair_forget("BTC")
    assert "maker" not in json.loads(path.read_text(encoding="utf-8"))
