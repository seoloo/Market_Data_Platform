"""post-only 지정가 진입 모델 (D16-2 / 18-h2-validation-result.md §3.3 B).

**수수료만 보면 안 된다.** maker 진입의 진짜 비용은 수수료 절감이 아니라 **역선택**이다.

    매수 지정가를 걸어두면
      판단이 맞을 때 (가격 상승) → 나를 지나쳐 올라간다 → **체결 안 됨**
      판단이 틀릴 때 (가격 하락) → 내려와서 나를 친다 → **체결됨**

즉 체결된 집합이 진 거래 쪽으로 치우친다. 수수료 6.5bps 를 아끼고 역선택으로
10bps 를 잃으면 비용이 준 게 아니라 는 것이다. 이 모듈은 그 둘을 **따로** 재서
"maker 로 바꾸면 실제로 싸지는가" 에 숫자로 답하기 위해 있다.

체결 판정은 보수적으로 한다 (07 §3.1).

* 지정가를 **뚫고 지나가야** 체결한다. 단순 터치는 체결로 보지 않는다 —
  같은 가격에 먼저 들어온 주문들 뒤에 서 있기 때문이다.
* 거래량 0 인 봉에서는 체결하지 않는다 (채움봉, 07 §2 실측).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from auto_trading.core.enums import PositionSide
from auto_trading.core.money import ZERO
from auto_trading.core.types import Candle

__all__ = [
    "MakerAttempt",
    "MakerOutcome",
    "TimeoutPolicy",
    "simulate_post_only",
]

_BPS = Decimal("10000")


class MakerOutcome(StrEnum):
    """지정가 진입 시도의 결말."""

    FILLED = "filled"
    """제한 시간 안에 지정가를 뚫고 지나갔다."""
    CHASED = "chased"
    """미체결 후 시장가로 따라 들어갔다 (taker 비용)."""
    MISSED = "missed"
    """미체결 후 취소했다. 거래 자체가 없다."""


class TimeoutPolicy(StrEnum):
    """제한 시간이 지났을 때 무엇을 하는가."""

    CANCEL = "cancel"
    """취소한다. 신호를 놓치지만 비용도 없다."""
    CHASE = "chase"
    """시장가로 따라 들어간다. 신호는 잡지만 taker 비용 + 불리한 가격."""


@dataclass(frozen=True, slots=True)
class MakerAttempt:
    """한 번의 post-only 진입 시도 결과."""

    outcome: MakerOutcome
    fill_price: Decimal | None
    wait_minutes: int
    fee_bps: Decimal
    slippage_bps: Decimal

    @property
    def traded(self) -> bool:
        return self.outcome is not MakerOutcome.MISSED

    @property
    def cost_bps(self) -> Decimal:
        """진입 쪽 비용만. 청산 비용은 호출자가 더한다."""
        return self.fee_bps + self.slippage_bps


def simulate_post_only(
    side: PositionSide,
    reference_price: Decimal,
    bars: Sequence[Candle],
    *,
    maker_fee_rate: Decimal,
    taker_fee_rate: Decimal,
    chase_slippage_bps: Decimal,
    timeout_minutes: int,
    policy: TimeoutPolicy = TimeoutPolicy.CANCEL,
) -> MakerAttempt:
    """``reference_price`` 에 post-only 를 걸고 1분봉으로 체결을 판정한다.

    ``bars`` 는 주문을 낸 **다음** 1분봉부터 순서대로 준다. 신호 봉 안에서 체결시키면
    신호를 만든 정보로 그 봉에 거래하는 셈이 된다 (R-1).

    매수는 저가가 지정가 **아래로** 내려가야, 매도는 고가가 **위로** 올라가야 체결이다.
    등호를 넣지 않는 것이 큐 뒤에 서 있다는 가정이다.
    """
    if reference_price <= ZERO:
        raise ValueError(f"지정가가 0 이하: {reference_price}")
    if timeout_minutes <= 0:
        raise ValueError(f"timeout_minutes 는 1 이상이어야 한다: {timeout_minutes}")

    window = bars[:timeout_minutes]
    for waited, bar in enumerate(window, start=1):
        if bar.volume <= ZERO:
            continue  # 채움봉 — 그 가격에 유동성이 없었다
        crossed = (
            bar.low < reference_price if side is PositionSide.LONG else bar.high > reference_price
        )
        if crossed:
            return MakerAttempt(
                outcome=MakerOutcome.FILLED,
                fill_price=reference_price,
                wait_minutes=waited,
                fee_bps=maker_fee_rate * _BPS,
                slippage_bps=ZERO,  # 지정가는 그 가격에 체결된다
            )

    if policy is TimeoutPolicy.CANCEL:
        return MakerAttempt(MakerOutcome.MISSED, None, len(window), ZERO, ZERO)

    chase_bar = next((b for b in bars[len(window) :] if b.volume > ZERO), None)
    if chase_bar is None:
        return MakerAttempt(MakerOutcome.MISSED, None, len(window), ZERO, ZERO)
    drift = chase_bar.open * chase_slippage_bps / _BPS
    price = chase_bar.open + drift if side is PositionSide.LONG else chase_bar.open - drift
    return MakerAttempt(
        outcome=MakerOutcome.CHASED,
        fill_price=price,
        wait_minutes=len(window),
        fee_bps=taker_fee_rate * _BPS,
        slippage_bps=chase_slippage_bps,
    )
