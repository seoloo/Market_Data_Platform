"""TA/v1 다종목 선물 추세 시뮬레이터 합성 데이터 테스트 (20 사전등록)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from auto_trading.backtest.multi_trend import (
    DayOHLC,
    MultiTrendConfig,
    MultiTrendResult,
    simulate_multi_trend,
    weight,
)

D = 86_400_000
BASE = 1_590_969_600_000  # 2020-06-01
HIST = 130
FREE = MultiTrendConfig(cost_major=0.0, cost_alt=0.0)


def series(n: int, drift: float, wobble: float = 0.01) -> list[float]:
    return [100 * math.exp(drift * k + wobble * math.sin(k)) for k in range(n)]


def to_bars(closes: Sequence[float], first: int = BASE - HIST * D) -> dict[int, DayOHLC]:
    return {first + k * D: DayOHLC(c * 1.001, c * 0.999, c) for k, c in enumerate(closes)}


def uni(symbols: Sequence[str]) -> dict[int, list[str]]:
    """2020-05 ~ 2021-06 월 시작마다 같은 유니버스."""
    out = {}
    for y, m in [(2020, k) for k in range(5, 13)] + [(2021, k) for k in range(1, 7)]:
        out[int(datetime(y, m, 1, tzinfo=UTC).timestamp() * 1000)] = list(symbols)
    return out


def run(
    bars: Mapping[str, Mapping[int, DayOHLC]],
    days: int,
    cfg: MultiTrendConfig = FREE,
    funding: Mapping[str, Sequence[tuple[int, float]]] | None = None,
    universe: Mapping[int, Sequence[str]] | None = None,
) -> MultiTrendResult:
    return simulate_multi_trend(
        bars,
        funding or {},
        universe if universe is not None else uni(sorted(bars)),
        from_ms=BASE,
        to_ms=BASE + (days - 1) * D,
        initial_equity=10_000.0,
        cfg=cfg,
    )


def test_weight_uses_per_asset_budget_and_cap() -> None:
    closes = series(200, 0.004)
    w = weight(closes, FREE.per_asset_vol, FREE)
    assert w is not None and 0 < w <= FREE.max_weight
    assert FREE.per_asset_vol == pytest.approx(0.015)
    assert weight(series(100, 0.004), 0.015, FREE) is None


def test_uptrend_long_downtrend_short_with_one_day_lag() -> None:
    bars = {"AUSDT": to_bars(series(HIST + 60, 0.004)), "BUSDT": to_bars(series(HIST + 60, -0.004))}
    result = run(bars, 60)
    assert result.returns[0] == 0.0  # 첫날은 목표만 정한다
    assert result.long_pnl > 0 and result.short_pnl > 0
    assert result.symbols_traded == {"AUSDT", "BUSDT"}


def test_funding_paid_by_longs_received_by_shorts() -> None:
    bars = {"AUSDT": to_bars(series(HIST + 30, 0.004))}
    funding = {"AUSDT": [(BASE + k * D + 5, 0.001) for k in range(30)]}
    assert run(bars, 30, funding=funding).funding < 0
    short = {"AUSDT": to_bars(series(HIST + 30, -0.004))}
    assert run(short, 30, funding=funding).funding > 0


def test_gross_cap_scales_targets() -> None:
    names = [f"S{k}USDT" for k in range(20)]
    calm = {n: to_bars(series(HIST + 20, 0.002, wobble=0.0005)) for n in names}
    capped = MultiTrendConfig(cost_major=0.0, cost_alt=0.0, gross_cap=0.5, max_weight=0.5)
    result = run(calm, 20, capped)
    assert result.gross[1] == pytest.approx(0.5)  # 체결 시점에는 상한을 정확히 지킨다
    # 그 뒤 가격 변동으로 밴드(종목당 0.5%p) 안에서만 넘칠 수 있다
    assert max(result.gross) <= 0.5 + 20 * capped.band


def test_leaving_universe_closes_position() -> None:
    bars = {"AUSDT": to_bars(series(HIST + 60, 0.004))}
    june = int(datetime(2020, 6, 1, tzinfo=UTC).timestamp() * 1000)
    july = int(datetime(2020, 7, 1, tzinfo=UTC).timestamp() * 1000)
    result = run(bars, 60, universe={june: ["AUSDT"], july: []})
    assert result.gross[-1] == 0.0
    assert result.gross[10] > 0.0


def test_delisted_symbol_is_force_closed_with_penalty() -> None:
    bars = {"AUSDT": to_bars(series(HIST + 20, 0.004))}
    del bars["AUSDT"][BASE + 20 * D - D]  # 마지막 날 봉이 없다
    result = run(bars, 20)
    assert result.forced_closes == 1 and result.costs > 0


def test_extreme_gap_liquidates_leveraged_book() -> None:
    closes = series(HIST + 10, 0.004)
    bars = {"AUSDT": to_bars(closes)}
    crash = BASE + 5 * D
    bars["AUSDT"][crash] = DayOHLC(closes[HIST + 5], closes[HIST + 5] * 0.05, closes[HIST + 5])
    wild = MultiTrendConfig(
        cost_major=0.0, cost_alt=0.0, risk_budget=1000.0, max_weight=10.0, gross_cap=10.0
    )
    assert run(bars, 10, wild).liquidations == 1


@pytest.mark.leakage
def test_future_bars_do_not_change_the_past() -> None:
    full = {"AUSDT": to_bars(series(HIST + 80, 0.003)), "BUSDT": to_bars(series(HIST + 80, -0.002))}
    cut = {s: {d: b for d, b in v.items() if d < BASE + 40 * D} for s, v in full.items()}
    a = run(full, 40)
    b = run(cut, 40)
    assert a.equity == b.equity
