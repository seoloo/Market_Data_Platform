"""문서 229 — 지표 기본 성질 · 숏 거래 손절 · 청산 · 겹침."""

from __future__ import annotations

import math

from auto_trading.research import indicators_229 as ind
from auto_trading.research.short_search_229 import (
    FEE,
    H4,
    SIZE,
    STOP_SLIP,
    Coin,
    TradeCache,
    run_config,
)


def test_basic_indicators() -> None:
    up = [100.0 + i for i in range(80)]
    assert ind.sma([1.0, 2.0, 3.0], 2)[1:] == [1.5, 2.5]
    assert ind.rsi(up)[-1] == 100.0
    down = [200.0 - i for i in range(80)]
    assert ind.rsi(down)[-1] == 0.0
    h = [x + 1 for x in up]
    lo = [x - 1 for x in up]
    assert abs(ind.atr(h, lo, up)[-1] - 2.0) < 1e-9
    k, _d = ind.stoch(h, lo, up)
    assert 0 <= k[-1] <= 100
    assert ind.psar(h, lo)[-1] == 1
    hd = [x + 1 for x in down]
    ld = [x - 1 for x in down]
    assert ind.psar(hd, ld)[-1] == -1
    assert ind.supertrend(hd, ld, down)[-1] == -1
    a, p, m = ind.adx(hd, ld, down)
    assert a[-1] > 25 and m[-1] > p[-1]
    _, _, sa, _ = ind.ichimoku(h, lo)
    assert all(math.isnan(x) for x in sa[:26])
    mid, upb, dnb = ind.bollinger([1.0] * 30)
    assert upb[-1] == dnb[-1] == mid[-1] == 1.0
    vw = ind.vwap_daily(
        [0, H4, 6 * H4], [2.0, 4.0, 10.0], [0.0, 2.0, 8.0], [1.0, 3.0, 9.0], [1.0, 1.0, 1.0]
    )
    assert vw[2] == 9.0  # 다음 날 처음부터


def _coin(n: int, highs: dict[int, float] | None = None) -> Coin:
    px = [100.0] * n
    h = list(px)
    for q, v in (highs or {}).items():
        h[q] = v
    k = Coin("BTCUSDT", [i * H4 for i in range(n)], list(px), h, list(px), list(px), [1.0] * n)
    k.ind = {
        "atr": [1.0] * n,
        "hh10": [100.0] * n,
        "rsi": [50.0] * n,
        "bbm": [99.0] * n,
    }
    k.regime = {"R0": [True] * n}
    k.trig = {"T1": [i in (5, 7, 20) for i in range(n)]}
    return k


def test_timed_exit_stop_and_overlap() -> None:
    k = _coin(60)
    cache = TradeCache([k])
    got = run_config(cache, "R0", "T1", "E1", "S3", 0, 60 * H4)
    # 5 번 신호 → 6 봉 보유(진입 6 ~ 11, 12 시가 청산) → 7 번은 겹쳐서 무시 → 20 번
    assert [t.signal for t in got] == [5, 20]
    assert abs(got[0].ret - SIZE * (-2 * FEE)) < 1e-12
    k2 = _coin(60, {8: 104.0})
    got2 = run_config(TradeCache([k2]), "R0", "T1", "E2", "S3", 0, 60 * H4)
    assert got2[0].stopped and got2[0].exit_i == 8
    px = 103.0 * (1 + STOP_SLIP)
    assert abs(got2[0].ret - SIZE * ((100 - px) / 100 - FEE - FEE * px / 100)) < 1e-12
    # 손절 봉(8) 마감 뒤 신호 20 은 다시 들어감
    assert [t.signal for t in got2] == [5, 20]
