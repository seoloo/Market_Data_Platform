"""바이낸스 돈을 Vega(현물) · Altair(USDⓈ-M 선물)으로 목표 비율대로 나누기 (사용자 2026-10-15).

사용자: "비율 대로 자동 분배 할 수 있도록 해줘". 목표 비율은 :data:`deposit_split.MIX` 의
Vega · Altair 몫(지금 10 : 10 → 선물 50%)이다. 순수 함수만 — 이체는 Vega 실행기가 한다.

규칙:

* **현물 → 선물만** 옮긴다. 선물 → 현물로 되돌리지 않는다(Altair 돈을 빼지 않는다).
* 옮기는 돈은 **새로 들어온 돈(입금) 안에서만** 모자란 몫을 채운다 — Vega 가 이미 굴리는 돈은
  건드리지 않는다. 단, 선물 지갑이 아직 비어 있는 첫 분배만 목표 몫 전체를 현물 USDT 에서 채운다.
* 선물 몫 목표가 :data:`MIN_ALTAIR` (500 USDT) 미만이면 옮기지 않는다 — Altair 가 BTC 주문을
  넣을 수 있는 최소 금액(인계 2026-10-15)보다 작으면 선물에서 놀기만 한다.
  그동안 돈은 Vega 가 쓴다.
* 한 번에 :data:`MIN_MOVE` (5 USDT) 미만은 옮기지 않는다.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from auto_trading.ops.deposit_split import MIX

__all__ = ["MIN_ALTAIR", "MIN_MOVE", "altair_share", "move_amount"]

MIN_ALTAIR = Decimal(500)
MIN_MOVE = Decimal(5)


def altair_share() -> Decimal:
    """바이낸스 돈 중 선물(Altair) 목표 비율 = Altair ÷ (Vega + Altair)."""
    w = dict(MIX)
    return Decimal(str(w["Altair"])) / Decimal(str(w["Vega"] + w["Altair"]))


def move_amount(
    *,
    spot_equity: Decimal,
    futures_balance: Decimal,
    new_money: Decimal,
    free_usdt: Decimal,
) -> Decimal:
    """현물 → 선물로 옮길 USDT (0 이면 안 옮김). 모든 값은 USDT.

    ``spot_equity`` = 현물 지갑의 Vega 돈 전체(USDT + 코인 평가).
    ``new_money`` = 지난 실행 뒤 새로 들어온 돈(입금, 없으면 0).
    ``free_usdt`` = 지금 쓸 수 있는 현물 USDT.
    """
    total = spot_equity + futures_balance
    target = total * altair_share()
    if target < MIN_ALTAIR:
        return Decimal(0)
    lack = target - futures_balance
    if lack <= 0:
        return Decimal(0)
    room = lack if futures_balance <= 0 else min(lack, max(new_money, Decimal(0)))
    move = min(room, free_usdt).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return move if move >= MIN_MOVE else Decimal(0)
