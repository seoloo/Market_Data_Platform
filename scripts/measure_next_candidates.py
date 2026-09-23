"""WA 기각 뒤 다음 후보의 **빈도·잡음·비용 대리치**만 잰다 (09 후보 비교).

수익 평균·방향별 성과는 계산하지 않는다(01·07과 같은 원칙). 선행수익은 부호 없는 SD만 쓴다.
DB는 읽기 전용이다.

* **L 지연 캐스케이드 반전**: 01 후보 B의 트리거(30분 3SD 급변 AND OI 0.5% 감소)를 그대로 쓰고
  진입만 ``delay``분 늦춘다. B는 급변 직후 비용(1분 움직임 3.06배)으로 탈락했다. 늦추면 비용이
  얼마나 내려오고 잡음은 얼마나 남는지만 본다. 방향은 가설이 정한다(급변의 반대).
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from auto_trading.backtest.power import detectable_effect_bps
from auto_trading.backtest.triggers import BARS_PER_HOUR, cascade_triggers, rolling_vol

DB = Path("data/trading.db")
OUTPUT = Path("reports/phase0/next_candidates.json")
_MINUTE = 60_000
_BAR = 5 * _MINUTE
_DELAYS_MIN = (0, 30, 60, 120)
_HORIZONS_H = (2, 6)
_BASE_COST_BPS = 20.0
"""WA TRAIN 실측: 엔진의 비용 전 − 순손익 평균이 주 arm·대조군 모두 약 19~20bps(08 §10)."""
_BASE_SLIPPAGE_BPS = 5.0
"""01의 슬리피지 여유. 진입 1분 움직임 배율이 1을 넘는 만큼만 더한다."""
_G5_PASS = 0.9
"""07과 같은 가정: G5 게이트 등으로 트리거의 약 90%가 거래가 된다."""


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).year


def load(conn: sqlite3.Connection) -> tuple[list[int], list[float], dict[int, float]]:
    rows = conn.execute(
        "SELECT open_ms, close FROM candles WHERE exchange = 'binance_usdm'"
        " AND symbol = 'BTCUSDT' AND timeframe = '5m' ORDER BY open_ms"
    ).fetchall()
    oi = conn.execute(
        "SELECT at_ms, open_interest FROM perp_metrics WHERE exchange = 'binance_usdm'"
        " AND symbol = 'BTCUSDT'"
    ).fetchall()
    return (
        [int(r[0]) for r in rows],
        [float(r[1]) for r in rows],
        {int(ms): float(v) for ms, v in oi if v is not None},
    )


def first_minute_move(conn: sqlite3.Connection, open_ms: int) -> float | None:
    row = conn.execute(
        "SELECT open, close FROM candles WHERE exchange = 'binance_usdm' AND symbol = 'BTCUSDT'"
        " AND timeframe = '1m' AND open_ms = ?",
        (open_ms,),
    ).fetchone()
    return abs(float(row[1]) / float(row[0]) - 1) * 1e4 if row else None


def baseline_move(conn: sqlite3.Connection) -> float:
    """07과 같은 기준: 1h 마감 직후 첫 1분봉의 평균 |움직임|."""
    rows = conn.execute(
        "SELECT open, close FROM candles WHERE exchange = 'binance_usdm' AND symbol = 'BTCUSDT'"
        " AND timeframe = '1m' AND open_ms % 3600000 = 0"
    ).fetchall()
    return statistics.fmean(abs(float(c) / float(o) - 1) * 1e4 for o, c in rows)


def main() -> int:
    uri = f"file:{DB.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        times, closes, oi = load(conn)
        vol = rolling_vol(closes)
        triggers, usable = cascade_triggers(times, closes, vol, oi)
        base = baseline_move(conn)
        years = usable / (BARS_PER_HOUR * 24 * 365.25)
        per_year: dict[int, int] = {}
        for t in triggers:
            per_year[_year(times[t.index])] = per_year.get(_year(times[t.index]), 0) + 1
        delays = {}
        for delay in _DELAYS_MIN:
            # 트리거 5분봉 마감 + delay 분에 시장가 진입. 진입가 = 직전 5분봉 종가.
            step = delay // 5
            moves = [
                m
                for t in triggers
                if (m := first_minute_move(conn, times[t.index] + _BAR + delay * _MINUTE))
                is not None
            ]
            ratio = statistics.fmean(moves) / base
            cost = _BASE_COST_BPS + _BASE_SLIPPAGE_BPS * max(ratio - 1.0, 0.0)
            train = [t for t in triggers if _year(times[t.index]) in (2023, 2024)]
            valid = [t for t in triggers if _year(times[t.index]) >= 2025]
            horizons = {}
            for h in _HORIZONS_H:
                fwd = [
                    closes[j + h * BARS_PER_HOUR] / closes[j] - 1
                    for t in triggers
                    if (j := t.index + step) + h * BARS_PER_HOUR < len(closes)
                ]
                sd = statistics.stdev(fwd) * 1e4
                n_train = round(_G5_PASS * len(train))
                n_valid = round(_G5_PASS * len(valid))
                horizons[f"{h}h"] = {
                    "sd_bps": sd,
                    "expected_trades_train(90%)": n_train,
                    "expected_trades_validation(90%)": n_valid,
                    "detectable_train_bps": detectable_effect_bps(sd, n_train, cost),
                    "detectable_validation_bps": detectable_effect_bps(sd, n_valid, cost),
                    "detectable_oos100_bps": detectable_effect_bps(sd, 100, cost),
                }
            delays[f"{delay}m"] = {
                "first_minute_move_bps": statistics.fmean(moves),
                "first_minute_vs_all": ratio,
                "cost_bps": cost,
                "horizons": horizons,
            }
        unconditional = {
            f"{h}h": statistics.stdev(
                closes[i + h * BARS_PER_HOUR] / closes[i] - 1
                for i in range(0, len(closes) - h * BARS_PER_HOUR, BARS_PER_HOUR)
            )
            * 1e4
            for h in _HORIZONS_H
        }
    summary = {
        "baseline_first_minute_move_bps": base,
        "unconditional_sd_bps": unconditional,
        "L_delayed_cascade": {
            "triggers": len(triggers),
            "per_year": dict(sorted(per_year.items())),
            "oi_years": years,
            "per_oi_year": len(triggers) / years if years else math.nan,
            "delays": delays,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
