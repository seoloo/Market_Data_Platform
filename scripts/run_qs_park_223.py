"""문서 223 — QS 가 꺼져 쉬는 동안 돈을 채권(AGG)에 두기, 후향 파일럿 1 회. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_qs_park_223      # reports/qs_park_223/result.json · 화면

QS(110) 시뮬레이터와 같은 순서 · 비용 · 세금이다. 꺼진 동안의 현금만 다르게 둔다:
``cash0`` = 이자 0(110 판정), ``tbill`` = DTB3 이자(110 참고 줄), ``agg`` = AGG(수정주가) 보유.
``cash0`` · ``tbill`` 은 ``run_qs_switch.simulate`` 와 숫자가 같다(시험).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.run_qs_switch import (
    CONTRIBUTION,
    FEE,
    FROM,
    TAX,
    TO,
    TQQQ_FEE_YEAR,
    Result,
    _inputs,
    irr,
    leveraged,
    trend_signals,
    window_starts,
)

REPO = Path(__file__).resolve().parent.parent
AGG_CSV = REPO / "data" / "research" / "tradfi" / "gem" / "AGG.csv"
OUT = REPO / "reports" / "qs_park_223"
WIN_SHARE = 0.60
MIN_IRR_GAIN = 0.002
MAX_MDD_WORSE = 0.02


def read_agg() -> dict[str, float]:
    with AGG_CSV.open(encoding="utf-8") as fh:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(fh)}


def fill_forward(days: Sequence[str], px: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    last = None
    for d in days:
        last = px.get(d, last)
        if last is not None:
            out[d] = last
    return out


def simulate_park(
    days: Sequence[str],
    qqq: Mapping[str, float],
    lev: Mapping[str, float],
    signal: Mapping[str, bool | None],
    rf: Mapping[str, float],
    agg: Mapping[str, float],
    *,
    park: str,
    start: str,
    end: str,
) -> Result:
    """QS 하나 (``run_qs_switch.simulate`` mode="QS" 와 같은 순서).

    꺼진 동안 현금 두는 곳만 ``park`` 로 다르다."""
    if park not in ("cash0", "tbill", "agg"):
        raise ValueError(park)
    span = [d for d in days if start <= d <= end]
    index = {d: i for i, d in enumerate(days)}
    res = Result()
    q_sh = l_sh = cash = a_sh = 0.0
    basis = a_basis = 0.0
    year_gain = 0.0
    units = 0.0
    peak_nav = 0.0
    prev_state: bool | None = None
    last_month = ""

    def sell_agg(d: str) -> None:
        nonlocal a_sh, a_basis, cash, year_gain
        if a_sh <= 0:
            return
        gross = a_sh * agg[d]
        res.fees += gross * FEE
        proceeds = gross * (1 - FEE)
        year_gain += proceeds - a_basis
        cash += proceeds
        a_sh = a_basis = 0.0
        res.trades += 1

    for n, d in enumerate(span):
        i = index[d]
        if park == "tbill" and cash > 0 and i > 0:
            cash *= 1 + rf.get(days[i - 1], 0.0) / 100 / 252
        value_before = q_sh * qqq[d] + l_sh * lev[d] + cash + a_sh * agg[d]
        if d[:7] != last_month:
            last_month = d[:7]
            nav = value_before / units if units > 0 else 1.0
            units += CONTRIBUTION / nav
            cash += CONTRIBUTION
            res.invested += CONTRIBUTION
            res.flows.append((d, -CONTRIBUTION))
        state = bool(signal.get(days[i - 1])) if i > 0 else False
        if prev_state is None:
            prev_state = state
        if state and not prev_state:  # 꺼짐 → 켜짐: (채권 팔고) 현금 전액 TQQQ
            sell_agg(d)
            if cash > 0:
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
        if park == "agg" and not state and cash > 0:  # 꺼진 동안 현금은 AGG 로
            res.fees += cash * FEE
            a_sh += cash * (1 - FEE) / agg[d]
            a_basis += cash
            cash = 0.0
        prev_state = state
        if not state:
            res.off_days += 1
        last_of_year = n == len(span) - 1 or span[n + 1][:4] != d[:4]
        if last_of_year:
            if year_gain > 0:
                t = TAX * year_gain
                cash -= t
                res.tax_paid += t
                res.taxes_by_year[d[:4]] = round(t, 2)
            year_gain = 0.0
        res.max_cash = max(res.max_cash, cash)
        value = q_sh * qqq[d] + l_sh * lev[d] + cash + a_sh * agg[d]
        nav = value / units if units > 0 else 1.0
        res.navs.append((d, nav))
        peak_nav = max(peak_nav, nav)
        if peak_nav > 0:
            res.max_drawdown = max(res.max_drawdown, 1 - nav / peak_nav)
        res.days += 1
        res.final = value
    res.flows.append((span[-1], res.final))
    return res


def summary(r: Result) -> dict[str, Any]:
    return {
        "final": round(r.final, 2),
        "irr": irr(r.flows),
        "mdd": round(r.max_drawdown, 4),
        "tax_paid": round(r.tax_paid, 2),
        "fees": round(r.fees, 2),
        "off_share": round(r.off_days / max(1, r.days), 4),
    }


def verdict(full_gain: float, win_share: float, med: float, mdd_worse: float) -> str:
    if full_gain <= 0 and med <= 0:
        return "불리"
    ok = (
        full_gain >= MIN_IRR_GAIN
        and win_share >= WIN_SHARE
        and med > 0
        and mdd_worse <= MAX_MDD_WORSE
    )
    return "지지" if ok else "불명"


def main() -> int:
    days, qqq, rf = _inputs()
    agg = fill_forward(days, read_agg())
    days = [d for d in days if d in agg]
    tqqq = leveraged(days, qqq, rf, 3.0, TQQQ_FEE_YEAR)
    sig = trend_signals(days, qqq)
    start = min(d for d in days if d >= FROM)
    end = max(d for d in days if d <= TO)
    full = {
        p: simulate_park(days, qqq, tqqq, sig, rf, agg, park=p, start=start, end=end)
        for p in ("cash0", "tbill", "agg")
    }
    s = {p: summary(r) for p, r in full.items()}
    gains = []
    gains_vs0 = []
    for ws, we in window_starts(days):
        r = {
            p: simulate_park(days, qqq, tqqq, sig, rf, agg, park=p, start=ws, end=we)
            for p in ("cash0", "tbill", "agg")
        }
        ia, it, i0 = irr(r["agg"].flows), irr(r["tbill"].flows), irr(r["cash0"].flows)
        if ia is not None and it is not None and i0 is not None:
            gains.append(ia - it)
            gains_vs0.append(it - i0)
    full_gain = (s["agg"]["irr"] or 0.0) - (s["tbill"]["irr"] or 0.0)
    win = sum(g > 0 for g in gains) / len(gains)
    med = statistics.median(gains)
    res: dict[str, Any] = {
        "period": [start, end],
        "full": s,
        "agg_minus_tbill": {
            "irr_full": full_gain,
            "windows_10y": len(gains),
            "win_share": win,
            "median": med,
            "min": min(gains),
            "max": max(gains),
            "mdd_worse": s["agg"]["mdd"] - s["tbill"]["mdd"],
        },
        "tbill_minus_cash0(참고)": {
            "irr_full": (s["tbill"]["irr"] or 0.0) - (s["cash0"]["irr"] or 0.0),
            "median_10y": statistics.median(gains_vs0),
        },
    }
    res["verdict"] = verdict(full_gain, win, med, res["agg_minus_tbill"]["mdd_worse"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
