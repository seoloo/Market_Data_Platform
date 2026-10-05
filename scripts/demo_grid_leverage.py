"""문서 144 — 보여주기 계산(판정 아님): 영상식 그리드를 선물 레버리지 1·3·5·10 배로.

사용 예::

    python -m scripts.demo_grid_leverage run   # → reports/grid_leverage/result.json

사용자 (2026-10-05): "하루 0.5%" → "그리드 봇 한번 보여줘봐". 137 §10 은 레버리지 없는 그리드였다
(연 약 18% = 하루 약 0.05%). 하루 0.5% 처럼 보이려면 레버리지가 필요하다 → 같은 그리드(137 GA:
추세 필터·손절 없음, 아래 10 칸, 간격 max(0.3%, 0.5σ), 한 칸 위 매도, 빈손이면 위로만 다시 깖)를
Binance USD-M BTCUSDT 1분봉에 레버리지 L 로 돌린다. 설정은 문서 144 에 결과 전 고정.

* 칸당 명목 = L × (깔 때 자본) / 10. 빌린 돈은 현금이 음수가 되는 것으로 나타낸다(현물식 장부,
  자본 = 현금 + 수량 × 가격). L=1 이면 137 ``simulate_grid``(필터·손절 없음)와 똑같다(시험).
* 펀딩: 실제 8 시간 펀딩률 × 보유 명목 (롱은 양수 때 낸다).
* 청산: 그 분 저가에서 자본 ≤ 0.4% × 명목 이면 청산 — 남은 증거금 0, 봇 끝.
  실제 거래소는 마크 가격으로 보므로 꼬리가 긴 분에는 우리가 조금 먼저 청산시킬 수 있다.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from array import array
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.backtest.grid import (
    DAY_MS,
    GridConfig,
    MinuteBars,
    daily_closes,
    hold_trend,
    trend_and_sigma,
)

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
OUT = REPO / "reports" / "grid_leverage" / "result.json"
LEVERAGES = (1.0, 3.0, 5.0, 10.0)
MAKER = 0.0002
"""그리드 칸은 지정가 → Binance USD-M 메이커 0.02%."""
MMR = 0.004
"""BTCUSDT 첫 구간 유지 증거금률 0.4%."""
CAPITAL = 10_000.0
GA = GridConfig(use_trend=False, use_stop=False, grid_fee=MAKER)
FIRST, LAST = "2020-01-01", "2026-08-31"
WINDOW_DAYS = 365
TARGET = 0.005


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


@dataclass(slots=True)
class LevResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    realized_by_day: dict[int, float] = field(default_factory=dict)
    """날마다 끝난 왕복 차익 — 앱 화면의 '오늘 수익' (절대 음수가 아니다)."""
    funding: float = 0.0
    fees: float = 0.0
    round_trips: int = 0
    liquidated_ms: int | None = None
    max_notional: float = 0.0


def simulate_lev_grid(
    bars: MinuteBars,
    sigma: Mapping[int, float],
    funding: Mapping[int, float],
    *,
    from_ms: int,
    to_ms: int,
    initial: float,
    leverage: float,
    cfg: GridConfig = GA,
    mmr: float = MMR,
) -> LevResult:
    """137 GA 그리드를 레버리지 ``leverage`` 로. 날마다 종가 자본, 청산 뒤는 0."""
    n = cfg.levels
    res = LevResult()
    cash = initial
    units = [0.0] * n
    basis = [0.0] * n
    bought_at = [-1] * n
    sold_at = [-1] * n
    level = [0.0] * n
    active = False
    c = s = size = ref = 0.0
    day_cur: int | None = None
    last_price = 0.0
    end = to_ms + DAY_MS
    fund_times = sorted(t for t in funding if from_ms <= t < end)
    fi = 0

    def held() -> float:
        return sum(units)

    def mark(day: int, price: float) -> None:
        res.days.append(day)
        res.equity.append(cash + held() * price)

    def buy(k: int, i: int) -> None:
        nonlocal cash
        available = cash + (leverage - 1) * ref
        notional = min(size, available / (1 + cfg.grid_fee))
        if notional <= 1e-9:
            return
        f = notional * cfg.grid_fee
        cash -= notional + f
        res.fees += f
        units[k] = notional / level[k]
        basis[k] = notional + f
        bought_at[k] = i

    for i, t in enumerate(bars.t):
        if t < from_ms:
            continue
        if t >= end:
            break
        op, hi, lo, cl = bars.o[i], bars.h[i], bars.low[i], bars.c[i]
        while fi < len(fund_times) and fund_times[fi] <= t:
            pay = held() * op * funding[fund_times[fi]]
            cash -= pay
            res.funding += pay
            fi += 1
        day = t - t % DAY_MS
        if day != day_cur:
            if day_cur is not None:
                mark(day_cur, last_price)
            day_cur = day
            if not active and day in sigma:
                s = max(cfg.min_step, cfg.vol_mult * sigma[day])
                c = op
                level = [c * (1 - (k + 1) * s) for k in range(n)]
                ref = cash
                size = leverage * cash / n
                active = True
        if active:
            for k in range(n):
                if units[k] > 0 and bought_at[k] != i:
                    target = level[k] * (1 + s)
                    if hi > target:
                        gross = units[k] * target
                        f = gross * cfg.grid_fee
                        res.fees += f
                        cash += gross - f
                        gain = gross - f - basis[k]
                        res.realized_by_day[day] = res.realized_by_day.get(day, 0.0) + gain
                        res.round_trips += 1
                        units[k] = 0.0
                        basis[k] = 0.0
                        sold_at[k] = i
            for k in range(n):
                if units[k] == 0 and sold_at[k] != i and lo < level[k]:
                    buy(k, i)
            qty = held()
            if qty > 0:
                notional = qty * lo
                res.max_notional = max(res.max_notional, notional)
                if leverage > 1 and cash + notional <= mmr * notional:
                    res.liquidated_ms = t
                    cash = 0.0
                    units = [0.0] * n
                    res.days.append(day)
                    res.equity.append(0.0)
                    break
            if held() == 0 and cl >= c * (1 + s):
                c = cl
                level = [c * (1 - (k + 1) * s) for k in range(n)]
                ref = cash
                size = leverage * cash / n
        last_price = cl
    else:
        if day_cur is not None:
            mark(day_cur, last_price)
    return _fill(res, from_ms - from_ms % DAY_MS, to_ms - to_ms % DAY_MS, initial)


def _fill(res: LevResult, from_day: int, to_day: int, start: float) -> LevResult:
    """빈 날은 전날 값, 청산 뒤는 0 으로 역일 목록을 채운다."""
    by_day = dict(zip(res.days, res.equity, strict=True))
    days, equity = [], []
    last = start
    for d in range(from_day, to_day + 1, DAY_MS):
        if res.liquidated_ms is not None and d > res.liquidated_ms:
            last = 0.0
        else:
            last = by_day.get(d, last)
        days.append(d)
        equity.append(last)
    res.days, res.equity = days, equity
    return res


def load_bars(conn: sqlite3.Connection) -> MinuteBars:
    t, o, h, lo, c = array("q"), array("d"), array("d"), array("d"), array("d")
    rows = conn.execute(
        "select open_ms, open, high, low, close from candles where exchange='binance_usdm'"
        " and symbol='BTCUSDT' and timeframe='1m' order by open_ms"
    )
    for ms, a, b, d, e in rows:
        t.append(ms)
        o.append(float(a))
        h.append(float(b))
        lo.append(float(d))
        c.append(float(e))
    return MinuteBars(t, o, h, lo, c)


def load_funding(conn: sqlite3.Connection) -> dict[int, float]:
    rows = conn.execute(
        "select funding_ms, rate from funding_rates where exchange='binance_usdm'"
        " and symbol='BTCUSDT'"
    )
    return {ms: float(r) for ms, r in rows}


def summarize(res: LevResult, initial: float) -> dict[str, Any]:
    eq = res.equity
    peak, mdd = initial, 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak if peak > 0 else 0.0)
    screen = [res.realized_by_day.get(d, 0.0) / initial for d in res.days]
    real = [b / a - 1 for a, b in zip([initial, *eq[:-1]], eq, strict=True) if a > 0]
    years = len(eq) / 365
    final = eq[-1]
    return {
        "final": round(final, 2),
        "return": round(final / initial - 1, 4),
        "cagr": round((final / initial) ** (1 / years) - 1, 4) if final > 0 else -1.0,
        "mdd": round(mdd, 4),
        "liquidated": _date(res.liquidated_ms) if res.liquidated_ms is not None else None,
        "days_to_liq": (
            (res.liquidated_ms - (res.days[0])) // DAY_MS if res.liquidated_ms is not None else None
        ),
        "screen_daily_mean": round(statistics.fmean(screen), 5),
        "screen_days_ge_target": round(sum(x >= TARGET for x in screen) / len(screen), 3),
        "screen_negative_days": sum(x < 0 for x in screen),
        "real_daily_mean": round(statistics.fmean(real), 5) if real else None,
        "real_negative_share": round(sum(x < 0 for x in real) / len(real), 3) if real else None,
        "worst_real_day": round(min(real), 4) if real else None,
        "screen_total": round(sum(res.realized_by_day.values()), 2),
        "funding": round(res.funding, 2),
        "fees": round(res.fees, 2),
        "round_trips": res.round_trips,
        "max_notional_x": round(res.max_notional / initial, 2),
    }


def windows() -> list[tuple[int, int]]:
    out = []
    y, m = 2020, 1
    last = _ms(LAST)
    while True:
        start = _ms(f"{y}-{m:02d}-01")
        end = start + (WINDOW_DAYS - 1) * DAY_MS
        if end > last:
            break
        out.append((start, end))
        m += 3
        if m > 12:
            y, m = y + 1, m - 12
    return out


def run() -> dict[str, Any]:
    if OUT.exists():
        raise SystemExit(f"이미 계산했다: {OUT}")
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)) as conn:
        bars = load_bars(conn)
        funding = load_funding(conn)
    _, sigma = trend_and_sigma(daily_closes(bars), GA)
    first = min(d for d in sigma if d >= _ms(FIRST))
    cont: dict[str, Any] = {}
    for lev in LEVERAGES:
        r = simulate_lev_grid(
            bars, sigma, funding, from_ms=first, to_ms=_ms(LAST), initial=CAPITAL, leverage=lev
        )
        yearly = {}
        for d, v in zip(r.days, r.equity, strict=True):
            yearly[_date(d)[:4]] = round(v, 2)
        cont[f"{lev:g}x"] = {**summarize(r, CAPITAL), "year_end_equity": yearly}
    bh = hold_trend(bars, None, from_ms=first, to_ms=_ms(LAST), initial=CAPITAL, fee=MAKER)
    cont["BTC_hold"] = summarize(LevResult(days=bh.days, equity=bh.equity), CAPITAL)
    rows: list[dict[str, Any]] = []
    for start, end in windows():
        row: dict[str, Any] = {"start": _date(start)}
        for lev in LEVERAGES:
            r = simulate_lev_grid(
                bars, sigma, funding, from_ms=start, to_ms=end, initial=CAPITAL, leverage=lev
            )
            row[f"{lev:g}x"] = summarize(r, CAPITAL)
        h = hold_trend(bars, None, from_ms=start, to_ms=end, initial=CAPITAL, fee=MAKER)
        row["BTC_hold"] = round(h.equity[-1] / CAPITAL - 1, 4)
        rows.append(row)
        print(row["start"], {k: row[k]["return"] for k in row if k.endswith("x")}, flush=True)
    agg: dict[str, Any] = {}
    for lev in LEVERAGES:
        key = f"{lev:g}x"
        rets = [r[key]["return"] for r in rows]
        liq = [r[key]["days_to_liq"] for r in rows if r[key]["liquidated"]]
        agg[key] = {
            "windows": len(rows),
            "liquidated": len(liq),
            "median_days_to_liq": statistics.median(liq) if liq else None,
            "median_return": round(statistics.median(rets), 4),
            "worst_return": round(min(rets), 4),
            "best_return": round(max(rets), 4),
            "screen_daily_mean_median": round(
                statistics.median(r[key]["screen_daily_mean"] for r in rows), 5
            ),
        }
    result = {
        "continuous": cont,
        "windows_1y": agg,
        "window_rows": rows,
        "config": {
            "leverages": LEVERAGES,
            "maker": MAKER,
            "mmr": MMR,
            "capital": CAPITAL,
            "grid": "137 GA: 10 levels, step max(0.3%, 0.5*sigma20), no trend, no stop",
            "range": [_date(first), LAST],
        },
        "computed": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.demo_grid_leverage run")
    result = run()
    sys.stdout.write(json.dumps(result["windows_1y"], ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
