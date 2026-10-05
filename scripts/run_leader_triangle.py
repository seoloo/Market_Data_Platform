"""문서 146 — 주도주(141) 안에서 어센딩 트라이앵글 돌파 날에만 처음 산다. 1회 판정.

사용 예::

    python -m scripts.run_leader_triangle audit   # 돌파 진입 수 (수익 없음)
    python -m scripts.run_leader_triangle run     # 깨끗한 트리 · 141 자료 해시 · 판정 파일 없음

종목 고르기·추세·시장 여지·추매·매도·비용은 141(``scripts.run_lt_kr``) 그대로다. 바뀌는 것은
**처음 사는 때** 하나: 그달 주도주 목록에 있고 141 진입 조건을 만족하는 날 중 그날 종가로
어센딩 트라이앵글 '돌파'(``ops.leader_alert.ascending_triangle``, 종가로 판정)가 나온 날 →
다음 거래일 종가에 10%. 위약은 같은 (종목, 달) 에서 무작위 하루에 산다.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_lt_kr import (
    DATA,
    EXPECTED_DATA,
    WATCH,
    KrPanel,
    _iso,
    _last_px,
    build_panel,
    data_hash,
    equal_weight_pit,
    leaders_pit,
    members,
    simulate_kr,
)
from scripts.run_lt_leaders import (
    FEE,
    FROM,
    MAX_POS,
    SPLIT,
    STOP_FROM_PEAK,
    TARGET,
    stats,
    trend_off,
    trend_up,
)

from auto_trading.ops.leader_alert import WINDOW, ascending_triangle

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "leader_triangle"
SEEDS = 100
MIN_ENTRIES = 30
FALSE_BREAK_DAYS = 10
HALF_END = "2016-12-31"

EntryRule = Callable[[str, int], bool]


def breakout(xs: Sequence[float | None], t: int) -> tuple[bool, float | None]:
    """(그날 종가로 '돌파'인가, 저항선). 61 거래일 중 거래 없는 날이 있으면 판정하지 않는다."""
    if t < WINDOW:
        return False, None
    window = xs[t - WINDOW : t + 1]
    if any(v is None for v in window):
        return False, None
    vals: list[float] = [v for v in window if v is not None]
    tri = ascending_triangle(vals, vals, vals)
    return tri.status == "돌파", tri.resistance


def month_plan(panel: KrPanel, start: int) -> tuple[dict[int, list[str]], dict[str, list[str]]]:
    """({월말 색인: 다음 달 주도주(점수 순)}, {월말 날짜: 종목군}) — 141 과 같은 계산."""
    plan: dict[int, list[str]] = {}
    universe: dict[str, list[str]] = {}
    days = panel.days
    for t in range(start - 1, len(days) - 1):
        if days[t + 1][:7] != days[t][:7]:
            pool = members(panel, t)
            universe[days[t]] = pool
            plan[t] = [s for s, _ in leaders_pit(panel, t, pool)]
    return plan, universe


@dataclass(slots=True)
class EntryRun:
    equity: list[float] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)
    trades: list[dict[str, object]] = field(default_factory=list)
    entries: list[dict[str, object]] = field(default_factory=list)
    """처음 산 기록: 종목 · 신호일 · 체결일 · 신호 달 · 저항선."""


def simulate_entry(
    panel: KrPanel,
    index_ok: Sequence[bool],
    plan: Mapping[int, Sequence[str]],
    entry: EntryRule,
    start_date: str = FROM,
) -> EntryRun:
    """141 ``simulate_kr`` 와 같은 순서 · 같은 매도. 처음 사는 때만 ``entry`` 가 정한다."""
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= start_date)
    cash = 1.0
    pos: dict[str, list[float]] = {}  # 수량 · 원가 · 진입 뒤 최고 · 진입 색인
    run = EntryRun()
    pending_sell: dict[str, str] = {}
    pending_new: list[tuple[str, int]] = []
    topup = False
    active: list[str] = list(plan.get(start - 1, []))

    def sell(sym: str, t: int, reason: str) -> None:
        nonlocal cash
        q, cost, _peak, i0 = pos.pop(sym)
        proceeds = q * _last_px(panel, sym, t) * (1 - FEE)
        cash += proceeds
        run.trades.append(
            {
                "symbol": sym,
                "entry": days[int(i0)],
                "exit": days[t],
                "ret": round(proceeds / cost - 1, 4),
                "reason": reason,
            }
        )

    for t in range(start, len(days)):
        for sym in sorted(pending_sell):
            if sym in pos and panel.close[sym][t] is not None:
                sell(sym, t, pending_sell.pop(sym))
        pending_sell = {s: r for s, r in pending_sell.items() if s in pos}
        equity = cash + sum(q * _last_px(panel, s, t) for s, (q, *_r) in pos.items())
        if topup:
            for sym in list(pos):
                p = panel.close[sym][t]
                if p is None or sym in pending_sell:
                    continue
                q, cost, peak, i0 = pos[sym]
                gap = TARGET * equity - q * p
                if gap > 0 and cash > 0:
                    amt = min(gap, cash)
                    pos[sym] = [q + amt * (1 - FEE) / p, cost + amt, peak, i0]
                    cash -= amt
            topup = False
        for sym, signal_t in pending_new:
            p = panel.close[sym][t]
            if p is None or sym in pos or len(pos) >= MAX_POS or cash <= 0:
                continue
            amt = min(TARGET * equity, cash)
            pos[sym] = [amt * (1 - FEE) / p, amt, p, t]
            cash -= amt
            _hit, res = breakout(panel.close[sym], signal_t)
            run.entries.append(
                {
                    "symbol": sym,
                    "signal": days[signal_t],
                    "fill": days[t],
                    "month": days[signal_t][:7],
                    "resistance": res,
                    "fill_t": t,
                }
            )
        pending_new = []
        for sym in sorted(pos):
            if panel.ends_at(sym, t):
                pending_sell.pop(sym, None)
                sell(sym, t, "delisted")
        for sym, rec in pos.items():
            p_now = panel.close[sym][t]
            if p_now is None:
                continue
            rec[2] = max(rec[2], p_now)
            if p_now <= (1 - STOP_FROM_PEAK) * rec[2]:
                pending_sell[sym] = "stop_35"
            elif trend_off(panel.close[sym], t) and sym not in pending_sell:
                pending_sell[sym] = "trend_off"
        if index_ok[t]:
            pending_new = [
                (s, t)
                for s in active
                if s not in pos
                and s not in pending_sell
                and trend_up(panel.close[s], t)
                and entry(s, t)
            ]
        month_end = t + 1 < len(days) and days[t + 1][:7] != days[t][:7]
        if month_end:
            active = list(plan.get(t, []))
            topup = bool(index_ok[t])
        value = sum(q * _last_px(panel, s, t) for s, (q, *_r) in pos.items())
        run.equity.append(cash + value)
        run.exposure.append(value / (cash + value) if cash + value > 0 else 0.0)
    return run


def triangle_rule(panel: KrPanel) -> EntryRule:
    return lambda s, t: breakout(panel.close[s], t)[0]


def placebo_rule(
    panel: KrPanel,
    index_ok: Sequence[bool],
    plan: Mapping[int, Sequence[str]],
    entries: Sequence[Mapping[str, object]],
    seed: int,
) -> EntryRule:
    """후보가 산 (종목, 신호 달) 마다, 그달 그 종목이 주도주였고 141 진입 조건을 만족한 날 중
    무작위 하루. 같은 종목·같은 횟수·같은 달 범위."""
    rng = random.Random(seed)
    days = panel.days
    leader_of: dict[str, set[str]] = defaultdict(set)  # 달 → 그달 주도주
    for t, names in plan.items():
        if t + 1 < len(days):
            leader_of[days[t + 1][:7]].update(names)
    by_month: dict[str, list[int]] = defaultdict(list)
    for t, d in enumerate(days):
        by_month[d[:7]].append(t)
    chosen: set[tuple[str, int]] = set()
    for e in entries:
        sym, month = str(e["symbol"]), str(e["month"])
        ok = [
            t
            for t in by_month[month]
            if index_ok[t] and sym in leader_of[month] and trend_up(panel.close[sym], t)
        ]
        if ok:
            chosen.add((sym, rng.choice(ok)))
    return lambda s, t: (s, t) in chosen


def _count_halves(entries: Sequence[Mapping[str, object]]) -> tuple[int, int]:
    first = sum(1 for e in entries if str(e["fill"]) < SPLIT)
    return first, len(entries) - first


def _forward(panel: KrPanel, e: Mapping[str, object], k: int) -> float | None:
    sym, t = str(e["symbol"]), int(str(e["fill_t"]))
    xs = panel.close[sym]
    if t + k >= len(xs) or xs[t] is None or xs[t + k] is None:
        return None
    return xs[t + k] / xs[t] - 1  # type: ignore[operator]


def _false_break(panel: KrPanel, e: Mapping[str, object]) -> bool | None:
    res = e.get("resistance")
    if res is None:
        return None
    sym, t = str(e["symbol"]), int(str(e["fill_t"]))
    window = [v for v in panel.close[sym][t : t + FALSE_BREAK_DAYS + 1] if v is not None]
    return any(v < float(str(res)) for v in window)


def audit() -> dict[str, object]:
    digest, _n = data_hash()
    panel, index_ok = build_panel()
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    plan, _u = month_plan(panel, start)
    run = simulate_entry(panel, index_ok, plan, triangle_rule(panel))
    first, second = _count_halves(run.entries)
    out: dict[str, object] = {
        "data_sha256": digest,
        "data_matches_141": digest == EXPECTED_DATA,
        "entries": len(run.entries),
        "entries_first_half": first,
        "entries_second_half": second,
        "min_entries_per_half": MIN_ENTRIES,
        "enough": first >= MIN_ENTRIES and second >= MIN_ENTRIES,
        "note": "수익은 계산·출력하지 않는다 (돌파 진입 수만).",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    digest, _n = data_hash()
    if digest != EXPECTED_DATA:
        raise SystemExit("자료 해시가 문서 141 고정값과 다르다")
    panel, index_ok = build_panel()
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    span = days[start:]
    end = span[-1]
    plan, _universe = month_plan(panel, start)
    cand = simulate_entry(panel, index_ok, plan, triangle_rule(panel))
    lt = simulate_kr(panel, index_ok)
    ew = equal_weight_pit(panel, lt.universe_log)
    first, second = _count_halves(cand.entries)
    enough = first >= MIN_ENTRIES and second >= MIN_ENTRIES

    def three(lo: str, hi: str) -> dict[str, dict[str, float]]:
        return {
            "C": stats(span, cand.equity, lo, hi),
            "LT": stats(span, lt.equity, lo, hi),
            "EW": stats(span, ew, lo, hi),
        }

    full = three(FROM, end)
    halves = {"first": three(FROM, HALF_END), "second": three(SPLIT, end)}
    placebo: list[float] = []
    placebo_entries: list[int] = []
    for seed in range(SEEDS):
        rule = placebo_rule(panel, index_ok, plan, cand.entries, seed)
        p = simulate_entry(panel, index_ok, plan, rule)
        placebo.append(stats(span, p.equity, FROM, end)["sharpe"])
        placebo_entries.append(len(p.entries))
    p90 = sorted(placebo)[int(0.9 * SEEDS) - 1]
    k1 = full["C"]["sharpe"] >= full["EW"]["sharpe"] + 0.20
    k2 = all(h["C"]["sharpe"] > h["LT"]["sharpe"] for h in halves.values())
    k3 = full["C"]["sharpe"] > p90
    k4 = full["C"]["mdd"] <= max(full["EW"]["mdd"], 0.40)
    passed = "PASS" if k1 and k2 and k3 and k4 else "REJECT"
    verdict = passed if enough else "DATA_INSUFFICIENT"
    yearly: dict[str, dict[str, float]] = {}
    for y in sorted({d[:4] for d in span}):
        idx = [i for i, d in enumerate(span) if d[:4] == y]
        prev = idx[0] - 1 if idx[0] > 0 else idx[0]
        yearly[y] = {
            k: round(eq[idx[-1]] / eq[prev] - 1, 4)
            for k, eq in (("C", cand.equity), ("LT", lt.equity), ("EW", ew))
        }
    reasons: dict[str, int] = defaultdict(int)
    for tr in cand.trades:
        reasons[str(tr["reason"])] += 1
    f20 = [x for e in cand.entries if (x := _forward(panel, e, 20)) is not None]
    f60 = [x for e in cand.entries if (x := _forward(panel, e, 60)) is not None]
    fb = [x for e in cand.entries if (x := _false_break(panel, e)) is not None]
    holds = [sum(1 for d in span if str(tr["entry"]) <= d <= str(tr["exit"])) for tr in cand.trades]
    rows = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kospi = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    out = {
        "git": sha,
        "data_sha256": digest,
        "full": {
            **full,
            "KOSPI": stats(span, [kospi[d] / kospi[span[0]] for d in span], FROM, end),
        },
        "halves": halves,
        "placebo_sharpe": {
            "p90": p90,
            "median": statistics.median(placebo),
            "min": min(placebo),
            "max": max(placebo),
            "rank_of_candidate": sum(1 for x in placebo if x < full["C"]["sharpe"]),
            "entries_median": statistics.median(placebo_entries),
        },
        "entries": {"first_half": first, "second_half": second, "total": len(cand.entries)},
        "K1_sharpe_vs_ew": k1,
        "K2_halves_vs_lt": k2,
        "K3_vs_placebo_p90": k3,
        "K4_mdd": k4,
        "enough_entries": enough,
        "verdict": verdict,
        "report": {
            "exposure_mean": {
                "C": round(statistics.fmean(cand.exposure), 3),
            },
            "krw_1m_final": {
                "C": round(1_000_000 * cand.equity[-1] / cand.equity[0]),
                "LT": round(1_000_000 * lt.equity[-1] / lt.equity[0]),
                "EW": round(1_000_000 * ew[-1] / ew[0]),
            },
            "yearly": yearly,
            "trades": len(cand.trades),
            "hold_days_median": statistics.median(holds) if holds else None,
            "exit_reasons": dict(reasons),
            "forward_20d_median": round(statistics.median(f20), 4) if f20 else None,
            "forward_60d_median": round(statistics.median(f60), 4) if f60 else None,
            "false_break_share_10d": round(sum(fb) / len(fb), 3) if fb else None,
            "watch_breakouts": {
                s: [str(e["fill"]) for e in cand.entries if e["symbol"] == s] for s in WATCH
            },
            "best_trades": sorted(cand.trades, key=lambda tr: -float(str(tr["ret"])))[:15],
            "worst_trades": sorted(cand.trades, key=lambda tr: float(str(tr["ret"])))[:10],
        },
        "note": "문서 146 1 회 판정. 141 자료·종목 고르기·매도 그대로, 처음 사는 때만 삼각형 돌파.",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv not in (["audit"], ["run"]):
        raise SystemExit("usage: python -m scripts.run_leader_triangle audit|run")
    result = audit() if argv == ["audit"] else run()
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
