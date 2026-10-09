from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.types import Candle
from auto_trading.ops.t2_upbit import nav_step, plan, targets

DAY = 86_400_000


def _series(n: int, growth: float, start: float = 100.0) -> list[Candle]:
    out = []
    px = start
    for k in range(n):
        o = k * DAY
        nxt = px * (1 + growth) * (1 + (0.01 if k % 2 else -0.01))
        out.append(
            Candle(
                o,
                o + DAY - 1,
                Decimal(str(px)),
                Decimal(str(max(px, nxt) * 1.001)),
                Decimal(str(min(px, nxt) * 0.999)),
                Decimal(str(nxt)),
                Decimal(1),
            )
        )
        px = nxt
    return out


def test_targets_scale_down_when_sum_over_one() -> None:
    up = _series(200, 0.01)
    w = targets({"BTC": up, "ETH": up, "SOL": up}, up[-1].close_ms)
    assert all(v is not None for v in w.values())
    assert sum(v for v in w.values() if v is not None) == pytest.approx(Decimal(1))


def test_plan_drops_buys_when_stopped() -> None:
    w = {"BTC": Decimal("0.5"), "ETH": Decimal(0), "SOL": None}
    hold = {"BTC": Decimal(0), "ETH": Decimal(1), "SOL": Decimal(0)}
    prices = {"BTC": Decimal(100_000), "ETH": Decimal(50_000), "SOL": Decimal(1000)}
    intents = plan(w, hold, prices, Decimal(300_000), allow_buy=False)
    assert [(i.symbol, i.side.value) for i in intents] == [("ETH", "SELL")]
    intents = plan(w, hold, prices, Decimal(300_000), allow_buy=True)
    assert ("BTC", "BUY") in [(i.symbol, i.side.value) for i in intents]


def test_nav_step_deposit_does_not_move_nav() -> None:
    prices = {"BTC": Decimal(100), "ETH": Decimal(1), "SOL": Decimal(1)}
    zero = {"BTC": Decimal(0), "ETH": Decimal(0), "SOL": Decimal(0)}
    s0 = nav_step(
        units=Decimal(0),
        peak=Decimal(0),
        expected_krw=Decimal(300_000),
        expected=zero,
        krw=Decimal(300_000),
        holdings=zero,
        prices=prices,
    )
    assert s0.nav == 1000 and s0.units == 300
    # 입금 30 만 원 → 단위만 늘고 단위 가치는 그대로
    s1 = nav_step(
        units=s0.units,
        peak=s0.peak,
        expected_krw=Decimal(300_000),
        expected=zero,
        krw=Decimal(600_000),
        holdings=zero,
        prices=prices,
    )
    assert s1.nav == pytest.approx(Decimal(1000)) and s1.flow == 300_000
    # 보유 코인이 반으로 → 낙폭
    hold = {"BTC": Decimal(3000), "ETH": Decimal(0), "SOL": Decimal(0)}
    half = {"BTC": Decimal(50), "ETH": Decimal(1), "SOL": Decimal(1)}
    s2 = nav_step(
        units=s1.units,
        peak=s1.peak,
        expected_krw=Decimal(300_000),
        expected=hold,
        krw=Decimal(300_000),
        holdings=hold,
        prices=half,
    )
    assert s2.flow == 0 and s2.dd == pytest.approx(Decimal("0.25"))


def test_books_average_cost_and_realized() -> None:
    from auto_trading.ops.t2_upbit import books

    orders = [
        {
            "identifier": "t2u-2026-10-08-BTC-B",
            "side": "bid",
            "executed_volume": "2",
            "funds": "200",
            "paid_fee": "2",
        },
        {
            "identifier": "t2u-2026-10-09-BTC-S",
            "side": "ask",
            "executed_volume": "1",
            "funds": "150",
            "paid_fee": "1",
        },
        {
            "identifier": "t2u-2026-10-09-ETH-B",
            "side": "bid",
            "executed_volume": "0",
            "funds": "0",
            "paid_fee": "0",
        },
    ]
    b = books(orders)["BTC"]
    assert b.qty == 1 and b.cost == 101 and b.realized == 150 - 1 - 101
    assert books(orders)["ETH"].cost == 0


def test_reason_icons() -> None:
    from auto_trading.ops.t2_upbit import CoinSignal, reason

    def sg(ups: int, vol: str = "0.5", raw: str = "0.3", w: str = "0.3") -> CoinSignal:
        parts = tuple(Decimal("0.05") if k < ups else Decimal("-0.05") for k in range(3))
        return CoinSignal(ups, parts, Decimal(vol), Decimal(raw), Decimal(w), Decimal(0))  # type: ignore[arg-type]

    prev = {"ups": "3", "vol": "0.5", "raw": "0.3", "weight": "0.3"}
    assert reason("ETH", sg(1, w="0", raw="0"), prev, Decimal("0.3"), flow=Decimal(0))[0] == "🔴"
    assert reason("ETH", sg(3), {**prev, "ups": "1"}, Decimal(0), flow=Decimal(0))[0] == "🟢"
    assert (
        reason("ETH", sg(2, raw="0.2", w="0.2"), prev, Decimal("0.3"), flow=Decimal(0))[0] == "🟡"
    )
    assert (
        reason(
            "ETH", sg(3, vol="0.8", raw="0.18", w="0.18"), prev, Decimal("0.3"), flow=Decimal(0)
        )[0]
        == "🟡"
    )
    assert reason("ETH", sg(3), prev, Decimal("0.2"), flow=Decimal(100))[0] == "💰"
    assert reason("ETH", sg(3), prev, Decimal("0.2"), flow=Decimal(0))[0] == "🔵"
    assert reason("ETH", sg(3), None, Decimal(0), flow=Decimal(0))[0] == "🆕"
