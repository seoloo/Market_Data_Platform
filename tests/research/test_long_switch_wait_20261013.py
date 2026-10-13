from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research import long_switch_wait_20261013 as p

D = Decimal


def bar(price: str = "100", volume: str = "10000") -> p.Bar:
    x = D(price)
    return p.Bar(x, x, x, x, D(volume))


def fixture(count: int = 100, off: int = 90) -> tuple[p.Event, list[p.Frame]]:
    frames = [
        p.Frame(i * p.MINUTE, bar(), bar(), bar(), bar(), D("0.1") if i < off else D(0), -p.DAY)
        for i in range(count)
    ]
    event = p.Event("BTCUSDT", 0, count * p.MINUTE, D(1), D(100), D(200), D(1), D(100))
    return event, frames


@pytest.mark.parametrize("wait", [0, 60])
def test_golden_cash_ledger_and_full_timing(wait: int) -> None:
    event, frames = fixture()
    result = p.simulate(event, frames, wait_min=wait, scenario=p.SCENARIOS[0])
    assert result["net"] == D("-0.3950225")
    assert result["short_net"] == D("-0.0950225")
    assert result["long_net"] == D("-0.30000")
    assert result["residual"] == 0
    assert result["ack_ms"] == p.MINUTE
    assert result["first_buy_ms"] == (1 + wait) * p.MINUTE
    assert result["full_buy_ms"] == result["first_buy_ms"]


def test_zero_does_not_mean_simultaneous_flip() -> None:
    event, frames = fixture()
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["fills"][0]["leg"] == "CLOSE_SHORT"
    assert result["fills"][1]["at_ms"] > result["fills"][0]["at_ms"]


def test_partial_close_starts_wait_after_last_fill() -> None:
    event, frames = fixture(count=150, off=140)
    frames = [replace(f, previous_perp=bar(volume="250")) for f in frames]
    result = p.simulate(event, frames, wait_min=60, scenario=p.SCENARIOS[0])
    assert result["counts"]["short_partial"] == 3
    assert result["flat_ms"] == 3 * p.MINUTE
    assert result["ack_ms"] == 4 * p.MINUTE
    assert result["first_buy_ms"] == 64 * p.MINUTE


def test_unclosed_short_blocks_all_long_buys() -> None:
    event, frames = fixture(count=400, off=390)
    frames = [replace(f, previous_perp=bar(volume="0")) for f in frames]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["first_buy_ms"] is None
    assert result["short_residual"] == 1
    assert result["counts"]["short_zero_liquidity"] == 240


def test_touch_is_not_a_fill_and_cap_does_not_chase() -> None:
    event, frames = fixture()
    frames = [replace(f, spot=p.Bar(D(101), D(101), D(99), D(100), D(10000))) for f in frames]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["bought_qty"] == 0
    assert result["counts"]["buy_limit_miss"] > 0


def test_short_limit_miss_is_not_optimistic_flat() -> None:
    event, frames = fixture()
    frames = [replace(f, perp=bar("101")) for f in frames]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["ack_ms"] is None
    assert result["short_residual"] == 1


def test_goal_disappears_during_wait() -> None:
    event, frames = fixture(count=100, off=20)
    result = p.simulate(event, frames, wait_min=60, scenario=p.SCENARIOS[0])
    assert result["bought_qty"] == 0
    assert result["counts"]["buy_canceled_on_goal_zero"] == 1


def test_partial_long_and_residual_not_called_complete() -> None:
    event, frames = fixture(count=5, off=4)
    frames = [replace(f, previous_spot=bar(volume="100")) for f in frames]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["bought_qty"] == D("0.3")
    assert result["full_buy_ms"] is None
    assert result["spot_residual"] == D("0.2")
    assert abs(result["residual"]) < D("1e-18")


def test_funding_carried_short_before_exact_boundary_close() -> None:
    event, frames = fixture()
    frames[0] = replace(frames[0], funding=(p.Funding(0, D("0.001"), D(100), True),))
    frames[1] = replace(frames[1], funding=(p.Funding(p.MINUTE, D("-0.001"), D(100), True),))
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["funding"] == D("0.100")


def test_negative_funding_and_proxy_count() -> None:
    event, frames = fixture()
    frames[0] = replace(frames[0], funding=(p.Funding(0, D("-0.001"), D(100), False),))
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["funding"] == D("-0.100")
    assert result["funding_mark_proxy_count"] == 1


def test_subminute_funding_is_not_given_to_already_closed_short() -> None:
    event, frames = fixture()
    frames[0] = replace(frames[0], funding=(p.Funding(11, D("0.001"), D(100), True),))
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["funding"] == 0


def test_subminute_funding_remains_on_partial_residual() -> None:
    event, frames = fixture()
    frames[0] = replace(
        frames[0],
        previous_perp=bar(volume="250"),
        funding=(p.Funding(11, D("0.001"), D(100), True),),
    )
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["funding"] == D("0.075")


def test_cost_double_keeps_cap_and_ledgers_balanced() -> None:
    event, frames = fixture()
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[1])
    assert result["net"] == D("-0.790090")
    assert result["residual"] == 0


@pytest.mark.leakage
def test_positive_current_volume_does_not_size_open_time_fill() -> None:
    event, frames = fixture()
    a = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    changed = [
        replace(f, spot=replace(f.spot, volume=D(1)), perp=replace(f.perp, volume=D(1)))
        for f in frames
    ]
    b = p.simulate(event, changed, wait_min=0, scenario=p.SCENARIOS[0])
    assert a == b


def test_realized_zero_perp_volume_does_not_fill_ordinary_close() -> None:
    event, frames = fixture()
    frames = [replace(f, perp=replace(f.perp, volume=D(0))) for f in frames]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["short_residual"] == 1
    assert result["first_buy_ms"] is None


def test_realized_zero_spot_volume_does_not_fill_buy_or_sell() -> None:
    event, frames = fixture()
    frames = [replace(f, spot=replace(f.spot, volume=D(0))) for f in frames]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["bought_qty"] == 0
    frames = [
        replace(f, spot=replace(f.spot, volume=D(0))) if i >= 90 else f
        for i, f in enumerate(fixture()[1])
    ]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["spot_residual"] == 1


def test_stop_is_conservative_and_ack_after_bar() -> None:
    event, frames = fixture()
    event = replace(event, stop_price=D(103))
    frames[0] = replace(frames[0], perp=p.Bar(D(100), D(104), D(99), D(100), D(10000)))
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["fills"][0]["leg"] == "STOP"
    assert result["fills"][0]["price"] == D("103.25235")
    assert result["first_buy_ms"] == 2 * p.MINUTE


@pytest.mark.parametrize("scenario,ack", [(0, 1), (3, 5), (4, 241)])
def test_previous_stop_flat_observation_sensitivities(scenario: int, ack: int) -> None:
    event, frames = fixture(count=400, off=390)
    event = replace(event, short_qty=D(0), already_flat=True)
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[scenario])
    assert result["first_buy_ms"] == ack * p.MINUTE
    assert result["short_net"] == 0


def test_selling_cap_and_dust_preserve_unrealized() -> None:
    event, frames = fixture(count=100, off=90)
    frames = [replace(f, spot=bar("98")) if i >= 90 else f for i, f in enumerate(frames)]
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["spot_residual"] > 0
    assert result["counts"]["sell_limit_miss"] == 10
    assert result["residual"] == 0


def test_unknown_extra_wait_and_gap_rejected() -> None:
    event, frames = fixture()
    with pytest.raises(DataIntegrityError):
        p.simulate(event, frames, wait_min=30, scenario=p.SCENARIOS[0])
    with pytest.raises(DataIntegrityError):
        p.simulate(event, [*frames[:4], *frames[5:]], wait_min=0, scenario=p.SCENARIOS[0])


@pytest.mark.leakage
def test_future_target_rejected() -> None:
    event, frames = fixture()
    frames[0] = replace(frames[0], goal_as_of_ms=p.MINUTE)
    with pytest.raises(CausalityError):
        p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])


@pytest.mark.leakage
def test_future_market_changes_cannot_change_earlier_fills() -> None:
    event, frames = fixture()
    first = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    changed = [replace(f, spot=bar("99")) if i >= 80 else f for i, f in enumerate(frames)]
    second = p.simulate(event, changed, wait_min=0, scenario=p.SCENARIOS[0])
    assert [f for f in first["fills"] if f["at_ms"] < 80 * p.MINUTE] == [
        f for f in second["fills"] if f["at_ms"] < 80 * p.MINUTE
    ]


@given(volume=st.integers(min_value=0, max_value=4000), wait=st.sampled_from([0, 60]))
def test_property_partial_close_never_opens_long_early(volume: int, wait: int) -> None:
    event, frames = fixture(count=100, off=90)
    frames = [replace(f, previous_perp=bar(volume=str(volume))) for f in frames]
    result = p.simulate(event, frames, wait_min=wait, scenario=p.SCENARIOS[0])
    if result["first_buy_ms"] is not None:
        assert result["first_buy_ms"] >= result["ack_ms"] + wait * p.MINUTE
        assert result["short_residual"] == 0
    assert abs(result["residual"]) < D("1e-18")


def test_determinism_and_step_floor() -> None:
    event, frames = fixture()
    a = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    for _ in range(20):
        assert p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0]) == a
    assert all(f["qty"] % p.SPOT_STEP == 0 for f in a["fills"])


def test_checkpoints_include_all_costs_not_extra_forced_sales() -> None:
    event, frames = fixture(count=3000, off=2990)
    result = p.simulate(event, frames, wait_min=0, scenario=p.SCENARIOS[0])
    assert result["checkpoints"]["1"] == result["checkpoints"]["2"]
    assert result["checkpoints"]["1"] == D("-0.2450725")
