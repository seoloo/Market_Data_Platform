"""스톱 발동 훈련의 순수 규칙: 매일 훈련과 겹침, 스톱가, 체결가 차이."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scripts import testnet_stop_drill as sd


def _ms(h: int, m: int, day: int = 25) -> int:
    return int(datetime(2026, 9, day, h, m, tzinfo=UTC).timestamp() * 1000)


@pytest.mark.parametrize(
    ("start", "minutes", "overlaps"),
    [
        (_ms(12, 8), 10, True),  # 매일 자동 실행(21:08 KST = 12:08 UTC) 한가운데
        (_ms(11, 30), 60, True),  # 대기 중 11:55 를 넘는다
        (_ms(11, 0), 25, False),  # 11:30 에 끝난다 (5분 여유 포함)
        (_ms(12, 31), 60, False),  # 구간이 끝난 뒤 시작
        (_ms(3, 0), 60, False),
        (_ms(10, 0), 180, True),  # 13:05 까지 — 구간을 지난다
    ],
)
def test_overlaps_daily_drill(start: int, minutes: float, overlaps: bool) -> None:
    assert sd.overlaps_daily_drill(start, minutes) is overlaps


def test_stop_below_floors_to_tick() -> None:
    assert sd.stop_below(Decimal("2000.00"), Decimal(15), Decimal("0.01")) == Decimal("1997.00")
    assert sd.stop_below(Decimal("2003.37"), Decimal(15), Decimal("0.01")) == Decimal("2000.36")


def test_slippage_sign() -> None:
    assert sd.slippage_bps(Decimal(2000), Decimal(1999)) == Decimal(5)  # 불리
    assert sd.slippage_bps(Decimal(2000), Decimal(2001)) == Decimal(-5)  # 유리
