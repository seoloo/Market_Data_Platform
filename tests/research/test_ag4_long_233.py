"""문서 233 — AG4 목표 비중 손보기(T · V · D)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag4_long_233 import DAY, DayInfo, account_for, day_info

D = Decimal
CASE = next(c for c in p.cases() if c.exposure == 4 and c.mode == "BASE")


def _acct(variant: str, info: tuple[DayInfo, DayInfo], at: int = 1_000) -> p.Account:
    cls = account_for(variant, {at: info})  # type: ignore[arg-type]
    a = cls(CASE)
    a.goals = (D(repr(info[0].target)), D(repr(info[1].target)))
    a.targets_updated_ms = at
    return a


def test_day_info_rebuilds_tv2_target() -> None:
    closes = [100.0 * (1.001**k) for k in range(200)]
    info = day_info(closes)
    assert info is not None and info.direction == 1.0
    assert info.target == pytest.approx(min(0.15 / info.vol60, 0.5))
    assert day_info(closes[:100]) is None


def test_t_cuts_leverage_unless_full_trend() -> None:
    full, part = DayInfo(1.0, 0.5, 0.5, 0.3), DayInfo(2 / 3, 0.5, 0.5, 0.2)
    a = _acct("T", (full, part))
    assert a.goals == (D("0.3"), D("0.2") * D("0.25"))


def test_v_uses_larger_of_20_and_60_day_vol() -> None:
    calm = DayInfo(1.0, 0.3, 0.6, min(0.15 / 0.6, 0.5))
    hot = DayInfo(1.0, 0.9, 0.6, min(0.15 / 0.6, 0.5))
    a = _acct("V", (calm, hot))
    assert float(a.goals[0]) == pytest.approx(0.25)
    assert float(a.goals[1]) == pytest.approx(0.15 / 0.9)


def test_target_mismatch_stops() -> None:
    a = _acct("L0", (DayInfo(1.0, 0.5, 0.5, 0.3), DayInfo(1.0, 0.5, 0.5, 0.3)))
    a.goals = (D("0.31"), D("0.3"))
    a.targets_updated_ms = 1_000
    with pytest.raises(DataIntegrityError):
        _ = a.goals


def test_d_brake_halves_for_30_days_then_reanchors() -> None:
    info = (DayInfo(1.0, 0.5, 0.5, 0.4), DayInfo(1.0, 0.5, 0.5, 0.2))
    a = _acct("D", info)
    a.trace.append({"at_ms": 0, "equity": D(10_000)})
    assert a.goals == (D("0.4"), D("0.2"))
    a.trace.append({"at_ms": DAY, "equity": D(7_900)})  # 21% 낙폭
    assert a.goals == (D("0.20"), D("0.10"))
    a.trace.append({"at_ms": 20 * DAY, "equity": D(9_000)})
    assert a.goals == (D("0.20"), D("0.10"))  # 아직 30 일 안
    a.trace.append({"at_ms": 31 * DAY, "equity": D(7_000)})
    assert a.goals == (D("0.4"), D("0.2"))  # 풀림, 새 기준 7,000
    a.trace.append({"at_ms": 32 * DAY, "equity": D(5_700)})  # 7,000 대비 18.6% — 아직 아님
    assert a.goals == (D("0.4"), D("0.2"))
    a.trace.append({"at_ms": 33 * DAY, "equity": D(5_500)})  # 21.4%
    assert a.goals == (D("0.20"), D("0.10"))
