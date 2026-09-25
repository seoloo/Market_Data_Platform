"""Elliott 데모 실행기 (56) — 가짜 거래소로 예약·체결 즉시 보호·청산·고아 포지션 정리 검증."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from scripts.demo_ew_btc import EwDemoRunner, Store

from auto_trading.exchange.futures import (
    FuturesAccount,
    FuturesFilters,
    FuturesOrderResult,
    FuturesOrderType,
    FuturesPosition,
    OrderStatus,
)
from auto_trading.research.elliott.demo_setup import EwSetup

SYM = "BTCUSDC"


def result(cid: str, status: OrderStatus, executed: str = "0", avg: str = "0", stop: str = "0"):
    return FuturesOrderResult(
        cid,
        "1",
        SYM,
        "BUY",
        FuturesOrderType.MARKET,
        status,
        Decimal(0),
        Decimal(executed),
        Decimal(avg),
        False,
        False,
        Decimal(stop),
        Decimal(0),
        "USDC",
    )


@dataclass
class FakeApi:
    mark: Decimal = Decimal("62000")
    balance: Decimal = Decimal("5000")
    position: Decimal = Decimal(0)
    orders: dict[str, FuturesOrderResult] = field(default_factory=dict)
    stops: dict[str, FuturesOrderResult] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def filters(self, symbol):
        return FuturesFilters(Decimal("0.001"), Decimal("0.001"), Decimal(100), Decimal("0.1"))

    def mark_price(self, symbol):
        return self.mark

    def asset_balance(self, asset):
        return self.balance

    def set_margin_type(self, symbol, t):
        self.calls.append(f"margin:{t}")

    def set_leverage(self, symbol, lev):
        self.calls.append(f"lev:{lev}")
        return lev

    def place_limit_order(self, symbol, side, qty, price, cid):
        self.calls.append(f"limit:{side}:{qty}@{price}")
        self.orders[cid] = result(cid, OrderStatus.NEW)
        return self.orders[cid]

    def get_order(self, symbol, cid):
        return self.orders.get(cid)

    def cancel_order(self, symbol, cid):
        self.calls.append(f"cancel:{cid}")
        self.orders[cid] = result(cid, OrderStatus.CANCELED)
        return self.orders[cid]

    def place_stop_market(self, symbol, side, price, cid):
        self.calls.append(f"stop:{side}@{price}")
        self.stops[cid] = result(cid, OrderStatus.NEW, stop=str(price))
        return self.stops[cid]

    def place_take_profit_market(self, symbol, side, price, cid):
        self.calls.append(f"tp:{side}@{price}")
        self.stops[cid] = result(cid, OrderStatus.NEW, stop=str(price))
        return self.stops[cid]

    def get_stop(self, symbol, cid):
        return self.stops.get(cid)

    def cancel_stop(self, symbol, cid):
        self.calls.append(f"cancelstop:{cid}")
        return

    def place_market_order(self, symbol, side, qty, cid, *, reduce_only):
        self.calls.append(f"market:{side}:{qty}:ro={reduce_only}")
        self.position = Decimal(0)
        return result(cid, OrderStatus.FILLED, str(qty), str(self.mark))

    def account(self):
        pos = {SYM: FuturesPosition(SYM, self.position, Decimal(0), 5)} if self.position else {}
        return FuturesAccount(self.balance, self.balance, pos)


SETUP = EwSetup(
    as_of_ms=1_790_000_000_000,
    direction=1,
    a=Decimal("58000"),
    b=Decimal("64000"),
    entry=Decimal("61000"),
    stop=Decimal("58000"),
    target=Decimal("61000") + Decimal("1.618") * 6000,
    agree=7,
    top_n=10,
    b_extreme_ms=1,
    scenario_ids=("s1",),
)


def runner(tmp_path: Path, api: FakeApi):
    notes: list[str] = []
    r = EwDemoRunner(
        api,  # type: ignore[arg-type]  # 테스트용 가짜 API
        Store(tmp_path),
        notes.append,
        clock=lambda: 1_790_000_100_000,
    )
    return r, notes


def test_setup_is_sized_to_one_percent_risk_and_placed_post_only(tmp_path: Path) -> None:
    api = FakeApi()
    r, notes = runner(tmp_path, api)
    r.place_setup(SETUP)
    # 위험 50 USDC / (3000 + 61000 × 0.0007) ≈ 0.01641 → 0.016
    assert "limit:BUY:0.016@61000.0" in api.calls
    assert "lev:5" in api.calls and "margin:CROSSED" in api.calls
    assert r.state["phase"] == "PENDING"
    assert "7개가 상승 충격파 5파 종료" in notes[0] and "위험 1%" in notes[0]


def test_fill_places_stop_and_target_immediately(tmp_path: Path) -> None:
    api = FakeApi()
    r, notes = runner(tmp_path, api)
    r.place_setup(SETUP)
    cid = r.state["ids"]["entry"]
    api.orders[cid] = result(cid, OrderStatus.FILLED, "0.016", "61000")
    api.position = Decimal("0.016")
    r.check()
    assert r.state["phase"] == "OPEN"
    assert "stop:SELL@58000.0" in api.calls
    assert any(c.startswith("tp:SELL@") for c in api.calls)
    assert "진입 체결" in notes[-1]
    r.check()  # 이미 걸린 보호 주문은 다시 걸지 않는다
    assert sum(c.startswith("stop:") for c in api.calls) == 1


def test_flat_after_stop_is_reported_in_r(tmp_path: Path) -> None:
    api = FakeApi()
    r, notes = runner(tmp_path, api)
    r.place_setup(SETUP)
    cid = r.state["ids"]["entry"]
    api.orders[cid] = result(cid, OrderStatus.FILLED, "0.016", "61000")
    api.position = Decimal("0.016")
    r.check()
    stop_id = r.state["ids"]["stop"]
    api.stops[stop_id] = result(stop_id, OrderStatus.FILLED, "0.016", "58000")
    api.position = Decimal(0)
    r.check()
    assert r.state["phase"] == "IDLE"
    assert "손절" in notes[-1] and "-0.96R" in notes[-1]


def test_new_extreme_before_fill_cancels_setup(tmp_path: Path) -> None:
    api = FakeApi()
    r, notes = runner(tmp_path, api)
    r.place_setup(SETUP)
    api.mark = Decimal("64100")
    r.check()
    assert r.state["phase"] == "IDLE" and "예약 취소" in notes[-1]


def test_setup_already_passed_is_skipped(tmp_path: Path) -> None:
    api = FakeApi(mark=Decimal("60500"))
    r, notes = runner(tmp_path, api)
    r.place_setup(SETUP)
    assert r.state["phase"] == "IDLE" and "건너뜀" in notes[-1]
    assert not any(c.startswith("limit:") for c in api.calls)


def test_orphan_position_is_closed_on_start(tmp_path: Path) -> None:
    api = FakeApi(position=Decimal("0.02"))
    r, notes = runner(tmp_path, api)
    r.reconcile()
    assert "market:SELL:0.02:ro=True" in api.calls
    assert "기록에 없는 포지션" in notes[-1]


def test_first_scan_only_records_old_triggers(tmp_path: Path) -> None:
    api = FakeApi()
    r, _ = runner(tmp_path, api)
    r.on_scan([(1_000, SETUP, 10), (2_000, None, 0)])
    assert r.state["last_trigger_ms"] == 2_000
    assert not any(c.startswith("limit:") for c in api.calls)  # 지난 셋업으로 주문하지 않는다
    r.on_scan([(1_000, SETUP, 10), (2_000, None, 0), (3_000, SETUP, 10)])
    assert r.state["phase"] == "PENDING"  # 켠 뒤 새로 확정된 발동만 쓴다


def test_single_instance_lock_blocks_a_second_holder(tmp_path: Path) -> None:
    import subprocess
    import sys

    from scripts import demo_ew_btc

    assert demo_ew_btc._lock(tmp_path) is True
    # 다른 프로세스는 같은 잠금을 잡지 못한다 (그리고 이 프로세스를 죽이지 않는다)
    code = (
        "import sys; sys.path[:0]=['.', 'src']; from pathlib import Path; "
        "from scripts import demo_ew_btc as d; "
        f"print(d._lock(Path(r'{tmp_path}')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
    demo_ew_btc._LOCK_HANDLE.close()
    demo_ew_btc._LOCK_HANDLE = None


def test_new_pivot_without_setup_explains_why_not(tmp_path: Path) -> None:
    api = FakeApi()
    r, notes = runner(tmp_path, api)
    r.on_scan([(1_000, None, 0)])  # 첫 실행: 기록만
    info = {"pivot": "고점", "price": "84000", "top": 10, "up": 3, "down": 1}
    r.on_scan([(1_000, None, 0), (2_000, None, 12, info)])
    assert "4h 고점 확정 @84,000.0 — 진입 안 함" in notes[-1]
    assert "상승 3 · 하락 1 (같은 방향 6개 이상 필요)" in notes[-1]
    assert not any(c.startswith("limit:") for c in api.calls)


def test_agreement_counts_top10_impulse_ends() -> None:
    from auto_trading.research.elliott.demo_setup import agreement

    scen = [
        {"scenario_id": f"s{i}", "current_state": st, "direction": d}
        for i, (st, d) in enumerate(
            [("WAVE_5_END_CANDIDATE", 1)] * 3
            + [("WAVE_5_END_CANDIDATE", -1)]
            + [("WAVE_C_END_CANDIDATE", 1)] * 8
        )
    ]
    ranks = {str(s["scenario_id"]): i for i, s in enumerate(scen)}
    assert agreement({"scenarios": scen}, ranks) == (10, 3, 1)
