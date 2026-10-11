# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 217 — 큰 하락 뒤 '추가 하락 실패 + 사전 가격대 회복' 코인 USD-M 롱 · 후향 파일럿. 주문 없음.

사용::

    python -m scripts.run_reclaim_217 audit   # 종목군 · 자료 · 사건 · 신호 수 (손익 없음)
    python -m scripts.run_reclaim_217 run     # 사전 고정(문서 217 §3 ~ §7) 뒤 한 번 → reports/reclaim_217/

규칙은 문서 217 에 고정. 2026-09-01 이후 자료는 읽지 않는다. 일봉 · funding 은 212 와 같은 원천.
"""

from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_resid_rev_212 import (
    MIN_HISTORY_DAYS,
    TOP_N,
    _ms,
    excluded,
    load_daily,
    month_starts,
)
from scripts.run_resid_rev_212_pilot import lot

from auto_trading.research.reclaim_217 import (
    DAY,
    Bar,
    Candidate,
    Config,
    Episode,
    Signal,
    Trade,
    arm_signals,
    cluster_bootstrap,
    find_episodes,
    random_signal,
    replay,
    simulate_trade,
    split_segments,
)

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "reports" / "reclaim_217"
EXINFO = REPO / "data" / "research" / "rr_212" / "exchange_info.json"
LAST_DAY = _ms(date(2026, 8, 31))
EVAL_FIRST = _ms(date(2020, 7, 1))
HALF_SPLIT = _ms(date(2023, 7, 1))
ARMS = ("A", "I", "D", "T")
CFG = Config()
SEED = 217
SUSPEND_DAYS = 7  # §11: 7 일 이상 빠진 곳 = 거래 중단 → 앞 조각은 '자료 끝' 규칙으로 정리 (v1.1)


def _d(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, UTC).date().isoformat()


# ------------------------------------------------------------------ 자료


def load() -> dict[str, Any]:
    daily = load_daily()
    exinfo: dict[str, Any] = json.loads(EXINFO.read_text(encoding="utf-8"))
    first = {s: min(d) for s, d in daily.items() if d}
    members: dict[str, list[str]] = {}
    for m in month_starts():
        t0 = _ms(m)
        cand = []
        for s, d in daily.items():
            if (s != "BTCUSDT" and excluded(s, exinfo)) or first[s] > t0 - MIN_HISTORY_DAYS * DAY:
                continue
            vols = [d.get(t0 - k * DAY) for k in range(1, 31)]
            if any(v is None for v in vols):
                continue
            cand.append((sum(v.qv for v in vols if v), s))
        cand.sort(key=lambda x: (-x[0], x[1]))
        members[m.isoformat()[:7]] = [s for _, s in cand[:TOP_N]]
    syms = sorted({s for v in members.values() for s in v})
    bars: dict[str, list[Bar]] = {}
    for s in syms:
        full = [
            Bar(t, x.o, x.h, x.lo, x.c, x.qv) for t, x in sorted(daily[s].items()) if t <= LAST_DAY
        ]
        for k, seg in enumerate(split_segments(full, SUSPEND_DAYS)):
            bars[s if k == 0 else f"{s}#{k}"] = seg
    db = sqlite3.connect(f"file:{REPO / 'data' / 'trading.db'}?mode=ro", uri=True)
    funding: dict[str, list[tuple[int, float]]] = {}
    for s in syms:
        funding[s] = [
            (int(t), float(r))
            for t, r in db.execute(
                "select funding_ms, rate from funding_rates where exchange='binance_usdm' and symbol=? and funding_ms < ? order by funding_ms",
                (s, LAST_DAY + DAY),
            )
        ]
    return {"members": members, "bars": bars, "funding": funding, "exinfo": exinfo}


def base(key: str) -> str:
    """조각 이름(BNXUSDT#1) → 종목 이름."""
    return key.split("#")[0]


def member_fn(members: dict[str, list[str]], s: str) -> Any:
    sets = {k: set(v) for k, v in members.items()}
    return lambda t: s in sets.get(_d(t)[:7], set())


def episodes_all(data: dict[str, Any]) -> list[tuple[Episode, list[Bar]]]:
    out = []
    for s, b in data["bars"].items():
        for ep in find_episodes(s, b, member_fn(data["members"], base(s)), CFG):
            out.append((ep, b))
    out.sort(key=lambda x: (x[1][x[0].i0].t, x[0].symbol))
    return out


def gaps(b: Sequence[Bar]) -> int:
    return sum(1 for x, y in pairwise(b) if y.t - x.t != DAY)


def funding_gap(fund: Sequence[tuple[int, float]], t0: int, t1: int) -> bool:
    """보유 구간 (t0, t1] 안에서 정산 간격이 그 종목 보통 간격의 1.5 배를 넘으면 True."""
    if len(fund) < 3:
        return True
    ts = [f[0] for f in fund]
    diffs = sorted(b - a for a, b in pairwise(ts))
    usual = diffs[len(diffs) // 2]
    inside = [t for t in ts if t0 < t <= t1]
    pts = [t0, *inside, t1]
    return any(b - a > 1.5 * usual for a, b in pairwise(pts))


# ------------------------------------------------------------------ 감사 (손익 없음)


def audit() -> dict[str, Any]:
    data = load()
    eps = episodes_all(data)
    sig_count: Counter[str] = Counter()
    by_year: Counter[str] = Counter()
    weeks = set()
    for ep, b in eps:
        by_year[_d(b[ep.i0].t)[:4]] += 1
        sg = arm_signals(b, ep, CFG)
        for k in sg:
            sig_count[k] += 1
        if "A" in sg:
            weeks.add(b[ep.i0].t // DAY // 7)
    syms = data["bars"]
    return {
        "months": len(data["members"]),
        "first_nonempty": next(k for k, v in data["members"].items() if v),
        "symbols": len(syms),
        "delisted_now": sorted(s for s in syms if base(s) not in data["exinfo"]),
        "funding_missing": sorted(s for s in syms if not data["funding"][base(s)]),
        "suspension_segments": sorted(s for s in syms if "#" in s),
        "daily_gaps": {s: gaps(b) for s, b in syms.items() if gaps(b)},
        "episodes": len(eps),
        "episodes_by_year": dict(sorted(by_year.items())),
        "signals": dict(sig_count),
        "A_trigger_weeks": len(weeks),
    }


# ------------------------------------------------------------------ 실행


def _sim(data: dict[str, Any], cfg: Config) -> Any:
    bar_t = {s: [x.t for x in b] for s, b in data["bars"].items()}
    fund_t = {s: [f[0] for f in v] for s, v in data["funding"].items()}

    def go(sig: Signal, equity: float) -> Trade | str:
        s, b0 = sig.symbol, base(sig.symbol)
        if not data["funding"][b0]:
            return "funding_missing"
        step, _minq, _mn = lot(data["exinfo"], b0)
        return simulate_trade(
            data["bars"][s],
            sig,
            equity,
            cfg,
            step=step,
            funding=data["funding"][b0],
            last_eval_day=LAST_DAY,
            bar_t=bar_t[s],
            fund_t=fund_t[b0],
        )

    return go


def _stats(trs: Sequence[Trade]) -> dict[str, Any]:
    if not trs:
        return {"n": 0}
    r = sorted(t.r for t in trs)
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    top5 = sorted(trs, key=lambda t: -t.r)[:5]
    top_ids = {id(t) for t in top5}
    rest5 = [t.r for t in trs if id(t) not in top_ids]
    k2 = max(1, round(0.02 * len(r)))
    m, lo, hi = cluster_bootstrap([(t.trigger_t // DAY // 7, t.r) for t in trs], seed=SEED)
    years: dict[str, list[float]] = defaultdict(list)
    for t in trs:
        years[_d(t.entry_t)[:4]].append(t.r)
    h1 = [t.r for t in trs if t.entry_t < HALF_SPLIT]
    h2 = [t.r for t in trs if t.entry_t >= HALF_SPLIT]
    reasons = Counter(t.exit_reason for t in trs)
    return {
        "n": len(r),
        "mean_R": round(m, 4),
        "ci90_R": [round(lo, 4), round(hi, 4)],
        "median_R": round(statistics.median(r), 4),
        "sd_R": round(statistics.stdev(r), 4) if len(r) > 1 else None,
        "hit": round(len(wins) / len(r), 3),
        "avg_win_R": round(statistics.fmean(wins), 3) if wins else None,
        "avg_loss_R": round(statistics.fmean(losses), 3) if losses else None,
        "mean_R_without_top5": round(statistics.fmean(rest5), 4) if rest5 else None,
        "mean_R_without_top2pct": round(statistics.fmean(r[:-k2]), 4) if len(r) > k2 else None,
        "top5": [
            {
                "symbol": t.symbol,
                "entry": _d(t.entry_t),
                "exit": _d(t.exit_t),
                "R": round(t.r, 2),
                "reason": t.exit_reason,
            }
            for t in top5
        ],
        "worst": round(r[0], 3),
        "halves_mean_R": [
            round(statistics.fmean(h1), 4) if h1 else None,
            round(statistics.fmean(h2), 4) if h2 else None,
        ],
        "halves_n": [len(h1), len(h2)],
        "by_year": {y: [len(v), round(statistics.fmean(v), 3)] for y, v in sorted(years.items())},
        "exit_reasons": dict(reasons),
        "avg_hold_days": round(statistics.fmean((t.exit_t - t.entry_t) / DAY for t in trs), 1),
        "open_at_end": sum(t.open_at_end for t in trs),
        "delisted_exits": reasons.get("delisted", 0),
        "gross_R_mean": round(statistics.fmean(t.gross / 25.0 for t in trs), 4),
        "fees_R_mean": round(statistics.fmean(t.fees / 25.0 for t in trs), 4),
        "funding_R_mean": round(statistics.fmean(t.funding / 25.0 for t in trs), 4),
        "emergency_R_mean": round(statistics.fmean(t.emergency / 25.0 for t in trs), 4),
        "avg_dist": round(statistics.fmean(t.dist for t in trs), 4),
        "avg_notional_pct": round(statistics.fmean(t.notional / CFG.equity0 for t in trs) * 100, 2),
    }


def event_level(
    eps: Sequence[tuple[Episode, list[Bar]]], data: dict[str, Any], cfg: Config
) -> tuple[dict[str, dict[tuple[str, int], Trade]], dict[str, Counter[str]]]:
    go = _sim(data, cfg)
    out: dict[str, dict[tuple[str, int], Trade]] = {a: {} for a in ARMS}
    skips: dict[str, Counter[str]] = {a: Counter() for a in ARMS}
    for ep, b in eps:
        sg = arm_signals(b, ep, cfg)
        for a in ARMS:
            if a not in sg:
                skips[a]["no_signal"] += 1
                continue
            res = go(sg[a], cfg.equity0)
            if isinstance(res, str):
                skips[a][res] += 1
            else:
                out[a][(ep.symbol, ep.i0)] = res
    return out, skips


def paired(x: dict[tuple[str, int], Trade], y: dict[tuple[str, int], Trade]) -> dict[str, Any]:
    keys = sorted(set(x) & set(y), key=lambda k: (x[k].trigger_t, k[0]))
    items = [(x[k].trigger_t // DAY // 7, x[k].r - y[k].r) for k in keys]
    if not items:
        return {"n": 0}
    m, lo, hi = cluster_bootstrap(items, seed=SEED)
    h1 = [x[k].r - y[k].r for k in keys if x[k].entry_t < HALF_SPLIT]
    h2 = [x[k].r - y[k].r for k in keys if x[k].entry_t >= HALF_SPLIT]
    return {
        "n_pairs": len(items),
        "only_first": len(set(x) - set(y)),
        "only_second": len(set(y) - set(x)),
        "mean_diff_R": round(m, 4),
        "ci90": [round(lo, 4), round(hi, 4)],
        "halves": [
            round(statistics.fmean(h1), 4) if h1 else None,
            round(statistics.fmean(h2), 4) if h2 else None,
        ],
        "share_positive": round(sum(d > 0 for _, d in items) / len(items), 3),
    }


def random_timing(
    eps: Sequence[tuple[Episode, list[Bar]]],
    a_trades: dict[tuple[str, int], Trade],
    data: dict[str, Any],
    cfg: Config,
) -> dict[str, Any]:
    go = _sim(data, cfg)
    rng = random.Random(SEED)
    target = [(ep, b) for ep, b in eps if (ep.symbol, ep.i0) in a_trades]
    means = []
    for _ in range(100):
        rs = []
        for ep, b in target:
            k = rng.randint(1, cfg.random_max)
            res = go(random_signal(b, ep, k), cfg.equity0)
            if isinstance(res, Trade):
                rs.append(res.r)
        means.append(statistics.fmean(rs) if rs else math.nan)
    a_mean = statistics.fmean(t.r for t in a_trades.values())
    srt = sorted(means)
    return {
        "draws": len(means),
        "random_mean_R_median": round(statistics.median(srt), 4),
        "random_p10_p90": [round(srt[9], 4), round(srt[89], 4)],
        "A_mean_R": round(a_mean, 4),
        "A_rank_above": sum(a_mean > m for m in means),
    }


def account(
    eps: Sequence[tuple[Episode, list[Bar]]], data: dict[str, Any], arm: str, cfg: Config
) -> dict[str, Any]:
    go = _sim(data, cfg)
    cands = []
    for ep, b in eps:
        sg = arm_signals(b, ep, cfg)
        if arm not in sg:
            continue
        s = sg[arm]
        if s.i + 1 >= len(b):
            continue
        pri = sum(x.qv for x in b[max(0, s.i - 29) : s.i + 1])
        cands.append(Candidate(s, b[s.i + 1].t, pri))
    days = list(range(EVAL_FIRST, LAST_DAY + DAY, DAY))
    res = replay(cands, go, days, cfg)
    trades: list[Trade] = res["trades"]  # type: ignore[assignment]
    daily: list[tuple[int, float, float]] = res["daily"]  # type: ignore[assignment]
    eq = [v for _, v, _ in daily]
    rets = [eq[k] / eq[k - 1] - 1 for k in range(1, len(eq))]
    sd = statistics.stdev(rets)
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    years = len(rets) / 365
    by_year: dict[str, float] = {}
    for (_d0, v0, _), (d1, v1, _) in pairwise(daily):
        by_year[_d(d1)[:4]] = by_year.get(_d(d1)[:4], 0.0) + (v1 - v0)
    expo = [x for _, _, x in daily]
    return {
        "candidates": len(cands),
        "trades": len(trades),
        "skips": res["skips"],
        "halted_at": _d(res["halted_at"]) if res["halted_at"] else None,  # type: ignore[arg-type]
        "net_pnl": round(eq[-1] - cfg.equity0, 2),
        "cagr_pct": round(((eq[-1] / eq[0]) ** (1 / years) - 1) * 100, 3),
        "sharpe": round(statistics.fmean(rets) / sd * math.sqrt(365), 3) if sd > 0 else 0.0,
        "mdd_pct": round(mdd * 100, 2),
        "avg_exposure_pct": round(statistics.fmean(expo) * 100, 2),
        "max_exposure_pct": round(max(expo) * 100, 2),
        "margin_note": "격리 1 배: 증거금 = 명목 (평균 증거금 % = 평균 노출 %)",
        "turnover_x": round(sum(t.notional + t.qty * t.exit_px for t in trades) / cfg.equity0, 2),
        "gross_before_costs": round(sum(t.gross for t in trades), 2),
        "fees": round(sum(t.fees for t in trades), 2),
        "funding": round(sum(t.funding for t in trades), 2),
        "emergency": round(sum(t.emergency for t in trades), 2),
        "open_at_end": sum(t.open_at_end for t in trades),
        "by_year_pnl": {y: round(v, 2) for y, v in sorted(by_year.items())},
        "mean_R_taken": round(statistics.fmean(t.r for t in trades), 4) if trades else None,
        "_daily": daily,
        "_trades": trades,
    }


def run() -> dict[str, Any]:
    target = REPORT / "result_v1_1.json"  # v1(result.json) 은 §11 실행 결함 전 기록으로 남긴다
    if target.exists():
        raise SystemExit(f"이미 실행했다: {target}")
    data = load()
    eps = episodes_all(data)
    res: dict[str, Any] = {
        "computed": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": asdict(CFG),
        "episodes": len(eps),
    }
    ev, skips = event_level(eps, data, CFG)
    res["event"] = {a: _stats(list(ev[a].values())) for a in ARMS}
    res["event_skips"] = {a: dict(skips[a]) for a in ARMS}
    res["paired"] = {
        "A-T": paired(ev["A"], ev["T"]),
        "A-D": paired(ev["A"], ev["D"]),
        "A-I": paired(ev["A"], ev["I"]),
    }
    res["funding_gap_trades_diag"] = {
        a: sum(
            funding_gap(
                data["funding"][base(t.symbol)],
                t.entry_t,
                t.exit_t + DAY - 1 if t.open_at_end else t.exit_t + DAY // 2,
            )
            for t in ev[a].values()
        )
        for a in ARMS
    }
    res["random_timing"] = random_timing(eps, ev["A"], data, CFG)
    sens = {
        "cost_x2": replace(CFG, cost=0.0019),
        "worst_fill": replace(CFG, worst_fill=True),
        "emergency_2pct": replace(CFG, emergency=0.02),
        "funding_off_diag": replace(CFG, use_funding=False),
        "realistic_cost_ref": replace(CFG, cost=0.00023),
    }
    res["sensitivity"] = {}
    for name, cfg in sens.items():
        e2, _ = event_level(eps, data, cfg)
        a = list(e2["A"].values())
        res["sensitivity"][name] = {
            "A_mean_R": round(statistics.fmean(t.r for t in a), 4) if a else None,
            "A-T_mean_diff": paired(e2["A"], e2["T"]).get("mean_diff_R"),
        }
    REPORT.mkdir(parents=True, exist_ok=True)
    res["account"] = {}
    for arm in ARMS:
        acc = account(eps, data, arm, CFG)
        daily = acc.pop("_daily")
        taken = acc.pop("_trades")
        res["account"][arm] = acc
        with (REPORT / f"daily_{arm}.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "equity", "exposure"])
            for d, v, x in daily:
                w.writerow([_d(d), round(v, 4), round(x, 5)])
        _write_trades(REPORT / f"trades_account_{arm}.csv", taken)
        _write_trades(REPORT / f"trades_event_{arm}.csv", list(ev[arm].values()))
    res["verdict"] = verdict(res)
    target.write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return res


def _write_trades(path: Path, trades: Sequence[Trade]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "symbol",
                "trigger",
                "signal",
                "entry",
                "exit",
                "entry_kind",
                "exit_reason",
                "qty",
                "entry_px",
                "exit_px",
                "dist",
                "notional",
                "gross",
                "fees",
                "funding",
                "emergency",
                "net",
                "R",
                "open_at_end",
            ]
        )
        for t in sorted(trades, key=lambda x: (x.entry_t, x.symbol)):
            w.writerow(
                [
                    t.symbol,
                    _d(t.trigger_t),
                    _d(t.signal_t),
                    _d(t.entry_t),
                    _d(t.exit_t),
                    t.entry_kind,
                    t.exit_reason,
                    t.qty,
                    round(t.entry_px, 8),
                    round(t.exit_px, 8),
                    round(t.dist, 4),
                    round(t.notional, 2),
                    round(t.gross, 4),
                    round(t.fees, 4),
                    round(t.funding, 4),
                    round(t.emergency, 4),
                    round(t.net, 4),
                    round(t.r, 4),
                    t.open_at_end,
                ]
            )


def verdict(res: dict[str, Any]) -> dict[str, Any]:
    """문서 217 §7 — 이 순서로 첫 번째 맞는 것."""
    a = res["event"]["A"]
    acc = res["account"]["A"]
    at, ad = res["paired"]["A-T"], res["paired"]["A-D"]
    rt = res["random_timing"]
    sens = res["sensitivity"]
    checks = {
        "A_mean_R_pos": a["n"] > 0 and a["mean_R"] > 0,
        "A_account_pos": acc["net_pnl"] > 0,
        "A-T_ci90_low_pos": at["ci90"][0] > 0,
        "A-T_halves_pos": all(h is not None and h > 0 for h in at["halves"]),
        "A-D_pos": ad["mean_diff_R"] > 0,
        "A_above_random_p90": rt["A_rank_above"] >= 90,
        "A_without_top5_pos": (a["mean_R_without_top5"] or 0) > 0,
        "A_cost_x2_pos": (sens["cost_x2"]["A_mean_R"] or 0) > 0,
        "A_worst_fill_pos": (sens["worst_fill"]["A_mean_R"] or 0) > 0,
        "A-T_ci90_high_neg": at["ci90"][1] < 0,
    }
    if not (checks["A_mean_R_pos"] and checks["A_account_pos"]):
        v = "종료(불리)"
    elif all(
        checks[k]
        for k in (
            "A-T_ci90_low_pos",
            "A-T_halves_pos",
            "A-D_pos",
            "A_above_random_p90",
            "A_without_top5_pos",
            "A_cost_x2_pos",
            "A_worst_fill_pos",
        )
    ):
        v = "새 기간 관찰 가치 있음"
    elif checks["A-T_ci90_high_neg"]:
        v = "종료(단순 규칙보다 나쁨)"
    else:
        v = "판단 불가"
    return {"conclusion": v, "checks": checks}


def main(argv: list[str]) -> int:
    if argv == ["audit"]:
        res = audit()
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / "audit.json").write_text(
            json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    elif argv == ["run"]:
        res = run()
    else:
        raise SystemExit("usage: python -m scripts.run_reclaim_217 audit|run")
    sys.stdout.write(json.dumps(res, indent=1, ensure_ascii=False, default=str)[:20000] + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
