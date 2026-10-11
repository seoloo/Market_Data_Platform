from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.pyramiding_209 import (
    DAY_MS,
    END_MS,
    MINUTE_MS,
    START_MS,
    SYMBOLS,
    DailySignal,
    MinuteBar,
    Pending,
    PilotConfig,
    PilotEngine,
    limit_fill,
)

D = Decimal


def engine(adds: bool = True, cfg: PilotConfig | None = None) -> PilotEngine:
    return PilotEngine(cfg or PilotConfig(), adds, {"BTCUSDT": D(1000), "ETHUSDT": D(100)})


def bars(
    t: int = START_MS, btc_low: str = "999", btc_close: str = "1000", volume: str = "100"
) -> dict[str, MinuteBar]:
    return {
        "BTCUSDT": MinuteBar(
            t, D(1000), max(D(1001), D(btc_close)), D(btc_low), D(btc_close), D(volume)
        ),
        "ETHUSDT": MinuteBar(t, D(100), D("100.1"), D("99.9"), D(100), D(volume)),
    }


def signals(
    btc_transition: bool = True,
    eth_transition: bool = False,
    allowed: bool = True,
    btc_close: str = "1000",
) -> dict[str, DailySignal]:
    return {
        "BTCUSDT": DailySignal(allowed, btc_transition, D(btc_close), D(20)),
        "ETHUSDT": DailySignal(allowed, eth_transition, D(100), D(2)),
    }


def seed(
    e: PilotEngine,
    s: str = "BTCUSDT",
    qty: str = "1",
    price: str = "1000",
    stage: int = 0,
    t: int = START_MS,
) -> None:
    e._fill(s, D(qty), D(price), stage, t, D(20), buy=True)
    pos = e.positions[s]
    pos.q0 = D(1)
    pos.p0 = D(1000)
    pos.initial_final = True


def test_touch_and_gap_are_not_fills():
    bar = bars(btc_low="999.9")["BTCUSDT"]
    assert limit_fill("BTCUSDT", bar, D(1000), D(1), True, PilotConfig())[0] == 0
    assert (
        limit_fill(
            "BTCUSDT", replace(bar, open=D(1004), high=D(1005)), D(1000), D(1), True, PilotConfig()
        )[0]
        == 0
    )


def test_volume_participation_and_qty_step():
    bar = bars(volume="1.25")["BTCUSDT"]
    assert limit_fill("BTCUSDT", bar, D(1000), D(1), True, PilotConfig())[0] == D("0.012")


def test_initial_partial_vwap_q0_freezes_and_no_retry():
    e = engine()
    e.begin_day(START_MS, signals())
    for i in range(3):
        e.step(bars(START_MS + i * MINUTE_MS, volume="1"), {})
    assert e.positions["BTCUSDT"].q0 == D("0.030")
    assert not e.pending
    assert e.events[0]["attempts"] == 3
    assert "REDUCED_OR_PARTIAL" in str(e.events[0]["reasons"])


def test_no_touch_entry_consumes_transition():
    e = engine()
    e.begin_day(START_MS, signals())
    for i in range(3):
        e.step(bars(START_MS + i * MINUTE_MS, btc_low="999.9"), {})
    assert not e.positions
    e.begin_day(START_MS + DAY_MS, signals(btc_transition=False))
    assert not e.pending


def test_b_has_no_add_orders_and_c_has_one_stage_per_decision():
    b, c = engine(False), engine(True)
    for e in (b, c):
        seed(e)
        e.prices["BTCUSDT"] = D(1080)
        e.begin_day(START_MS + DAY_MS, signals(btc_transition=False, btc_close="1080"))
    assert not b.pending
    assert c.pending["BTCUSDT"].stage == 1
    assert c.pending["BTCUSDT"].requested == D("0.5")
    assert c.positions["BTCUSDT"].next_stage == 2


def test_unfilled_first_add_skips_second():
    e = engine()
    seed(e)
    e.prices["BTCUSDT"] = D(1080)
    e.begin_day(START_MS + DAY_MS, signals(btc_transition=False, btc_close="1080"))
    e._finish_order("BTCUSDT")
    e.begin_day(START_MS + 2 * DAY_MS, signals(btc_transition=False, btc_close="1080"))
    assert not e.pending
    assert e.events[-1]["reasons"] == ["FIRST_ADD_UNFILLED"]


def test_funding_only_existing_quantity_then_add_cost():
    e = engine()
    seed(e)
    e.pay_funding("BTCUSDT", START_MS + 1, D("0.001"))
    seed(e, qty="0.5", price="1040", stage=1, t=START_MS + MINUTE_MS)
    assert e.funding == D(1)
    assert e.positions["BTCUSDT"].lots[1].funding == 0
    e.prices["BTCUSDT"] = D(1040)
    e.pay_funding("BTCUSDT", START_MS + 28_800_001, D("-0.001"))
    assert e.funding == D("-0.56")
    assert e.positions["BTCUSDT"].lots[1].funding == D("-0.52")


def test_hand_accounting_b_c_and_pro_rata_close():
    b, c = engine(False), engine(True)
    for e in (b, c):
        seed(e)
        e.pay_funding("BTCUSDT", START_MS + 1, D("0.001"))
    seed(c, qty="0.5", price="1040", stage=1)
    seed(c, qty="0.25", price="1080", stage=2)
    b._close("BTCUSDT", D(1), D(1100), START_MS + MINUTE_MS, "TEST")
    c._close("BTCUSDT", D("1.75"), D(1100), START_MS + MINUTE_MS, "TEST")
    # B gross100 - entry/exit1.995 - funding1; C gross135 - costs3.52925 - funding1.
    assert b.equity() == D("10097.005")
    assert c.equity() == D("10130.47075")
    for e in (b, c):
        e.end_day(START_MS)
        assert e.daily[-1].equity - e.cfg.capital == sum(e.daily[-1].symbol_value.values())
        assert not e.positions


def test_same_minute_stop_and_emergency_separate_cash_drag():
    e = engine()
    e.begin_day(START_MS, signals())
    e.step(bars(btc_low="950"), {})
    assert not e.positions
    assert e.finished[0].reason == "STOP"
    qty = e.finished[0].q0
    assert e.emergency == qty * D(960) * D("0.01")
    assert e.slippage == qty * D(1960) * D("0.0005")
    assert e.fees == qty * D(1960) * D("0.00045")


@pytest.mark.leakage
def test_new_trail_only_next_decision_not_previous_minute():
    e = engine()
    seed(e)
    e.step(bars(btc_low="970", btc_close="1100"), {})
    assert "BTCUSDT" in e.positions  # old stop960, NOT new hypothetical1040
    e.begin_day(START_MS + DAY_MS, signals(btc_transition=False, btc_close="1100"))
    assert e.positions["BTCUSDT"].stop == D(1040)


def test_daily_loss_dd_and_loss_cooldown_are_not_disabled():
    e = engine()
    e.day_start = D(11000)
    order = Pending(0, D(1), D(1), D(20), START_MS)
    assert not e._allocate({"BTCUSDT": order}, START_MS)
    assert "DAILY_LOSS" in order.reasons
    e.day_start = e.cfg.capital
    e.high_water = D(20000)
    seed(e)
    e.step(bars(), {})
    assert e.halted
    assert not e._allocate({"BTCUSDT": order}, START_MS)
    e.cooldown_until_ms = START_MS + MINUTE_MS
    e.losses = 4
    e._expire_cooldown(START_MS + MINUTE_MS)
    assert e.losses == 0


def test_proportional_two_symbol_risk_and_minimums():
    e = engine()
    orders = {
        s: Pending(0, D(100), D(100), D(20 if s == "BTCUSDT" else 2), START_MS) for s in SYMBOLS
    }
    q = e._allocate(orders, START_MS)
    risk = sum(q[s] * (2 * orders[s].atr + e._reserve(s)) for s in q)
    assert risk <= e.equity() * D("0.01")
    assert q["BTCUSDT"] * D(1000) <= D(3000)
    assert q["ETHUSDT"] * D(100) <= D(3000)


@pytest.mark.leakage
def test_future_prefix_invariant():
    def run(n: int) -> list[tuple[Decimal, list[dict[str, object]]]]:
        e = engine()
        e.begin_day(START_MS, signals())
        prefixes = []
        for i in range(n):
            e.step(bars(START_MS + i * MINUTE_MS), {})
            prefixes.append((e.equity(), list(e.fill_ledger)))
        return prefixes

    assert run(3) == run(6)[:3]


@pytest.mark.leakage
def test_protected_boundary_and_gap_rejected():
    e = engine()
    with pytest.raises(DataIntegrityError, match="boundary"):
        e.step(bars(END_MS), {})
    e.step(bars(), {})
    with pytest.raises(DataIntegrityError, match="gap"):
        e.step(bars(START_MS + 2 * MINUTE_MS), {})


def test_operational_layers_do_not_import_pilot():
    root = Path(__file__).resolve().parents[2] / "src/auto_trading"
    for directory in ("app", "execution", "exchange", "strategy", "risk", "ui", "ops"):
        for file in (root / directory).rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                assert all("pyramiding_209" not in name for name in names), file


def test_partial_normal_close_conserves_every_lot():
    e = engine()
    seed(e)
    seed(e, qty="0.5", price="1040", stage=1)
    e._close("BTCUSDT", D("0.3"), D(1050), START_MS + MINUTE_MS, "PARTIAL")
    assert e.positions["BTCUSDT"].qty == D("1.2")
    e.prices["BTCUSDT"] = D(1060)
    e.end_day(START_MS)
    assert abs(e.equity() - e.cfg.capital - e.positions["BTCUSDT"].net(D(1060))) < D("1e-15")
    e._close("BTCUSDT", D("1.2"), D(1070), START_MS + 2 * MINUTE_MS, "TEST")
    assert not e.positions
    assert sum(lot.exited_qty for lot in e.finished[0].lots.values()) == D("1.5")


def test_funding_same_minute_precedes_new_position():
    e = engine()
    e.begin_day(START_MS, signals())
    e.step(bars(), {"BTCUSDT": [(START_MS + 17, D("0.001"))]})
    assert e.funding == 0
    assert e.positions["BTCUSDT"].qty > 0


def test_off_and_already_crossed_trail_take_priority_over_add():
    for allowed in (False, True):
        e = engine()
        seed(e)
        e.positions["BTCUSDT"].peak_close = D(1200)
        e.prices["BTCUSDT"] = D(1100)
        e.begin_day(
            START_MS + DAY_MS, signals(btc_transition=False, btc_close="1100", allowed=allowed)
        )
        assert not e.pending
        assert e.positions["BTCUSDT"].closing is not None


def test_fixed_stress_costs_do_not_alter_signal_thresholds():
    from auto_trading.research.pyramiding_209 import SCENARIOS

    assert len(SCENARIOS) == 5
    assert SCENARIOS[-1].cost_rate == D("0.0019")
    assert SCENARIOS[-1].initial_risk_pct == SCENARIOS[0].initial_risk_pct


@pytest.mark.leakage
def test_archive_paths_are_bounded_without_reading_protected_folder():
    from scripts.run_pyramiding_209 import month_bounds

    months = list(month_bounds())
    assert len(months) == 78
    assert months[0][0] == "2020-03"
    assert months[-1][0] == "2026-08"
    assert all(START_MS <= begin < end <= END_MS for _, begin, end in months)
