"""문서 113 — LT: 주도 섹터의 주도주 추세 보유 (추매·−35% 손절·추세 꺾이면 매도) vs 고르게 들기(EW).

사용 예::

    python -m scripts.run_lt_leaders run   # 판정 한 번 (트리 깨끗 · 결과 파일 없음)

자료: ``data/research/stocks/*.csv`` (111, 유니버스 해시 고정) · ``sectors.json`` · SPY·QQQ.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_sb_breakout import QQQ, SPY, _sma, load
from scripts.run_sb_breakout import STOCKS as STOCKS

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "lt_leaders"
AUDIT = REPO / "reports" / "sb_breakout" / "audit.json"
UNIVERSE_SHA256 = "df6c54e323f7fadafd99184ced13b15ba0fb86a860b4478249a1966029419837"
FROM, TO, SPLIT = "2008-06-01", "2026-08-31", "2017-01-01"
FEE, TAX = 0.0025, 0.22
LOOKS = (21, 63, 126)
TOP_SECTORS, TOP_STOCKS = 3, 10
MAX_POS, TARGET = 10, 0.10
TREND_LB = (20, 60, 120)
STOP_FROM_PEAK = 0.35
SPY_SMA, STOCK_SMA = 200, 50
WATCH: tuple[str, ...] = ("NVDA", "MU", "INTC", "SNDK", "APH")


@dataclass(slots=True)
class Panel:
    days: list[str]
    close: dict[str, list[float | None]]
    sector: dict[str, str]


def build_panel() -> Panel:
    info = json.loads(AUDIT.read_text(encoding="utf-8"))
    if info["universe_sha256"] != UNIVERSE_SHA256:
        raise SystemExit("유니버스가 111 고정값과 다르다")
    sectors = json.loads((STOCKS / "sectors.json").read_text(encoding="utf-8"))["sectors"]
    spy = load(SPY)
    days = [d for d in spy.dates if d <= TO]
    close: dict[str, list[float | None]] = {}
    for sym in info["kept_symbols"]:
        s = load(STOCKS / f"{sym}.csv")
        m = dict(zip(s.dates, s.close, strict=True))
        close[sym] = [m.get(d) for d in days]
    return Panel(days, close, {k: sectors.get(k, "Unknown") for k in close})


def _ret(xs: Sequence[float | None], t: int, k: int) -> float | None:
    if t - k < 0 or xs[t] is None or xs[t - k] is None:
        return None
    return xs[t] / xs[t - k] - 1  # type: ignore[operator]


def trend_up(xs: Sequence[float | None], t: int) -> bool:
    """T 규칙(20·60·120 부호 평균 > 0) 그리고 종가 > 50 일선."""
    signs = []
    for k in TREND_LB:
        r = _ret(xs, t, k)
        if r is None:
            return False
        signs.append(1 if r > 0 else -1 if r < 0 else 0)
    if sum(signs) <= 0:
        return False
    window = xs[t - STOCK_SMA + 1 : t + 1]
    if t + 1 < STOCK_SMA or any(v is None for v in window):
        return False
    return xs[t] > sum(window) / STOCK_SMA  # type: ignore[arg-type,operator]


def trend_off(xs: Sequence[float | None], t: int) -> bool:
    signs = []
    for k in TREND_LB:
        r = _ret(xs, t, k)
        if r is None:
            return False
        signs.append(1 if r > 0 else -1 if r < 0 else 0)
    return sum(signs) <= 0


def leaders(panel: Panel, t: int) -> list[tuple[str, float]]:
    """그날 주도 섹터 상위 3 개 안의 주도 점수 상위 10 종목 (점수 높은 순).

    동점은 종목 · 섹터 이름으로 정한다(141 ``leaders_pit`` 과 같음) — 예전엔 집합 순서라
    파이썬 해시 시드마다 순위가 달라졌다(2026-10-17 고침, 판정들은 그대로)."""
    rets: dict[int, dict[str, float]] = {}
    for k in LOOKS:
        rets[k] = {s: r for s, xs in panel.close.items() if (r := _ret(xs, t, k)) is not None}
    common = set.intersection(*(set(v) for v in rets.values()))
    if len(common) < 50:
        return []
    score: dict[str, float] = defaultdict(float)
    for k in LOOKS:
        ordered = sorted(common, key=lambda s: (rets[k][s], s))
        n = len(ordered)
        for i, s in enumerate(ordered):
            score[s] += i / (n - 1) / len(LOOKS)
    by_sector: dict[str, list[float]] = defaultdict(list)
    for s in common:
        by_sector[panel.sector[s]].append(score[s])
    top = sorted(by_sector, key=lambda x: (statistics.median(by_sector[x]), x), reverse=True)[
        :TOP_SECTORS
    ]
    pool = sorted(
        (s for s in common if panel.sector[s] in top), key=lambda s: (score[s], s), reverse=True
    )
    return [(s, score[s]) for s in pool[:TOP_STOCKS]]


@dataclass(slots=True)
class Run:
    equity: list[float] = field(default_factory=list)
    trades: list[dict[str, object]] = field(default_factory=list)
    leader_log: dict[str, list[str]] = field(default_factory=dict)
    tax_paid: float = 0.0


def simulate(panel: Panel, spy_ok: Sequence[bool], *, tax: bool = False) -> Run:
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    cash = 1.0
    pos: dict[str, list[float]] = {}  # sym -> [수량, 원가, 진입 뒤 최고 종가, 진입 색인]
    run = Run()
    pending_sell: set[str] = set()
    stop_hits: set[str] = set()
    pending_buy: list[str] | None = None
    year_gain = 0.0

    def px(sym: str, t: int) -> float:
        xs = panel.close[sym]
        while xs[t] is None:
            t -= 1
        return xs[t]  # type: ignore[return-value]

    def sell(sym: str, t: int, reason: str) -> None:
        nonlocal cash, year_gain
        q, cost, _peak, i0 = pos.pop(sym)
        proceeds = q * px(sym, t) * (1 - FEE)
        cash += proceeds
        year_gain += proceeds - cost
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
        d = days[t]
        # 1) 어제 신호로 정한 매도·매수를 오늘 종가에
        for sym in sorted(pending_sell):
            if sym in pos:
                sell(sym, t, "trend_off" if sym not in stop_hits else "stop_35")
        pending_sell = set()
        equity = cash + sum(q * px(s, t) for s, (q, *_r) in pos.items())
        if pending_buy is not None:
            for sym in list(pos) + pending_buy:
                if sym in pos:
                    q, cost, peak, i0 = pos[sym]
                    gap = TARGET * equity - q * px(sym, t)
                    if gap > 0 and cash > 0:
                        amt = min(gap, cash)
                        pos[sym] = [q + amt * (1 - FEE) / px(sym, t), cost + amt, peak, i0]
                        cash -= amt
                elif len(pos) < MAX_POS and cash > 0:
                    amt = min(TARGET * equity, cash)
                    p = px(sym, t)
                    pos[sym] = [amt * (1 - FEE) / p, amt, p, t]
                    cash -= amt
            pending_buy = None
        # 2) 오늘 종가로 내일 할 일
        stop_hits = set()  # 오늘 종가 기준 −35% 손절 대상
        for sym, rec in pos.items():
            p = px(sym, t)
            rec[2] = max(rec[2], p)
            if p <= (1 - STOP_FROM_PEAK) * rec[2]:
                pending_sell.add(sym)
                stop_hits.add(sym)
            elif trend_off(panel.close[sym], t):
                pending_sell.add(sym)
        month_end = t + 1 < len(days) and days[t + 1][:7] != d[:7]
        if month_end:
            lead = leaders(panel, t)
            run.leader_log[d] = [s for s, _ in lead]
            if spy_ok[t]:
                pending_buy = [s for s, _ in lead if s not in pos and trend_up(panel.close[s], t)]
                pending_buy = [s for s in pending_buy if s not in pending_sell]
        # 3) 세금 (해마다 실현 이익이 양수면 22%)
        if tax and (t == len(days) - 1 or days[t + 1][:4] != d[:4]):
            if year_gain > 0:
                cash -= TAX * year_gain
                run.tax_paid += TAX * year_gain
            year_gain = 0.0
        run.equity.append(cash + sum(q * px(s, t) for s, (q, *_r) in pos.items()))
    return run


def equal_weight(panel: Panel) -> list[float]:
    """같은 종목을 매달 똑같은 비중으로 (월말 신호 → 다음 거래일 종가에 맞춤, 바뀐 금액에 0.25%)."""
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    hold: dict[str, float] = {}
    cash, out = 1.0, []
    pending = True

    def value_of(book: Mapping[str, float], t: int) -> dict[str, float]:
        vals: dict[str, float] = {}
        for sym, q in book.items():
            c = panel.close[sym][t]
            if c is not None:
                vals[sym] = q * c
        return vals

    for t in range(start, len(days)):
        values = value_of(hold, t)
        total = cash + sum(values.values())
        if pending:
            prices = {sym: c for sym, xs in panel.close.items() if (c := xs[t]) is not None}
            each = total / len(prices)
            moved = sum(abs(each - values.get(sym, 0.0)) for sym in prices) + sum(
                v for sym, v in values.items() if sym not in prices
            )
            total -= moved * FEE
            each = total / len(prices)
            hold = {sym: each / c for sym, c in prices.items()}
            cash = 0.0
            pending = False
        out.append(cash + sum(value_of(hold, t).values()))
        if t + 1 < len(days) and days[t + 1][:7] != days[t][:7]:
            pending = True
    return out


def stats(days: Sequence[str], eq: Sequence[float], lo: str, hi: str) -> dict[str, float]:
    idx = [i for i, d in enumerate(days) if lo <= d <= hi]
    seg = [eq[i] for i in idx]
    rets = [b / a - 1 for a, b in pairwise(seg) if a > 0]
    years = (len(seg) - 1) / 252
    cagr = (seg[-1] / seg[0]) ** (1 / years) - 1 if years > 0 and seg[0] > 0 else 0.0
    sd = statistics.stdev(rets) if len(rets) > 1 else 0.0
    sharpe = statistics.fmean(rets) / sd * math.sqrt(252) if sd > 0 else 0.0
    peak, mdd = 0.0, 0.0
    for v in seg:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"cagr": round(cagr, 4), "sharpe": round(sharpe, 3), "mdd": round(mdd, 4)}


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    panel = build_panel()
    spy = load(SPY)
    spy_map = dict(zip(spy.dates, spy.close, strict=True))
    spy_close = [spy_map[d] for d in panel.days]
    spy_sma = _sma(spy_close, SPY_SMA)
    spy_ok = [m is not None and c > m for c, m in zip(spy_close, spy_sma, strict=True)]
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    span = days[start:]
    lt = simulate(panel, spy_ok)
    lt_tax = simulate(panel, spy_ok, tax=True)
    ew = equal_weight(panel)
    qqq = load(QQQ)
    qmap = dict(zip(qqq.dates, qqq.close, strict=True))
    spy_hold = [spy_map[d] / spy_map[span[0]] for d in span]
    qqq_hold = [qmap[d] / qmap[span[0]] for d in span]
    end = span[-1]
    full = {
        "LT": stats(span, lt.equity, FROM, end),
        "EW": stats(span, ew, FROM, end),
        "SPY": stats(span, spy_hold, FROM, end),
        "QQQ": stats(span, qqq_hold, FROM, end),
        "LT_tax": stats(span, lt_tax.equity, FROM, end),
    }
    halves = {
        "first": {
            "LT": stats(span, lt.equity, FROM, "2016-12-31"),
            "EW": stats(span, ew, FROM, "2016-12-31"),
        },
        "second": {"LT": stats(span, lt.equity, SPLIT, end), "EW": stats(span, ew, SPLIT, end)},
    }
    l1 = full["LT"]["sharpe"] >= full["EW"]["sharpe"] + 0.20
    l2 = all(h["LT"]["cagr"] > h["EW"]["cagr"] for h in halves.values())
    l3 = full["LT"]["mdd"] <= max(full["EW"]["mdd"], 0.40)
    yearly: dict[str, float] = {}
    for y in sorted({d[:4] for d in span}):
        idx = [i for i, d in enumerate(span) if d[:4] == y]
        prev = idx[0] - 1 if idx[0] > 0 else idx[0]
        yearly[y] = round(lt.equity[idx[-1]] / lt.equity[prev] - 1, 4)
    reasons: dict[str, int] = defaultdict(int)
    by_sym: dict[str, float] = defaultdict(float)
    for tr in lt.trades:
        reasons[str(tr["reason"])] += 1
        by_sym[str(tr["symbol"])] += float(str(tr["ret"]))
    i10 = next((i for i, d in enumerate(span) if d >= "2018-06-01"), len(span) - 1)
    watch = {
        s: {
            "trades": [tr for tr in lt.trades if tr["symbol"] == s],
            "months_as_leader": sum(1 for v in lt.leader_log.values() if s in v),
        }
        for s in WATCH
    }
    holds = [sum(1 for d in span if str(tr["entry"]) <= d <= str(tr["exit"])) for tr in lt.trades]
    out = {
        "git": sha,
        "full": full,
        "halves": halves,
        "L1_sharpe": l1,
        "L2_both_halves": l2,
        "L3_mdd": l3,
        "verdict": "PASS" if l1 and l2 and l3 else "REJECT",
        "report": {
            "usd_1000_after_10y_from_2008_06": round(1000 * lt.equity[i10] / lt.equity[0], 1),
            "usd_1000_final": round(1000 * lt.equity[-1] / lt.equity[0], 1),
            "ew_usd_1000_final": round(1000 * ew[-1] / ew[0], 1),
            "yearly": yearly,
            "trades": len(lt.trades),
            "hold_days_median": statistics.median(holds) if holds else None,
            "exit_reasons": dict(reasons),
            "top_symbols_sum_ret": dict(sorted(by_sym.items(), key=lambda x: -x[1])[:10]),
            "watch": watch,
            "best_trades": sorted(lt.trades, key=lambda tr: -float(str(tr["ret"])))[:15],
            "longest_holds": [
                {**tr, "hold_days": h}
                for tr, h in sorted(zip(lt.trades, holds, strict=True), key=lambda x: -x[1])[:15]
            ],
            "most_months_as_leader": {
                sym: {
                    "months": n,
                    "trades": [tr for tr in lt.trades if tr["symbol"] == sym],
                }
                for sym, n in sorted(
                    {
                        s2: sum(1 for v in lt.leader_log.values() if s2 in v)
                        for s2 in {x for v in lt.leader_log.values() for x in v}
                    }.items(),
                    key=lambda x: -x[1],
                )[:15]
            },
            "tax_paid_fraction_of_start": round(lt_tax.tax_paid, 3),
        },
        "note": "문서 113 1 회 판정. 생존 편향으로 절대 수익은 부풀려진다 — EW 비교가 주.",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_lt_leaders run")
    sys.stdout.write(json.dumps(run(), indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
