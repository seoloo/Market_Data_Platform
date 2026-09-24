"""F1 빠른 추세 롱숏(BTC·ETH 무기한) 후보 비교 측정 — **수익·전략 성과는 계산하지 않는다** (44).

잰 것 (부호·비중·가격 급등 일수만):

1. lookback 세트별 방향(부호 평균)의 롱·숏 비율, 방향 전환 횟수, 회전율과 비용 부담(기준·참고 비용).
2. T/v2 목표와의 관계: F1 이 숏인 날 중 T/v2 가 롱인 날(충돌)·현금인 날(보완).
3. 숏 스퀴즈 꼬리: F1 숏 보유 날 중 무기한 **고가**가 전날 종가 대비 +10·15·20% 이상인 날
   (27 §4 와 같은 방식).
4. 검정력: 기간별 최소 검출 연 샤프.

신호는 현물 일봉 종가(2017-08~), 급등 일수는 무기한 일봉(2020-01~). DB 읽기 전용.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

from auto_trading.backtest.trend import (
    REALISTIC_PERP_COST,
    TrendConfig,
    target_weight,
)

DB = Path("data/trading.db")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
VARIANTS = {
    "F1a (5·10·20)": (5, 10, 20),
    "F1b (10·20·40)": (10, 20, 40),
    "T 참고 (20·60·120)": (20, 60, 120),
}
BASE_PERP_COST = TrendConfig().perp_cost
SQUEEZE = (0.10, 0.15, 0.20)
FROM_MS = int(datetime(2020, 3, 1, tzinfo=UTC).timestamp() * 1000)
"""무기한 구간 판정 시작 (O·42 와 같다)."""
TO_MS = int(datetime(2026, 8, 31, tzinfo=UTC).timestamp() * 1000)
_Z = 2.0 + NormalDist().inv_cdf(0.80)
_DAY = 86_400_000


def _load(conn: sqlite3.Connection, exchange: str, symbol: str) -> dict[int, tuple[float, float]]:
    return {
        int(ms): (float(hi), float(c))
        for ms, hi, c in conn.execute(
            "SELECT open_ms, high, close FROM candles WHERE exchange = ? AND symbol = ?"
            " AND timeframe = '1d' AND open_ms <= ? ORDER BY open_ms",
            (exchange, symbol, TO_MS),
        )
    }


def _weights(
    closes: list[float], lookbacks: tuple[int, ...], *, long_only: bool = False
) -> list[float | None]:
    """크기 = 방향 × min(15%/σ60, 0.5) (T 와 같다). 회전율은 밴드 없이 센다(상한 추정)."""
    cfg = TrendConfig(lookbacks=lookbacks, long_only=long_only)
    return [target_weight(closes[: i + 1], cfg) for i in range(len(closes))]


def main() -> int:
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        spot = {s: _load(conn, "binance_spot", s) for s in SYMBOLS}
        perp = {s: _load(conn, "binance_usdm", s) for s in SYMBOLS}
    years = (TO_MS - FROM_MS + _DAY) / _DAY / 365
    out: dict[str, Any] = {
        "window": "2020-03-01 ~ 2026-08-31 (판정 d, 체결 d+1)",
        "detectable_sharpe_80pct": {
            "perp_2020_03_2026_08": round(_Z / math.sqrt(years), 2),
            "spot_proxy_2017_12_2026_08": round(_Z / math.sqrt(8.7), 2),
        },
        "variants": {},
    }
    t2 = {
        s: _weights([spot[s][d][1] for d in sorted(spot[s])], (20, 60, 120), long_only=True)
        for s in SYMBOLS
    }
    for name, lbs in VARIANTS.items():
        agg: dict[str, Any] = {
            "days": 0,
            "long": 0,
            "short": 0,
            "flips": 0,
            "turnover": 0.0,
            "short_while_t2_long": 0,
            "short_while_t2_cash": 0,
            "squeeze_days": dict.fromkeys(map(str, SQUEEZE), 0),
            "squeeze_dates_20pct": [],
            "max_short_day_rise": 0.0,
        }
        for s in SYMBOLS:
            days = sorted(spot[s])
            closes = [spot[s][d][1] for d in days]
            w = _weights(closes, lbs)
            prev_w, prev_sign = 0.0, 0
            for i, d in enumerate(days):
                fill = d + _DAY  # 종가 d 판정 → 종가 d+1 체결
                if not FROM_MS <= fill <= TO_MS or w[i] is None:
                    continue
                cur = float(w[i] or 0.0)
                agg["days"] += 1
                agg["long"] += cur > 0
                agg["short"] += cur < 0
                sign = (cur > 0) - (cur < 0)
                agg["flips"] += prev_sign != 0 and sign != prev_sign
                agg["turnover"] += abs(cur - prev_w)
                prev_w, prev_sign = cur, sign
                if cur < 0:
                    tw = t2[s][i]
                    agg["short_while_t2_long"] += bool(tw and tw > 0)
                    agg["short_while_t2_cash"] += not (tw and tw > 0)
                    # 숏을 든 날(fill 다음 날) 무기한 고가 / 그날 전날 종가
                    hold, prev = fill + _DAY, fill
                    if hold in perp[s] and prev in perp[s]:
                        rise = perp[s][hold][0] / perp[s][prev][1] - 1
                        agg["max_short_day_rise"] = max(agg["max_short_day_rise"], rise)
                        for q in SQUEEZE:
                            agg["squeeze_days"][str(q)] += rise >= q
                        if rise >= 0.20:
                            day = datetime.fromtimestamp(hold / 1000, UTC).strftime("%Y-%m-%d")
                            agg["squeeze_dates_20pct"].append(f"{s[:3]} {day} +{rise:.1%}")
        agg["short_share"] = round(agg["short"] / agg["days"], 3)
        agg["flips_per_year_per_symbol"] = round(agg["flips"] / years / len(SYMBOLS), 1)
        agg["turnover_per_year"] = round(agg["turnover"] / years, 2)
        agg["cost_burden_pct_per_year"] = {
            "기준 0.095%": round(agg["turnover"] / years * BASE_PERP_COST * 100, 2),
            "참고 0.023%": round(agg["turnover"] / years * REALISTIC_PERP_COST * 100, 2),
        }
        agg["max_short_day_rise"] = round(agg["max_short_day_rise"], 4)
        del agg["turnover"]
        out["variants"][name] = agg
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
