"""P1/v1 ETH/BTC 페어 추세 시뮬레이터 합성 데이터 테스트 (22 사전등록)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import pytest

from auto_trading.backtest.pair_trend import (
    PairBar,
    PairConfig,
    PairResult,
    pair_weight,
    simulate_pair_trend,
)

D = 86_400_000
START = 1_514_764_800_000  # 2018-01-01
HIST = 130
FREE = PairConfig(cost_per_leg=0.0)


def path(n: int, drift: float, wobble: float = 0.01, base: float = 100.0) -> list[float]:
    return [base * math.exp(drift * k + wobble * math.sin(k * 1.3)) for k in range(n)]


def bars(closes: Sequence[float]) -> dict[int, PairBar]:
    first = START - HIST * D
    return {first + k * D: PairBar(c * 1.001, c * 0.999, c) for k, c in enumerate(closes)}


def run(
    eth: Sequence[float],
    btc: Sequence[float],
    days: int,
    cfg: PairConfig = FREE,
    funding: Mapping[str, Sequence[tuple[int, float]]] | None = None,
) -> PairResult:
    return simulate_pair_trend(
        {"ETHUSDT": bars(eth), "BTCUSDT": bars(btc)},
        funding or {},
        from_ms=START,
        to_ms=START + (days - 1) * D,
        initial_equity=10_000.0,
        cfg=cfg,
    )


def test_pair_weight_follows_ratio_trend() -> None:
    up = [math.exp(0.004 * k + 0.01 * math.sin(k)) for k in range(200)]
    w = pair_weight(up, FREE)
    assert w is not None and 0 < w <= 0.5
    down = [1 / x for x in up]
    assert pair_weight(down, FREE) == pytest.approx(-w, rel=0.2)
    assert pair_weight(up[:100], FREE) is None


def test_eth_outperforming_goes_long_eth_short_btc_and_profits() -> None:
    n = HIST + 80
    result = run(path(n, 0.004), path(n, 0.001, base=200.0), 80)
    assert result.returns[0] == 0.0  # 1일 지연
    assert result.days_pos > 0 and result.days_neg == 0
    assert result.price_pnl > 0


def test_common_market_move_is_hedged() -> None:
    """두 자산이 같은 비율로 움직이면(비율 불변) 손익이 거의 없다."""
    n = HIST + 60
    common = path(n, 0.01, wobble=0.0)
    ratio_trend = [math.exp(0.004 * k + 0.01 * math.sin(k)) for k in range(n)]
    eth = [c * r for c, r in zip(common, ratio_trend, strict=True)]
    hedged = run(eth, common, 60)
    btc_only_move = run(ratio_trend, [1.0] * n, 60)  # 같은 비율 경로, 시장 움직임 없음
    assert hedged.price_pnl == pytest.approx(btc_only_move.price_pnl, rel=0.2)


def test_legs_are_dollar_neutral_after_trades() -> None:
    n = HIST + 40
    result = run(path(n, 0.004), path(n, -0.002, base=300.0), 40)
    assert result.trades >= 1
    assert all(abs(w) <= 0.5 + 0.05 for w in result.weights)


def test_costs_and_funding_are_charged_per_leg() -> None:
    n = HIST + 30
    eth, btc = path(n, 0.004), path(n, 0.0, base=200.0)
    costly = run(eth, btc, 30, PairConfig(cost_per_leg=0.001))
    assert costly.costs > 0
    fund = {s: [(START + k * D + 7, 0.001) for k in range(30)] for s in ("ETHUSDT", "BTCUSDT")}
    same_rate = run(eth, btc, 30, FREE, funding=fund)
    # 같은 rate면 롱 지급과 숏 수취가 상쇄되고, 체결 뒤 가격 변화로 생긴 명목 차이만큼만 남는다.
    one_leg = 0.001 * 30 * 0.5 * 10_000
    assert abs(same_rate.funding) < 0.1 * one_leg


def test_missing_bar_raises() -> None:
    n = HIST + 20
    eth_bars = bars(path(n, 0.004))
    del eth_bars[START + 5 * D]
    with pytest.raises(ValueError, match="결측"):
        simulate_pair_trend(
            {"ETHUSDT": eth_bars, "BTCUSDT": bars(path(n, 0.0))},
            {},
            from_ms=START,
            to_ms=START + 19 * D,
            initial_equity=1e4,
            cfg=FREE,
        )


@pytest.mark.leakage
def test_future_bars_do_not_change_the_past() -> None:
    n = HIST + 90
    eth, btc = path(n, 0.003), path(n, -0.001, base=250.0)
    full = run(eth, btc, 50)
    cut = run(eth[: HIST + 50], btc[: HIST + 50], 50)
    assert full.equity == cut.equity
