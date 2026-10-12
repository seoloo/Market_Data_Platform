"""Synthetic portfolio and long-return counterfactuals; no real performance inputs."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Literal

import pytest

from auto_trading.core.errors import CausalityError, DataIntegrityError
from auto_trading.research.short_long_return_20261013 import H4, Bar, CashEvent, Frame, simulate

D = Decimal
S = "BTCUSDT"


def frames(count: int = 16, *, returns: bool = False) -> list[Frame]:
    out = []
    previous = Bar(D(100), D(100), D(100), D(100), D(100000))
    for i in range(count):
        px = D(100) + D("0.1") * i
        bar = Bar(px, px, px, px, D(100000))
        targets = {S: D("0.5") if returns and i == 2 else D(0)} if i in (0, 2) else None
        out.append(
            Frame(
                i * H4,
                {S: bar},
                {S: bar},
                {S: previous},
                {S: previous},
                targets,
                i * H4 if targets is not None else -1,
                frozenset({S}) if i == 0 else frozenset(),
                i * H4,
                {S: ()},
            )
        )
        previous = bar
    return out


@pytest.mark.parametrize("mode", ["BASE", "PRIORITY", "WAIT"])
def test_no_signal_same_shared_account(mode: Literal["BASE", "PRIORITY", "WAIT"]) -> None:
    data = [replace(f, signals=frozenset()) for f in frames(returns=True)]
    actual = simulate(data, end_ms=16 * H4, mode=mode)
    baseline = simulate(data, end_ms=16 * H4, mode="BASE")
    assert actual["net"] == baseline["net"]
    assert abs(actual["residual"]) < D("1e-20")


def test_return_priority_reduces_rebound_and_waiting_cost() -> None:
    data = frames(returns=True)
    priority = simulate(data, end_ms=16 * H4, mode="PRIORITY")
    waiting = simulate(data, end_ms=16 * H4, mode="WAIT")
    assert priority["net"] > waiting["net"]
    assert priority["trades"][0]["reason"] == "LONG_PRIORITY"
    assert priority["return_delays_ms"] == [H4]
    assert waiting["return_delays_ms"] == [11 * H4]
    assert priority["trades"][0]["net"] > waiting["trades"][0]["net"]


@pytest.mark.parametrize("mode", ["BASE", "PRIORITY", "WAIT"])
@pytest.mark.parametrize("cost", [1, 2])
@pytest.mark.parametrize("delay", [0, 1])
def test_exact_accounting_identity(
    mode: Literal["BASE", "PRIORITY", "WAIT"], cost: int, delay: int
) -> None:
    run = simulate(frames(returns=True), end_ms=16 * H4, mode=mode, cost=cost, delay=delay)
    assert abs(run["residual"]) < D("1e-20")
    assert run["terminal_open"] == 0


def test_existing_long_prevents_short_entry() -> None:
    data = frames()
    data[0] = replace(data[0], targets={S: D("0.5")})
    run = simulate(data, end_ms=16 * H4, mode="PRIORITY")
    assert run["counts"]["short_long_conflict"] == 1
    assert "short_opened" not in run["counts"]


def test_funding_entry_excluded_carried_boundary_included() -> None:
    data = frames(2)
    data[0] = replace(data[0], funding={S: (CashEvent(0, D("0.01"), D(100)),)})
    data[1] = replace(data[1], funding={S: (CashEvent(H4, D("0.01"), D(100)),)})
    run = simulate(data, end_ms=2 * H4, mode="WAIT")
    assert run["funding"] == D("25.011")
    assert abs(run["residual"]) < D("1e-20")


def test_stop_credit_omitted_debit_retained() -> None:
    data = frames(1)
    hot = replace(data[0].perp[S], h=D(104))
    data[0] = replace(
        data[0],
        perp={S: hot},
        funding={
            S: (
                CashEvent(1, D("0.01"), D(100)),
                CashEvent(2, D("-0.01"), D(100)),
            )
        },
    )
    run = simulate(data, end_ms=H4, mode="WAIT")
    assert run["funding"] == D("-25.011")
    assert run["trades"][0]["reason"] == "STOP"


def test_partial_exit_blocks_return_until_flat() -> None:
    data = frames(returns=True)
    for i in (2, 3):
        data[i] = replace(
            data[i], previous_perp={S: replace(data[i].previous_perp[S], volume=D(1000))}
        )
    run = simulate(data, end_ms=16 * H4, mode="PRIORITY")
    assert run["counts"]["short_exit_partial"] == 2
    assert run["return_delays_ms"][0] == 3 * H4


def test_terminal_unfilled_has_fee_reserve_not_fictional_fill() -> None:
    data = frames(2)
    data[1] = replace(data[1], previous_perp={S: replace(data[1].previous_perp[S], volume=D(0))})
    run = simulate(data, end_ms=2 * H4, mode="WAIT")
    assert run["terminal_open"] == 1
    assert run["fee_reserve"] > 0
    assert not run["trades"]
    assert abs(run["residual"]) < D("1e-20")


@pytest.mark.leakage
def test_appended_future_frames_do_not_change_registered_window() -> None:
    data = frames(returns=True)
    first = simulate(data[:8], end_ms=8 * H4, mode="PRIORITY")
    second = simulate(data, end_ms=8 * H4, mode="PRIORITY")
    assert first == second


@pytest.mark.leakage
def test_future_targets_and_signal_evidence_rejected() -> None:
    data = frames(1)
    with pytest.raises(CausalityError):
        simulate([replace(data[0], target_as_of_ms=1)], end_ms=H4, mode="BASE")
    with pytest.raises(CausalityError):
        simulate([replace(data[0], signal_as_of_ms=1)], end_ms=H4, mode="WAIT")


def test_gap_data_and_invalid_mode_rejected() -> None:
    data = frames(3)
    with pytest.raises(DataIntegrityError):
        simulate([data[0], data[2]], end_ms=3 * H4, mode="BASE")


def test_price_limit_refusal_does_not_open_short() -> None:
    data = frames(1)
    data[0] = replace(data[0], perp={S: Bar(D(98), D(98), D(98), D(98), D(100000))})
    run = simulate(data, end_ms=H4, mode="WAIT")
    assert run["counts"]["short_entry_limit_miss"] == 1
    assert run["net"] == 0
