"""문서 111 — SB: 강한 주식 돌파 매수 (다바스·쿨라매기·쟁어·오닐) vs 같은 종목 모멘텀만 대조.

사용::

    python -m scripts.run_sb_breakout audit     # 자료 점검 (분할 이상·이력 부족 종목 제외 목록)
    python -m scripts.run_sb_breakout count     # 돌파·대조 매매 수와 달 수 (수익 미계산) → 관문
    python -m scripts.run_sb_breakout verdict   # 사용자 승인 뒤 한 번

자료: ``data/research/stocks/<SYM>.csv`` (KIS 수정주가) · ``data/research/tradfi/SPY_adj.csv``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import sys
from bisect import insort
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist

REPO = Path(__file__).resolve().parent.parent
STOCKS = REPO / "data" / "research" / "stocks"
SPY = REPO / "data" / "research" / "tradfi" / "SPY_adj.csv"
QQQ = REPO / "data" / "research" / "tradfi" / "QQQ_adj.csv"
OUT = REPO / "reports" / "sb_breakout"
FROM, TO = "2008-06-01", "2026-08-31"
FEE = 0.0025
MOM_DAYS, MOM_MIN = 63, 0.30
RS_DAYS, RS_TOP = 126, 0.80
BASE_DAYS, BASE_MAX = 20, 0.25
CHASE_MAX = 0.05
VOL_DAYS, VOL_MULT = 50, 1.4
SMA_TREND, SMA_EXIT, SPY_SMA = 50, 10, 200
LAGGARD_DAYS = 10
HTF_DAYS, HTF_MIN = 40, 0.80
MIN_TRADES, MIN_MONTHS = 300, 80
ALPHA, REL_MIN = 0.05, 0.005
SPLIT_JUMP = 3.0
"""하루 종가 비율이 3 배 넘게 뛰거나 1/3 아래면 분할 미반영으로 보고 그 종목을 뺀다."""
MIN_BARS = 260
UNIVERSE_SHA256 = ""
EXPECTED: tuple[int, int, int] | None = None
"""(돌파 매매, 대조 매매, 둘 다 있는 달) — count 뒤(수익 보기 전) 고정."""


@dataclass(slots=True)
class Series:
    dates: list[str]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


def load(path: Path) -> Series:
    s = Series([], [], [], [], [], [])
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            c = float(row["close"])
            if c <= 0:
                continue
            s.dates.append(row["date"])
            s.open.append(float(row["open"]))
            s.high.append(float(row["high"]))
            s.low.append(float(row["low"]))
            s.close.append(c)
            s.volume.append(float(row["volume"]))
    return s


def _sma(xs: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    run = 0.0
    for i, x in enumerate(xs):
        run += x
        if i >= n:
            run -= xs[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def _bad(s: Series) -> str | None:
    if len(s.close) < MIN_BARS:
        return "short"
    for a, b in zip(s.close, s.close[1:], strict=False):
        if b / a > SPLIT_JUMP or b / a < 1 / SPLIT_JUMP:
            return "split_jump"
    return None


def audit() -> dict[str, object]:
    universe = json.loads((STOCKS / "universe.json").read_text(encoding="utf-8"))["union"]
    status = json.loads((STOCKS / "fetch_status.json").read_text(encoding="utf-8"))
    kept: list[str] = []
    dropped: dict[str, str] = {}
    for sym in universe:
        path = STOCKS / f"{sym}.csv"
        if not path.exists():
            dropped[sym] = "no_data" if not status.get(sym, {}).get("ok") else "missing_file"
            continue
        reason = _bad(load(path))
        if reason:
            dropped[sym] = reason
        else:
            kept.append(sym)
    digest = hashlib.sha256(
        "".join(
            hashlib.sha256((STOCKS / f"{s}.csv").read_bytes()).hexdigest() for s in kept
        ).encode()
    ).hexdigest()
    out = {"kept": len(kept), "dropped": dropped, "kept_symbols": kept, "universe_sha256": digest}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return {k: v for k, v in out.items() if k != "kept_symbols"}


# ------------------------------------------------------------------ 신호·매매


@dataclass(slots=True)
class Trade:
    symbol: str
    kind: str  # "E" 돌파 · "C" 대조
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    reason: str
    hold: int
    htf: bool

    @property
    def net(self) -> float:
        return math.log(self.exit / self.entry * (1 - FEE) ** 2)


def _spy_filter() -> dict[str, bool]:
    s = load(SPY)
    sma = _sma(s.close, SPY_SMA)
    return {d: (m is not None and c > m) for d, c, m in zip(s.dates, s.close, sma, strict=True)}


def _rs_cut(data: dict[str, Series]) -> dict[str, float]:
    """날짜마다 126 일 수익의 80 백분위 값 (그날 자료가 있는 종목들)."""
    by_day: dict[str, list[float]] = defaultdict(list)
    for s in data.values():
        for i in range(RS_DAYS, len(s.close)):
            by_day[s.dates[i]].append(s.close[i] / s.close[i - RS_DAYS] - 1)
    cut: dict[str, float] = {}
    for d, xs in by_day.items():
        if len(xs) >= 50:
            xs.sort()
            cut[d] = xs[int(RS_TOP * (len(xs) - 1))]
    return cut


def simulate_symbol(
    sym: str, s: Series, mkt: dict[str, bool], rs_cut: dict[str, float]
) -> list[Trade]:
    n = len(s.close)
    sma50 = _sma(s.close, SMA_TREND)
    sma10 = _sma(s.close, SMA_EXIT)
    trades: list[Trade] = []
    for kind in ("E", "C"):
        holding: tuple[int, float, float, bool] | None = None  # (진입 색인, 진입가, 기준선 L, HTF)
        for t in range(max(RS_DAYS, VOL_DAYS, BASE_DAYS, MOM_DAYS, HTF_DAYS), n):
            d = s.dates[t]
            if holding is not None:
                i0, px, line, htf = holding
                c = s.close[t]
                reason = None
                if c < line:
                    reason = "fail"
                elif t - i0 == LAGGARD_DAYS and c <= px:
                    reason = "laggard"
                elif (m10 := sma10[t]) is not None and c < m10:
                    reason = "ma10"
                if reason or t == n - 1:
                    trades.append(
                        Trade(sym, kind, s.dates[i0], d, px, c, reason or "end", t - i0, htf)
                    )
                    holding = None
                continue
            if not (FROM <= d <= TO) or not mkt.get(d, False):
                continue
            c = s.close[t]
            m50 = sma50[t]
            cut = rs_cut.get(d)
            mom = c / s.close[t - MOM_DAYS] - 1
            rs = c / s.close[t - RS_DAYS] - 1
            if m50 is None or cut is None or mom < MOM_MIN or rs < cut or c <= m50:
                continue
            pivot = max(s.high[t - BASE_DAYS : t])
            base_low = min(s.low[t - BASE_DAYS : t])
            vol_avg = sum(s.volume[t - VOL_DAYS : t]) / VOL_DAYS
            breakout = (
                pivot / base_low - 1 <= BASE_MAX
                and c > pivot
                and c <= pivot * (1 + CHASE_MAX)
                and vol_avg > 0
                and s.volume[t] >= VOL_MULT * vol_avg
            )
            htf = s.close[t - BASE_DAYS] / s.close[t - HTF_DAYS] - 1 >= HTF_MIN
            if kind == "E" and breakout:
                holding = (t, c, pivot, htf)
            elif kind == "C" and not breakout:
                holding = (t, c, s.low[t], htf)
    return trades


def all_trades() -> tuple[list[Trade], dict[str, object]]:
    audit_info = json.loads((OUT / "audit.json").read_text(encoding="utf-8"))
    kept = audit_info["kept_symbols"]
    data = {sym: load(STOCKS / f"{sym}.csv") for sym in kept}
    mkt = _spy_filter()
    cut = _rs_cut(data)
    trades: list[Trade] = []
    for sym, s in data.items():
        trades.extend(simulate_symbol(sym, s, mkt, cut))
    return trades, audit_info


def _months(trades: Sequence[Trade], kind: str) -> dict[str, list[Trade]]:
    out: dict[str, list[Trade]] = defaultdict(list)
    for tr in trades:
        if tr.kind == kind:
            out[tr.entry_date[:7]].append(tr)
    return out


def count() -> dict[str, object]:
    trades, info = all_trades()
    ev, co = _months(trades, "E"), _months(trades, "C")
    both = sorted(set(ev) & set(co))
    n_e = sum(1 for t in trades if t.kind == "E")
    n_c = sum(1 for t in trades if t.kind == "C")
    by_year: dict[str, int] = defaultdict(int)
    for t in trades:
        if t.kind == "E":
            by_year[t.entry_date[:4]] += 1
    out = {
        "kept_symbols": info["kept"],
        "universe_sha256": info["universe_sha256"],
        "event_trades": n_e,
        "control_trades": n_c,
        "months_event": len(ev),
        "months_both": len(both),
        "events_by_year": dict(sorted(by_year.items())),
        "htf_events": sum(1 for t in trades if t.kind == "E" and t.htf),
        "gate_pass": n_e >= MIN_TRADES and len(both) >= MIN_MONTHS,
        "note": "문서 111 §5 관문. 수익률은 계산하지 않았다.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "count.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


# ------------------------------------------------------------------ 판정


def _t_one_sided(xs: Sequence[float]) -> tuple[float, float, float]:
    m = statistics.fmean(xs)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    t = m / se
    return m, t, 1 - NormalDist().cdf(t)


def _portfolio(trades: Sequence[Trade], slots: int = 5, start: float = 1_000.0) -> dict[str, float]:
    """1,000 USD · 동시에 최대 5 종목 · 진입 때 자본의 1/5 (보고용, 생존 편향으로 부풀려짐)."""
    events = [t for t in trades if t.kind == "E"]
    days = sorted({t.entry_date for t in events} | {t.exit_date for t in events})
    cash, equity_peak, mdd = start, start, 0.0
    open_pos: list[tuple[str, float, Trade]] = []  # (청산일, 투입금, 매매)
    by_entry: dict[str, list[Trade]] = defaultdict(list)
    for t in events:
        by_entry[t.entry_date].append(t)
    for d in days:
        still = []
        for exit_d, amt, tr in open_pos:
            if exit_d == d:
                cash += amt * math.exp(tr.net)
            else:
                still.append((exit_d, amt, tr))
        open_pos = still
        equity = cash + sum(amt for _, amt, _ in open_pos)
        for tr in by_entry.get(d, []):
            if len(open_pos) >= slots:
                break
            amt = min(cash, equity / slots)
            if amt <= 0:
                break
            cash -= amt
            insort(open_pos, (tr.exit_date, amt, tr), key=lambda x: x[0])
        equity = cash + sum(amt for _, amt, _ in open_pos)
        equity_peak = max(equity_peak, equity)
        mdd = max(mdd, 1 - equity / equity_peak)
    final = cash + sum(amt * math.exp(tr.net) for _, amt, tr in open_pos)
    return {"start": start, "final": round(final, 2), "mdd_at_trade_dates": round(mdd, 4)}


def verdict() -> dict[str, object]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    if EXPECTED is None or not UNIVERSE_SHA256:
        raise SystemExit("관문 수·유니버스 해시가 고정되지 않았다")
    trades, info = all_trades()
    if info["universe_sha256"] != UNIVERSE_SHA256:
        raise SystemExit("유니버스 자료가 관문 때와 다르다")
    ev, co = _months(trades, "E"), _months(trades, "C")
    both = sorted(set(ev) & set(co))
    n_e = sum(1 for t in trades if t.kind == "E")
    n_c = sum(1 for t in trades if t.kind == "C")
    if (n_e, n_c, len(both)) != EXPECTED:
        raise SystemExit(f"수가 관문과 다르다: {(n_e, n_c, len(both))}")
    diffs = [
        statistics.fmean(t.net for t in ev[m]) - statistics.fmean(t.net for t in co[m])
        for m in both
    ]
    rel_m, rel_t, rel_p = _t_one_sided(diffs)
    abs_m, abs_t, abs_p = _t_one_sided([statistics.fmean(t.net for t in ev[m]) for m in sorted(ev)])
    q_rel = rel_p < ALPHA and rel_m > REL_MIN
    q_abs = abs_p < ALPHA
    e_trades = [t for t in trades if t.kind == "E"]
    c_trades = [t for t in trades if t.kind == "C"]

    def describe(ts: Sequence[Trade]) -> dict[str, object]:
        nets = [t.net for t in ts]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        reasons: dict[str, int] = defaultdict(int)
        for t in ts:
            reasons[t.reason] += 1
        top = sorted(nets, reverse=True)
        return {
            "n": len(ts),
            "mean_net": round(statistics.fmean(nets), 5),
            "median_net": round(statistics.median(nets), 5),
            "win_rate": round(len(wins) / len(nets), 4),
            "avg_win": round(statistics.fmean(wins), 5) if wins else None,
            "avg_loss": round(statistics.fmean(losses), 5) if losses else None,
            "hold_median": statistics.median(t.hold for t in ts),
            "exit_reasons": dict(reasons),
            "top_5pct_share_of_sum": round(sum(top[: max(1, len(top) // 20)]) / sum(nets), 3)
            if sum(nets) != 0
            else None,
        }

    by_year: dict[str, list[float]] = defaultdict(list)
    for t in e_trades:
        by_year[t.entry_date[:4]].append(t.net)
    record: dict[str, object] = {
        "Q_REL": {
            "months": len(both),
            "mean_diff_per_trade": round(rel_m, 5),
            "t": round(rel_t, 3),
            "p_one_sided": round(rel_p, 5),
            "pass": q_rel,
        },
        "Q_ABS": {
            "months": len(ev),
            "mean_event_month": round(abs_m, 5),
            "t": round(abs_t, 3),
            "p_one_sided": round(abs_p, 5),
            "pass": q_abs,
        },
        "verdict": "PASS" if q_rel and q_abs else "REJECT",
        "report": {
            "event": describe(e_trades),
            "control": describe(c_trades),
            "htf_subset": describe([t for t in e_trades if t.htf])
            if any(t.htf for t in e_trades)
            else None,
            "event_by_year_mean": {
                k: round(statistics.fmean(v), 5) for k, v in sorted(by_year.items())
            },
            "portfolio_1000usd_5slots": _portfolio(trades),
        },
        "note": "문서 111 1 회 판정. 절대 수익(Q_ABS·보고)은 생존 편향으로 부풀려진다.",
    }
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    commands = {"audit": audit, "count": count, "verdict": verdict}
    if len(argv) != 1 or argv[0] not in commands:
        raise SystemExit("usage: python -m scripts.run_sb_breakout audit | count | verdict")
    sys.stdout.write(json.dumps(commands[argv[0]](), indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
