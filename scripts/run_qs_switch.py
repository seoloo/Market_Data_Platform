"""문서 110 — QS: QQQ 적립 → 하락 추세면 멈추고 현금 → 추세 회복 때 TQQQ (추세 꺼지면 TQQQ 매도).

사용 예::

    python -m scripts.run_qs_switch fetch-tqqq   # 실제 TQQQ 일봉 (KIS 읽기 전용) — 가상 TQQQ 점검용
    python -m scripts.run_qs_switch audit        # A1: 가상 TQQQ vs 실제 (수익 비교 아님)
    python -m scripts.run_qs_switch run          # 판정 한 번 (트리 깨끗 · A1 통과 · 판정 파일 없음)

자료: ``data/research/tradfi/QQQ_adj.csv`` · ``DTB3.csv`` (문서 86 감사 통과).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from scripts.run_a1_train import git_state

from auto_trading.features.tsmom import trend_direction

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "tradfi"
OUTPUT = REPO / "reports" / "qs_switch"
CONTRIBUTION = 1_000.0
FEE = 0.0025
TAX = 0.22
TQQQ_FEE_YEAR = 0.0084
QLD_FEE_YEAR = 0.0095
LOOKBACKS = (20, 60, 120)
FROM, TO = "2008-06-01", "2026-08-31"
WINDOW_YEARS = 10
WINDOW_LAST_START = "2016-08-31"
Q2_SHARE = 0.60
Q3_MARGIN = 0.10
A1_CORR, A1_YEAR_DIFF = 0.99, 0.03


def read_close(name: str) -> dict[str, float]:
    with (DATA / f"{name}.csv").open(encoding="utf-8") as fh:
        return {row["date"]: float(row["close"]) for row in csv.DictReader(fh)}


# ------------------------------------------------------------------ 순수 계산


def leveraged(
    days: Sequence[str], base: Mapping[str, float], rf: Mapping[str, float], k: float, fee: float
) -> dict[str, float]:
    """가상 k 배 ETF 가격: 일간 수익 = k × 기초 수익 − ((k − 1) × 전날 금리 + 보수) / 252."""
    out = {days[0]: 100.0}
    for prev, cur in pairwise(days):
        r = base[cur] / base[prev] - 1
        carry = ((k - 1) * rf.get(prev, 0.0) / 100 + fee) / 252
        out[cur] = out[prev] * (1 + k * r - carry)
    return out


def trend_signals(days: Sequence[str], closes: Mapping[str, float]) -> dict[str, bool | None]:
    """t 일 종가까지로 계산한 T 규칙 신호 (> 0 이면 켜짐)."""
    hist: list[Decimal] = []
    out: dict[str, bool | None] = {}
    for d in days:
        hist.append(Decimal(str(closes[d])))
        v = trend_direction(hist, LOOKBACKS)
        out[d] = None if v is None else v > 0
    return out


def ma_signals(
    days: Sequence[str], closes: Mapping[str, float], n: int = 200
) -> dict[str, bool | None]:
    out: dict[str, bool | None] = {}
    window: list[float] = []
    for d in days:
        window.append(closes[d])
        if len(window) > n:
            window.pop(0)
        out[d] = None if len(window) < n else closes[d] > sum(window) / n
    return out


@dataclass(slots=True)
class Result:
    final: float = 0.0
    invested: float = 0.0
    tax_paid: float = 0.0
    fees: float = 0.0
    trades: int = 0
    off_days: int = 0
    days: int = 0
    max_cash: float = 0.0
    max_drawdown: float = 0.0
    round_trips: list[float] = field(default_factory=list)
    taxes_by_year: dict[str, float] = field(default_factory=dict)
    flows: list[tuple[str, float]] = field(default_factory=list)


def simulate(
    days: Sequence[str],
    qqq: Mapping[str, float],
    lev: Mapping[str, float],
    signal: Mapping[str, bool | None],
    rf: Mapping[str, float],
    *,
    mode: str,
    start: str,
    end: str,
    tax: bool = True,
    cash_interest: bool = False,
) -> Result:
    """``mode`` "A" (매달 QQQ) 또는 "QS". 신호는 전날 값을 오늘 종가에 실행한다."""
    span = [d for d in days if start <= d <= end]
    index = {d: i for i, d in enumerate(days)}
    res = Result()
    q_sh = l_sh = cash = 0.0
    basis = 0.0  # TQQQ 원가 (매수 수수료 포함)
    year_gain = 0.0
    units = 0.0
    peak_nav = 0.0
    prev_state: bool | None = None
    last_month = ""
    for n, d in enumerate(span):
        i = index[d]
        if cash_interest and cash > 0 and i > 0:
            cash *= 1 + rf.get(days[i - 1], 0.0) / 100 / 252
        value_before = q_sh * qqq[d] + l_sh * lev[d] + cash
        if d[:7] != last_month:
            last_month = d[:7]
            nav = value_before / units if units > 0 else 1.0
            units += CONTRIBUTION / nav
            cash += CONTRIBUTION
            res.invested += CONTRIBUTION
            res.flows.append((d, -CONTRIBUTION))
        state = bool(signal.get(days[i - 1])) if i > 0 else False
        if mode == "A":
            if cash > 0:
                res.fees += cash * FEE
                q_sh += cash * (1 - FEE) / qqq[d]
                cash = 0.0
        else:
            if prev_state is None:
                prev_state = state
            if state and not prev_state and cash > 0:  # 꺼짐 → 켜짐: 현금 전액 TQQQ
                res.fees += cash * FEE
                l_sh += cash * (1 - FEE) / lev[d]
                basis += cash
                cash = 0.0
                res.trades += 1
            elif not state and prev_state and l_sh > 0:  # 켜짐 → 꺼짐: TQQQ 전부 매도
                gross = l_sh * lev[d]
                proceeds = gross * (1 - FEE)
                res.fees += gross * FEE
                gain = proceeds - basis
                year_gain += gain
                res.round_trips.append(round(gain / basis, 4) if basis > 0 else 0.0)
                cash += proceeds
                l_sh = basis = 0.0
                res.trades += 1
            elif state and cash > 0:  # 켜짐: 입금은 QQQ
                res.fees += cash * FEE
                q_sh += cash * (1 - FEE) / qqq[d]
                cash = 0.0
            prev_state = state
            if not state:
                res.off_days += 1
        last_of_year = n == len(span) - 1 or span[n + 1][:4] != d[:4]
        if mode == "QS" and last_of_year:
            if tax and year_gain > 0:
                t = TAX * year_gain
                cash -= t
                res.tax_paid += t
                res.taxes_by_year[d[:4]] = round(t, 2)
            year_gain = 0.0
        res.max_cash = max(res.max_cash, cash)
        value = q_sh * qqq[d] + l_sh * lev[d] + cash
        nav = value / units if units > 0 else 1.0
        peak_nav = max(peak_nav, nav)
        if peak_nav > 0:
            res.max_drawdown = max(res.max_drawdown, 1 - nav / peak_nav)
        res.days += 1
        res.final = value
    res.flows.append((span[-1], res.final))
    return res


def irr(flows: Sequence[tuple[str, float]]) -> float | None:
    t0 = date.fromisoformat(flows[0][0])
    pts = [((date.fromisoformat(d) - t0).days / 365.25, v) for d, v in flows]

    def npv(r: float) -> float:
        return float(sum(v / (1 + r) ** t for t, v in pts))

    lo, hi = -0.99, 3.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 5)


def window_starts(days: Sequence[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in days:
        if d < FROM or d > WINDOW_LAST_START or d[:7] in seen:
            continue
        seen.add(d[:7])
        y, m, dd = (int(x) for x in d.split("-"))
        end = date(y + WINDOW_YEARS, m, min(dd, 28)).isoformat()
        last = max(x for x in days if x <= end)
        out.append((d, last))
    return out


# ------------------------------------------------------------------ 명령


def _inputs() -> tuple[list[str], dict[str, float], dict[str, float]]:
    qqq = read_close("QQQ_adj")
    rf_raw = read_close("DTB3")
    days = sorted(d for d in qqq if d <= TO)
    rf: dict[str, float] = {}
    last = 0.0
    for d in days:
        last = rf_raw.get(d, last)
        rf[d] = last
    return days, qqq, rf


def fetch_tqqq() -> int:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    broker = KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )
    try:
        bars = broker.daily_history("TQQQ", since="2010-02-01", until=TO, adjusted=True)
    finally:
        broker.close()
    path = DATA / "TQQQ_adj.csv"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(("date", "open", "high", "low", "close", "volume"))
        for b in bars:
            day = datetime.fromtimestamp(b.open_ms / 1000, UTC).strftime("%Y-%m-%d")
            w.writerow((day, b.open, b.high, b.low, b.close, b.volume))
    print(json.dumps({"rows": len(bars), "path": str(path)}))
    return 0


def audit() -> dict[str, object]:
    days, qqq, rf = _inputs()
    real = read_close("TQQQ_adj")
    common = [d for d in days if d in real]
    synth = leveraged(common, qqq, rf, 3.0, TQQQ_FEE_YEAR)
    rs = [synth[b] / synth[a] - 1 for a, b in pairwise(common)]
    rr = [real[b] / real[a] - 1 for a, b in pairwise(common)]
    corr = statistics.correlation(rs, rr)
    diffs = []
    for year in sorted({d[:4] for d in common}):
        ys = [d for d in common if d[:4] == year]
        if len(ys) < 200:
            continue
        diffs.append((synth[ys[-1]] / synth[ys[0]]) - (real[ys[-1]] / real[ys[0]]))
    med = statistics.median(diffs)
    out = {
        "days": len(common),
        "first": common[0],
        "daily_return_corr": round(corr, 5),
        "yearly_diff_median": round(med, 4),
        "yearly_diffs": [round(x, 4) for x in diffs],
        "pass": corr >= A1_CORR and abs(med) <= A1_YEAR_DIFF,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit_a1.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _summary(r: Result) -> dict[str, object]:
    return {
        "final": round(r.final, 2),
        "invested": r.invested,
        "irr": irr(r.flows),
        "max_drawdown": round(r.max_drawdown, 4),
        "tax_paid": round(r.tax_paid, 2),
        "fees": round(r.fees, 2),
        "trades": r.trades,
        "off_share": round(r.off_days / max(1, r.days), 4),
        "max_cash": round(r.max_cash, 2),
        "round_trips": r.round_trips,
        "taxes_by_year": r.taxes_by_year,
    }


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 판정")
    a1 = json.loads((OUTPUT / "audit_a1.json").read_text(encoding="utf-8"))
    if not a1["pass"]:
        raise SystemExit("A1 (가상 TQQQ 점검) 미통과 — 판정하지 않는다")
    days, qqq, rf = _inputs()
    tqqq = leveraged(days, qqq, rf, 3.0, TQQQ_FEE_YEAR)
    qld = leveraged(days, qqq, rf, 2.0, QLD_FEE_YEAR)
    sig_t = trend_signals(days, qqq)
    sig_ma = ma_signals(days, qqq)
    end = max(d for d in days if d <= TO)
    start = min(d for d in days if d >= FROM)

    def sim(mode: str, **kw: object) -> Result:
        lev = kw.pop("lev", tqqq)
        signal = kw.pop("signal", sig_t)
        return simulate(days, qqq, lev, signal, rf, mode=mode, start=start, end=end, **kw)  # type: ignore[arg-type]

    a, qs = sim("A"), sim("QS")
    windows = window_starts(days)
    ratios = []
    for ws, we in windows:
        ra = simulate(days, qqq, tqqq, sig_t, rf, mode="A", start=ws, end=we)
        rq = simulate(days, qqq, tqqq, sig_t, rf, mode="QS", start=ws, end=we)
        ratios.append(rq.final / ra.final)
    q1 = qs.final > a.final
    q2_share = sum(1 for x in ratios if x > 1) / len(ratios)
    q2 = q2_share >= Q2_SHARE
    q3 = qs.max_drawdown <= a.max_drawdown + Q3_MARGIN
    references: dict[str, Callable[[], Result]] = {
        "QS_no_tax": lambda: sim("QS", tax=False),
        "QS_cash_interest": lambda: sim("QS", cash_interest=True),
        "A_cash_interest": lambda: sim("A", cash_interest=True),
        "QS_ma200": lambda: sim("QS", signal=sig_ma),
        "QS_qld_2x": lambda: sim("QS", lev=qld),
        "QS_qqq_1x": lambda: sim("QS", lev=qqq),
    }
    out = {
        "git": sha,
        "period": [start, end],
        "A": _summary(a),
        "QS": _summary(qs),
        "Q1_final_QS_gt_A": q1,
        "Q2_window_share": round(q2_share, 4),
        "Q2_pass": q2,
        "Q3_mdd": [round(qs.max_drawdown, 4), round(a.max_drawdown, 4)],
        "Q3_pass": q3,
        "verdict": "PASS" if q1 and q2 and q3 else "REJECT",
        "windows": {
            "count": len(windows),
            "ratio_min": round(min(ratios), 4),
            "ratio_median": round(statistics.median(ratios), 4),
            "ratio_max": round(max(ratios), 4),
        },
        "references": {k: _summary(f()) for k, f in references.items()},
        "note": "문서 110 1 회 판정. 참고 줄은 판정에 쓰지 않는다.",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch-tqqq", "audit", "run"))
    args = parser.parse_args(argv)
    if args.command == "fetch-tqqq":
        return fetch_tqqq()
    result = audit() if args.command == "audit" else run()
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
