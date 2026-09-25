"""F1 빠른 추세 롱숏 무기한 시뮬레이터 합성 데이터 테스트 (45 사전등록)."""

from __future__ import annotations

import math
from dataclasses import replace

from auto_trading.backtest.fast_trend import F1B, FastBar, simulate_fast_trend

D = 86_400_000
START = 1_583_020_800_000  # 2020-03-01
HISTORY = 80


def path(n: int, drift: float, *, wobble: float = 0.01) -> list[float]:
    return [100 * math.exp(drift * k + wobble * math.sin(k)) for k in range(n)]


def bars(closes: list[float], *, wick: dict[int, float] | None = None) -> dict[int, FastBar]:
    first = START - HISTORY * D
    out = {}
    for k, c in enumerate(closes):
        hi, lo = c * 1.001, c * 0.999
        out[first + k * D] = FastBar(hi, lo, c, hi, lo)
    for k, high in (wick or {}).items():
        b = out[first + k * D]
        out[first + k * D] = FastBar(
            high, b.low, b.close, b.mark_high, b.mark_low
        )  # 최종 체결가만 튄다
    return out


def run(closes: list[float], *, cfg=F1B, wick=None, funding=None):
    n = len(closes)
    first = START - HISTORY * D
    return simulate_fast_trend(
        {"AUSDT": {first + k * D: c for k, c in enumerate(closes)}},
        {"AUSDT": bars(closes, wick=wick)},
        {"AUSDT": funding or []},
        from_ms=START,
        to_ms=START + (n - HISTORY - 1) * D,
        initial_equity=10_000.0,
        cfg=cfg,
    )


def test_uptrend_is_long_and_pays_positive_funding() -> None:
    closes = path(300, 0.004)
    rates = [(START + k * D + 1, 0.0001) for k in range(220)]
    r = run(closes, funding=rates)
    assert r.days_short == 0 and r.days_long > 0
    assert r.long_pnl > 0
    assert r.long_funding < 0  # 롱은 양수 funding 을 낸다


def test_downtrend_is_short_and_receives_positive_funding() -> None:
    closes = path(300, -0.004)
    rates = [(START + k * D + 1, 0.0001) for k in range(220)]
    r = run(closes, funding=rates)
    assert r.days_long == 0 and r.days_short > 0
    assert r.short_pnl > 0
    assert r.short_funding > 0
    assert r.short_net == r.short_pnl + r.short_funding - r.short_costs


def test_first_day_does_not_trade_on_same_close() -> None:
    r = run(path(200, 0.004))
    assert r.returns[0] == 0.0  # 첫날 목표는 그날 종가로 정해지고 다음 날 체결된다
    assert r.equity[0] == 10_000.0


def test_last_price_wick_triggers_stop_but_mark_does_not() -> None:
    closes = path(300, -0.004)
    spike = HISTORY + 150
    wick = {spike: closes[spike - 1] * 3.0}  # 최종 체결가 +200% 윅, mark 는 그대로
    last = run(closes, wick=wick)
    mark = run(closes, wick=wick, cfg=replace(F1B, stop_on_mark=True))
    assert last.stop_hits_short == 1
    assert mark.stop_hits_short == 0
    assert last.liquidations == 0 and mark.liquidations == 0  # 청산은 mark 기준
    assert last.halted_day is None and mark.halted_day is None  # 정지는 mark 기준
    assert mark.worst_intraday_drawdown_last > 0.3 > mark.worst_intraday_drawdown
    # 스톱 난 날: 전날 종가 × 1.2 × 1.01 에 닫아 그날 손실이 난다
    idx = spike - HISTORY
    assert last.returns[idx] < mark.returns[idx]


def test_liquidation_on_last_price_when_configured() -> None:
    closes = path(300, -0.004)
    spike = HISTORY + 150
    wick = {spike: closes[spike - 1] * 3.0}
    cfg = replace(F1B, stop=None, liquidate_on_mark=False, max_weight=1.0, target_vol=10.0)
    assert run(closes, wick=wick, cfg=cfg).liquidations == 1
    assert run(closes, wick=wick, cfg=replace(cfg, liquidate_on_mark=True)).liquidations == 0
