"""선물 롱숏 후보의 **거래 수·거래당 손익 SD·비용·증명 가능 효과**만 잰다 (17 후보 비교).

수익 평균·방향별 성과는 계산하지 않는다. BTC·ETH 현물 일봉(2017-08~2026-08)을 가격 대리로 쓴다.

* **D 돈치안 돌파 (표준형 55/20)**: 종가가 직전 55일 최고 종가를 넘으면 롱, 최저를 밑돌면
  숏 — 다음 종가 진입. 롱은 종가가 직전 20일 최저 종가를 밑돌면, 숏은 20일 최고를 넘으면
  다음 종가 청산. 자산당 포지션 1개.
* **V 변동성 돌파 (k=0.5, 당일 청산)**: 당일 시가 ± 0.5×전날 고저폭을 고가/저가가 건드리면
  그 가격에 진입, 당일 종가 청산. 같은 날 양방향이 모두 닿으면 순서를 알 수 없어 건너뛴다.
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

DB = Path("data/trading.db")
OUTPUT = Path("reports/phase0/futures_candidates.json")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
_DAY = 86_400_000
COST_D_BPS = 19.0
"""선물 taker 4.5 + 슬리피지 5, 왕복. funding은 별도(보유일수에 비례)."""
COST_V_BPS = 24.0
"""돌파 순간 진입이라 슬리피지 1.5배 가정: 4.5×2 + 5×1.5×2."""


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


def load() -> dict[str, list[tuple[int, float, float, float, float]]]:
    out = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for s in SYMBOLS:
            out[s] = [
                (int(ms), float(o), float(h), float(lo), float(c))
                for ms, o, h, lo, c in conn.execute(
                    "SELECT open_ms, open, high, low, close FROM candles"
                    " WHERE exchange = 'binance_spot' AND symbol = ? AND timeframe = '1d'"
                    " AND open_ms <= ? ORDER BY open_ms",
                    (s, _ms(2026, 8, 31)),
                )
            ]
    return out


def donchian(rows: list[tuple[int, float, float, float, float]]) -> list[tuple[int, float, int]]:
    """(진입일, 부호 반영 수익률, 보유일)."""
    closes = [r[4] for r in rows]
    trades = []
    side = 0
    entry = 0.0
    entry_i = 0
    pending: tuple[str, int] | None = None
    for i in range(56, len(rows)):
        if pending is not None:
            action, direction = pending
            if action == "open":
                side, entry, entry_i = direction, closes[i], i
            else:
                trades.append((rows[entry_i][0], side * (closes[i] / entry - 1), i - entry_i))
                side = 0
            pending = None
            continue
        if side == 0:
            if closes[i] > max(closes[i - 55 : i]):
                pending = ("open", 1)
            elif closes[i] < min(closes[i - 55 : i]):
                pending = ("open", -1)
        elif (side > 0 and closes[i] < min(closes[i - 20 : i])) or (
            side < 0 and closes[i] > max(closes[i - 20 : i])
        ):
            pending = ("close", side)
    return trades


def breakout(rows: list[tuple[int, float, float, float, float]]) -> tuple[list[float], int]:
    trades = []
    ambiguous = 0
    for i in range(1, len(rows)):
        _, o, h, lo, c = rows[i]
        rng = rows[i - 1][2] - rows[i - 1][3]
        up, down = o + 0.5 * rng, o - 0.5 * rng
        hit_up, hit_down = h >= up, lo <= down
        if hit_up and hit_down:
            ambiguous += 1
        elif hit_up:
            trades.append(c / up - 1)
        elif hit_down:
            trades.append(-(c / down - 1))
    return trades, ambiguous


def plan(returns: list[float], years: float, cost: float) -> dict[str, float]:
    sd = statistics.stdev(returns) * 1e4
    n = len(returns)
    return {
        "trades": n,
        "per_year": n / years,
        "trade_sd_bps": sd,
        "cost_bps": cost,
        "detectable_bps_all": detectable_effect_bps(sd, n, cost),
        "detectable_bps_if_half_the_trades": detectable_effect_bps(sd, n // 2, cost),
    }


def main() -> int:
    data = load()
    years = (_ms(2026, 9, 1) - data["BTCUSDT"][0][0]) / _DAY / 365.25
    d_trades = [t for s in SYMBOLS for t in donchian(data[s])]
    v_trades: list[float] = []
    ambiguous = 0
    for s in SYMBOLS:
        trades, amb = breakout(data[s])
        v_trades += trades
        ambiguous += amb
    holding = [h for _, _, h in d_trades]
    summary = {
        "years": years,
        "D_donchian_55_20": {
            **plan([r for _, r, _ in d_trades], years, COST_D_BPS),
            "median_holding_days": statistics.median(holding),
            "mean_holding_days": statistics.fmean(holding),
            "note": "funding: 롱 보유 중 강세장 funding 지급(연 10% 안팎이면 30일 보유당 약 80bps)",
        },
        "V_volatility_breakout_k05": {
            **plan(v_trades, years, COST_V_BPS),
            "ambiguous_days_skipped": ambiguous,
        },
        "unconditional_1d_sd_bps": {
            s: statistics.stdev(
                math.log(b[4] / a[4]) for a, b in zip(data[s], data[s][1:], strict=False)
            )
            * 1e4
            for s in SYMBOLS
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
