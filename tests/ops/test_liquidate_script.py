"""'전부 정리' 실행기 흐름.

그룹 정지 → 토스 매수 취소 → 업비트 즉시 · 토스 장 열린 뒤 → Deneb 장부.

거래소 · 텔레그램은 가짜로 바꿔 끼운다. 실제 주문 없음.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import pytest

from auto_trading.exchange.toss import TossOpenOrder, TossPosition
from auto_trading.ops.kill_switch import group_path
from auto_trading.ops.liquidate import Store
from scripts import liquidate as liq

D = Decimal


@dataclass
class _UFill:
    executed_volume: Decimal
    funds: Decimal
    paid_fee: Decimal
    state: str = "done"


class FakeUpbit:
    sent: ClassVar[list[tuple[str, Decimal, Decimal]]] = []

    def balances(self) -> dict[str, Decimal]:
        return {"BTC": D("0.01"), "ETH": D(0), "KRW": D(1000)}

    def tickers(self, markets: list[str]) -> dict[str, Decimal]:
        return {m: D(100_000_000) for m in markets}

    def orderbook(self, market: str) -> tuple[list[Decimal], list[Decimal]]:
        return [], [D(100_000_000) - D(1000) * k for k in range(8)]

    def place_limit_ioc(
        self, market: str, side: str, volume: Decimal, price: Decimal, identifier: str
    ) -> _UFill:
        FakeUpbit.sent.append((market, volume, price))
        return _UFill(volume, volume * price, D(1))

    def get_order(self, identifier: str) -> _UFill | None:
        return None

    def close(self) -> None:
        pass


@dataclass
class _St:
    status: str
    filled_qty: Decimal
    filled_amount: Decimal | None
    commission: Decimal | None


@dataclass
class _Res:
    accepted: bool
    code: str
    message: str = ""


class FakeToss:
    open_session = False
    orders: ClassVar[dict[str, _St]] = {}
    sent: ClassVar[list[tuple[str, str, int, Decimal, str]]] = []
    canceled: ClassVar[list[str]] = []

    def holdings(self) -> dict[str, TossPosition]:
        return {
            "TQQQ": TossPosition("TQQQ", D(3), D(40), D(50), "USD"),
            "SCHD": TossPosition("SCHD", D(3), D(25), D(27), "USD"),
            "BTQ": TossPosition("BTQ", D(1), D(3), D(3), "USD"),
        }

    def sellable(self, symbol: str) -> Decimal:
        return self.holdings()[symbol].qty

    def us_regular_session(self, us_date: str) -> tuple[str, str] | None:
        return None

    def kr_open_today(self, day: str) -> bool:
        return False

    def open_orders(self) -> list[TossOpenOrder]:
        return [
            TossOpenOrder("b1", "BTQ", "BUY", "PENDING", "CLS", D(1), D(0)),
            TossOpenOrder("b2", "SCHD", "BUY", "PENDING", "CLS", D(1), D(0)),
        ]

    def cancel(self, order_id: str) -> _Res:
        FakeToss.canceled.append(order_id)
        return _Res(True, "c" + order_id)

    def bids(self, symbol: str) -> list[tuple[Decimal, Decimal]]:
        return [(D("27.00"), D(100))]

    def order(
        self,
        symbol: str,
        side: str,
        qty: int,
        limit: Decimal,
        *,
        client_order_id: str,
        time_in_force: str = "CLS",
    ) -> _Res:
        oid = f"s{len(FakeToss.sent)}"
        FakeToss.sent.append((symbol, side, qty, limit, time_in_force))
        FakeToss.orders[oid] = _St("PENDING", D(0), None, None)
        return _Res(True, oid)

    def order_status(self, order_id: str) -> _St:
        return FakeToss.orders[order_id]

    def close(self) -> None:
        pass


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    FakeUpbit.sent, FakeToss.sent, FakeToss.canceled, FakeToss.orders = [], [], [], {}
    deneb = tmp_path / "DENEB.json"
    deneb.write_text(
        json.dumps({"start": "2026-10-01", "cash_usd": 10.0, "qty": {"TQQQ": 1.0, "SCHD": 3.0}}),
        encoding="utf-8",
    )
    sent: list[str] = []
    monkeypatch.setattr(liq, "STORE", Store(tmp_path / "liq"))
    monkeypatch.setattr(liq, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(liq, "DENEB_FILE", deneb)
    monkeypatch.setattr(liq, "TOSS_DATA", tmp_path)  # 진짜 장부를 읽지 않게
    (tmp_path / "BTQ.json").write_text(
        json.dumps({"symbol": "BTQ", "baseline_qty": 0.0, "last_qty": 1.0}), encoding="utf-8"
    )
    monkeypatch.setattr(liq, "_upbit", FakeUpbit)
    # 바이낸스 현물(Vega) · 선물(Altair) · 개별주 장부도 반드시 가짜로 — 2026-10-16 이 시험이
    # 진짜 바이낸스 클라이언트로 실계좌 Vega 코인을 팔았다(tests/conftest.py 참고).
    monkeypatch.setattr(liq, "_binance", lambda: None)
    monkeypatch.setattr(liq, "_futures", lambda: None)
    monkeypatch.setattr(liq, "SIZE_STATE", tmp_path / "size_state.json")
    monkeypatch.setattr(liq, "_reader", FakeToss)
    monkeypatch.setattr(liq, "_trader", lambda held, syms: FakeToss())
    monkeypatch.setattr(liq, "_us_open", lambda r, now: FakeToss.open_session)
    monkeypatch.setattr(liq, "_notify", sent.append)
    monkeypatch.setattr("scripts.liquidate.time.sleep", lambda _s: None)
    return {"tmp": tmp_path, "m3": deneb, "sent": sent}


def test_deneb_only_halts_cancels_buys_and_waits_for_session(env: dict[str, Any]) -> None:
    text = liq.start("deneb", "텔레그램")
    tmp = env["tmp"]
    assert group_path(tmp / "HALT", "deneb").exists()
    assert not group_path(tmp / "HALT", "vega").exists()
    assert FakeToss.canceled == ["b2"]  # Deneb 종목 매수만 (BTQ 적립 매수는 그대로)
    assert not FakeUpbit.sent and not FakeToss.sent  # 장 닫힘 → 대기
    assert "⏳ 장 열리면 토스 미국 TQQQ 0/1" in text and "SCHD 0/3" in text
    assert env["sent"] and "Deneb 만" in env["sent"][0]
    FakeToss.open_session = True
    try:
        liq.resume()
        assert [(s[0], s[2], s[4]) for s in FakeToss.sent] == [
            ("SCHD", 3, "DAY"),
            ("TQQQ", 1, "DAY"),
        ]
        FakeToss.orders["s0"] = _St("FILLED", D(3), D(81), D("0.081"))
        FakeToss.orders["s1"] = _St("FILLED", D(1), D(27), D("0.027"))
        liq.resume()
    finally:
        FakeToss.open_session = False
    deneb = json.loads(env["m3"].read_text(encoding="utf-8"))
    assert deneb["qty"] == {"TQQQ": 0.0, "SCHD": 0.0}
    assert deneb["cash_usd"] == pytest.approx(10 + 81 - 0.081 + 27 - 0.027)
    # 매도도 장부 주문 목록에 남는다 (오르빗 · 양도세가 볼 수 있게, 다시 돌려도 한 번만)
    sells = [o for o in deneb["orders"] if o["side"] == "SELL"]
    assert sorted((o["symbol"], o["filled_qty"], o["kind"], o["booked"]) for o in sells) == [
        ("SCHD", 3.0, "liquidate", True),
        ("TQQQ", 1.0, "liquidate", True),
    ]
    assert {o["order_id"] for o in sells} == {"s0", "s1"}
    job = liq.STORE.jobs()[-1]
    assert job.done and job.reported and "✅ 정리 끝" in env["sent"][-1]
    liq.resume()  # 끝난 작업은 다시 보내지 않는다
    assert sum("정리 끝" in s for s in env["sent"]) == 1


def test_vega_sells_upbit_now_and_unhalt(env: dict[str, Any]) -> None:
    text = liq.start("vega", "오르빗")
    assert FakeUpbit.sent == [("KRW-BTC", D("0.01"), D(99_998_000))]
    assert "✅ 끝 업비트 BTC 0.01/0.01" in text and not FakeToss.canceled
    assert liq.STORE.jobs()[-1].done
    assert "정지 풀림" in liq.unhalt("vega", "텔레그램")
    assert not group_path(env["tmp"] / "HALT", "vega").exists()
    assert "정지 중이 아니에요" in liq.unhalt("vega", "텔레그램")


def test_acc_scope_sells_only_ledger_share(env: dict[str, Any]) -> None:
    text = liq.start("acc", "텔레그램")
    assert FakeToss.canceled == ["b1"]  # 적립 매수만 취소 (Deneb SCHD 매수는 그대로)
    assert "토스 미국 BTQ 0/1" in text and "TQQQ" not in text.split("수동 보유")[0]
    manual = text.split("수동 보유")[1]
    assert "TQQQ 2" in manual and "SCHD" not in manual  # Deneb 몫 3 주는 수동이 아님


def test_second_start_while_running_is_refused(env: dict[str, Any]) -> None:
    with liq.STORE.running() as ok:
        assert ok
        assert "다른 정리가 실행 중" in liq.start("all", "텔레그램")
    assert not FakeUpbit.sent


def test_two_starts_in_the_same_second_keep_both_jobs(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 초에 두 범위를 시작해도 작업 기록이 둘 다 남는다 (2026-10-21 — 예전엔 덮어씀)."""
    monkeypatch.setattr(liq, "_now_ms", lambda: 1_792_000_000_000)
    liq.start("vega", "텔레그램")
    liq.start("acc", "텔레그램")
    jobs = liq.STORE.jobs()
    assert [job.scope for job in jobs] == ["vega", "acc"]
    assert jobs[1].job_id == jobs[0].job_id + "-2"
