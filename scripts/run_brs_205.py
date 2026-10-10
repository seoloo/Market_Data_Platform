"""문서 205 — Breakdown-Retest Short v1 (BTC · ETH USDT 무기한).

명령::

    python -m scripts.run_brs_205 data    # 15 분봉 만들기 · 품질 검사 · 해시 (수익 없음)
    python -m scripts.run_brs_205 count   # 붕괴 사건 · 진입 수만 (검정력 관문, 수익 없음)
    python -m scripts.run_brs_205 run     # 사전 등록 뒤 한 번 — reports/brs_205/

자료: BTC 15 분봉 = trading.db ``binance_usdm`` 15m, ETH 15 분봉 = Binance 공개 1 분봉 zip 을 묶음.
funding = trading.db ``funding_rates`` (정산 시각을 15 분 아래로 내림). 1 분봉(BTC DB · ETH zip)은
같은 봉 손절 · 익절 순서 확인(보고만)에 쓴다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sqlite3
import statistics
import sys
import zipfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.backtest.breakdown_retest import (
    DAY_MS,
    DEFAULT,
    MIN15_MS,
    Breakdown,
    Config,
    Run,
    Trade,
    breakdowns,
    daily_equity,
    drawdowns,
    max_streak,
    resample,
    simulate,
    summary,
    with_costs,
)
from auto_trading.backtest.charts import write_histogram, write_line_chart
from auto_trading.backtest.ema_box import Bars
from auto_trading.backtest.indicator_signals import ema

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
ZIPS = REPO / "data" / "external" / "binance_vision" / "um"
CACHE = REPO / "data" / "research" / "brs_205"
OUT = REPO / "reports" / "brs_205"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
START = "2020-01-01"
END = "2026-08-31"
SPLITS = {
    "TRAIN": ("2020-01-01", "2023-12-31"),
    "VALIDATION": ("2024-01-01", "2024-12-31"),
    "OOS": ("2025-01-01", "2026-08-31"),
}
COST_USER = 0.0006  # 사용자 기본: 수수료 0.04% + 미끄럼 0.02%
COST_PROJECT = 0.00095  # 프로젝트 판정 기본 (선물 taker 보수)
COST_REALISTIC = 0.00023  # 참고 줄만
BALANCE = 10_000.0
GRID = {
    "lookback": (15, 20, 25, 30),
    "atr_mult": (1.2, 1.5, 1.8, 2.0),
    "rr": (1.5, 2.0, 2.5, 3.0),
    "vol_mult": (1.0, 1.2, 1.5),
}


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _stamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


FROM_MS, TO_MS = _ms(START), _ms(END) + DAY_MS - MIN15_MS


# ------------------------------------------------------------------ 자료


def _db() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)


def _zip_rows(symbol: str, interval: str, month: str) -> list[list[str]]:
    path = ZIPS / symbol / interval / f"{symbol}-{interval}-{month}.zip"
    with zipfile.ZipFile(path) as z:
        text = z.read(z.namelist()[0]).decode("ascii")
    return [r for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]


def _months() -> list[str]:
    y, m = 2020, 1
    out = []
    while (y, m) <= (2026, 8):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _from_1m(rows: Sequence[Sequence[str]]) -> list[tuple[int, float, float, float, float, float]]:
    """1 분봉 → 15 분봉 (15 개가 다 있는 묶음만)."""
    groups: dict[int, list[Sequence[str]]] = {}
    for r in rows:
        t = int(r[0])
        groups.setdefault(t - t % MIN15_MS, []).append(r)
    out = []
    for t, g in sorted(groups.items()):
        if len(g) != 15:
            continue
        g = sorted(g, key=lambda r: int(r[0]))
        out.append(
            (
                t,
                float(g[0][1]),
                max(float(r[2]) for r in g),
                min(float(r[3]) for r in g),
                float(g[-1][4]),
                sum(float(r[5]) for r in g),
            )
        )
    return out


def build() -> dict[str, Any]:
    """15 분봉 CSV 를 만들고 품질을 잰다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    with _db() as con:
        btc = [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in con.execute(
                "select open_ms, open, high, low, close, volume from candles where "
                "exchange='binance_usdm' and symbol='BTCUSDT' and timeframe='15m' "
                "and open_ms between ? and ? order by open_ms",
                (FROM_MS, TO_MS),
            )
        ]
    eth: list[tuple[int, float, float, float, float, float]] = []
    btc_check: list[tuple[int, float, float, float, float, float]] = []
    for month in _months():
        eth += _from_1m(_zip_rows("ETHUSDT", "1m", month))
    # BTC 대조: 1 분봉(DB) → 15 분봉 을 몇 달 골라 DB 15 분봉과 비교
    with _db() as con:
        for month in ("2020-03", "2022-06", "2024-08", "2026-08"):
            lo = _ms(month + "-01")
            hi = lo + 31 * DAY_MS
            raw = [
                [str(r[0]), *map(str, r[1:])]
                for r in con.execute(
                    "select open_ms, open, high, low, close, volume from candles where "
                    "exchange='binance_usdm' and symbol='BTCUSDT' and timeframe='1m' "
                    "and open_ms >= ? and open_ms < ? order by open_ms",
                    (lo, hi),
                )
            ]
            btc_check += [x for x in _from_1m(raw) if _day(x[0])[:7] == month]
    by_t = {x[0]: x for x in btc}
    diffs = [
        max(abs(a - b) / b for a, b in zip(x[1:5], by_t[x[0]][1:5], strict=True))
        for x in btc_check
        if x[0] in by_t
    ]
    report["btc_15m_vs_1m"] = {
        "bars_compared": len(diffs),
        "max_rel_diff": max(diffs) if diffs else None,
    }
    for sym, rows in (("BTCUSDT", btc), ("ETHUSDT", eth)):
        path = CACHE / f"{sym}_15m.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["open_ms", "open", "high", "low", "close", "volume"])
            w.writerows(rows)
        expected = (TO_MS - FROM_MS) // MIN15_MS + 1
        gaps = [
            (rows[k - 1][0], rows[k][0])
            for k in range(1, len(rows))
            if rows[k][0] - rows[k - 1][0] != MIN15_MS
        ]
        bad = sum(1 for r in rows if not (r[3] <= min(r[1], r[4]) <= max(r[1], r[4]) <= r[2]))
        report[sym] = {
            "rows": len(rows),
            "expected": expected,
            "first": _stamp(rows[0][0]),
            "last": _stamp(rows[-1][0]),
            "gaps": len(gaps),
            "missing_bars": sum((b - a) // MIN15_MS - 1 for a, b in gaps),
            "longest_gap_bars": max(((b - a) // MIN15_MS - 1 for a, b in gaps), default=0),
            "gap_list": [[_stamp(a), _stamp(b)] for a, b in gaps[:20]],
            "ohlc_inconsistent": bad,
            "zero_volume": sum(1 for r in rows if r[5] <= 0),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    with _db() as con:
        for sym in SYMBOLS:
            f = list(
                con.execute(
                    "select funding_ms, rate from funding_rates where symbol=? and "
                    "funding_ms between ? and ? order by funding_ms",
                    (sym, FROM_MS, TO_MS + MIN15_MS),
                )
            )
            report[sym]["funding"] = {
                "rows": len(f),
                "first": _stamp(f[0][0]),
                "last": _stamp(f[-1][0]),
                "positive_share": round(sum(float(r[1]) > 0 for r in f) / len(f), 3),
            }
    return report


def load(sym: str) -> Bars:
    path = CACHE / f"{sym}_15m.csv"
    t: list[int] = []
    o: list[float] = []
    h: list[float] = []
    low: list[float] = []
    c: list[float] = []
    v: list[float] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t.append(int(r["open_ms"]))
            o.append(float(r["open"]))
            h.append(float(r["high"]))
            low.append(float(r["low"]))
            c.append(float(r["close"]))
            v.append(float(r["volume"]))
    return Bars(t, o, h, low, c, v)


def funding(sym: str) -> dict[int, float]:
    with _db() as con:
        return {
            int(ms) - int(ms) % MIN15_MS: float(rate)
            for ms, rate in con.execute(
                "select funding_ms, rate from funding_rates where symbol=?", (sym,)
            )
        }


def resolver(sym: str) -> Callable[[int, float, float], str | None]:
    """같은 15 분봉에서 손절 · 익절이 다 닿았을 때 1 분봉으로 먼저 닿은 쪽.

    같은 1 분봉에서 둘 다 닿거나 1 분봉이 없으면 None (손절로 둔다)."""

    @cache
    def month_rows(month: str) -> dict[int, tuple[float, float]]:
        if sym == "BTCUSDT":
            lo = _ms(month + "-01")
            with _db() as con:
                return {
                    int(r[0]): (float(r[1]), float(r[2]))
                    for r in con.execute(
                        "select open_ms, high, low from candles where exchange='binance_usdm' "
                        "and symbol='BTCUSDT' and timeframe='1m' and open_ms >= ? and "
                        "open_ms < ?",
                        (lo, lo + 32 * DAY_MS),
                    )
                }
        return {int(r[0]): (float(r[2]), float(r[3])) for r in _zip_rows(sym, "1m", month)}

    def judge(t: int, stop: float, tp: float) -> str | None:
        rows = month_rows(_day(t)[:7])
        for k in range(15):
            bar = rows.get(t + k * 60_000)
            if bar is None:
                return None
            hi, lo = bar
            if hi >= stop and lo < tp:
                return None
            if hi >= stop:
                return "stop"
            if lo < tp:
                return "take_profit"
        return None

    return judge


# ------------------------------------------------------------------ 계산


def frames(sym: str, cfg: Config = DEFAULT) -> tuple[Bars, list[Breakdown]]:
    b = load(sym)
    return b, breakdowns(resample(b, 60), resample(b, 240), cfg)


def count() -> dict[str, Any]:
    """검정력 관문 — 사건 · 진입 수만 (손익 열람 없음)."""
    out: dict[str, Any] = {}
    for sym in SYMBOLS:
        b, ev = frames(sym)
        row: dict[str, Any] = {}
        for name, (lo, hi) in SPLITS.items():
            run = simulate(
                sym,
                b,
                ev,
                with_costs(DEFAULT, COST_USER),
                from_ms=_ms(lo),
                to_ms=_ms(hi) + DAY_MS - MIN15_MS,
            )
            row[name] = {
                "breakdowns": run.breakdowns,
                "busy": run.busy,
                "replaced": run.replaced,
                "expired": run.expired,
                "entries": len(run.trades),
                "capped_entries": sum(tr.capped for tr in run.trades),
                "stop_pct_median": round(
                    statistics.median(
                        (tr.stop_price / tr.entry_price - 1) * 100 for tr in run.trades
                    ),
                    3,
                )
                if run.trades
                else None,
            }
        out[sym] = row
    return out


def _period(trades: Sequence[Trade], lo: str, hi: str) -> list[Trade]:
    a, z = _ms(lo), _ms(hi) + DAY_MS
    return [tr for tr in trades if a <= tr.entry_ms < z]


def r_stats(trades: Sequence[Trade]) -> dict[str, float | int | None]:
    rs = [tr.r_multiple for tr in trades]
    n = len(rs)
    if n < 2:
        return {"n": n, "mean_r": statistics.fmean(rs) if rs else None, "t": None}
    m, sd = statistics.fmean(rs), statistics.stdev(rs)
    gl = -sum(tr.net_pnl for tr in trades if tr.net_pnl <= 0)
    gp = sum(tr.net_pnl for tr in trades if tr.net_pnl > 0)
    return {
        "n": n,
        "mean_r": round(m, 4),
        "sd_r": round(sd, 3),
        "t": round(m / (sd / math.sqrt(n)), 2) if sd > 0 else None,
        "ci95": [round(m - 1.96 * sd / math.sqrt(n), 4), round(m + 1.96 * sd / math.sqrt(n), 4)],
        "win_rate": round(sum(tr.net_pnl > 0 for tr in trades) / n, 3),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
    }


def _round(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items()}


def regimes_daily(b: Bars) -> dict[int, str]:
    """일봉 국면(인과, 전날 마감): 종가 vs EMA200 · EMA200 20 일 기울기 → 상승 · 하락 · 횡보."""
    d = resample(b, 24 * 60)
    e = ema(d.c, 200)
    out: dict[int, str] = {}
    for k in range(len(d.t)):
        e0 = e[k]
        e20 = e[k - 20] if k >= 20 else None
        if e0 is None or e20 is None:
            continue
        lab = (
            "bull"
            if d.c[k] > e0 and e0 > e20
            else "bear"
            if d.c[k] < e0 and e0 < e20
            else "sideways"
        )
        out[d.t[k] + DAY_MS] = lab  # 그다음 날부터 쓸 수 있다
    return out


def regimes_monthly(b: Bars) -> dict[str, str]:
    """사후 달 수익(자산 자신): > +10% 상승 · < −10% 하락 · 나머지 횡보 (설명용)."""
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for t, o, c in zip(b.t, b.o, b.c, strict=True):
        m = _day(t)[:7]
        first.setdefault(m, o)
        last[m] = c
    return {
        m: "bull"
        if last[m] / first[m] - 1 > 0.10
        else "bear"
        if last[m] / first[m] - 1 < -0.10
        else "sideways"
        for m in first
    }


def _group(trades: Sequence[Trade], key: Callable[[Trade], str]) -> dict[str, Any]:
    by: dict[str, list[Trade]] = {}
    for tr in trades:
        by.setdefault(key(tr), []).append(tr)
    out = {}
    for k, g in sorted(by.items()):
        net = sum(tr.net_pnl for tr in g)
        out[k] = {**r_stats(g), "net_pnl": round(net, 2)}
    return out


def yearly(trades: Sequence[Trade], curve: Sequence[tuple[int, float]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for y in range(2020, 2027):
        lo, hi = f"{y}-01-01", f"{y}-12-31"
        g = _period(trades, lo, hi)
        pts = [(t, v) for t, v in curve if _day(t)[:4] == str(y)]
        if not pts:
            continue
        prev = [v for t, v in curve if t < pts[0][0]]
        start = prev[-1] if prev else BALANCE
        peak, mdd = start, 0.0
        for _, v in pts:
            peak = max(peak, v)
            mdd = max(mdd, 1 - v / peak)
        out[str(y)] = {
            **r_stats(g),
            "net_return": round(pts[-1][1] / start - 1, 4),
            "mdd": round(mdd, 4),
        }
    return out


def dd_detail(
    trades: Sequence[Trade], curve: Sequence[tuple[int, float]], reg: dict[int, str]
) -> list[dict[str, Any]]:
    out = []
    for d in drawdowns(curve, 10):
        end = d.recovery_ms if d.recovery_ms is not None else curve[-1][0]
        g = [tr for tr in trades if d.peak_ms <= tr.exit_ms <= d.trough_ms + DAY_MS]
        out.append(
            {
                "peak": _day(d.peak_ms),
                "trough": _day(d.trough_ms),
                "recovery": _day(d.recovery_ms) if d.recovery_ms is not None else None,
                "depth": round(d.depth, 4),
                "days_to_trough": (d.trough_ms - d.peak_ms) // DAY_MS,
                "days_underwater": (end - d.peak_ms) // DAY_MS,
                "trades_to_trough": len(g),
                "losses_to_trough": sum(tr.net_pnl <= 0 for tr in g),
                "exit_reasons": dict(Counter(tr.exit_reason for tr in g)),
                "regimes": dict(Counter(_regime_at(reg, tr.entry_ms) for tr in g)),
            }
        )
    return out


def _regime_at(reg: dict[int, str], ms: int) -> str:
    day = ms - ms % DAY_MS
    return reg.get(day, "warmup")


def streaks(trades: Sequence[Trade]) -> dict[str, Any]:
    worst = cur = 0.0
    run_start = 0
    worst_span: tuple[int, int] = (0, 0)
    k_cur = 0
    lens: list[int] = []
    for k, tr in enumerate(trades):
        if tr.net_pnl <= 0:
            if k_cur == 0:
                run_start = k
                cur = 1.0
            k_cur += 1
            cur *= tr.balance_after_trade / (tr.balance_after_trade - tr.net_pnl)
            if 1 - cur > worst:
                worst, worst_span = 1 - cur, (run_start, k)
        else:
            if k_cur:
                lens.append(k_cur)
            k_cur = 0
    if k_cur:
        lens.append(k_cur)
    losers = [tr.r_multiple for tr in trades if tr.net_pnl <= 0]
    avg_loss_r = statistics.fmean(losers) if losers else -1.0
    return {
        "max_consecutive_losses": max_streak(trades, wins=False),
        "worst_losing_run_drawdown": round(worst, 4),
        "worst_run": [_stamp(trades[worst_span[0]].entry_ms), _stamp(trades[worst_span[1]].exit_ms)]
        if trades
        else None,
        "losing_run_lengths": dict(sorted(Counter(lens).items())),
        "avg_loss_r": round(avg_loss_r, 3),
        "hypothetical_loss_after": {
            str(k): round(1 - (1 + 0.005 * avg_loss_r) ** k, 4) for k in (8, 10, 12, 15, 20)
        },
    }


def r_hist(trades: Sequence[Trade]) -> dict[str, Any]:
    edges = [-10.0, -1.5, -1.2, -1.05, -0.95, -0.5, 0.0, 0.5, 1.0, 1.5, 1.8, 2.2, 10.0]
    rs = [tr.r_multiple for tr in trades]
    bins = {}
    for a, z in pairwise(edges):
        bins[f"[{a}, {z})"] = sum(1 for r in rs if a <= r < z)
    gross_r = [tr.gross_pnl / tr.risk_amount for tr in trades]
    cost_r = [(tr.entry_fee + tr.exit_fee) / tr.risk_amount for tr in trades]
    return {
        "bins": bins,
        "quantiles_net_r": [round(q, 3) for q in statistics.quantiles(rs, n=10)]
        if len(rs) > 10
        else None,
        "mean_gross_r": round(statistics.fmean(gross_r), 4) if trades else None,
        "mean_fee_r": round(statistics.fmean(cost_r), 4) if trades else None,
        "mean_funding_r": round(
            statistics.fmean(tr.funding_fee / tr.risk_amount for tr in trades), 4
        )
        if trades
        else None,
        "by_exit": {k: len(v) for k, v in _by_exit(trades).items()},
    }


def _by_exit(trades: Sequence[Trade]) -> dict[str, list[Trade]]:
    out: dict[str, list[Trade]] = {}
    for tr in trades:
        out.setdefault(tr.exit_reason, []).append(tr)
    return out


def full(
    sym: str, b: Bars, ev: list[Breakdown], cfg: Config, *, resolve: bool = False
) -> tuple[Run, list[tuple[int, float]]]:
    run = simulate(
        sym,
        b,
        ev,
        cfg,
        from_ms=FROM_MS,
        to_ms=TO_MS,
        balance=BALANCE,
        funding=funding(sym),
        resolver=resolver(sym) if resolve else None,
    )
    return run, daily_equity(run.trades, run.marks, BALANCE, FROM_MS, TO_MS)


def write_trades(path: Path, trades: Sequence[Trade]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        names = list(asdict(trades[0]).keys()) if trades else []
        w.writerow(names)
        for tr in trades:
            row = asdict(tr)
            for k in ("breakdown_ms", "signal_ms", "entry_ms", "exit_ms"):
                row[k] = _stamp(row[k])
            w.writerow([row[k] for k in names])


def analyze(sym: str) -> dict[str, Any]:
    b, ev = frames(sym)
    cfg = with_costs(DEFAULT, COST_USER)
    run, curve = full(sym, b, ev, cfg)
    trades = run.trades
    reg_d = regimes_daily(b)
    reg_m = regimes_monthly(b)
    out_dir = OUT / sym
    out_dir.mkdir(parents=True, exist_ok=True)
    write_trades(out_dir / "trades.csv", trades)
    with (out_dir / "equity_daily.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "equity"])
        w.writerows((_day(t), round(v, 2)) for t, v in curve)
    write_line_chart(out_dir / "equity.png", [v for _, v in curve])
    write_histogram(out_dir / "r_hist.png", [tr.r_multiple for tr in trades], bins=30)
    res: dict[str, Any] = {
        "counts": {
            "breakdowns": run.breakdowns,
            "busy": run.busy,
            "replaced": run.replaced,
            "expired": run.expired,
        },
        "summary": _round(summary(trades, curve, BALANCE)),
        "splits": {k: r_stats(_period(trades, lo, hi)) for k, (lo, hi) in SPLITS.items()},
        "yearly": yearly(trades, curve),
        "regime_daily_causal": _group(trades, lambda tr: _regime_at(reg_d, tr.entry_ms)),
        "regime_monthly_expost": _group(trades, lambda tr: reg_m[_day(tr.entry_ms)[:7]]),
        "months_by_regime": dict(Counter(reg_m.values())),
        "drawdowns_top10": dd_detail(trades, curve, reg_d),
        "streaks": streaks(trades),
        "r_distribution": r_hist(trades),
        "exit_reason": _group(trades, lambda tr: tr.exit_reason),
        "no_funding": r_stats(full(sym, b, ev, replace(cfg, funding=False))[0].trades),
    }
    costs = {}
    for name, c in (
        ("realistic_0.023", COST_REALISTIC),
        ("user_0.06", COST_USER),
        ("project_0.095", COST_PROJECT),
    ):
        tr_c = full(sym, b, ev, with_costs(DEFAULT, c))[0].trades
        costs[name] = {k: r_stats(_period(tr_c, lo, hi)) for k, (lo, hi) in SPLITS.items()}
        costs[name]["ALL"] = r_stats(tr_c)
    gross = full(sym, b, ev, replace(DEFAULT, fee=0.0, slippage=0.0, funding=False))[0].trades
    costs["zero_cost_no_funding"] = {"ALL": r_stats(gross)}
    res["costs"] = costs
    # 같은 봉 손절 · 익절 → 1 분봉으로 다시 (보고만)
    run_1m, _ = full(sym, b, ev, cfg, resolve=True)
    res["intrabar_1m"] = {
        "ambiguous_bars": sum(tr.ambiguous for tr in trades),
        "resolved_as": dict(Counter(tr.exit_reason for tr in run_1m.trades if tr.ambiguous)),
        "all_with_1m": r_stats(run_1m.trades),
    }
    return res


def robustness(sym: str) -> dict[str, Any]:
    """주변 값 — 한 번에 하나씩 · 전체 격자 (TRAIN · 전체 평균 R)."""
    b = load(sym)
    h1, h4 = resample(b, 60), resample(b, 240)
    fund = funding(sym)
    base = with_costs(DEFAULT, COST_USER)
    ev_cache: dict[tuple[float, float], list[Breakdown]] = {}
    rows: list[dict[str, Any]] = []
    for lb in GRID["lookback"]:
        for vm in GRID["vol_mult"]:
            cfg_ev = replace(base, lookback=lb, vol_mult=vm)
            ev_cache[(lb, vm)] = breakdowns(h1, h4, cfg_ev)
            for am in GRID["atr_mult"]:
                for rr in GRID["rr"]:
                    cfg = replace(cfg_ev, atr_mult=am, rr=rr)
                    run = simulate(
                        sym, b, ev_cache[(lb, vm)], cfg, from_ms=FROM_MS, to_ms=TO_MS, funding=fund
                    )
                    tr = run.trades
                    lo, hi = SPLITS["TRAIN"]
                    rows.append(
                        {
                            "lookback": lb,
                            "vol_mult": vm,
                            "atr_mult": am,
                            "rr": rr,
                            "train": r_stats(_period(tr, lo, hi)),
                            "oos": r_stats(_period(tr, *SPLITS["OOS"])),
                            "all": r_stats(tr),
                        }
                    )

    def one_at_a_time(param: str) -> dict[str, Any]:
        out = {}
        for val in GRID[param]:
            key = {"lookback": 20, "vol_mult": 1.2, "atr_mult": 1.5, "rr": 2.0, param: val}
            row = next(r for r in rows if all(r[k] == key[k] for k in key))
            out[str(val)] = {
                "train_mean_r": row["train"]["mean_r"],
                "all_mean_r": row["all"]["mean_r"],
                "n_all": row["all"]["n"],
            }
        return out

    train_means = [r["train"]["mean_r"] for r in rows if r["train"]["mean_r"] is not None]
    all_means = [r["all"]["mean_r"] for r in rows if r["all"]["mean_r"] is not None]
    return {
        "combos": len(rows),
        "train_positive_share": round(sum(m > 0 for m in train_means) / len(train_means), 3),
        "train_median_mean_r": round(statistics.median(train_means), 4),
        "all_positive_share": round(sum(m > 0 for m in all_means) / len(all_means), 3),
        "all_median_mean_r": round(statistics.median(all_means), 4),
        "one_at_a_time": {p: one_at_a_time(p) for p in GRID},
        "best_all": max(rows, key=lambda r: r["all"]["mean_r"] or -9),
        "worst_all": min(rows, key=lambda r: r["all"]["mean_r"] or 9),
        "grid": rows,
    }


def judge(res: dict[str, Any]) -> dict[str, Any]:
    """사전 등록 §8. BTC 가 주 판정, ETH 는 교차 확인."""
    btc, eth = res["BTCUSDT"], res["ETHUSDT"]
    u, p = btc["costs"]["user_0.06"], btc["costs"]["project_0.095"]
    tr, va, oos = u["TRAIN"], u["VALIDATION"], u["OOS"]
    crit = {
        "V1_train_mean_r_pos_t2": (tr["mean_r"] or 0) > 0 and (tr["t"] or 0) >= 2.0,
        "V2_validation_mean_r_pos": (va["mean_r"] or 0) > 0,
        "V3_oos_mean_r_pos": (oos["mean_r"] or 0) > 0,
        "V4_project_cost_train_oos_pos": (p["TRAIN"]["mean_r"] or 0) > 0
        and (p["OOS"]["mean_r"] or 0) > 0,
        "V5_eth_all_mean_r_pos": (eth["costs"]["user_0.06"]["ALL"]["mean_r"] or 0) > 0,
        "V6_grid_train_median_pos": res["robustness"]["BTCUSDT"]["train_median_mean_r"] > 0,
    }
    return {"criteria": crit, "verdict": "PASS" if all(crit.values()) else "REJECT"}


def run_all() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    for sym in SYMBOLS:
        path = CACHE / f"{sym}_15m.csv"
        if hashlib.sha256(path.read_bytes()).hexdigest() != PREREG_SHA[sym]:
            raise SystemExit(f"{path.name} 해시가 사전 등록과 다르다")
    OUT.mkdir(parents=True, exist_ok=True)
    res: dict[str, Any] = {"computed": datetime.now(UTC).isoformat(timespec="seconds")}
    res["config"] = asdict(with_costs(DEFAULT, COST_USER))
    for sym in SYMBOLS:
        res[sym] = analyze(sym)
    res["robustness"] = {sym: robustness(sym) for sym in SYMBOLS}
    res["judgement"] = judge(res)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


PREREG_SHA = {
    "BTCUSDT": "31b8b9c72c6718bb6b3c3c33ee92c6bbc2ceaaa0cb68bdac1e245d05d33ede8b",
    "ETHUSDT": "efaf7a9c881ba0ed11dcc2a2ad79cbbe3952cc6a18ab996a43694a509a2ea61e",
}


def main(argv: list[str]) -> int:
    cmds: dict[str, Callable[[], dict[str, Any]]] = {"data": build, "count": count, "run": run_all}
    if len(argv) != 1 or argv[0] not in cmds:
        raise SystemExit("usage: python -m scripts.run_brs_205 data|count|run")
    res = cmds[argv[0]]()
    if argv[0] == "run":
        res = {"judgement": res["judgement"]}
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
