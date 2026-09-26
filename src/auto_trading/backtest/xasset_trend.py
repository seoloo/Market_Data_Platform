"""여러 자산군 T/v2 추세 시뮬레이터 (docs/research/86 §2·§3.2). 순수 함수, I/O 없음.

T/v2(:func:`auto_trading.backtest.trend.simulate_trend`, ``long_only=True``)와 **같은 규칙**을
거래일이 서로 다른 자산(암호화폐 = 매일, 미국 ETF = 평일·휴장 제외)에 적용한다.

* 신호는 자산마다 **자기 봉**으로: ``(sign(r20)+sign(r60)+sign(r120))/3`` (음수 → 0),
  크기 = 방향 × min(15% / 60봉 실현 연변동성, 0.5). 연율화는 √(그 자산의 연간 봉 수).
* 목표는 그 자산의 종가에 정하고 **그 자산의 다음 봉 종가**에 체결한다. 밴드 5%p.
* 계좌는 **UTC 역일**로 평가한다. 쉬는 날 자산은 가격이 그대로(손익 0)이고 거래하지 않는다.
* 목표 합이 ``max_total`` 을 넘으면 체결하는 날 그때 아는 모든 목표를 같은 비율로 줄인다.

BTC·ETH 만 넣으면 기존 ``simulate_trend(long_only=True)`` 와 같은 결과가 나와야 한다(86 §3.2 V0).
(문서 86 §3.2 에 적은 이름 ``multi_trend`` 는 이미 TA 시뮬레이터가 쓰고 있어 이 이름으로 둔다.)
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from auto_trading.backtest.trend import TrendConfig

__all__ = ["AssetSeries", "MultiResult", "simulate_multi", "target_weight_bars"]

_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class AssetSeries:
    closes: Mapping[int, float]
    """{UTC 날짜 00:00 의 ms: 그날 종가}. 거래하지 않은 날은 없다."""
    bars_per_year: int
    """연율화용 연간 봉 수 (암호화폐 365, 미국 ETF 252)."""


@dataclass(slots=True)
class MultiResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    costs: float = 0.0
    traded_notional: float = 0.0
    trades: int = 0
    target_sum_max: float = 0.0
    """축소 뒤 목표 비중 합의 최댓값 (86 Y4)."""
    gross_max: float = 0.0
    """실제 보유 비중 합의 최댓값 (밴드 때문에 1 을 조금 넘을 수 있다 — 보고만)."""
    exposure_sum: float = 0.0
    """날마다 보유 비중 합의 누계 (평균 노출 = exposure_sum / 날 수)."""
    trading_days: dict[str, list[int]] = field(default_factory=dict)
    """자산별로 기간 안에서 봉이 있던 날."""


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def target_weight_bars(
    closes: Sequence[float], cfg: TrendConfig, bars_per_year: int
) -> float | None:
    """:func:`auto_trading.backtest.trend.target_weight` 와 같은 계산, 연율화만 √bars_per_year."""
    need = max(max(cfg.lookbacks), cfg.vol_days) + 1
    if len(closes) < need:
        return None
    last = closes[-1]
    direction = statistics.fmean(_sign(last / closes[-1 - k] - 1) for k in cfg.lookbacks)
    if cfg.long_only:
        direction = max(direction, 0.0)
    steps = [math.log(closes[-k] / closes[-k - 1]) for k in range(1, cfg.vol_days + 1)]
    vol = statistics.stdev(steps) * math.sqrt(bars_per_year)
    if vol <= 0:
        return 0.0
    return direction * min(cfg.target_vol / vol, cfg.max_weight)


def simulate_multi(
    assets: Mapping[str, AssetSeries],
    *,
    from_ms: int,
    to_ms: int,
    initial_equity: float,
    cfg: TrendConfig,
    max_total: float = 1.0,
) -> MultiResult:
    """``[from_ms, to_ms]`` 의 UTC 역일을 재생한다. 그 전 봉은 신호 이력으로만 쓴다. 롱 전용."""
    if not cfg.long_only:
        raise ValueError("multi_trend 는 롱 전용(T/v2)만 지원한다")
    symbols = sorted(assets)
    result = MultiResult()
    qty = dict.fromkeys(symbols, 0.0)
    raw: dict[str, float | None] = dict.fromkeys(symbols)
    history: dict[str, list[float]] = {}
    last_close: dict[str, float | None] = {}
    for s in symbols:
        before = sorted(d for d in assets[s].closes if d < from_ms)
        history[s] = [assets[s].closes[d] for d in before]
        last_close[s] = history[s][-1] if history[s] else None
        result.trading_days[s] = []
    equity = initial_equity
    day = from_ms
    while day <= to_ms:
        start_equity = equity
        open_today = [s for s in symbols if day in assets[s].closes]
        for s in open_today:
            close = assets[s].closes[day]
            prev = last_close[s]
            if prev is not None and qty[s]:
                equity += qty[s] * (close - prev)
        total = sum(max(v, 0.0) for v in raw.values() if v is not None)
        scale = 1.0 if total <= max_total else max_total / total
        result.target_sum_max = max(result.target_sum_max, total * scale)
        for s in open_today:
            close = assets[s].closes[day]
            want = raw[s]
            if want is not None:
                want *= scale
                current = qty[s] * close / equity if equity > 0 else 0.0
                if abs(want - current) >= cfg.band or _sign(want) != _sign(current):
                    new_qty = want * equity / close
                    cost = close * abs(new_qty - qty[s]) * cfg.spot_cost
                    result.traded_notional += abs(new_qty - qty[s]) * close
                    result.costs += cost
                    result.trades += 1
                    equity -= cost
                    qty[s] = new_qty
            history[s].append(close)
            raw[s] = target_weight_bars(history[s], cfg, assets[s].bars_per_year) or 0.0
            last_close[s] = close
            result.trading_days[s].append(day)
        gross = (
            sum(qty[s] * (last_close[s] or 0.0) for s in symbols) / equity if equity > 0 else 0.0
        )
        result.gross_max = max(result.gross_max, gross)
        result.exposure_sum += gross
        result.days.append(day)
        result.equity.append(equity)
        result.returns.append(equity / start_equity - 1 if start_equity > 0 else 0.0)
        day += _DAY_MS
    return result
