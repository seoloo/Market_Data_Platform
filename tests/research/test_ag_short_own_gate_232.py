"""문서 232 숏 자기 손익 차단 — 엔진의 계좌 전체 차단을 무시하고 닫힌 숏만 본다."""

from __future__ import annotations

from decimal import Decimal

from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_own_gate_232 import (
    NoGateAccount,
    OwnGateAccount,
    account_class,
)

D = Decimal
CASE = next(c for c in p.cases() if c.mode == "SHORT_1X")


def _short(net: str, at: int) -> dict[str, object]:
    return {"side": "SHORT", "net": D(net), "exit_ms": at}


def test_engine_joint_halt_is_ignored() -> None:
    a = OwnGateAccount(CASE)
    a.short_halted = True  # 엔진이 계좌 전체 25% 낙폭으로 쓰는 자리
    assert a.short_halted is False


def test_own_halt_after_short_drawdown_of_2_5pct_of_peak() -> None:
    a = OwnGateAccount(CASE)
    a.peak = D(10_000)
    a.trades += [_short("100", 1), {"side": "LONG", "net": D("-5000"), "exit_ms": 2}]
    a.trades += [_short("-200", 3), _short("-100", 4)]  # 누적 고점 100 → -200
    assert a.short_halted is True  # 300 ≥ 250
    assert a.__dict__["_own"]["halted_at_ms"] == 4
    a.trades.append(_short("10000", 5))
    assert a.short_halted is True  # 영구


def test_own_not_halted_below_limit_and_long_losses_ignored() -> None:
    a = OwnGateAccount(CASE)
    a.peak = D(10_000)
    a.trades += [_short("-249", 1), {"side": "LONG", "net": D("-9000"), "exit_ms": 2}]
    assert a.short_halted is False


def test_none_gate_never_halts() -> None:
    a = NoGateAccount(CASE)
    a.peak = D(10_000)
    a.trades.append(_short("-9999", 1))
    assert a.short_halted is False


def test_account_class_patch_is_restored() -> None:
    original = p.Account
    with account_class(OwnGateAccount):
        assert p.Account is OwnGateAccount
    assert p.Account is original
