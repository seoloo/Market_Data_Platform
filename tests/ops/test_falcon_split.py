"""Altair 자동 분배 — 선물 몫 최소 · 새 돈 안에서만 · 첫 분배 · 되돌리지 않음."""

from __future__ import annotations

from decimal import Decimal

from auto_trading.ops.falcon_split import falcon_share, move_amount


def test_share_is_half_for_10_10() -> None:
    assert falcon_share() == Decimal("0.5")


def test_small_account_moves_nothing() -> None:
    # 바이낸스 218 USDT → 선물 목표 109 < 500 → 안 옮김 (Vega 가 씀)
    assert (
        move_amount(
            spot_equity=Decimal(218),
            futures_balance=Decimal(0),
            new_money=Decimal(218),
            free_usdt=Decimal(218),
        )
        == 0
    )


def test_first_split_fills_target_from_spot_cash() -> None:
    got = move_amount(
        spot_equity=Decimal(1200),
        futures_balance=Decimal(0),
        new_money=Decimal(0),
        free_usdt=Decimal(900),
    )
    assert got == Decimal(600)
    capped = move_amount(
        spot_equity=Decimal(1200),
        futures_balance=Decimal(0),
        new_money=Decimal(0),
        free_usdt=Decimal(400),
    )
    assert capped == Decimal(400)


def test_later_only_new_money_and_never_back() -> None:
    # 선물 600 · 현물 700(새 입금 100 포함) → 목표 650, 모자란 50 을 새 돈 안에서
    assert move_amount(
        spot_equity=Decimal(700),
        futures_balance=Decimal(600),
        new_money=Decimal(100),
        free_usdt=Decimal(300),
    ) == Decimal(50)
    # 새 돈이 없으면 Vega 돈은 건드리지 않는다
    assert (
        move_amount(
            spot_equity=Decimal(700),
            futures_balance=Decimal(600),
            new_money=Decimal(0),
            free_usdt=Decimal(300),
        )
        == 0
    )
    # 선물이 목표보다 많아도 되돌리지 않는다
    assert (
        move_amount(
            spot_equity=Decimal(500),
            futures_balance=Decimal(800),
            new_money=Decimal(100),
            free_usdt=Decimal(300),
        )
        == 0
    )
    # 5 USDT 미만은 안 옮김
    assert (
        move_amount(
            spot_equity=Decimal(1004),
            futures_balance=Decimal(600),
            new_money=Decimal(4),
            free_usdt=Decimal(300),
        )
        == 0
    )
