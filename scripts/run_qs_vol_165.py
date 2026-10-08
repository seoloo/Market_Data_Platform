"""문서 165 — QS 의 TQQQ 몫을 변동성에 따라 3 배 → 1 배로 낮추기.

1987 ~ 1997 시작 창으로 고르고 2008 ~ 로 검증한다.

사용 예::

    python -m scripts.run_qs_vol_165 run     # → reports/qs_vol_165/result.json (한 번만)

* 사전 등록: docs/research/165-qs-vol-managed-tqqq.md. 120 · 124 와 같은 틀(FRED 나스닥 100 · DTB3,
  110 의 ``simulate`` 그대로)이고, TQQQ 자리에 넣는 가격 줄만 바꾼다.
* 변동성 관리 3 배 몫: 그날 배율 ``L = clip(V* / σ20, 1, 3)``. σ20 은 **전날까지** 20 거래일
  지수 일간 수익 표준편차 × √252. 몫은 TQQQ 비중 w = (L − 1)/2 · 나머지 1 배(지수)로 본다.
  배율은 새 목표가 지금보다 0.5 이상 다를 때만 바꾼다(띠). 바꿀 때 회전 |ΔL| × 0.25% 를 뺀다.
* 후보 V* ∈ {40%, 50%, 60%, 75%}. 기준 = 지금 QS(L 늘 3).
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_qs_confirm import _windows
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import FEE, TQQQ_FEE_YEAR, leveraged, simulate, trend_signals

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "qs_vol_165"
CANDIDATES = (0.40, 0.50, 0.60, 0.75)
VOL_WINDOW = 20
BAND = 0.5
TRAIN = ("1987-01", "1997-12")
TEST = ("2008-01", "2016-08")
KEEP = 0.95
"""고른 V* 의 창 비율(QS_V ÷ A) 중앙값 ≥ 기준 중앙값 × 0.95 (상승장 이득을 5% 넘게 버리지 않음)."""
DD_GAIN = 0.05
"""낙폭 중앙값이 기준보다 5%p 이상 작아야 한다."""
T3_SHARE = 0.90


def vol_managed(
    days: Sequence[str],
    base: Mapping[str, float],
    rf: Mapping[str, float],
    target: float,
    *,
    window: int = VOL_WINDOW,
    band: float = BAND,
) -> tuple[dict[str, float], dict[str, float]]:
    """변동성 관리 1 ~ 3 배 가격 줄과 날마다 배율 (순수).

    오늘 수익에 쓰는 배율은 **전날까지**의 σ 로 정한다. 배율 k 의 하루 비용은 110 의 가상 레버리지와
    같은 식(차입 (k − 1) × 금리 + TQQQ 보수 × TQQQ 비중)이고, 배율을 바꾼 날 |ΔL| × FEE 를 뺀다."""
    price = {days[0]: 100.0}
    lev: dict[str, float] = {days[0]: 3.0}
    rets: list[float] = []
    cur = 3.0
    for prev, d in pairwise(days):
        r = base[d] / base[prev] - 1
        if len(rets) >= window:
            sd = statistics.pstdev(rets[-window:]) * math.sqrt(252)
            want = min(3.0, max(1.0, target / sd)) if sd > 0 else 3.0
        else:
            want = 3.0
        cost = 0.0
        if abs(want - cur) >= band:
            cost = abs(want - cur) * FEE
            cur = want
        w = (cur - 1) / 2
        carry = ((cur - 1) * rf.get(prev, 0.0) / 100 + TQQQ_FEE_YEAR * w) / 252
        price[d] = price[prev] * (1 + cur * r - carry) * (1 - cost)
        lev[d] = cur
        rets.append(r)
    return price, lev


def _stats(ratios: Sequence[float], dds: Sequence[float]) -> dict[str, float]:
    return {
        "ratio_min": round(min(ratios), 4),
        "ratio_median": round(statistics.median(ratios), 4),
        "share_gt_A": round(sum(r > 1 for r in ratios) / len(ratios), 4),
        "dd_median": round(statistics.median(dds), 4),
        "dd_max": round(max(dds), 4),
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate_raw = _series("DTB3_full.csv", "DTB3")
    days = sorted(ndx)
    rf: dict[str, float] = {}
    last = 0.0
    for d in sorted(set(days) | set(rate_raw)):
        last = rate_raw.get(d, last)
        rf[d] = last
    sig = trend_signals(days, ndx)
    lines: dict[str, dict[str, float]] = {"base": leveraged(days, ndx, rf, 3.0, TQQQ_FEE_YEAR)}
    levs: dict[str, dict[str, float]] = {}
    for v in CANDIDATES:
        lines[str(v)], levs[str(v)] = vol_managed(days, ndx, rf, v)

    def evaluate(key: str, wins: list[tuple[str, str]]) -> dict[str, float]:
        ratios, dds = [], []
        for s, e in wins:
            a = simulate(days, ndx, lines[key], sig, rf, mode="A", start=s, end=e)
            q = simulate(days, ndx, lines[key], sig, rf, mode="QS", start=s, end=e)
            ratios.append(q.final / a.final)
            dds.append(q.max_drawdown)
        return _stats(ratios, dds)

    train_w, test_w = _windows(days, *TRAIN), _windows(days, *TEST)
    keys = ["base", *map(str, CANDIDATES)]
    train = {k: evaluate(k, train_w) for k in keys}
    ok = [k for k in keys[1:] if train[k]["ratio_median"] >= train["base"]["ratio_median"] * KEEP]
    result: dict[str, Any] = {
        "train_windows": len(train_w),
        "test_windows": len(test_w),
        "train": train,
        "train_eligible": ok,
    }
    if not ok:
        result |= {"verdict": "REJECT", "reason": "학습: 수익 조건(× 0.95)을 지키는 후보 없음"}
    else:
        best = sorted(ok, key=lambda k: (train[k]["dd_median"], -train[k]["ratio_median"]))[0]
        gain = train["base"]["dd_median"] - train[best]["dd_median"]
        result |= {"chosen": best, "train_dd_gain": round(gain, 4)}
        if gain < DD_GAIN:
            result |= {
                "verdict": "REJECT",
                "reason": f"학습 문턱 미달 (낙폭 {gain:.4f} < {DD_GAIN})",
            }
        else:
            tb, tc = evaluate("base", test_w), evaluate(best, test_w)
            t1 = tc["ratio_median"] >= tb["ratio_median"] * KEEP
            t2 = tb["dd_median"] - tc["dd_median"] >= DD_GAIN
            t3 = tc["share_gt_A"] >= T3_SHARE
            result |= {
                "test_base": tb,
                "test_chosen": tc,
                "T1": t1,
                "T2": t2,
                "T3": t3,
                "verdict": "PASS" if t1 and t2 and t3 else "REJECT",
            }
    # 보고만: 큰 하락 구간의 최대 낙폭 · 한 경로 2008-06 ~ · 배율 분포
    spans = {
        "2000-2002": ("2000-01-01", "2002-12-31"),
        "2008": ("2008-01-01", "2009-06-30"),
        "2020-02~04": ("2020-01-01", "2020-06-30"),
        "2022": ("2021-11-01", "2023-06-30"),
        "2008-06~": ("2008-06-01", "2026-08-31"),
    }
    rep: dict[str, Any] = {}
    for k in keys:
        rep[k] = {}
        for name, (s, e) in spans.items():
            q = simulate(days, ndx, lines[k], sig, rf, mode="QS", start=s, end=e)
            rep[k][name] = {
                "dd": round(q.max_drawdown, 4),
                "final": round(q.final),
                "trades": q.trades,
            }
    a_full = simulate(
        days, ndx, lines["base"], sig, rf, mode="A", start="2008-06-01", end="2026-08-31"
    )
    rep["A_2008-06~"] = {"dd": round(a_full.max_drawdown, 4), "final": round(a_full.final)}
    result["report"] = rep
    result["leverage_share"] = {
        k: {
            str(x): round(
                sum(1 for d in days if d >= "1987-01-01" and abs(v[d] - x) < 0.25) / len(v), 3
            )
            for x in (1.0, 1.5, 2.0, 2.5, 3.0)
        }
        for k, v in levs.items()
    }
    result["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_qs_vol_165 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
