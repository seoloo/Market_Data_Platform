"""워뇨띠 탐색에서 나온 후보 상태의 **빈도·잡음·비용 대리치·게이트 겹침**만 잰다 (07 후보 비교).

수익 평균·방향별 성과는 계산하지 않는다(01 후보 비교와 같은 원칙). 선행수익은 부호 없는 SD만 쓴다.
판단은 1시간봉 마감 시점, 입력은 그 시점까지 마감된 봉만 쓴다. DB는 읽기 전용이다.

후보 상태 (방향은 미리 정하지 않는다 — 사전등록 시 TRAIN에서 부호를 읽고 검증 구간에서 확인):

* **WA 하락 뒤 7일 저점 부근**: 직전 24h 수익률 < 0 이고 종가가 직전 7일 범위 하위 20% 안.
  워뇨띠 2020~2021 진입이 하락 뒤에 몰렸고 롱은 7일 저점 부근이 가장 좋았다는 관찰(06 §5)에서 왔다.
* **WB 큰 움직임 직후**: |직전 24h 수익률|이 직전 30일(720개 1h 시점) 분포의 상위 10% 이상.
  수익의 62%가 상위 20일에 몰렸다는 관찰(06 §3)에서 왔다.

두 상태 모두 트리거 뒤 ``horizon`` 시간 동안 새 트리거를 만들지 않는다.
"""

from __future__ import annotations

import bisect
import json
import math
import sqlite3
import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from auto_trading.backtest.power import detectable_effect_bps, required_samples
from auto_trading.features.normalize import causal_percentile_rank_series

DB = Path("data/trading.db")
OUTPUT = Path("reports/phase0/w_candidates.json")
_HOUR = 3_600_000
_HORIZONS = (6, 12, 24)
_WEEK = 7 * 24
_MONTH = 30 * 24
_YEAR_HOURS = 365 * 24
_FEE_BPS = 9.0
"""현재 설정 taker 4.5bps × 2 (A1과 같은 가정)."""
_BASE_SLIPPAGE_BPS = 5.0
"""01 후보 비교의 슬리피지 여유. 트리거 시점 1분 움직임 배율로 조정한다."""


@dataclass(frozen=True, slots=True)
class Hours:
    open_ms: list[int]
    high: list[float]
    low: list[float]
    close: list[float]


def ret(close: Sequence[float], i: int, back: int) -> float | None:
    return close[i] / close[i - back] - 1 if i - back >= 0 else None


def position_7d(hours: Hours, i: int) -> float | None:
    if i - _WEEK + 1 < 0:
        return None
    low = min(hours.low[i - _WEEK + 1 : i + 1])
    high = max(hours.high[i - _WEEK + 1 : i + 1])
    return (hours.close[i] - low) / (high - low) if high > low else 0.5


def state_wa(hours: Hours, i: int, _abs_rank: Sequence[float | None]) -> bool:
    r24 = ret(hours.close, i, 24)
    pos = position_7d(hours, i)
    return r24 is not None and pos is not None and r24 < 0 and pos <= 0.2


def state_wb(_hours: Hours, i: int, abs_rank: Sequence[float | None]) -> bool:
    rank = abs_rank[i]
    return rank is not None and rank >= 0.9


def triggers(
    hours: Hours,
    state: Callable[[Hours, int, Sequence[float | None]], bool],
    abs_rank: Sequence[float | None],
    lock: int,
) -> list[int]:
    """상태가 참인 1h 마감 인덱스. 트리거 뒤 ``lock``개 봉 동안은 다시 세지 않는다."""
    out: list[int] = []
    blocked_until = 0
    for i in range(len(hours.close)):
        if i < blocked_until or not state(hours, i, abs_rank):
            continue
        out.append(i)
        blocked_until = i + lock
    return out


def abs_move_rank(close: Sequence[float]) -> list[float | None]:
    """|24h 수익률|의 직전 30일 백분위. causal (현재 값 포함 창)."""
    moves = [
        abs(r) if (r := ret(close, i, 24)) is not None else math.nan for i in range(len(close))
    ]
    ranks: list[float | None] = [None] * len(close)
    valid = [(i, m) for i, m in enumerate(moves) if not math.isnan(m)]
    series = causal_percentile_rank_series([m for _, m in valid], _MONTH)
    for (i, _), rank in zip(valid, series, strict=True):
        ranks[i] = rank
    return ranks


def atr_percentile(hours: Hours, period: int = 14) -> list[float | None]:
    """G5와 같은 정의의 1h ATR/가격 1년 백분위 근사 (Wilder ATR)."""
    ratios: list[float] = []
    atr: float | None = None
    for i in range(len(hours.close)):
        prev = hours.close[i - 1] if i else hours.close[i]
        tr = max(hours.high[i] - hours.low[i], abs(hours.high[i] - prev), abs(hours.low[i] - prev))
        atr = tr if atr is None else (atr * (period - 1) + tr) / period
        ratios.append(atr / hours.close[i])
    return list(causal_percentile_rank_series(ratios, _YEAR_HOURS))


def load(path: Path) -> tuple[Hours, dict[int, float], list[tuple[int, float]]]:
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT open_ms, high, low, close FROM candles WHERE exchange = 'binance_usdm'"
            " AND symbol = 'BTCUSDT' AND timeframe = '1h' ORDER BY open_ms"
        ).fetchall()
        first_minutes = conn.execute(
            "SELECT open_ms, open, close FROM candles WHERE exchange = 'binance_usdm'"
            " AND symbol = 'BTCUSDT' AND timeframe = '1m' AND open_ms % 3600000 = 0"
        ).fetchall()
        funding = conn.execute(
            "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
            " AND symbol = 'BTCUSDT' ORDER BY funding_ms"
        ).fetchall()
    hours = Hours(
        open_ms=[int(r[0]) for r in rows],
        high=[float(r[1]) for r in rows],
        low=[float(r[2]) for r in rows],
        close=[float(r[3]) for r in rows],
    )
    # 1h 마감 직후 첫 1분봉의 |움직임| (bps). 체결 지연·슬리피지 대리치.
    first_move = {int(ms): abs(float(c) / float(o) - 1) * 1e4 for ms, o, c in first_minutes}
    return hours, first_move, [(int(ms), float(rate)) for ms, rate in funding]


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).year


def measure(
    name: str,
    hours: Hours,
    idx: list[int],
    first_move: dict[int, float],
    atr_rank: Sequence[float | None],
    funding: Sequence[tuple[int, float]],
    baseline_move: float,
) -> dict[str, object]:
    n = len(hours.close)
    per_year: dict[int, int] = {}
    for i in idx:
        per_year[_year(hours.open_ms[i])] = per_year.get(_year(hours.open_ms[i]), 0) + 1
    moves = [first_move[m] for i in idx if (m := hours.open_ms[i] + _HOUR) in first_move]
    cost_ratio = statistics.fmean(moves) / baseline_move if moves else math.nan
    cost_bps = _FEE_BPS + _BASE_SLIPPAGE_BPS * cost_ratio
    horizons: dict[str, object] = {}
    train = [i for i in idx if _year(hours.open_ms[i]) in (2023, 2024)]
    for h in _HORIZONS:
        fwd = [hours.close[i + h] / hours.close[i] - 1 for i in idx if i + h < n]
        sd = statistics.stdev(fwd) * 1e4
        horizons[f"{h}h"] = {
            "sd_bps": sd,
            "train_2023_2024_n": len(train),
            "detectable_train_bps": detectable_effect_bps(sd, max(len(train), 1), cost_bps),
            "detectable_oos100_bps": detectable_effect_bps(sd, 100, cost_bps),
        }
    times = [ms for ms, _ in funding]

    def funding_at(ms: int) -> float | None:
        k = bisect.bisect_right(times, ms) - 1
        return funding[k][1] if k >= 0 else None

    gate = [atr_rank[i] for i in idx if atr_rank[i] is not None]
    rates = [f for i in idx if (f := funding_at(hours.open_ms[i] + _HOUR - 1)) is not None]
    return {
        "candidate": name,
        "triggers": len(idx),
        "per_year": dict(sorted(per_year.items())),
        "first_minute_move_bps": statistics.fmean(moves) if moves else None,
        "first_minute_vs_all": cost_ratio,
        "cost_bps": cost_bps,
        "horizons": horizons,
        "share_vol_gate_veto(G5 <0.05 or >0.98)": (
            sum(r < 0.05 or r > 0.98 for r in gate if r is not None) / len(gate) if gate else None
        ),
        "share_extreme_funding(|f|>0.001)": (
            sum(abs(f) > 0.001 for f in rates) / len(rates) if rates else None
        ),
        "required_train_n_for_detectable_oos100_12h": required_samples(
            horizons["12h"]["sd_bps"],  # type: ignore[index]
            horizons["12h"]["detectable_oos100_bps"],  # type: ignore[index]
            cost_bps,
        ),
    }


STOP_MULTIPLE = 2.0
"""A1과 같은 손절 배수. 새 자유도를 만들지 않는다."""
MIN_STOP = 0.01
"""TRADING_CORE min_stop_distance. 사이징 분모 하한에만 쓴다(REDUCE_POSITION)."""


def stop_distance(close: Sequence[float], i: int, horizon: int) -> float | None:
    """직전 24개 1h 수익률 σ × √horizon × 2. 진입 시점까지 마감된 봉만 쓴다."""
    if i < 25:
        return None
    steps = [close[k] / close[k - 1] - 1 for k in range(i - 23, i + 1)]
    return STOP_MULTIPLE * statistics.stdev(steps) * math.sqrt(horizon)


def stopped_return(
    side: int, entry: float, stop: float, path: Sequence[tuple[float, float]], exit_price: float
) -> tuple[float, bool]:
    """(부호 반영 비용 전 수익률, 손절 여부). ``path``는 보유 중 1분봉 (고가, 저가)."""
    level = entry * (1 - stop) if side > 0 else entry * (1 + stop)
    for high, low in path:
        if (side > 0 and low <= level) or (side < 0 and high >= level):
            return -stop, True
    return side * (exit_price / entry - 1), False


def stop_applied_sd(
    hours: Hours,
    idx: Sequence[int],
    horizon: int,
    path: Callable[[int, int], list[tuple[float, float]]],
) -> dict[str, float | int]:
    """방향별 손절 적용 거래 수익률의 SD·손절 체결률. 평균은 계산하지 않는다."""
    out: dict[str, float | int] = {}
    stops = []
    for side, name in ((1, "long"), (-1, "short")):
        returns: list[float] = []
        hits = 0
        for i in idx:
            if (
                i + horizon >= len(hours.close)
                or (d := stop_distance(hours.close, i, horizon)) is None
            ):
                continue
            start = hours.open_ms[i] + _HOUR
            value, hit = stopped_return(
                side,
                hours.close[i],
                d,
                path(start, start + horizon * _HOUR),
                hours.close[i + horizon],
            )
            returns.append(value)
            hits += hit
            if side > 0:
                stops.append(d)
        out[f"{name}_sd_bps"] = statistics.stdev(returns) * 1e4
        out[f"{name}_stop_hit_rate"] = hits / len(returns)
        out["n"] = len(returns)
    out["stop_median_bps"] = statistics.median(stops) * 1e4
    out["share_stop_below_min"] = sum(d < MIN_STOP for d in stops) / len(stops)
    return out


def main() -> int:
    hours, first_move, funding = load(DB)
    ranks = abs_move_rank(hours.close)
    atr_rank = atr_percentile(hours)
    baseline_move = statistics.fmean(first_move.values())
    unconditional = {
        f"{h}h": statistics.stdev(
            hours.close[i + h] / hours.close[i] - 1 for i in range(len(hours.close) - h)
        )
        * 1e4
        for h in _HORIZONS
    }
    results = []
    uri = f"file:{DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:

        def path(start_ms: int, end_ms: int) -> list[tuple[float, float]]:
            rows = conn.execute(
                "SELECT high, low FROM candles WHERE exchange = 'binance_usdm'"
                " AND symbol = 'BTCUSDT' AND timeframe = '1m' AND open_ms >= ? AND open_ms < ?"
                " ORDER BY open_ms",
                (start_ms, end_ms),
            ).fetchall()
            return [(float(h), float(lo)) for h, lo in rows]

        for name, state in (("WA", state_wa), ("WB", state_wb)):
            idx = triggers(hours, state, ranks, lock=12)
            measured = measure(name, hours, idx, first_move, atr_rank, funding, baseline_move)
            stopped = stop_applied_sd(hours, idx, 6, path)
            train = round(0.9 * len([i for i in idx if _year(hours.open_ms[i]) in (2023, 2024)]))
            valid = round(0.9 * len([i for i in idx if _year(hours.open_ms[i]) >= 2025]))
            sd = max(float(stopped["long_sd_bps"]), float(stopped["short_sd_bps"]))
            cost = float(measured["cost_bps"])  # type: ignore[arg-type]
            stopped["expected_trades_train(90%)"] = train
            stopped["expected_trades_validation(90%)"] = valid
            stopped["detectable_train_bps"] = detectable_effect_bps(sd, train, cost)
            stopped["detectable_validation_bps"] = detectable_effect_bps(sd, valid, cost)
            stopped["detectable_oos100_bps"] = detectable_effect_bps(sd, 100, cost)
            measured["stop_applied_6h"] = stopped
            results.append(measured)
    summary = {
        "hours": len(hours.close),
        "from": datetime.fromtimestamp(hours.open_ms[0] / 1000, tz=UTC).isoformat(),
        "to": datetime.fromtimestamp(hours.open_ms[-1] / 1000, tz=UTC).isoformat(),
        "baseline_first_minute_move_bps": baseline_move,
        "unconditional_sd_bps": unconditional,
        "candidates": results,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
