"""문서 127 — 코인 T/v2 × QQQ 추세 배분(매달 맞춤). 앞 반쪽 변동성 반비례 비중 → 뒤 반쪽 확인.

사용 예::

    python -m scripts.run_alloc run     # → reports/alloc/result.json
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_ag_small import crypto_path, load_crypto
from scripts.run_index_mr import trend_returns
from scripts.run_qs_switch import read_close

from auto_trading.backtest.overlay import T2_ALONE

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "alloc"
START, END = "2020-03-01", "2026-08-31"
SPLIT = "2023-01-01"
WEIGHTS = [w / 10 for w in range(11)]


def _calendar(lo: str, hi: str) -> list[str]:
    d, e = date.fromisoformat(lo), date.fromisoformat(hi)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def daily_returns() -> tuple[list[str], list[float], list[float]]:
    """(달력 날짜, 코인 T/v2 일간 수익, QQQ 추세 일간 수익 — 휴장일 0)."""
    path = crypto_path(load_crypto(), T2_ALONE, START, END, 1.0)
    cdays = sorted(path)
    q = read_close("QQQ_adj")
    qd = [d for d in sorted(q) if d <= END]
    tr = trend_returns(qd, [q[d] for d in qd])
    srets = dict(zip(qd, tr, strict=True))
    days = [d for d in _calendar(cdays[0], cdays[-1]) if d in path]
    c = [0.0] + [path[b] / path[a] - 1 for a, b in pairwise(days)]
    s = [srets.get(d, 0.0) for d in days]
    return days, c, s


def mix(days: Sequence[str], c: Sequence[float], s: Sequence[float], w: float) -> list[float]:
    """매달 첫날 코인 w · 주식 1−w 로 맞추는 계좌의 일간 수익."""
    vc, vs = w, 1 - w
    out = []
    month = ""
    for d, rc, rs in zip(days, c, s, strict=True):
        if d[:7] != month:
            month = d[:7]
            total = vc + vs
            vc, vs = total * w, total * (1 - w)
        before = vc + vs
        vc *= 1 + rc
        vs *= 1 + rs
        out.append((vc + vs) / before - 1 if before > 0 else 0.0)
    return out


def stats(rets: Sequence[float]) -> dict[str, float]:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    years = len(rets) / 365
    sd = statistics.stdev(rets)
    return {
        "cagr": round(eq ** (1 / years) - 1, 4),
        "sharpe": round(statistics.fmean(rets) / sd * math.sqrt(365), 3) if sd > 0 else 0.0,
        "mdd": round(mdd, 4),
        "vol": round(sd * math.sqrt(365), 4),
    }


def _seg(days: Sequence[str], xs: Sequence[float], lo: str, hi: str) -> list[float]:
    return [x for d, x in zip(days, xs, strict=True) if lo <= d <= hi]


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    days, c, s = daily_returns()
    trade_days = [i for i, v in enumerate(s) if v != 0.0]
    corr_daily = statistics.correlation([c[i] for i in trade_days], [s[i] for i in trade_days])

    def monthly(xs: Sequence[float]) -> list[float]:
        acc: dict[str, float] = {}
        for d, r in zip(days, xs, strict=True):
            acc[d[:7]] = acc.get(d[:7], 1.0) * (1 + r)
        return [v - 1 for v in acc.values()]

    corr_monthly = statistics.correlation(monthly(c), monthly(s))
    grid = {f"{int(w * 100)}%": stats(mix(days, c, s, w)) for w in WEIGHTS}
    first_c, first_s = _seg(days, c, START, "2022-12-31"), _seg(days, s, START, "2022-12-31")
    vc, vs = statistics.stdev(first_c), statistics.stdev(first_s)
    w_iv = vs / (vc + vs)
    second = (SPLIT, END)
    m2 = stats(_seg(days, mix(days, c, s, w_iv), *second))
    c2 = stats(_seg(days, c, *second))
    s2 = stats(_seg(days, s, *second))
    r1 = m2["sharpe"] >= max(c2["sharpe"], s2["sharpe"])
    r2 = m2["mdd"] <= (c2["mdd"] + s2["mdd"]) / 2
    y22 = ("2022-01-01", "2022-12-31")
    result = {
        "verdict": "RECOMMEND" if r1 and r2 else "NO_CLEAR_GAIN",
        "R1": r1,
        "R2": r2,
        "inverse_vol_crypto_weight": round(w_iv, 3),
        "second_half": {"mix": m2, "crypto": c2, "stock": s2},
        "corr_daily_trading_days": round(corr_daily, 3),
        "corr_monthly": round(corr_monthly, 3),
        "grid_full": grid,
        "y2022": {
            "crypto": stats(_seg(days, c, *y22)),
            "stock": stats(_seg(days, s, *y22)),
            "mix_iv": stats(_seg(days, mix(days, c, s, w_iv), *y22)),
        },
        "range": [days[0], days[-1]],
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_alloc run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["mix", "stats"]
