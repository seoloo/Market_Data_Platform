"""문서 270 — 과하게 빠진 종목 되사기(151 BNF) + 코스닥 200 일선 필터 + −15% 손절 (1 회).

사용 예::

    python -m scripts.run_kq_oversold_270 audit   # 자료 해시 · 거래 수 (수익 없이)
    python -m scripts.run_kq_oversold_270 run     # 한 번 → reports/kq_oversold_270/result.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_community_150 import (
    KR_FEE,
    KR_FEE_REF,
    MIN_N,
    equity_stats,
    summary,
    trade_verdict,
)
from scripts.run_kq_154 import DATA as KQ
from scripts.run_kq_154 import build_kq_panel
from scripts.run_kq_154 import data_hash as kq_hash
from scripts.run_lt_kr import KrPanel
from scripts.run_traders_151 import (
    BNF_DEV,
    BNF_MA,
    BNF_MAX,
    BNF_MIN_PRICE,
    Trade,
    _week,
    pit_members,
    sma_full,
)

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "kq_oversold_270"
EXPECTED_DATA = "66cec2b5da49e25e83f6059aea9864a4879219fdf2800993e424bd8e6d568052"
"""154 와 같은 코스닥 자료 묶음 (7,820 파일)."""
FROM, SPLIT, TO = "2011-01-01", "2017-01-01", "2026-08-31"
STOP = 0.15
SLOTS = 10


def signals(panel: KrPanel, ok: Sequence[bool], *, use_filter: bool) -> list[tuple[str, int]]:
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    end = max(i for i, d in enumerate(panel.days) if d <= TO)
    out: list[tuple[str, int]] = []
    for sym, xs in panel.close.items():
        prev_below = False
        for t in range(max(start, BNF_MA), end + 1):
            c, m = xs[t], sma_full(xs, t, BNF_MA)
            if c is None or m is None:
                prev_below = False
                continue
            below = c <= m * (1 - BNF_DEV)
            if below and not prev_below and c >= BNF_MIN_PRICE and (ok[t] or not use_filter):
                out.append((sym, t))
            prev_below = below
    return out


def trades(panel: KrPanel, sigs: Sequence[tuple[str, int]], *, use_stop: bool) -> list[Trade]:
    universe = pit_members(panel, [t for _, t in sigs])
    end = max(i for i, d in enumerate(panel.days) if d <= TO)
    busy: dict[str, int] = defaultdict(lambda: -1)
    out: list[Trade] = []
    for sym, t in sorted(sigs, key=lambda x: (x[1], x[0])):
        if sym not in universe[t] or t <= busy[sym]:
            continue
        xs = panel.close[sym]
        j = t + 1
        if j > end or xs[j] is None:
            continue
        entry = xs[j]
        assert entry is not None
        exit_t, reason, held, u = None, "", 0, j
        while u < min(end, panel.last[sym]):
            u += 1
            c = xs[u]
            if c is None:
                continue
            held += 1
            m = sma_full(xs, u, BNF_MA)
            if use_stop and c <= entry * (1 - STOP):
                exit_t, reason = u, "stop"
                break
            if m is not None and c >= m:
                exit_t, reason = u, "back_to_ma"
                break
            if held >= BNF_MAX:
                exit_t, reason = u, "max_days"
                break
        if exit_t is None:
            exit_t = min(end, panel.last[sym])
            while xs[exit_t] is None:
                exit_t -= 1
            reason = "delisted" if panel.last[sym] < end else "period_end"
        px = xs[exit_t]
        assert px is not None
        out.append(Trade(sym, panel.days[t], px / entry - 1, exit_t - j, reason))
        busy[sym] = exit_t
    return out


def portfolio(
    panel: KrPanel, trs: Sequence[Trade], fee: float = KR_FEE
) -> tuple[list[str], list[float], int]:
    """칸 10 개 (151 §9 와 같음). 같은 날은 25 일선 아래로 깊은 순."""
    at = {d: i for i, d in enumerate(panel.days)}

    def dev(x: Trade) -> float:
        t = at[x.date]
        c, m = panel.close[x.sym][t], sma_full(panel.close[x.sym], t, BNF_MA)
        return c / m - 1 if c is not None and m else 0.0

    by_entry: dict[int, list[Trade]] = defaultdict(list)
    for x in trs:
        by_entry[at[x.date] + 1].append(x)
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    end = max(i for i, d in enumerate(panel.days) if d <= TO)
    cash, missed = 1.0, 0
    open_: list[tuple[Trade, float, float, int]] = []
    days: list[str] = []
    eq: list[float] = []
    for t in range(start, end + 1):
        kept = []
        for x, q, last, out_t in open_:
            c = panel.close[x.sym][t]
            p = c if c is not None else last
            if t >= out_t:
                cash += q * p * (1 - fee)
            else:
                kept.append((x, q, p, out_t))
        open_ = kept
        value = cash + sum(q * last for _, q, last, _ in open_)
        for x in sorted(by_entry.get(t, []), key=dev):
            entry = panel.close[x.sym][t]
            amt = min(cash, value / SLOTS)
            if len(open_) >= SLOTS or entry is None or amt <= 0:
                missed += 1
                continue
            cash -= amt
            open_.append((x, amt * (1 - fee) / entry, entry, t + x.days))
        days.append(panel.days[t])
        eq.append(cash + sum(q * last for _, q, last, _ in open_))
    return days, eq, missed


def kosdaq() -> dict[str, float]:
    rows = (KQ / "kosdaq.csv").read_text(encoding="utf-8").splitlines()[1:]
    return {f"{r[:4]}-{r[4:6]}-{r[6:8]}": float(r.split(",")[1]) for r in rows}


def evaluate(panel: KrPanel, trs: list[Trade]) -> dict[str, Any]:
    nets = [x.ret - 2 * KR_FEE for x in trs]
    dates = [x.date for x in trs]
    weeks = [_week(d) for d in dates]
    tv = trade_verdict(nets, dates, SPLIT, MIN_N, keys=weeks)
    days, eq, missed = portfolio(panel, trs)
    kq = kosdaq()
    k = [kq[d] for d in days]
    split = next(i for i, d in enumerate(days) if d >= SPLIT)
    full, idx = equity_stats(eq), equity_stats(k)
    halves = [
        [equity_stats(eq[:split]), equity_stats(k[:split])],
        [equity_stats(eq[split:]), equity_stats(k[split:])],
    ]
    acct = {
        "a_cagr_gt_kosdaq": full["cagr"] > idx["cagr"],
        "b_sharpe_gt_kosdaq": full["sharpe"] > idx["sharpe"],
        "c_halves_cagr_gt_kosdaq": all(h[0]["cagr"] > h[1]["cagr"] for h in halves),
    }
    wk = Counter(weeks)
    cut = sorted(wk.values())[int(0.95 * (len(wk) - 1))] if wk else 0
    busy = [n for n, w in zip(nets, weeks, strict=True) if wk[w] >= cut]
    by_year: dict[str, list[float]] = defaultdict(list)
    for x, n in zip(trs, nets, strict=True):
        by_year[x.date[:4]].append(n)
    yearly = {}
    for y in sorted({d[:4] for d in days}):
        ix = [i for i, d in enumerate(days) if d[:4] == y]
        yearly[y] = round(eq[ix[-1]] / eq[max(ix[0] - 1, 0)] - 1, 4)
    return {
        "verdict": "PASS" if tv.get("verdict") == "PASS" and all(acct.values()) else "REJECT",
        "trade": tv,
        "account_checks": acct,
        "account": full,
        "kosdaq": idx,
        "account_halves": halves,
        "account_by_year": yearly,
        "missed_signals": missed,
        "exit_reasons": dict(Counter(x.reason for x in trs)),
        "mean_days": round(sum(x.days for x in trs) / len(trs), 1) if trs else 0,
        "by_year": {y: [len(v), round(sum(v) / len(v), 4)] for y, v in sorted(by_year.items())},
        "ref_cost": summary([x.ret - 2 * KR_FEE_REF for x in trs], weeks),
        "busy_share": round(len(busy) / len(nets), 3) if nets else 0,
    }


def variants(panel: KrPanel, ok: Sequence[bool]) -> dict[str, list[Trade]]:
    return {
        f"{'F' if f else '-'}{'S' if s else '-'}": trades(
            panel, signals(panel, ok, use_filter=f), use_stop=s
        )
        for f in (True, False)
        for s in (True, False)
    }


def audit() -> dict[str, Any]:
    digest, n = kq_hash()
    panel, ok = build_kq_panel()
    vs = variants(panel, ok)
    return {
        "data_sha256": digest,
        "files": n,
        "trades": {k: len(v) for k, v in vs.items()},
        "halves_main": [
            sum(x.date < SPLIT for x in vs["FS"]),
            sum(x.date >= SPLIT for x in vs["FS"]),
        ],
        "filter_on_share": round(
            sum(ok[i] for i, d in enumerate(panel.days) if FROM <= d <= TO)
            / sum(1 for d in panel.days if FROM <= d <= TO),
            3,
        ),
    }


def run() -> dict[str, Any]:
    target = OUTPUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    digest, _ = kq_hash()
    if digest != EXPECTED_DATA:
        raise SystemExit(f"자료 해시가 다르다: {digest}")
    panel, ok = build_kq_panel()
    vs = variants(panel, ok)
    result: dict[str, Any] = {"commit": sha, "data_sha256": digest}
    result["main_FS"] = evaluate(panel, vs["FS"])
    result["verdict"] = result["main_FS"]["verdict"]
    for k, name in (
        ("--", "report_plain_bnf"),
        ("F-", "report_filter_only"),
        ("-S", "report_stop_only"),
    ):
        result[name] = evaluate(panel, vs[k])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=1))
        return 0
    if cmd != "run":
        raise SystemExit(f"알 수 없는 명령: {cmd}")
    r = run()
    for k in ("main_FS", "report_plain_bnf", "report_filter_only", "report_stop_only"):
        v, t = r[k], r[k]["trade"]
        print(
            k,
            v["verdict"],
            f"거래 {t.get('n')} 평균 {t.get('mean')} 하한 {t.get('lo2')} 반쪽 {t.get('halves')}",
            f"| 계좌 {v['account']} 코스닥 {v['kosdaq']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
