"""Finite, preregistered retrospective search. Never imports exchange or operating ledgers."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import sqlite3
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from itertools import pairwise, product
from pathlib import Path
from typing import Any

from auto_trading.research import indicators_229 as ind
from auto_trading.research.short_search_229 import H4, Coin, build_coin

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs/research/RSI_STOCH_FOLLOWUP_20261012.md"
OUT = REPO / "reports/rsi_stoch_pilot_20261012"
DAY = 86_400_000
WEEK = 7 * DAY
START = int(datetime(2020, 6, 1, tzinfo=UTC).timestamp() * 1000)
END = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
TRAIN_END = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
CHECK_END = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
PERIODS = {
    "train": (START, TRAIN_END),
    "check": (TRAIN_END, CHECK_END),
    "tail": (CHECK_END, END),
    "all": (START, END),
}
SYMBOLS = ("BTCUSDT", "ETHUSDT")
ZERO = Decimal(0)
ONE = Decimal(1)
FEE = Decimal("0.0005")
SLIP = Decimal("0.00045")
STOP_SLIP = Decimal("0.002")
BAND = Decimal("0.003")
PARTICIPATION = Decimal("0.001")
STEP = Decimal("0.001")
PURGE = 36 * H4


def dec(value: float | int | str) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class Config:
    mode: str
    rsi_n: int
    rsi_level: int
    stoch_n: int
    stoch_level: int
    regime: str
    exit: str
    stop_pct: str

    @property
    def family(self) -> str:
        return self.mode if self.mode in ("RSI", "STOCH") else "COMBO"

    @property
    def key(self) -> str:
        return "-".join(str(x) for x in asdict(self).values())


def configs() -> list[Config]:
    signals = [
        Config("RSI", n, level, 0, 0, "", "", "") for n, level in product((7, 14, 21), (50, 60, 70))
    ]
    signals += [
        Config("STOCH", 0, 0, n, level, "", "", "")
        for n, level in product((9, 14, 21), (70, 80, 90))
    ]
    signals += [
        Config(mode, rn, rl, sn, sl, "", "", "")
        for mode, rn, rl, sn, sl in product(
            ("AND", "OR", "RSI_THEN_STOCH", "STOCH_THEN_RSI"),
            (7, 14, 21),
            (50, 60, 70),
            (9, 14, 21),
            (70, 80, 90),
        )
    ]
    return [
        Config(s.mode, s.rsi_n, s.rsi_level, s.stoch_n, s.stoch_level, r, e, stop)
        for s, r, e, stop in product(
            signals, ("R0", "R4", "R5"), ("24H", "48H", "ATR72H"), ("0.02", "0.03", "0.05")
        )
    ]


def combine(rsi: list[bool], stoch: list[bool], mode: str) -> list[bool]:
    if len(rsi) != len(stoch):
        raise ValueError("indicator length mismatch")
    out: list[bool] = []
    for i, (r, s) in enumerate(zip(rsi, stoch, strict=True)):
        if mode == "RSI":
            out.append(r)
        elif mode == "STOCH":
            out.append(s)
        elif mode == "AND":
            out.append(r and s)
        elif mode == "OR":
            out.append(r or s)
        elif mode == "RSI_THEN_STOCH":
            out.append(s and any(rsi[max(0, i - 3) : i + 1]))
        elif mode == "STOCH_THEN_RSI":
            out.append(r and any(stoch[max(0, i - 3) : i + 1]))
        else:
            raise ValueError(mode)
    return out


def load() -> tuple[list[Coin], dict[str, Any]]:
    coins: list[Coin] = []
    quality: dict[str, Any] = {}
    source = hashlib.sha256()
    with sqlite3.connect(f"file:{(REPO / 'data/trading.db').as_posix()}?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        for symbol in SYMBOLS:
            rows = db.execute(
                "SELECT open_ms,open,high,low,close,volume FROM candles WHERE exchange=? "
                "AND symbol=? AND timeframe='1h' AND open_ms>=? AND open_ms<? ORDER BY open_ms",
                ("binance_usdm", symbol, START - 365 * DAY, END),
            ).fetchall()
            grouped: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
            for row in rows:
                source.update(f"{symbol}:{row!r}\n".encode())
                grouped[int(row[0]) // H4 * H4].append(row)
            bars: list[tuple[int, float, float, float, float, float]] = []
            incomplete = 0
            for at, block in sorted(grouped.items()):
                if [int(x[0]) for x in block] != [at + h * H4 // 4 for h in range(4)]:
                    incomplete += 1
                    continue
                bar = (
                    at,
                    float(block[0][1]),
                    max(float(x[2]) for x in block),
                    min(float(x[3]) for x in block),
                    float(block[-1][4]),
                    sum(float(x[5]) for x in block),
                )
                if not all(math.isfinite(x) and x > 0 for x in bar[1:5]) or bar[5] < 0:
                    raise ValueError(f"invalid OHLCV: {symbol}:{at}")
                if not bar[3] <= min(bar[1], bar[4]) <= max(bar[1], bar[4]) <= bar[2]:
                    raise ValueError(f"OHLC order: {symbol}:{at}")
                bars.append(bar)
            observed = {b[0] for b in bars}
            missing = sum(t not in observed for t in range(START - H4, END, H4))
            if missing:
                raise ValueError(f"complete4h missing {symbol}: {missing}")
            funds = db.execute(
                "SELECT funding_ms,rate,mark_price FROM funding_rates WHERE exchange=? "
                "AND symbol=? AND funding_ms>=? AND funding_ms<? ORDER BY funding_ms",
                ("binance_usdm", symbol, START - 365 * DAY, END),
            ).fetchall()
            for row in funds:
                source.update(f"fund:{symbol}:{row!r}\n".encode())
            times = [int(x[0]) for x in funds]
            if not times or len(set(times)) != len(times):
                raise ValueError(f"funding empty/duplicate: {symbol}")
            gaps = max((b - a for a, b in pairwise(times)), default=0)
            if gaps > 9 * 3_600_000:
                raise ValueError(f"funding gap {symbol}: {gaps}")
            if times[0] > START or times[-1] < END - 9 * 3_600_000:
                raise ValueError(f"funding boundary {symbol}")
            coins.append(build_coin(symbol, bars, [(int(t), float(r)) for t, r, _ in funds]))
            quality[symbol] = {
                "hour_rows": len(rows),
                "complete4h": len(bars),
                "incomplete4h": incomplete,
                "missing_evaluation4h": missing,
                "first_ms": bars[0][0],
                "last_ms": bars[-1][0],
                "funding_rows": len(funds),
                "funding_null_mark": sum(x[2] is None for x in funds),
                "funding_max_gap_ms": gaps,
                "zero_volume4h": sum(b[5] == 0 for b in bars),
            }
    quality["source_sha256"] = source.hexdigest()
    return coins, quality


@dataclass(frozen=True)
class Episode:
    symbol: str
    signal: int
    entry_i: int
    exit_i: int
    entry_ms: int
    exit_ms: int
    entry_px: Decimal
    exit_px: Decimal
    gross: Decimal
    fees: Decimal
    funding: Decimal
    slip: Decimal
    stopped: bool
    unresolved: bool
    exit_delays: int

    @property
    def net(self) -> Decimal:
        return self.gross - self.fees + self.funding


class Engine:
    def __init__(self, coins: list[Coin]) -> None:
        self.coins = {c.name: c for c in coins}
        self.triggers: dict[tuple[str, str, int, int, int, int], list[int]] = {}
        self.cache: dict[tuple[str, int, str, str, int, int, int], Episode | None] = {}
        self.fund_prefix: dict[str, tuple[list[Decimal], list[Decimal]]] = {}
        self.rsi: dict[tuple[str, int], list[float]] = {}
        self.stoch: dict[tuple[str, int], tuple[list[float], list[float]]] = {}
        for c in coins:
            for n in (7, 14, 21):
                self.rsi[c.name, n] = ind.rsi(c.c, n)
            for n in (9, 14, 21):
                self.stoch[c.name, n] = ind.stoch(c.h, c.lo, c.c, n)
            total, negative = [ZERO], [ZERO]
            for t, rate in zip(c.fund_t, c.fund_r, strict=True):
                p = bisect.bisect_left(c.t, t) - 1
                amount = ZERO if p < 0 else dec(c.c[p]) * dec(rate)
                total.append(total[-1] + amount)
                negative.append(negative[-1] + min(ZERO, amount))
            self.fund_prefix[c.name] = total, negative

    def signals(self, c: Coin, cfg: Config) -> list[int]:
        key = (c.name, cfg.mode, cfg.rsi_n, cfg.rsi_level, cfg.stoch_n, cfg.stoch_level)
        if key not in self.triggers:
            r, s = [False] * len(c.t), [False] * len(c.t)
            if cfg.rsi_n:
                values = self.rsi[c.name, cfg.rsi_n]
                for i in range(1, len(c.t)):
                    r[i] = values[i - 1] >= cfg.rsi_level > values[i]
            if cfg.stoch_n:
                k, d = self.stoch[c.name, cfg.stoch_n]
                for i in range(1, len(c.t)):
                    s[i] = k[i - 1] >= cfg.stoch_level and k[i - 1] >= d[i - 1] and k[i] < d[i]
            self.triggers[key] = [i for i, v in enumerate(combine(r, s, cfg.mode)) if v]
        return self.triggers[key]

    def episode(
        self, c: Coin, signal: int, cfg: Config, end_ms: int, cost: int = 1, delay: int = 0
    ) -> Episode | None:
        key = (c.name, signal, cfg.exit, cfg.stop_pct, end_ms, cost, delay)
        if key not in self.cache:
            self.cache[key] = self._episode(c, signal, cfg, end_ms, cost, delay)
        return self.cache[key]

    def _episode(
        self, c: Coin, signal: int, cfg: Config, end_ms: int, cost: int, delay: int
    ) -> Episode | None:
        j = signal + 1 + delay
        end_i = bisect.bisect_left(c.t, end_ms)
        if j >= end_i or not math.isfinite(c.ind["atr"][signal]):
            return None
        entry = dec(c.o[j]) * (ONE - SLIP * cost)
        if entry < dec(c.c[j - 1]) * (ONE - BAND) or c.v[j - 1] <= 0:
            return None
        stop = entry * (ONE + dec(cfg.stop_pct))
        lowest_close = entry
        horizon = {"24H": 6, "48H": 12, "ATR72H": 18}[cfg.exit]
        delays = 0
        q = j
        stopped = unresolved = False
        for q in range(j, end_i):
            if q >= j + horizon:
                buy = dec(c.o[q]) * (ONE + SLIP * cost)
                if buy <= dec(c.c[q - 1]) * (ONE + BAND) and c.v[q - 1] > 0:
                    px = buy
                    exit_ms = c.t[q]
                    break
                delays += 1
            if dec(c.h[q]) >= stop:
                px = max(dec(c.o[q]), stop) * (ONE + (STOP_SLIP + SLIP) * cost)
                exit_ms = c.t[q] + H4
                stopped = True
                break
            if cfg.exit == "ATR72H":
                lowest_close = min(lowest_close, dec(c.c[q]))
                if math.isfinite(c.ind["atr"][q]):
                    stop = min(stop, lowest_close + 2 * dec(c.ind["atr"][q]))
        else:
            px = dec(c.c[q])
            exit_ms = c.t[q] + H4
            unresolved = True
        left = bisect.bisect_right(c.fund_t, c.t[j])
        right = bisect.bisect_right(c.fund_t, exit_ms)
        total, neg = self.fund_prefix[c.name]
        if stopped:
            known = max(left, bisect.bisect_right(c.fund_t, c.t[q]))
            funding = total[known] - total[left] + neg[right] - neg[known]
        else:
            funding = total[right] - total[left]
        fees = FEE * cost * (entry + px) / entry
        # Unresolved fee is a valuation reserve, not a claimed executed closing order.
        raw_in = dec(c.o[j])
        raw_out = px / (ONE + (STOP_SLIP + SLIP) * cost) if stopped else dec(c.o[q])
        if unresolved:
            raw_out = px
        slip = ((raw_in - entry) + max(ZERO, px - raw_out)) / entry
        return Episode(
            c.name,
            signal,
            j,
            q,
            c.t[j],
            exit_ms,
            entry,
            px,
            (entry - px) / entry,
            fees,
            funding / entry,
            slip,
            stopped,
            unresolved,
            delays,
        )

    def trades(
        self, cfg: Config, start_ms: int, end_ms: int, cost: int = 1, delay: int = 0
    ) -> tuple[list[Episode], dict[str, int]]:
        trades: list[Episode] = []
        counts: Counter[str] = Counter()
        for c in self.coins.values():
            busy = -1
            for i in self.signals(c, cfg):
                if not start_ms <= c.t[i] + H4 < end_ms - PURGE or not c.regime[cfg.regime][i]:
                    continue
                counts["signals"] += 1
                if c.t[i + 1 + delay] < busy:
                    counts["overlap_skipped"] += 1
                    continue
                ep = self.episode(c, i, cfg, end_ms, cost, delay)
                if ep is None:
                    counts["entry_unfilled"] += 1
                    continue
                trades.append(ep)
                busy = ep.exit_ms
        return sorted(trades, key=lambda ep: (ep.entry_ms, ep.symbol)), dict(counts)


def hac_t(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = statistics.fmean(values)
    centered = [x - mean for x in values]
    variance = sum(x * x for x in centered) / n
    for lag in range(1, min(4, n - 1) + 1):
        variance += (
            2 * (1 - lag / 5) * sum(centered[i] * centered[i - lag] for i in range(lag, n)) / n
        )
    return mean / math.sqrt(variance / n) if variance > 0 else 0.0


def summary(trades: list[Episode], start_ms: int, end_ms: int) -> dict[str, Any]:
    week0, week1 = (start_ms + 3 * DAY) // WEEK, (end_ms - 1 + 3 * DAY) // WEEK
    weekly = dict.fromkeys(range(week0, week1 + 1), ZERO)
    annual: dict[str, list[float]] = defaultdict(list)
    by_coin: dict[str, list[float]] = {s: [] for s in SYMBOLS}
    for ep in trades:
        weekly[(ep.entry_ms + 3 * DAY) // WEEK] += ep.net
        annual[str(datetime.fromtimestamp(ep.entry_ms / 1000, UTC).year)].append(float(ep.net))
        by_coin[ep.symbol].append(float(ep.net))
    returns = [float(t.net) for t in trades]
    t = hac_t([float(x) for x in weekly.values()])
    cut = math.ceil(len(returns) * 0.01)
    trimmed = sorted(returns)[:-cut] if cut else returns
    return {
        "trades": len(trades),
        "mean_net_bps": statistics.fmean(returns) * 1e4 if returns else 0,
        "sum_unit_net_bps": sum(returns) * 1e4,
        "weekly_hac_t": t,
        "weekly_calendar_n": len(weekly),
        "bonferroni_normal_p": min(1.0, 9234 * math.erfc(t / math.sqrt(2)) / 2),
        "mean_gross_bps": float(sum((e.gross + e.slip for e in trades), ZERO) / len(trades) * 10000)
        if trades
        else 0,
        "mean_fee_bps": float(sum((e.fees for e in trades), ZERO) / len(trades) * 10000)
        if trades
        else 0,
        "mean_funding_bps": float(sum((e.funding for e in trades), ZERO) / len(trades) * 10000)
        if trades
        else 0,
        "mean_slip_bps": float(sum((e.slip for e in trades), ZERO) / len(trades) * 10000)
        if trades
        else 0,
        "win_rate": sum(x > 0 for x in returns) / len(returns) if returns else 0,
        "trim_top1_mean_bps": statistics.fmean(trimmed) * 1e4 if trimmed else None,
        "mean_hold_hours": statistics.fmean((e.exit_ms - e.entry_ms) / 3600000 for e in trades)
        if trades
        else 0,
        "stops": sum(e.stopped for e in trades),
        "unresolved": sum(e.unresolved for e in trades),
        "exit_delays": sum(e.exit_delays for e in trades),
        "symbols": {
            s: {
                "trades": len(v),
                "mean_bps": statistics.fmean(v) * 1e4 if v else 0,
                "sum_bps": sum(v) * 1e4,
            }
            for s, v in by_coin.items()
        },
        "years": {
            y: {"trades": len(v), "sum_bps": sum(v) * 1e4, "mean_bps": statistics.fmean(v) * 1e4}
            for y, v in annual.items()
        },
    }


def nominate(rows: list[dict[str, Any]], grid: list[Config]) -> dict[str, Any]:
    chosen: dict[str, Any] = {}
    for family in ("RSI", "STOCH", "COMBO"):
        pool = [
            row
            for row, cfg in zip(rows, grid, strict=True)
            if cfg.family == family and row["trades"] >= 100
        ]
        eligible = [
            row
            for row in pool
            if row["unresolved"] == 0
            and all(v["trades"] >= 20 and v["mean_bps"] > 0 for v in row["symbols"].values())
        ]
        selection = max(
            eligible or pool, key=lambda row: (row["weekly_hac_t"], row["key"]), default=None
        )
        chosen[family] = {
            "key": selection["key"] if selection else None,
            "train_eligible": bool(eligible),
        }
    return chosen


def account(engine: Engine, cfg: Config, cost: int = 1, delay: int = 0) -> dict[str, Any]:
    """Shared wallet, entry-fixed Decimal quantities, MTM; no exit-order compounding."""
    entries: dict[tuple[str, int], Episode] = {}
    for c in engine.coins.values():
        for i in engine.signals(c, cfg):
            if START <= c.t[i] + H4 < END - PURGE and c.regime[cfg.regime][i]:
                candidate = engine.episode(c, i, cfg, END, cost, delay)
                if candidate is not None:
                    entries[c.name, candidate.entry_ms] = candidate
    bars = {s: {t: i for i, t in enumerate(c.t)} for s, c in engine.coins.items()}
    initial = dec(10000)
    wallet = peak = day_start = initial
    active: dict[str, tuple[Episode, Decimal]] = {}
    curve: list[Decimal] = []
    exposures: list[float] = []
    fees = funding = turnover = ZERO
    mdd = worst_mdd = ZERO
    halted = False
    loss_streak = 0
    cooldown_ms = -1
    counts: Counter[str] = Counter()
    realized: list[Decimal] = []
    symbol_pnl = dict.fromkeys(SYMBOLS, ZERO)
    residual = ZERO
    for t in range(START, END, H4):
        if t % DAY == 0:
            day_start = wallet + sum(
                (
                    qty * (ep.entry_px - dec(engine.coins[s].o[bars[s][t]]))
                    for s, (ep, qty) in active.items()
                ),
                ZERO,
            )
        # Exits at the open, including any carried funding event at that instant.
        for s in list(active):
            ep, qty = active[s]
            if ep.exit_ms == t:
                if qty > dec(engine.coins[s].v[bars[s][t] - 1]) * PARTICIPATION:
                    raise ValueError("ordinary exit exceeds predeclared volume participation")
                gross = qty * (ep.entry_px - ep.exit_px)
                exit_fee = qty * ep.exit_px * FEE * cost
                wallet += gross - exit_fee
                fees += exit_fee
                turnover += qty * ep.exit_px
                net = qty * ep.entry_px * ep.net
                realized.append(net)
                symbol_pnl[s] += net
                loss_streak = loss_streak + 1 if net < 0 else 0
                if loss_streak >= 4:
                    cooldown_ms = t + 240 * 60000
                    loss_streak = 0
                active.pop(s)
                counts["closed"] += 1
        equity_open = wallet + sum(
            (
                qty * (ep.entry_px - dec(engine.coins[s].o[bars[s][t]]))
                for s, (ep, qty) in active.items()
            ),
            ZERO,
        )
        gross_open = sum(
            (qty * dec(engine.coins[s].o[bars[s][t]]) for s, (_ep, qty) in active.items()), ZERO
        )
        for s in SYMBOLS:
            candidate = entries.get((s, t))
            if candidate is None or s in active:
                continue
            ep = candidate
            equity_open = wallet + sum(
                (
                    q * (a.entry_px - dec(engine.coins[n].o[bars[n][t]]))
                    for n, (a, q) in active.items()
                ),
                ZERO,
            )
            if (
                halted
                or equity_open <= 0
                or equity_open <= day_start * dec("0.98")
                or t < cooldown_ms
            ):
                counts["risk_veto"] += 1
                continue
            loss_budget = (
                dec(cfg.stop_pct)
                + (STOP_SLIP + SLIP) * cost * (ONE + dec(cfg.stop_pct))
                + FEE * cost * (2 + dec(cfg.stop_pct))
            )
            notional = min(
                equity_open * dec("0.25"),
                equity_open * dec("0.01") / loss_budget,
                max(ZERO, equity_open - gross_open),
            )
            c = engine.coins[s]
            j = bars[s][t]
            qty = min(notional / ep.entry_px, dec(c.v[j - 1]) * PARTICIPATION)
            qty = (qty / STEP).to_integral_value(rounding=ROUND_FLOOR) * STEP
            if qty <= 0:
                counts["size_zero"] += 1
                continue
            fee = qty * ep.entry_px * FEE * cost
            wallet -= fee
            fees += fee
            turnover += qty * ep.entry_px
            active[s] = ep, qty
            gross_open += qty * ep.entry_px
            counts["opened"] += 1
        worst = wallet + sum(
            (
                qty * (ep.entry_px - dec(engine.coins[s].h[bars[s][t]]))
                for s, (ep, qty) in active.items()
            ),
            ZERO,
        )
        worst_mdd = max(worst_mdd, ONE - worst / peak)
        for s in list(active):
            ep, qty = active[s]
            c = engine.coins[s]
            j = bars[s][t]
            # Funding at (t,t+4h]; opening boundary was excluded at initial entry.
            a, b = bisect.bisect_right(c.fund_t, t), bisect.bisect_right(c.fund_t, t + H4)
            total, neg = engine.fund_prefix[s]
            amount = qty * (
                (neg[b] - neg[a]) if ep.stopped and ep.exit_i == j else (total[b] - total[a])
            )
            wallet += amount
            funding += amount
            if ep.exit_ms == t + H4 and ep.stopped:
                exit_fee = qty * ep.exit_px * FEE * cost
                wallet += qty * (ep.entry_px - ep.exit_px) - exit_fee
                fees += exit_fee
                turnover += qty * ep.exit_px
                net = qty * ep.entry_px * ep.net
                realized.append(net)
                symbol_pnl[s] += net
                loss_streak = loss_streak + 1 if net < 0 else 0
                if loss_streak >= 4:
                    cooldown_ms = t + H4 + 240 * 60000
                    loss_streak = 0
                active.pop(s)
                counts["closed"] += 1
        equity = wallet + sum(
            (
                qty * (ep.entry_px - dec(engine.coins[s].c[bars[s][t]]))
                for s, (ep, qty) in active.items()
            ),
            ZERO,
        )
        peak = max(peak, equity)
        mdd = max(mdd, ONE - equity / peak)
        if worst <= peak * dec("0.70") and not halted:
            halted = True
            counts["dd_halt"] += 1
        exposures.append(
            float(
                sum(
                    (qty * dec(engine.coins[s].c[bars[s][t]]) for s, (_ep, qty) in active.items()),
                    ZERO,
                )
                / equity
            )
            if equity > 0
            else 0
        )
        curve.append(equity)
    last_equity = curve[-1]
    expected = initial + sum(realized, ZERO)
    if not active:
        residual = abs(last_equity - expected)
    daily = [initial, *curve[5::6]]
    changes = [float(b / a - 1) for a, b in pairwise(daily) if a > 0]
    years = (END - START) / (365.25 * DAY)
    return {
        "net_usdt": float(last_equity - initial),
        "cagr": float(last_equity / initial) ** (1 / years) - 1,
        "sharpe": statistics.fmean(changes) / statistics.stdev(changes) * math.sqrt(365.25)
        if len(changes) > 1 and statistics.stdev(changes) > 0
        else 0,
        "mdd4h": float(mdd),
        "worst_bar_mdd_bound": float(worst_mdd),
        "mean_exposure": statistics.fmean(exposures),
        "max_exposure": max(exposures),
        "fees_usdt": float(fees),
        "funding_usdt": float(funding),
        "turnover_initial_multiple": float(turnover / initial),
        "counts": dict(counts),
        "open_terminal": len(active),
        "symbol_closed_pnl": {s: float(v) for s, v in symbol_pnl.items()},
        "accounting_residual_usdt": float(residual),
        "halted": halted,
        "daily_returns": {
            str(datetime.fromtimestamp(START / 1000 + i * 86400, UTC).date()): r
            for i, r in enumerate(changes)
        },
    }


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8"
    )
    temporary.replace(path)


def code_hash() -> str:
    hasher = hashlib.sha256()
    for path in (
        Path(__file__),
        REPO / "src/auto_trading/research/indicators_229.py",
        REPO / "src/auto_trading/research/short_search_229.py",
    ):
        hasher.update(path.relative_to(REPO).as_posix().encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def run() -> None:
    tracked = [
        str(Path(__file__).relative_to(REPO)),
        str(DOC.relative_to(REPO)),
        "src/auto_trading/research/indicators_229.py",
        "src/auto_trading/research/short_search_229.py",
    ]
    for file in tracked:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", file], cwd=REPO, check=True, capture_output=True
        )
    if subprocess.check_output(["git", "diff", "HEAD", "--", *tracked], cwd=REPO):
        raise ValueError("registration/code must be committed before performance")
    grid = configs()
    coins, quality = load()
    engine = Engine(coins)
    manifest_file = OUT / "manifest.json"
    manifest = {
        "code_sha256": code_hash(),
        "source_sha256": quality["source_sha256"],
        "grid_count": len(grid),
        "quality": quality,
        "registration_git": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
    }
    if manifest_file.exists():
        old = json.loads(manifest_file.read_text(encoding="utf8"))
        if any(old[k] != manifest[k] for k in ("code_sha256", "source_sha256", "grid_count")):
            raise ValueError("checkpoint inputs/code changed")
    else:
        save(manifest_file, manifest)
    result_file = OUT / "result.json"
    if result_file.exists():
        raise ValueError("completed one-shot search must not be rerun")
    train_file = OUT / "train.json"
    train_rows = json.loads(train_file.read_text(encoding="utf8")) if train_file.exists() else []
    for index in range(len(train_rows), len(grid)):
        cfg = grid[index]
        trades, counts = engine.trades(cfg, *PERIODS["train"])
        train_rows.append({"key": cfg.key, **summary(trades, *PERIODS["train"]), "counts": counts})
        if (index + 1) % 100 == 0:
            save(train_file, train_rows)
            sys.stdout.write(
                json.dumps({"phase": "TRAIN", "done": index + 1, "total": len(grid)}) + "\n"
            )
            sys.stdout.flush()
    save(train_file, train_rows)
    selection_file = OUT / "selection.json"
    chosen = nominate(train_rows, grid)
    if selection_file.exists() and json.loads(selection_file.read_text(encoding="utf8")) != chosen:
        raise ValueError("frozen TRAIN selection changed")
    save(selection_file, chosen)
    rows_file = OUT / "grid.json"
    rows = json.loads(rows_file.read_text(encoding="utf8")) if rows_file.exists() else []
    for index in range(len(rows), len(grid)):
        cfg = grid[index]
        row: dict[str, Any] = {"key": cfg.key, "config": asdict(cfg), "train": train_rows[index]}
        for period in ("check", "tail", "all"):
            trades, counts = engine.trades(cfg, *PERIODS[period])
            row[period] = {**summary(trades, *PERIODS[period]), "counts": counts}
        rows.append(row)
        if (index + 1) % 100 == 0:
            save(rows_file, rows)
            sys.stdout.write(json.dumps({"phase": "CHECK_TAIL_ALL", "done": index + 1}) + "\n")
            sys.stdout.flush()
    save(rows_file, rows)
    fixed = {
        "H1": Config("RSI", 14, 60, 0, 0, "R5", "ATR72H", "0.03"),
        "H3": Config("STOCH", 0, 0, 14, 80, "R4", "48H", "0.03"),
    }
    by_key = {cfg.key: cfg for cfg in grid}
    for family, pick in chosen.items():
        if pick["key"]:
            fixed[family] = by_key[pick["key"]]
    if "COMBO" in fixed:
        combo = fixed["COMBO"]
        fixed["MATCHED_RSI"] = Config(
            "RSI", combo.rsi_n, combo.rsi_level, 0, 0, combo.regime, combo.exit, combo.stop_pct
        )
        fixed["MATCHED_STOCH"] = Config(
            "STOCH",
            0,
            0,
            combo.stoch_n,
            combo.stoch_level,
            combo.regime,
            combo.exit,
            combo.stop_pct,
        )
    details: dict[str, Any] = {}
    for name, cfg in fixed.items():
        details[name] = {"config": asdict(cfg), "key": cfg.key, "scenarios": {}}
        for label, cost, delay in (("base", 1, 0), ("cost2", 2, 0), ("delay4h", 1, 1)):
            trades, counts = engine.trades(cfg, START, END, cost, delay)
            details[name]["scenarios"][label] = {
                "trades": summary(trades, START, END),
                "counts": counts,
                "account": account(engine, cfg, cost, delay),
            }
            details[name]["scenarios"][label]["periods"] = {
                period: summary(
                    engine.trades(cfg, *PERIODS[period], cost, delay)[0], *PERIODS[period]
                )
                for period in ("train", "check", "tail")
            }
        save(OUT / f"detail_{name}.json", details[name])
    family_results: dict[str, Any] = {}
    for family in ("RSI", "STOCH", "COMBO"):
        group = [r for r in rows if by_key[r["key"]].family == family]
        family_results[family] = {
            "configs": len(group),
            "positive": {p: sum(r[p]["mean_net_bps"] > 0 for r in group) for p in PERIODS},
            "positive_all_three": sum(
                all(r[p]["mean_net_bps"] > 0 for p in ("train", "check", "tail")) for r in group
            ),
            "positive_all_three_train_n100": sum(
                r["train"]["trades"] >= 100
                and all(r[p]["mean_net_bps"] > 0 for p in ("train", "check", "tail"))
                for r in group
            ),
            "empty_all_configs": sum(r["all"]["trades"] == 0 for r in group),
            "median_all_bps": statistics.median(r["all"]["mean_net_bps"] for r in group),
            "train_choice": chosen[family],
            "posthoc_best_all": max(group, key=lambda r: r["all"]["mean_net_bps"])["key"],
        }
    result = {
        "status": "RETROSPECTIVE_EXPLORATION_ONLY_UNVERIFIED",
        "oos_samples": 0,
        "power80": "NOT_CERTIFIED_USER_EXCEPTION",
        "manifest": manifest,
        "families": family_results,
        "selection": chosen,
        "details": details,
    }
    save(result_file, result)
    sys.stdout.write(json.dumps({"phase": "DONE", "result": str(result_file)}) + "\n")


def main() -> None:
    if sys.argv[1:] == ["audit"]:
        coins, quality = load()
        save(
            OUT / "audit.json",
            {
                "quality": quality,
                "grid_count": len(configs()),
                "families": dict(Counter(c.family for c in configs())),
                "symbols": [c.name for c in coins],
                "performance_runs": 0,
            },
        )
        sys.stdout.write(json.dumps({"phase": "AUDIT", "quality": quality}) + "\n")
    elif sys.argv[1:] == ["run"]:
        run()
    else:
        raise ValueError("use audit or run")


if __name__ == "__main__":
    main()
