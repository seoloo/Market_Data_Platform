"""지표 방향 후보(RSI·MACD·MA·EMA·일목) 비교 측정 — **수익·전략 성과는 계산하지 않는다** (46).

잰 것 (방향·비중·가격 급등 일수만):

1. 후보별 롱·숏·쉼 비율, 방향 변경 횟수, 회전율과 비용 부담(기준·참고 비용). 크기는 T 와 같다:
   방향 × min(15% / σ60, 0.5). 회전율은 밴드 없이 센 **상한**이다.
2. 이미 판정한 추세 신호와 얼마나 같은가: T(20·60·120, PASS — 숏은 0 근처)·F1b(10·20·40, REJECT)
   방향과의 일간 상관, 같은 방향 비율. 후보끼리의 상관.
3. 숏의 성격: 숏인 날 중 T/v2 가 롱(충돌)·현금(보완)인 날,
   숏 보유일의 무기한 고가 +10·15·20% 이상 일수.
4. 진입 횟수·평균 보유일 — 느린 후보·역추세(I2)는 독립 표본 수가 적다.
5. 숏 한 번(연속 숏 구간)의 **최대 역행**: 진입 체결가(무기한 종가) 대비
   보유 중 무기한 고가의 최대 상승. 하루 급등이 아니라 여러 날에 걸친 상승이
   역추세 숏의 꼬리이기 때문이다. 청산 가격을 보지 않으므로 구간 손익은 계산하지 않는다 (위험 측정).
6. 검정력: 후보 1개 / 6개(Holm) 기준 최소 검출 연 샤프.

신호는 현물 일봉(2017-08~), 급등 일수는 무기한 일봉(2020-01~). DB 읽기 전용.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

from auto_trading.backtest.indicator_signals import INDICATOR_RULES, direction_series
from auto_trading.backtest.trend import REALISTIC_PERP_COST, TrendConfig

DB = Path("data/trading.db")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
BASE_PERP_COST = TrendConfig().perp_cost
SQUEEZE = (0.10, 0.15, 0.20)
WINDOWS = {
    "perp": (datetime(2020, 3, 1, tzinfo=UTC), datetime(2026, 8, 31, tzinfo=UTC)),
    "spot_proxy": (datetime(2017, 12, 1, tzinfo=UTC), datetime(2026, 8, 31, tzinfo=UTC)),
}
REFERENCES = {"T (20·60·120)": (20, 60, 120), "F1b (10·20·40)": (10, 20, 40)}
_DAY = 86_400_000
_ALPHA = 0.05


def _ms(d: datetime) -> int:
    return int(d.timestamp() * 1000)


def _load(conn: sqlite3.Connection, exchange: str, symbol: str) -> dict[int, tuple[float, ...]]:
    to_ms = _ms(WINDOWS["perp"][1])
    return {
        int(ms): (float(h), float(lo), float(c))
        for ms, h, lo, c in conn.execute(
            "SELECT open_ms, high, low, close FROM candles WHERE exchange = ? AND symbol = ?"
            " AND timeframe = '1d' AND open_ms <= ? ORDER BY open_ms",
            (exchange, symbol, to_ms),
        )
    }


def _trend_direction(closes: list[float], lookbacks: tuple[int, ...]) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(closes)):
        if i < max(lookbacks):
            out.append(None)
            continue
        signs = [
            math.copysign(1, closes[i] / closes[i - k] - 1) if closes[i] != closes[i - k] else 0
            for k in lookbacks
        ]
        out.append(statistics.fmean(signs))
    return out


def _vol_scale(closes: list[float], cfg: TrendConfig) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(closes)):
        if i < cfg.vol_days:
            out.append(None)
            continue
        steps = [math.log(closes[i - k] / closes[i - k - 1]) for k in range(cfg.vol_days)]
        vol = statistics.stdev(steps) * math.sqrt(365)
        out.append(min(cfg.target_vol / vol, cfg.max_weight) if vol > 0 else 0.0)
    return out


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    return round(statistics.correlation(xs, ys), 2)


def _detectable(years: float, z_alpha: float) -> float:
    return round((z_alpha + NormalDist().inv_cdf(0.80)) / math.sqrt(years), 2)


def _short_excursions(
    fills: list[int], dirs: list[float | None], perp: dict[int, tuple[float, ...]]
) -> list[tuple[int, float]]:
    """연속 숏 구간마다 (진입 체결일, 최대 역행).

    진입가 = 체결일 무기한 종가, 보유 = 다음 날부터 청산 체결일까지.
    """
    out: list[tuple[int, float]] = []
    n = 0
    while n < len(dirs):
        dv = dirs[n]
        if dv is None or dv >= 0:
            n += 1
            continue
        start = n
        while n < len(dirs) and dirs[n] is not None and dirs[n] < 0:  # type: ignore[operator]
            n += 1
        entry = fills[start]
        exit_fill = fills[n] if n < len(fills) else fills[-1]
        if entry not in perp:
            continue
        highs = [perp[d][0] for d in range(entry + _DAY, exit_fill + _DAY, _DAY) if d in perp]
        if highs:
            out.append((entry, max(highs) / perp[entry][2] - 1))
    return out


def measure(
    window: str,
    spot: dict[str, dict[int, tuple[float, ...]]],
    perp: dict[str, dict[int, tuple[float, ...]]],
) -> dict[str, Any]:
    start, end = (_ms(d) for d in WINDOWS[window])
    years = (end - start + _DAY) / _DAY / 365
    cfg = TrendConfig()
    series: dict[
        str, dict[str, list[float]]
    ] = {}  # 이름 → 종목 → 창 안 방향 (상관용, 모두 정의된 날만)
    rows: dict[str, dict[str, Any]] = {}
    per_symbol: dict[str, dict[str, list[float | None]]] = {}
    fills: dict[str, list[int]] = {}
    for s in SYMBOLS:
        days = sorted(spot[s])
        closes = [spot[s][d][2] for d in days]
        highs = [spot[s][d][0] for d in days]
        lows = [spot[s][d][1] for d in days]
        dirs: dict[str, list[float | None]] = {
            r.key: [
                None if x is None else float(x) for x in direction_series(r, closes, highs, lows)
            ]
            for r in INDICATOR_RULES
        }
        for name, lbs in REFERENCES.items():
            dirs[name] = _trend_direction(closes, lbs)
        t2 = [None if x is None else max(x, 0.0) for x in dirs["T (20·60·120)"]]
        scale = _vol_scale(closes, cfg)
        idx = [i for i, d in enumerate(days) if start <= d + _DAY <= end]  # 종가 d 판정 → d+1 체결
        fills[s] = [days[i] + _DAY for i in idx]
        per_symbol[s] = {k: [v[i] for i in idx] for k, v in dirs.items()}
        per_symbol[s]["_scale"] = [scale[i] for i in idx]
        per_symbol[s]["_t2"] = [t2[i] for i in idx]
    names = [r.key for r in INDICATOR_RULES] + list(REFERENCES)
    for key in names:
        agg: dict[str, Any] = {
            "days": 0,
            "long": 0,
            "short": 0,
            "flat": 0,
            "changes": 0,
            "turnover": 0.0,
            "entries": 0,
            "hold_days": 0,
            "short_while_t2_long": 0,
            "short_while_t2_cash": 0,
            "squeeze_days": dict.fromkeys(map(str, SQUEEZE), 0),
            "squeeze_dates_20pct": [],
        }
        for s in SYMBOLS:
            d_list, sc_list, t2_list = (
                per_symbol[s][key],
                per_symbol[s]["_scale"],
                per_symbol[s]["_t2"],
            )
            prev_dir, prev_w = None, 0.0
            for n, (dv, sc, tv) in enumerate(zip(d_list, sc_list, t2_list, strict=True)):
                if dv is None or sc is None:
                    continue
                agg["days"] += 1
                agg["long"] += dv > 0
                agg["short"] += dv < 0
                agg["flat"] += dv == 0
                if prev_dir is not None and dv != prev_dir:
                    agg["changes"] += 1
                if dv != 0 and (
                    prev_dir is None
                    or prev_dir == 0
                    or math.copysign(1, dv) != math.copysign(1, prev_dir)
                ):
                    agg["entries"] += 1
                agg["hold_days"] += dv != 0
                w = dv * sc
                agg["turnover"] += abs(w - prev_w)
                prev_dir, prev_w = dv, w
                if dv < 0:
                    agg["short_while_t2_long"] += bool(tv and tv > 0)
                    agg["short_while_t2_cash"] += not (tv and tv > 0)
                    fill = fills[s][n]
                    hold, prev = fill + _DAY, fill
                    if hold in perp[s] and prev in perp[s]:
                        rise = perp[s][hold][0] / perp[s][prev][2] - 1
                        for q in SQUEEZE:
                            agg["squeeze_days"][str(q)] += rise >= q
                        if rise >= 0.20:
                            day = datetime.fromtimestamp(hold / 1000, UTC).strftime("%Y-%m-%d")
                            agg["squeeze_dates_20pct"].append(f"{s[:3]} {day} +{rise:.0%}")
        if window == "perp":
            exc = [
                (s[:3], ms, x)
                for s in SYMBOLS
                for ms, x in _short_excursions(fills[s], per_symbol[s][key], perp[s])
            ]
            exc.sort(key=lambda t: -t[2])
            agg["short_excursion"] = {
                "episodes": len(exc),
                ">=20%": sum(x >= 0.20 for *_, x in exc),
                ">=30%": sum(x >= 0.30 for *_, x in exc),
                ">=50%": sum(x >= 0.50 for *_, x in exc),
                "worst3": [
                    f"{sym} {datetime.fromtimestamp(ms / 1000, UTC):%Y-%m-%d} +{x:.0%}"
                    for sym, ms, x in exc[:3]
                ],
            }
        n_days = agg["days"] or 1
        row: dict[str, Any] = {
            "long_share": round(agg["long"] / n_days, 3),
            "short_share": round(agg["short"] / n_days, 3),
            "flat_share": round(agg["flat"] / n_days, 3),
            "changes_per_year_per_symbol": round(agg["changes"] / years / len(SYMBOLS), 1),
            "entries_per_year_per_symbol": round(agg["entries"] / years / len(SYMBOLS), 1),
            "avg_hold_days": round(agg["hold_days"] / agg["entries"], 1)
            if agg["entries"]
            else None,
            "turnover_per_year": round(agg["turnover"] / years, 1),
            "cost_burden_pct_per_year": {
                "기준 0.095%": round(agg["turnover"] / years * BASE_PERP_COST * 100, 2),
                "참고 0.023%": round(agg["turnover"] / years * REALISTIC_PERP_COST * 100, 2),
            },
            "short_days": agg["short"],
            "short_while_t2_long": agg["short_while_t2_long"],
            "short_while_t2_cash": agg["short_while_t2_cash"],
        }
        if window == "perp":
            row["squeeze_days"] = agg["squeeze_days"]
            row["squeeze_dates_20pct"] = agg["squeeze_dates_20pct"]
            row["short_excursion"] = agg["short_excursion"]
        rows[key] = row
    # 상관 · 같은 방향 비율 — 모든 이름이 정의된 날만, 두 종목을 이어 붙인다
    for key in names:
        series[key] = {"all": []}
    for s in SYMBOLS:
        for n in range(len(per_symbol[s]["_scale"])):
            vals = [per_symbol[s][k][n] for k in names]
            if any(v is None for v in vals):
                continue
            for k, v in zip(names, vals, strict=True):
                series[k]["all"].append(float(v or 0.0))
    corr = {a: {b: _corr(series[a]["all"], series[b]["all"]) for b in names} for a in names}
    agree: dict[str, dict[str, float | None]] = {}
    for a in [r.key for r in INDICATOR_RULES]:
        agree[a] = {}
        for ref in REFERENCES:
            pairs = [
                (x, y)
                for x, y in zip(series[a]["all"], series[ref]["all"], strict=True)
                if x != 0 and y != 0
            ]
            same = sum((x > 0) == (y > 0) for x, y in pairs)
            agree[a][ref] = round(same / len(pairs), 3) if pairs else None
    z1 = NormalDist().inv_cdf(1 - _ALPHA)
    z6 = NormalDist().inv_cdf(1 - _ALPHA / len(INDICATOR_RULES))
    return {
        "window": f"{WINDOWS[window][0]:%Y-%m-%d} ~ {WINDOWS[window][1]:%Y-%m-%d}"
        " (판정 d, 체결 d+1)",
        "years": round(years, 2),
        "common_days_for_corr": len(series[names[0]]["all"]),
        "detectable_sharpe_80pct": {
            "후보 1개 (단측 5%)": _detectable(years, z1),
            "후보 6개 Holm 최소 p (단측 5%/6)": _detectable(years, z6),
            "현행 판정식 평균−2SE>0": _detectable(years, 2.0),
        },
        "candidates": rows,
        "corr": corr,
        "same_direction_when_both_active": agree,
    }


def main() -> int:
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        spot = {s: _load(conn, "binance_spot", s) for s in SYMBOLS}
        perp = {s: _load(conn, "binance_usdm", s) for s in SYMBOLS}
    out = {
        "rules": {r.key: f"{r.family} — {r.label}" for r in INDICATOR_RULES},
        "perp": measure("perp", spot, perp),
        "spot_proxy": measure("spot_proxy", spot, perp),
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
