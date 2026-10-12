"""H1/O/AG historical accounting audit; no exchange, orders, or operating ledger writes.

``audit`` runs synthetic diagnostics and source/data checks only.
``pilot`` requires the registration and this file to be committed before calculation.
Frozen O quantities are an attribution experiment, not a new live strategy.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import sqlite3
import statistics
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.backtest.overlay import (
    O2_STOP,
    O3_2X_LIMIT40,
    OverlayBar,
    OverlayConfig,
    OverlayMarketDay,
    OverlayResult,
    OverlayState,
    next_targets,
    step_overlay_day,
)
from auto_trading.research.short_search_229 import (
    H4,
    NORMAL,
    Coin,
    TradeCache,
    build_coin,
    run_config,
)

REPO = Path(__file__).resolve().parent.parent
DAY = 86_400_000
FROM_MS = int(datetime(2020, 6, 1, tzinfo=UTC).timestamp() * 1000)
END_MS = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
SYMBOLS = ("BTCUSDT", "ETHUSDT")
OUT = REPO / "reports" / "h1_o_ag_accounting_audit_20261012"
REGISTRATION = REPO / "docs/research/H1_O_AG_ACCOUNTING_AUDIT_20261012.md"
INITIAL = 10_000.0
LIMIT_BAND = 0.003
PARTICIPATION = 0.001
RISK = 0.01
EXPECTED_INPUT_SHA256 = "f8c3e7666f2d10bfa37ab3349306543aa99cd79d2fd4add823b44c92413e16c9"
CONFIGS = {
    "O2": O2_STOP,
    "O3_PAPER": O3_2X_LIMIT40,
    "AG_CR3": OverlayConfig(exposure=3.0, catastrophe_stop=0.20, halt_drawdown=1.0),
    "AG_CR4": OverlayConfig(
        exposure=4.0, max_futures_leverage=4.0, catastrophe_stop=0.20, halt_drawdown=1.0
    ),
}


@dataclass(frozen=True)
class Bar:
    at_ms: int
    o: float
    h: float
    low: float
    c: float
    v: float


@dataclass(frozen=True)
class Episode:
    symbol: str
    entry_ms: int
    exit_ms: int
    exit_px: float
    stopped: bool


@dataclass
class Inputs:
    spot: dict[str, dict[int, OverlayBar]]
    perp: dict[str, dict[int, OverlayBar]]
    spot4: dict[str, dict[int, Bar]]
    perp4: dict[str, dict[int, Bar]]
    funding: dict[str, list[tuple[int, float]]]
    coins: list[Coin]
    quality: dict[str, Any]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_digest(data: Inputs) -> str:
    h = hashlib.sha256()
    for label, mapping in (
        ("spot1d", data.spot),
        ("perp1d", data.perp),
        ("spot4h", data.spot4),
        ("perp4h", data.perp4),
    ):
        for symbol in SYMBOLS:
            for at, bar in sorted(mapping[symbol].items()):
                h.update(f"{label}:{symbol}:{at}:{bar!r}\n".encode("ascii"))
    for symbol in SYMBOLS:
        for at, rate in data.funding[symbol]:
            h.update(f"funding:{symbol}:{at}:{rate.hex()}\n".encode("ascii"))
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def aggregate(rows: list[tuple[Any, ...]]) -> dict[int, Bar]:
    groups: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        groups[int(row[0]) // H4 * H4].append(row)
    out: dict[int, Bar] = {}
    for at, block in groups.items():
        if [int(x[0]) for x in block] != [at + i * H4 // 4 for i in range(4)]:
            continue
        out[at] = Bar(
            at,
            float(block[0][1]),
            max(float(x[2]) for x in block),
            min(float(x[3]) for x in block),
            float(block[-1][4]),
            sum(float(x[5]) for x in block),
        )
    return out


def archived_spot(symbol: str) -> dict[int, Bar]:
    """Existing official monthly archives; no download and no unbounded September reads."""
    result: dict[int, Bar] = {}
    directory = REPO / "data/external/binance_vision/spot" / symbol / "4h"
    for path in sorted(directory.glob("*.zip")):
        month = path.stem[-7:]
        if month > "2026-08":
            continue
        with zipfile.ZipFile(path) as archive:
            for raw in archive.read(archive.namelist()[0]).decode("ascii").splitlines():
                row = raw.split(",")
                if not row[0].isdigit():
                    continue
                at = int(row[0])
                at = at // 1000 if at > 10**14 else at
                if at + H4 <= END_MS:
                    result[at] = Bar(at, *(float(x) for x in row[1:6]))
    return result


def load() -> Inputs:
    """SQL is bounded even for warm-up; September/operating records are not read."""
    spot: dict[str, dict[int, OverlayBar]] = {}
    perp: dict[str, dict[int, OverlayBar]] = {}
    spot4: dict[str, dict[int, Bar]] = {}
    perp4: dict[str, dict[int, Bar]] = {}
    funding: dict[str, list[tuple[int, float]]] = {}
    quality: dict[str, Any] = {}
    with sqlite3.connect(f"file:{(REPO / 'data/trading.db').as_posix()}?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        for s in SYMBOLS:
            for exchange, daily, four in (
                ("binance_spot", spot, spot4),
                ("binance_usdm", perp, perp4),
            ):
                daily[s] = {
                    int(t): OverlayBar(float(lo), float(c))
                    for t, lo, c in db.execute(
                        "SELECT open_ms,low,close FROM candles WHERE exchange=? AND symbol=?"
                        " AND timeframe='1d' AND open_ms<? ORDER BY open_ms",
                        (exchange, s, END_MS),
                    )
                }
                rows = db.execute(
                    "SELECT open_ms,open,high,low,close,volume FROM candles WHERE exchange=?"
                    " AND symbol=? AND timeframe='1h' AND open_ms<? ORDER BY open_ms",
                    (exchange, s, END_MS),
                ).fetchall()
                four[s] = aggregate(rows)
                initial_missing = [t for t in range(FROM_MS - H4, END_MS, H4) if t not in four[s]]
                supplemented = 0
                if initial_missing and exchange == "binance_spot":
                    archive = archived_spot(s)
                    for t in initial_missing:
                        if t in archive:
                            four[s][t] = archive[t]
                            supplemented += 1
                    four[s] = dict(sorted(four[s].items()))
                missing = sum(t not in four[s] for t in range(FROM_MS - H4, END_MS, H4))
                quality[f"{exchange}:{s}"] = {
                    "hour_rows": len(rows),
                    "complete4h": len(four[s]),
                    "missing4h": missing,
                    "archive_supplemented4h": supplemented,
                    "daily_close_mismatch": sum(
                        abs(four[s][d + DAY - H4].c / b.close - 1) > 1e-7
                        for d, b in daily[s].items()
                        if FROM_MS <= d < END_MS and d + DAY - H4 in four[s]
                    ),
                }
                if missing:
                    raise ValueError(f"missing complete bars: {exchange}:{s}:{missing}")
            funding[s] = [
                (int(t), float(r))
                for t, r in db.execute(
                    "SELECT funding_ms,rate FROM funding_rates WHERE exchange='binance_usdm'"
                    " AND symbol=? AND funding_ms<? ORDER BY funding_ms",
                    (s, END_MS),
                )
            ]
            funding_count = db.execute(
                "SELECT COUNT(*),SUM(mark_price IS NULL) FROM funding_rates"
                " WHERE exchange='binance_usdm' AND symbol=? AND funding_ms>=? AND funding_ms<?",
                (s, FROM_MS, END_MS),
            ).fetchone()
            quality[f"funding:{s}"] = dict(zip(("rows", "null_mark"), funding_count, strict=True))
    coins = [
        build_coin(s, [(b.at_ms, b.o, b.h, b.low, b.c, b.v) for b in perp4[s].values()], funding[s])
        for s in SYMBOLS
    ]
    return Inputs(spot, perp, spot4, perp4, funding, coins, quality)


def reference(
    data: Inputs, cfg: OverlayConfig
) -> tuple[dict[int, dict[str, tuple[float, float]]], list[float]]:
    """Original Pure Core supplies frozen, causal quantities; do not insert shorts into it."""
    state = OverlayState.initial(SYMBOLS, INITIAL, cfg)
    tally = OverlayResult()
    history = {
        s: [b.close for d, b in sorted(data.spot[s].items()) if d < FROM_MS] for s in SYMBOLS
    }
    plan: dict[int, dict[str, tuple[float, float]]] = {}
    equity: list[float] = []
    ft = {s: [t for t, _ in data.funding[s]] for s in SYMBOLS}
    fr = {s: [r for _, r in data.funding[s]] for s in SYMBOLS}
    for d in range(FROM_MS, END_MS, DAY):
        market = OverlayMarketDay(
            {s: data.spot[s][d - DAY].close for s in SYMBOLS},
            {s: data.spot[s][d] for s in SYMBOLS},
            {s: data.perp[s][d - DAY].close for s in SYMBOLS},
            {s: data.perp[s][d] for s in SYMBOLS},
            {
                s: fr[s][bisect.bisect_left(ft[s], d) : bisect.bisect_left(ft[s], d + DAY)]
                for s in SYMBOLS
            },
        )
        result = step_overlay_day(state, d, market, cfg, tally)
        if cfg.exposure >= 3 and result.equity <= INITIAL * 0.30:
            state.spot_qty = dict.fromkeys(SYMBOLS, 0.0)
            state.fut_qty = dict.fromkeys(SYMBOLS, 0.0)
            state.target = None
            state.halted_day = d
        plan[d + DAY] = {s: (state.spot_qty[s], state.fut_qty[s]) for s in SYMBOLS}
        equity.append(result.equity)
        for s in SYMBOLS:
            history[s].append(data.spot[s][d].close)
        state.target = (
            next_targets(history, cfg) if state.halted_day is None else dict.fromkeys(SYMBOLS, 0.0)
        )
    return plan, equity


def episodes(data: Inputs) -> list[Episode]:
    """Same completed H1 transactions as 230/231; terminal censoring is reported separately."""
    trades = run_config(TradeCache(data.coins), "R5", "T2", "E5", "S3", FROM_MS, END_MS)
    by = {k.name: k for k in data.coins}
    result: list[Episode] = []
    for tr in trades:
        k = by[tr.coin]
        j = bisect.bisect_left(k.t, tr.entry_t)
        a, b = bisect.bisect_left(k.fund_t, tr.entry_t), bisect.bisect_left(k.fund_t, tr.exit_t)
        fee_out = NORMAL[2] if tr.stopped else NORMAL[1]
        ratio = (1 + sum(k.fund_r[a:b]) - NORMAL[0] - tr.ret / 0.5) / (1 + fee_out)
        if tr.exit_t <= END_MS:
            result.append(Episode(tr.coin, tr.entry_t, tr.exit_t, k.o[j] * ratio, tr.stopped))
    return result


def metrics(curve: list[float], frequency: int = 365 * 6) -> dict[str, float]:
    path = [INITIAL, *curve]
    returns = [b / a - 1 for a, b in pairwise(path) if a > 0]
    peak, mdd = INITIAL, 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    years = len(curve) / frequency
    sd = statistics.stdev(returns) if len(returns) > 1 else 0.0
    return {
        "final": curve[-1],
        "net": curve[-1] - INITIAL,
        "cagr": (curve[-1] / INITIAL) ** (1 / years) - 1 if curve[-1] > 0 else -1.0,
        "sharpe": statistics.fmean(returns) / sd * math.sqrt(frequency) if sd else 0.0,
        "mdd": mdd,
    }


def replay(
    data: Inputs,
    plan: dict[int, dict[str, tuple[float, float]]],
    eps: list[Episode],
    cfg: OverlayConfig,
    mode: str,
    fee_mult: float = 1.0,
    delay: int = 0,
) -> dict[str, Any]:
    """Fixed-quantity attribution with signed futures and common wallet constraints."""
    sq = dict.fromkeys(SYMBOLS, 0.0)
    fq = dict.fromkeys(SYMBOLS, 0.0)
    wallet = INITIAL
    peak = INITIAL
    halted = False
    account_stopped = False
    day_start_equity = INITIAL
    counters: Counter[str] = Counter()
    costs = funding_pnl = turnover = 0.0
    spot_pnl = long_pnl = short_pnl = 0.0
    contribution = dict.fromkeys(SYMBOLS, 0.0)
    curve: list[float] = []
    exposures: list[float] = []
    worst_dd = 0.0
    by_entry = {(e.symbol, e.entry_ms + delay * H4): e for e in eps}
    active: dict[str, Episode] = {}
    last = {s: data.perp4[s][FROM_MS - H4].c for s in SYMBOLS}
    last_spot = {s: data.spot4[s][FROM_MS - H4].c for s in SYMBOLS}
    fund_t = {s: [t for t, _ in data.funding[s]] for s in SYMBOLS}
    fund_r = {s: [r for _, r in data.funding[s]] for s in SYMBOLS}
    pf = cfg.trend.perp_cost * fee_mult
    sf = cfg.trend.spot_cost * fee_mult

    def fut_fill(
        s: str,
        want: float,
        px: float,
        *,
        protect: bool = False,
        reference_px: float | None = None,
        liquidity: float | None = None,
    ) -> bool:
        nonlocal wallet, costs, turnover
        change = want - fq[s]
        if abs(change) < 1e-12:
            return True
        if (
            not protect
            and reference_px is not None
            and (
                (change > 0 and px > reference_px * (1 + LIMIT_BAND))
                or (change < 0 and px < reference_px * (1 - LIMIT_BAND))
            )
        ):
            counters["limit_miss"] += 1
            return False
        if liquidity is not None and abs(change) > liquidity * PARTICIPATION:
            change = math.copysign(liquidity * PARTICIPATION, change)
            counters["liquidity_partial"] += 1
        fee = abs(change) * px * pf
        wallet -= fee
        costs += fee
        turnover += abs(change) * px
        fq[s] += change
        counters["futures_fills"] += 1
        return abs(fq[s] - want) < 1e-10

    for t in range(FROM_MS, END_MS, H4):
        bars = {s: data.perp4[s][t] for s in SYMBOLS}
        sp = {s: data.spot4[s][t] for s in SYMBOLS}
        if t % DAY == 0:
            day_start_equity = curve[-1] if curve else INITIAL
        # Carried contracts pay/receive the exact-boundary funding before new entry.
        for s in SYMBOLS:
            gap = fq[s] * (bars[s].o - last[s])
            wallet += gap
            long_pnl += gap if fq[s] > 0 else 0
            short_pnl += gap if fq[s] < 0 else 0
            contribution[s] += gap
            spot_change = sq[s] * (sp[s].c - last_spot[s])
            spot_pnl += spot_change
            contribution[s] += spot_change
            left = bisect.bisect_left(fund_t[s], t)
            right = bisect.bisect_right(fund_t[s], t)
            amt = -fq[s] * last[s] * sum(fund_r[s][left:right])
            wallet += amt
            funding_pnl += amt
        for s in SYMBOLS:
            e = active.get(s)
            if e and e.exit_ms <= t:
                if fq[s] < 0:
                    if fut_fill(s, 0, bars[s].o, reference_px=last[s]):
                        active.pop(s)
                    else:
                        counters["exit_delayed"] += 1
                else:
                    active.pop(s)
            new = by_entry.get((s, t)) if mode != "BASE" and not halted else None
            if new and s not in active and new.exit_ms > t:
                active[s] = new
                counters["h1_windows"] += 1
            if s in active and fq[s] > 0:
                fut_fill(s, 0, bars[s].o, reference_px=last[s])
            e = active.get(s)
            if mode == "SWITCH" and e and fq[s] == 0 and e.entry_ms + delay * H4 == t:
                equity = wallet + sum(sq[n] * sp[n].o for n in SYMBOLS)
                risk_fraction = 0.03 + pf + 1.03 * (0.002 + pf)
                notional = min(0.5 * equity, RISK * equity / risk_fraction)
                if notional < 0.5 * equity:
                    counters["risk_clipped"] += 1
                gross_other = sum(abs(fq[n]) * bars[n].o for n in SYMBOLS if n != s)
                cap = max(
                    0.0,
                    (cfg.max_futures_leverage * wallet - gross_other)
                    / (1 + cfg.max_futures_leverage * pf),
                )
                if cap < notional:
                    counters["margin_clipped"] += 1
                notional = min(notional, cap)
                if (
                    equity <= 0
                    or notional <= 0
                    or equity < peak * (1 - cfg.halt_drawdown)
                    or equity / day_start_equity - 1 <= -cfg.daily_loss_limit
                ):
                    counters["entry_veto"] += 1
                elif fut_fill(
                    s,
                    -notional / bars[s].o,
                    bars[s].o,
                    reference_px=last[s],
                    liquidity=data.perp4[s][t - H4].v,
                ):
                    counters["short_entries"] += 1
        # Funding proxy is a confirmed close, not today's future close.
        before = dict(fq)
        for s in SYMBOLS:
            a, b = bisect.bisect_right(fund_t[s], t), bisect.bisect_left(fund_t[s], t + H4)
            amt = -fq[s] * last[s] * sum(fund_r[s][a:b])
            wallet += amt
            funding_pnl += amt
        worst_wallet = wallet + sum(
            fq[s] * ((bars[s].low if fq[s] >= 0 else bars[s].h) - bars[s].o) for s in SYMBOLS
        )
        worst_equity = worst_wallet + sum(sq[s] * sp[s].low for s in SYMBOLS)
        worst_dd = max(worst_dd, 1 - worst_equity / peak)
        for s in SYMBOLS:
            px = bars[s].c
            e = active.get(s)
            reason = ""
            if fq[s] < 0 and e and e.stopped and e.exit_ms == t + H4:
                px, reason = e.exit_px, "h1_stop"
            elif fq[s] > 0 and cfg.catastrophe_stop is not None:
                d = t // DAY * DAY
                stop = data.perp[s][d - DAY].close * (1 - cfg.catastrophe_stop)
                if bars[s].low <= stop:
                    px, reason = min(bars[s].o, stop) * (1 - cfg.stop_slippage), "long_stop"
            change_pnl = fq[s] * (px - bars[s].o)
            wallet += change_pnl
            long_pnl += change_pnl if fq[s] > 0 else 0
            short_pnl += change_pnl if fq[s] < 0 else 0
            contribution[s] += change_pnl
            if reason:
                fut_fill(s, 0, px, protect=True)
                counters[reason] += 1
                if reason == "h1_stop":
                    active.pop(s)
        gross = sum(
            abs(before[s]) * (bars[s].low if before[s] >= 0 else bars[s].h) for s in SYMBOLS
        )
        if gross > 0 and worst_wallet <= cfg.maintenance_margin * gross:
            counters["conservative_margin_event"] += 1
            for s in SYMBOLS:
                if fq[s]:
                    fut_fill(s, 0, bars[s].c, protect=True)
            halted = True
        equity = wallet + sum(sq[s] * sp[s].c for s in SYMBOLS)
        if t + H4 in plan and equity > 0:
            if not halted and worst_equity <= peak * (1 - cfg.halt_drawdown):
                halted = True
                counters["dd_halt"] += 1
            want = plan[t + H4]
            no_increase = equity / day_start_equity - 1 <= -cfg.daily_loss_limit
            for s in SYMBOLS:
                spot_goal, future_goal = want[s]
                if account_stopped:
                    spot_goal = future_goal = 0.0
                if no_increase and spot_goal > sq[s]:
                    spot_goal = sq[s]
                    counters["daily_loss_veto"] += 1
                delta = spot_goal - sq[s]
                if delta > 0:
                    delta = min(delta, max(wallet, 0.0) / (sp[s].c * (1 + sf)))
                    spot_goal = sq[s] + delta
                fee = abs(delta) * sp[s].c * sf
                wallet -= delta * sp[s].c + fee
                costs += fee
                turnover += abs(delta) * sp[s].c
                sq[s] = spot_goal
                if s in active:
                    continue
                if halted:
                    future_goal = 0.0
                if no_increase and future_goal > fq[s]:
                    future_goal = max(fq[s], 0)
                    counters["daily_loss_veto"] += 1
                other = sum(abs(fq[n]) * bars[n].c for n in SYMBOLS if n != s)
                affordable = (
                    max(
                        0.0,
                        (
                            cfg.max_futures_leverage * wallet
                            - other
                            + cfg.max_futures_leverage * pf * fq[s] * bars[s].c
                        )
                        / (1 + cfg.max_futures_leverage * pf),
                    )
                    / bars[s].c
                )
                if future_goal > affordable:
                    counters["reference_margin_clipped"] += 1
                fut_fill(
                    s,
                    min(future_goal, affordable),
                    bars[s].c,
                    reference_px=bars[s].c,
                    liquidity=data.perp4[s][t - H4].v,
                )
        equity = wallet + sum(sq[s] * sp[s].c for s in SYMBOLS)
        if cfg.exposure >= 3 and equity <= INITIAL * 0.30:
            counters["initial_capital_halt"] += int(not halted)
            halted = True
            account_stopped = True
            for s in SYMBOLS:
                fee = sq[s] * sp[s].c * sf
                wallet += sq[s] * sp[s].c - fee
                costs += fee
                turnover += sq[s] * sp[s].c
                sq[s] = 0.0
        if halted:
            for s in SYMBOLS:
                if fq[s]:
                    fut_fill(s, 0, bars[s].c, protect=True)
            equity = wallet + sum(sq[s] * sp[s].c for s in SYMBOLS)
        peak = max(peak, equity)
        curve.append(equity)
        exposures.append(
            (sum(sq[s] * sp[s].c + abs(fq[s]) * bars[s].c for s in SYMBOLS) / equity)
            if equity > 0
            else 0
        )
        last = {s: bars[s].c for s in SYMBOLS}
        last_spot = {s: sp[s].c for s in SYMBOLS}
    # Common terminal liquidation: no invisible unpaid closing costs.
    for s in SYMBOLS:
        px = data.spot4[s][END_MS - H4].c
        fee = sq[s] * px * sf
        wallet += sq[s] * px - fee
        costs += fee
        turnover += sq[s] * px
        sq[s] = 0.0
        fut_fill(s, 0, last[s], protect=True)
    curve[-1] = wallet
    result: dict[str, Any] = metrics(curve)
    result.update(
        {
            "costs": costs,
            "funding": funding_pnl,
            "spot_pnl": spot_pnl,
            "long_futures_pnl": long_pnl,
            "short_futures_pnl": short_pnl,
            "gross_contribution_by_symbol": contribution,
            "accounting_residual": wallet
            - INITIAL
            - spot_pnl
            - long_pnl
            - short_pnl
            - funding_pnl
            + costs,
            "turnover": turnover / INITIAL,
            "mean_gross_exposure": statistics.fmean(exposures),
            "max_gross_exposure": max(exposures),
            "conservative_bound_dd": worst_dd,
            "counters": dict(counters),
            "curve": curve,
        }
    )
    return result


def synthetic() -> dict[str, float]:
    """Accounting counterexamples, not historical performance."""
    overlap_old = 100 * 1.10 * 0.90
    overlap_cash = 100 + 10 - 10
    funding_long, funding_short = -2 * 120 * 0.001, 2 * 120 * 0.001
    rng = random.Random(231)
    sorted_mean = statistics.fmean(min(rng.sample(range(1000), 3)) for _ in range(10_000))
    marked = [100.0, 100 + (100 - 120), 100 + (100 - 90)]
    peak = marked[0]
    mdd = 0.0
    for equity in marked:
        peak = max(peak, equity)
        mdd = max(mdd, 1 - equity / peak)
    return {
        "overlap_sequential": overlap_old,
        "overlap_fixed_cash": overlap_cash,
        "hidden_short_mdd": mdd,
        "exit_only_mdd": 0.0,
        "funding_long": funding_long,
        "funding_short": funding_short,
        "funding_entry_price_proxy_long": -2 * 100 * 0.001,
        "sorted_first_of3_mean_index": sorted_mean,
        "uniform_expected_index": 499.5,
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if mode not in {"audit", "pilot"}:
        raise ValueError("audit or pilot")
    if mode == "pilot" and git("status", "--porcelain", "--", str(REGISTRATION), __file__):
        raise ValueError("registration/code must be committed before performance")
    if mode == "pilot":
        git("ls-files", "--error-unmatch", str(REGISTRATION), __file__)
    data = load()
    if mode == "pilot" and input_digest(data) != EXPECTED_INPUT_SHA256:
        raise ValueError("inputs differ from pre-performance registration")
    out: dict[str, Any] = {
        "as_of": "2026-10-12",
        "git": git("rev-parse", "HEAD"),
        "code_sha256": digest(Path(__file__)),
        "registration_sha256": digest(REGISTRATION),
        "scope": "explanatory fixed-quantity attribution; not OOS, PASS, or operating strategy",
        "data_quality": data.quality,
        "normalized_input_sha256": input_digest(data),
        "preserved_source_sha256": {
            p: digest(REPO / p)
            for p in (
                "src/auto_trading/backtest/overlay.py",
                "src/auto_trading/research/short_search_229.py",
                "scripts/run_h1_univ_231.py",
                "scripts/run_h1_extra_231.py",
                "reports/alt_short_230/result.json",
                "reports/h1_univ_231/result.json",
                "reports/h1_univ_231/extra.txt",
            )
        },
        "synthetic": synthetic(),
    }
    if mode == "pilot":
        eps = episodes(data)
        out["h1_completed_episodes"] = len(eps)
        out["scenarios"] = {}
        for label, mult, delay in (
            ("base_cost", 1.0, 0),
            ("cost2x", 2.0, 0),
            ("delay1bar", 1.0, 1),
        ):
            runs: dict[str, Any] = {}
            for name, cfg in CONFIGS.items():
                plan, eq = reference(data, cfg)
                records: dict[str, Any] = {"original_daily": metrics(eq, 365)}
                for arm in ("BASE", "CASH", "SWITCH"):
                    r = replay(data, plan, eps, cfg, arm, mult, delay)
                    r.pop("curve")
                    records[arm] = r
                runs[name] = records
            out["scenarios"][label] = runs
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{mode}.json"
    if target.exists():
        raise ValueError(f"do not overwrite existing audit: {target}")
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
