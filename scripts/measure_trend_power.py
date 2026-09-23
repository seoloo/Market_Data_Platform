"""추세 추종(시계열 모멘텀) 검정력 검토 — **수익 평균·전략 성과는 계산하지 않는다** (14).

잰 것:

1. 데이터 길이별로 증명 가능한 최소 연 샤프 비율 (z=2, 검정력 80%). 연속 보유 포트폴리오는
   거래 수가 아니라 **관측 기간**이 표본이다.
2. 월별 유동성 상위 20 선물 종목의 일간 수익률 평균 쌍별 상관 → 유효 독립 종목 수
   ``N_eff = N / (1 + (N−1)ρ)``. 종목을 늘려 얻는 분산 효과의 상한.
3. lookback L(20·60·120일)별 추세 부호의 전환 빈도(→ 회전율·비용)와 종목 간 부호 일치율.
   부호만 보고 그 뒤 수익은 보지 않는다.

입력은 선물 1일봉(`binance_usdm`, 폐지 포함)과 현물 1일봉 가용 기간이다. DB는 읽기 전용.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from statistics import NormalDist

from scripts.build_universe import NON_CRYPTO_PREFIXES
from scripts.m_data import candidates

DB = Path("data/trading.db")
OUTPUT = Path("reports/phase0/trend_power.json")
TOP = 20
LOOKBACKS = (20, 60, 120)
CORR_DAYS = 90
FEE_SLIP_BPS = 9.5
"""선물 taker 4.5 + 슬리피지 5 (편도, 명목 대비). 부호 전환은 명목 2배를 거래한다."""
_DAY = 86_400_000
_Z = 2.0 + NormalDist().inv_cdf(0.80)


def detectable_sharpe(years: float) -> float:
    """일간 수익이 독립일 때 `평균 − 2SE > 0`을 80% 확률로 통과시키는 최소 연 샤프."""
    return _Z / math.sqrt(years)


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).year


def main() -> int:
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        cands = candidates(conn)
        closes: dict[str, dict[int, float]] = defaultdict(dict)
        for symbol, ms, close in conn.execute(
            "SELECT symbol, open_ms, close FROM candles"
            " WHERE exchange = 'binance_usdm' AND timeframe = '1d'"
        ):
            closes[symbol][int(ms)] = float(close)
        spot_first = dict(
            conn.execute(
                "SELECT symbol, MIN(open_ms) FROM candles WHERE exchange = 'binance_spot'"
                " GROUP BY symbol"
            ).fetchall()
        )
    universe = {
        start: [s for s in symbols if not s.startswith(NON_CRYPTO_PREFIXES)][:TOP]
        for start, symbols in cands.items()
    }

    def ret(symbol: str, day: int) -> float | None:
        a, b = closes[symbol].get(day - _DAY), closes[symbol].get(day)
        return math.log(b / a) if a and b else None

    # 2. 월별 상관 (월 시작 직전 90일, 그 달 유니버스)
    corr_by_year: dict[int, list[float]] = defaultdict(list)
    for start, members in universe.items():
        if len(members) < TOP:
            continue
        days = [start - k * _DAY for k in range(1, CORR_DAYS + 1)]
        series = {}
        for symbol in members:
            values = [ret(symbol, d) for d in days]
            if all(v is not None for v in values):
                series[symbol] = values
        pairs = []
        for a, b in combinations(series, 2):
            try:
                pairs.append(statistics.correlation(series[a], series[b]))  # type: ignore[arg-type]
            except statistics.StatisticsError:
                continue
        if pairs:
            corr_by_year[_year(start)].append(statistics.fmean(pairs))
    correlation = {}
    for year, values in sorted(corr_by_year.items()):
        rho = statistics.fmean(values)
        correlation[str(year)] = {
            "mean_pairwise_rho": rho,
            "n_eff_of_20": TOP / (1 + (TOP - 1) * rho),
        }

    # 3. 추세 부호: 전환 빈도, 종목 간 일치율
    signals: dict[str, object] = {}
    for lookback in LOOKBACKS:
        flips = symbol_days = 0
        agreement: list[float] = []
        unanimous = days_counted = 0
        for start, members in universe.items():
            if len(members) < TOP:
                continue
            for k in range(0, 31):
                day = start + k * _DAY
                if _year(day) != _year(start) and k > 27:
                    break
                signs = {}
                for symbol in members:
                    now, then = closes[symbol].get(day), closes[symbol].get(day - lookback * _DAY)
                    prev = closes[symbol].get(day - _DAY)
                    prev_then = closes[symbol].get(day - (lookback + 1) * _DAY)
                    if now and then and prev and prev_then:
                        sign = 1 if now > then else -1
                        prev_sign = 1 if prev > prev_then else -1
                        signs[symbol] = sign
                        flips += sign != prev_sign
                        symbol_days += 1
                if len(signs) >= 2:
                    values = list(signs.values())
                    share = max(values.count(1), values.count(-1)) / len(values)
                    agreement.append(share)
                    unanimous += share >= 0.8
                    days_counted += 1
        signals[f"L{lookback}"] = {
            "flips_per_symbol_year": flips / symbol_days * 365 if symbol_days else math.nan,
            "annual_cost_pct_per_unit_exposure": (
                flips / symbol_days * 365 * 2 * FEE_SLIP_BPS / 100 if symbol_days else math.nan
            ),
            "mean_majority_share": statistics.fmean(agreement) if agreement else math.nan,
            "share_days_80pct_same_sign": unanimous / days_counted if days_counted else math.nan,
        }

    # 1. 데이터 길이
    btc_perp_first = min(closes["BTCUSDT"])
    lengths = {
        "TRAIN 2년": 2.0,
        "3년": 3.0,
        "4년": 4.0,
        "선물 BTC 전체 2019-09~2026-08": (_ms(2026, 9, 1) - btc_perp_first) / _DAY / 365.25,
        "현물 BTC 2017-08~2026-08": (_ms(2026, 9, 1) - _ms(2017, 8, 17)) / _DAY / 365.25,
    }
    summary = {
        "detectable_annual_sharpe": {
            k: [round(v, 2), detectable_sharpe(v)] for k, v in lengths.items()
        },
        "correlation_by_year": correlation,
        "signals": signals,
        "spot_first_bar_in_db": {
            k: v for k, v in spot_first.items() if k in {"BTCUSDT", "ETHUSDT"}
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
