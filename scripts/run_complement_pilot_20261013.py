"""One-shot role-complement pilot. Read-only data, fixed costs, no operating imports."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from itertools import pairwise
from typing import Any

from auto_trading.research import complement_20261013 as roles
from auto_trading.research.short_search_229 import H4, Coin
from scripts import run_rsi_stoch_pilot_20261012 as base

REPO = base.REPO
DOC = REPO / "docs/research/MULTI_INDICATOR_COMPLEMENT_DRAFT_20261012.md"
OUT = REPO / "reports/multi_indicator_complement_20261013"
TRIALS = 9234 + 3840
CODE_FILES = (
    "scripts/run_complement_pilot_20261013.py",
    "src/auto_trading/research/complement_20261013.py",
    "scripts/run_rsi_stoch_pilot_20261012.py",
    "src/auto_trading/research/short_search_229.py",
    "src/auto_trading/research/indicators_229.py",
)


def ledger_config(cfg: roles.RoleConfig) -> base.Config:
    return base.Config(cfg.key, 0, 0, 0, 0, "R0", "48H", "0.03")


class Engine(base.Engine):
    def __init__(self, coins: list[Coin]) -> None:
        super().__init__(coins)
        self.role_data = {c.name: roles.masks(c) for c in coins}
        self.role_signals: dict[tuple[str, str], list[int]] = {}

    def signals(self, c: Coin, cfg: base.Config) -> list[int]:
        if ":" not in cfg.mode:
            return super().signals(c, cfg)
        key = (c.name, cfg.mode)
        if key not in self.role_signals:
            role_cfg = roles.RoleConfig(*cfg.mode.split(":"))
            self.role_signals[key] = roles.signals(c, self.role_data[c.name], role_cfg)
        return self.role_signals[key]


def summary(episodes: list[base.Episode], start_ms: int, end_ms: int) -> dict[str, Any]:
    result = base.summary(episodes, start_ms, end_ms)
    result["bonferroni_normal_p"] = min(
        1.0, TRIALS * math.erfc(result["weekly_hac_t"] / math.sqrt(2)) / 2
    )
    return result


def nominate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pool = [r for r in rows if r["trades"] >= 100]
    eligible = [
        r
        for r in pool
        if r["unresolved"] == 0
        and all(x["trades"] >= 20 and x["mean_bps"] > 0 for x in r["symbols"].values())
    ]
    winner = max(eligible or pool, key=lambda r: (r["weekly_hac_t"], r["key"]), default=None)
    return {
        "key": winner["key"] if winner else None,
        "train_eligible": bool(eligible),
        "eligible_count": len(eligible),
        "n100_count": len(pool),
    }


def account(
    engine: base.Engine,
    cfg: base.Config,
    start_ms: int,
    end_ms: int,
    cost: int = 1,
    delay: int = 0,
    size_scale: Decimal = base.ONE,
) -> dict[str, Any]:
    """Same frozen ledger as the prior pilot; only interval and TRAIN-fixed size scale added."""
    if (
        not base.START <= start_ms < end_ms <= base.END
        or not start_ms % base.DAY == end_ms % base.DAY == 0
    ):
        raise ValueError("account interval outside registered closed-day boundary")
    if not base.ZERO < size_scale <= base.ONE or cost not in (1, 2) or delay not in (0, 1):
        raise ValueError("unregistered cost/delay/scale")
    entries: dict[tuple[str, int], base.Episode] = {}
    for c in engine.coins.values():
        for i in engine.signals(c, cfg):
            if start_ms <= c.t[i] + H4 < end_ms - base.PURGE and c.regime[cfg.regime][i]:
                ep = engine.episode(c, i, cfg, end_ms, cost, delay)
                if ep is not None:
                    entries[c.name, ep.entry_ms] = ep
    bars = {s: {t: i for i, t in enumerate(c.t)} for s, c in engine.coins.items()}
    initial = base.dec(10000)
    wallet = peak = day_start = initial
    active: dict[str, tuple[base.Episode, Decimal]] = {}
    curve: list[Decimal] = []
    exposures: list[float] = []
    fees = funding = turnover = mdd = worst_mdd = base.ZERO
    halted = False
    loss_streak, cooldown_ms = 0, -1
    counts: Counter[str] = Counter()
    realized: list[Decimal] = []
    symbol_pnl = dict.fromkeys(base.SYMBOLS, base.ZERO)

    def equity_at(t: int, field: str) -> Decimal:
        return wallet + sum(
            (
                q * (ep.entry_px - base.dec(getattr(engine.coins[s], field)[bars[s][t]]))
                for s, (ep, q) in active.items()
            ),
            base.ZERO,
        )

    def close_position(s: str, at_ms: int) -> None:
        nonlocal wallet, fees, turnover, loss_streak, cooldown_ms
        ep, qty = active.pop(s)
        exit_fee = qty * ep.exit_px * base.FEE * cost
        wallet += qty * (ep.entry_px - ep.exit_px) - exit_fee
        fees += exit_fee
        turnover += qty * ep.exit_px
        net = qty * ep.entry_px * ep.net
        realized.append(net)
        symbol_pnl[s] += net
        loss_streak = loss_streak + 1 if net < 0 else 0
        if loss_streak >= 4:
            cooldown_ms, loss_streak = at_ms + 240 * 60000, 0
        counts["closed"] += 1

    for t in range(start_ms, end_ms, H4):
        if t % base.DAY == 0:
            day_start = equity_at(t, "o")
        for s in list(active):
            ep, qty = active[s]
            if ep.exit_ms == t:
                if qty > base.dec(engine.coins[s].v[bars[s][t] - 1]) * base.PARTICIPATION:
                    raise ValueError("ordinary exit exceeds predeclared volume participation")
                close_position(s, t)
        gross_open = sum(
            (q * base.dec(engine.coins[s].o[bars[s][t]]) for s, (_, q) in active.items()), base.ZERO
        )
        for s in base.SYMBOLS:
            ep = entries.get((s, t))
            if ep is None or s in active:
                continue
            equity = equity_at(t, "o")
            if halted or equity <= 0 or equity <= day_start * base.dec("0.98") or t < cooldown_ms:
                counts["risk_veto"] += 1
                continue
            budget = (
                base.dec(cfg.stop_pct)
                + (base.STOP_SLIP + base.SLIP) * cost * (base.ONE + base.dec(cfg.stop_pct))
                + base.FEE * cost * (2 + base.dec(cfg.stop_pct))
            )
            notional = min(
                equity * base.dec("0.25") * size_scale,
                equity * base.dec("0.01") / budget,
                max(base.ZERO, equity - gross_open),
            )
            c, j = engine.coins[s], bars[s][t]
            qty = min(notional / ep.entry_px, base.dec(c.v[j - 1]) * base.PARTICIPATION)
            qty = (qty / base.STEP).to_integral_value(rounding=ROUND_FLOOR) * base.STEP
            if qty <= 0:
                counts["size_zero"] += 1
                continue
            fee = qty * ep.entry_px * base.FEE * cost
            wallet -= fee
            fees += fee
            turnover += qty * ep.entry_px
            active[s] = ep, qty
            gross_open += qty * ep.entry_px
            counts["opened"] += 1
        worst = equity_at(t, "h")
        worst_mdd = max(worst_mdd, base.ONE - worst / peak)
        for s in list(active):
            ep, qty = active[s]
            c, j = engine.coins[s], bars[s][t]
            a, b = bisect.bisect_right(c.fund_t, t), bisect.bisect_right(c.fund_t, t + H4)
            total, negative = engine.fund_prefix[s]
            amount = qty * (
                (negative[b] - negative[a])
                if ep.stopped and ep.exit_i == j
                else (total[b] - total[a])
            )
            wallet += amount
            funding += amount
            if ep.stopped and ep.exit_ms == t + H4:
                close_position(s, t + H4)
        equity = equity_at(t, "c")
        peak = max(peak, equity)
        mdd = max(mdd, base.ONE - equity / peak)
        if worst <= peak * base.dec("0.70") and not halted:
            halted = True
            counts["dd_halt"] += 1
        exposures.append(
            float(
                sum(
                    (
                        qty * base.dec(engine.coins[s].c[bars[s][t]])
                        for s, (_, qty) in active.items()
                    ),
                    base.ZERO,
                )
                / equity
            )
            if equity > 0
            else 0
        )
        curve.append(equity)
    terminal = curve[-1]
    residual = abs(terminal - initial - sum(realized, base.ZERO)) if not active else None
    changes = [float(b / a - 1) for a, b in pairwise([initial, *curve[5::6]]) if a > 0]
    years = (end_ms - start_ms) / (365.25 * base.DAY)
    return {
        "net_usdt": float(terminal - initial),
        "cagr": float(terminal / initial) ** (1 / years) - 1,
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
        "accounting_residual_usdt": float(residual) if residual is not None else None,
        "halted": halted,
        "daily_returns": {
            str(datetime.fromtimestamp(start_ms / 1000 + i * 86400, UTC).date()): r
            for i, r in enumerate(changes)
        },
        "size_scale": str(size_scale),
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def counterfactual(
    engine: Engine, selected: roles.RoleConfig, start_ms: int, end_ms: int
) -> dict[str, Any]:
    unfiltered = roles.RoleConfig(selected.trigger, "R0", "NONE", "NONE", "NONE")
    episodes = engine.trades(ledger_config(unfiltered), start_ms, end_ms)[0]
    skipped = [
        e
        for e in episodes
        if not roles.admitted(
            engine.coins[e.symbol], engine.role_data[e.symbol], selected, e.signal
        )
    ]
    avoided = -sum((min(e.net, base.ZERO) for e in skipped), base.ZERO)
    missed = sum((max(e.net, base.ZERO) for e in skipped), base.ZERO)
    by_role: dict[str, Any] = {}
    for role in ("backdrop", "confirm", "participation", "risk"):
        rejected = []
        for e in episodes:
            c, data, i = engine.coins[e.symbol], engine.role_data[e.symbol], e.signal
            allowed = {
                "backdrop": c.regime[selected.backdrop][i],
                "confirm": data.confirms[selected.confirm][i],
                "participation": data.participations[selected.participation][i],
                "risk": data.risks[selected.risk][i],
            }
            if not allowed[role]:
                rejected.append(e)
        by_role[role] = {
            "rejected": len(rejected),
            "avoided_loss_unit_bps": float(
                -sum((min(e.net, base.ZERO) for e in rejected), base.ZERO) * 10000
            ),
            "missed_profit_unit_bps": float(
                sum((max(e.net, base.ZERO) for e in rejected), base.ZERO) * 10000
            ),
            "net_skipping_unit_bps": float(-sum((e.net for e in rejected), base.ZERO) * 10000),
        }
    return {
        "baseline_episodes": len(episodes),
        "skipped": len(skipped),
        "avoided_loss_unit_bps": float(avoided * 10000),
        "missed_profit_unit_bps": float(missed * 10000),
        "net_skipping_unit_bps": float((avoided - missed) * 10000),
        "by_role_overlapping_not_additive": by_role,
        "interpretation": (
            "fixed baseline episodes/unit-notional counterfactual, "
            "not endogenous account causal attribution"
        ),
    }


def excursions(engine: base.Engine, episodes: list[base.Episode]) -> dict[str, Any]:
    mfe, mae = [], []
    for ep in episodes:
        c = engine.coins[ep.symbol]
        # Exclude the entire final bar: exit ordering is unknown / occurs at its open.
        lows = c.lo[ep.entry_i : ep.exit_i]
        highs = c.h[ep.entry_i : ep.exit_i]
        mfe.append(
            float(max(base.ZERO, (ep.entry_px - base.dec(min(lows))) / ep.entry_px)) * 1e4
            if lows
            else 0
        )
        mae.append(
            float(max(base.ZERO, (base.dec(max(highs)) - ep.entry_px) / ep.entry_px)) * 1e4
            if highs
            else 0
        )
    return {
        "mean_mfe_lower_bound_bps": statistics.fmean(mfe) if mfe else None,
        "mean_mae_lower_bound_bps": statistics.fmean(mae) if mae else None,
        "exit_bar_excluded": True,
        "trades": len(episodes),
    }


def mask_audit(engine: Engine) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s, c in engine.coins.items():
        m = engine.role_data[s]
        indexes = [i for i, t in enumerate(c.t) if base.START <= t + H4 < base.END]
        index_set = set(indexes)
        masks = {
            **{f"confirm_{k}": v for k, v in m.confirms.items()},
            **{f"participation_{k}": v for k, v in m.participations.items()},
            **{f"risk_{k}": v for k, v in m.risks.items()},
            **{f"backdrop_{k}": c.regime[k] for k in roles.BACKDROPS},
        }
        pairs = []
        keys = [k for k in masks if not k.endswith(("NONE", "R0"))]
        for p, a in enumerate(keys):
            for b in keys[p + 1 :]:
                x, y = masks[a], masks[b]
                union = sum(x[i] or y[i] for i in indexes)
                intersection = sum(x[i] and y[i] for i in indexes)
                pairs.append({"a": a, "b": b, "jaccard": intersection / union if union else None})
        out[s] = {
            "trigger_counts": {k: sum(i in index_set for i in v) for k, v in m.triggers.items()},
            "mask_true_counts": {k: sum(v[i] for i in indexes) for k, v in masks.items()},
            "divergence_unknown_bars": sum(not m.pivots.known[i] for i in indexes),
            "divergence_event_counts": dict(
                Counter(label for _, available, label in m.pivots.events if available in index_set)
            ),
            "pairwise_jaccard": pairs,
            "elliott": {
                "status": "NOT_AVAILABLE",
                "reason": (
                    "this frozen loader has 1h-derived4h OHLCV, not the required "
                    "minute-path/subdivision evidence; no verified wave label "
                    "or wave-conditioned PnL fabricated"
                ),
                "order_effect": False,
            },
        }
    return out


def code_hash() -> str:
    h = hashlib.sha256()
    for file in CODE_FILES:
        h.update(file.encode())
        h.update((REPO / file).read_bytes())
    return h.hexdigest()


def ensure_registered() -> None:
    tracked = [
        *CODE_FILES,
        str(DOC.relative_to(REPO)),
        "tests/scripts/test_complement_pilot_20261013.py",
    ]
    for file in tracked:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", file], cwd=REPO, check=True, capture_output=True
        )
    if subprocess.check_output(["git", "diff", "HEAD", "--", *tracked], cwd=REPO):
        raise ValueError("registration/code/tests must be committed before outcomes")


def run() -> None:
    ensure_registered()
    if (OUT / "result.json").exists():
        raise ValueError("completed fixed one-shot search cannot be rerun")
    grid = roles.configs()
    coins, quality = base.load()
    engine = Engine(coins)
    manifest = {
        "code_sha256": code_hash(),
        "source_sha256": quality["source_sha256"],
        "grid_count": len(grid),
        "quality": quality,
        "registration_sha256": hashlib.sha256(DOC.read_bytes()).hexdigest(),
        "registration_git": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
    }
    mf = OUT / "manifest.json"
    if mf.exists():
        old = json.loads(mf.read_text(encoding="utf8"))
        if any(
            old[k] != manifest[k]
            for k in ("code_sha256", "source_sha256", "registration_sha256", "grid_count")
        ):
            raise ValueError("checkpoint code/data/design changed")
    else:
        base.save(mf, manifest)
    base.save(OUT / "mask_audit.json", mask_audit(engine))
    train_file = OUT / "train.json"
    train = json.loads(train_file.read_text(encoding="utf8")) if train_file.exists() else []
    for index in range(len(train), len(grid)):
        cfg = grid[index]
        episodes, counts = engine.trades(ledger_config(cfg), *base.PERIODS["train"])
        train.append(
            {"key": cfg.key, **summary(episodes, *base.PERIODS["train"]), "counts": counts}
        )
        if (index + 1) % 100 == 0:
            base.save(train_file, train)
            sys.stdout.write(
                json.dumps({"phase": "TRAIN", "done": index + 1, "total": len(grid)}) + "\n"
            )
            sys.stdout.flush()
    base.save(train_file, train)
    chosen = nominate(train)
    selection_file = OUT / "selection.json"
    if selection_file.exists() and json.loads(selection_file.read_text(encoding="utf8")) != chosen:
        raise ValueError("frozen TRAIN selection changed")
    base.save(selection_file, chosen)
    rows_file = OUT / "grid.json"
    rows = json.loads(rows_file.read_text(encoding="utf8")) if rows_file.exists() else []
    for index in range(len(rows), len(grid)):
        cfg = grid[index]
        row: dict[str, Any] = {"key": cfg.key, "config": asdict(cfg), "train": train[index]}
        for label in ("check", "tail", "all"):
            episodes, counts = engine.trades(ledger_config(cfg), *base.PERIODS[label])
            row[label] = {**summary(episodes, *base.PERIODS[label]), "counts": counts}
        rows.append(row)
        if (index + 1) % 100 == 0:
            base.save(rows_file, rows)
            sys.stdout.write(json.dumps({"phase": "CHECK_TAIL_ALL", "done": index + 1}) + "\n")
            sys.stdout.flush()
    base.save(rows_file, rows)
    details: dict[str, Any] = {}
    fixed = {
        f"BASE_{t}": ledger_config(roles.RoleConfig(t, "R0", "NONE", "NONE", "NONE"))
        for t in roles.TRIGGERS
    }
    fixed.update(
        {
            "H1_REFERENCE": base.Config("RSI", 14, 60, 0, 0, "R5", "ATR72H", "0.03"),
            "PRIOR_RSI_REFERENCE": base.Config("RSI", 7, 70, 0, 0, "R5", "48H", "0.03"),
        }
    )
    selected = roles.RoleConfig(*chosen["key"].split(":")) if chosen["key"] else None
    scale = base.ONE
    if selected is not None:
        fixed["SELECTED"] = ledger_config(selected)
        for role, empty in (
            ("backdrop", "R0"),
            ("confirm", "NONE"),
            ("participation", "NONE"),
            ("risk", "NONE"),
        ):
            fixed[f"WITHOUT_{role}"] = ledger_config(replace(selected, **{role: empty}))
        full_baseline = fixed[f"BASE_{selected.trigger}"]
        a = account(engine, fixed["SELECTED"], *base.PERIODS["train"])["mean_exposure"]
        b = account(engine, full_baseline, *base.PERIODS["train"])["mean_exposure"]
        scale = min(base.ONE, max(base.dec("0.000001"), base.dec(a / b))) if b > 0 else base.ONE
        # Scale is fixed from TRAIN once; no exact matching to unseen period exposure.
        fixed["EXPOSURE_MATCHED_BASE"] = full_baseline
    for name, frozen_cfg in fixed.items():
        details[name] = {"config": asdict(frozen_cfg), "scenarios": {}}
        for label, cost, delay in (("base", 1, 0), ("cost2", 2, 0), ("delay4h", 1, 1)):
            scale_value = scale if name == "EXPOSURE_MATCHED_BASE" else base.ONE
            periods: dict[str, Any] = {}
            for period, bounds in base.PERIODS.items():
                episodes, counts = engine.trades(frozen_cfg, *bounds, cost, delay)
                periods[period] = {
                    "trades": summary(episodes, *bounds),
                    "counts": counts,
                    "account": account(engine, frozen_cfg, *bounds, cost, delay, scale_value),
                    "excursions": excursions(engine, episodes),
                }
            details[name]["scenarios"][label] = periods
        base.save(OUT / f"detail_{name}.json", details[name])
        sys.stdout.write(json.dumps({"phase": "ACCOUNT", "name": name}) + "\n")
        sys.stdout.flush()
    counter = (
        {p: counterfactual(engine, selected, *bounds) for p, bounds in base.PERIODS.items()}
        if selected
        else {}
    )
    report = {
        "status": "RETROSPECTIVE_EXPLORATION_ONLY_UNVERIFIED",
        "power80": "NOT_CERTIFIED_USER_EXCEPTION_20261013",
        "oos_samples": 0,
        "manifest": manifest,
        "selection": chosen,
        "size_scale_train_fixed": str(scale),
        "grid": {
            "configs": len(grid),
            "positive": {p: sum(row[p]["mean_net_bps"] > 0 for row in rows) for p in base.PERIODS},
            "positive_all_three": sum(
                all(row[p]["mean_net_bps"] > 0 for p in ("train", "check", "tail")) for row in rows
            ),
            "empty_all": sum(row["all"]["trades"] == 0 for row in rows),
            "median_all_bps": statistics.median(row["all"]["mean_net_bps"] for row in rows),
            "historical_trial_count_minimum": TRIALS,
        },
        "counterfactual": counter,
        "details": details,
    }
    base.save(OUT / "result.json", report)
    sys.stdout.write(json.dumps({"phase": "DONE", "result": str(OUT / "result.json")}) + "\n")


def main() -> None:
    if sys.argv[1:] == ["audit"]:
        coins, quality = base.load()
        engine = Engine(coins)
        base.save(
            OUT / "audit.json",
            {
                "quality": quality,
                "grid_count": len(roles.configs()),
                "code_sha256": code_hash(),
                "masks": mask_audit(engine),
                "performance_runs": 0,
            },
        )
        sys.stdout.write(
            json.dumps({"phase": "AUDIT", "quality": quality, "grid_count": len(roles.configs())})
            + "\n"
        )
    elif sys.argv[1:] == ["run"]:
        run()
    else:
        raise ValueError("use audit or run")


if __name__ == "__main__":
    main()
