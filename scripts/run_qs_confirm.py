"""문서 120 — QS 확인 필터("N 일 연속 켜져야 켜짐"): 1987~1997 시작 창으로 고르고 2008~ 으로 검증.

사용 예::

    python -m scripts.run_qs_confirm run     # → reports/qs_confirm/result.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_qs_dotcom import MONTHS, _add_months, _series
from scripts.run_qs_switch import TQQQ_FEE_YEAR, leveraged, simulate, trend_signals

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "qs_confirm"
CANDIDATES = (3, 5, 10, 20)
TRAIN = ("1987-01", "1997-12")
TEST = ("2008-01", "2016-08")
TRAIN_GAIN = 0.05
T1_KEEP = 0.97
T2_SHARE = 0.90


def confirm(days: Sequence[str], sig: Mapping[str, bool | None], n: int) -> dict[str, bool | None]:
    """원래 신호가 n 거래일 연속 켜져 있어야 켜짐. 꺼짐은 그날 바로. n 1 이면 원래 신호."""
    out: dict[str, bool | None] = {}
    run = 0
    for d in days:
        s = sig.get(d)
        if s is None:
            out[d] = None
            run = 0
            continue
        run = run + 1 if s else 0
        out[d] = run >= n
    return out


def _windows(days: Sequence[str], first: str, last: str) -> list[tuple[str, str]]:
    months = sorted({d[:7] for d in days})
    first_day = {}
    last_day = {}
    for d in days:
        first_day.setdefault(d[:7], d)
        last_day[d[:7]] = d
    out = []
    for m in months:
        if not first <= m <= last:
            continue
        end_m = _add_months(m, MONTHS - 1)
        if end_m in last_day:
            out.append((first_day[m], last_day[end_m]))
    return out


def _stats(ratios: list[float]) -> dict[str, float]:
    return {
        "min": round(min(ratios), 4),
        "median": round(statistics.median(ratios), 4),
        "max": round(max(ratios), 4),
        "share_gt_A": round(sum(r > 1 for r in ratios) / len(ratios), 4),
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
    raw = trend_signals(days, ndx)
    tqqq = leveraged(days, ndx, rf, 3.0, TQQQ_FEE_YEAR)
    sigs = {n: confirm(days, raw, n) for n in (1, *CANDIDATES)}

    def ratios(n: int, wins: list[tuple[str, str]]) -> list[float]:
        out = []
        for s, e in wins:
            a = simulate(days, ndx, tqqq, sigs[n], rf, mode="A", start=s, end=e)
            q = simulate(days, ndx, tqqq, sigs[n], rf, mode="QS", start=s, end=e)
            out.append(q.final / a.final)
        return out

    train_w = _windows(days, *TRAIN)
    test_w = _windows(days, *TEST)
    train = {n: _stats(ratios(n, train_w)) for n in (1, *CANDIDATES)}
    best = sorted(CANDIDATES, key=lambda n: (-train[n]["min"], -train[n]["median"], n))[0]
    gain = train[best]["min"] - train[1]["min"]
    result: dict[str, Any] = {
        "train_windows": len(train_w),
        "test_windows": len(test_w),
        "train": {str(n): v for n, v in train.items()},
        "chosen_n": best,
        "train_gain_min": round(gain, 4),
    }
    if gain < TRAIN_GAIN:
        result["verdict"] = "REJECT"
        result["reason"] = f"학습 문턱 미달 ({gain:.4f} < {TRAIN_GAIN})"
    else:
        base_test = _stats(ratios(1, test_w))
        best_test = _stats(ratios(best, test_w))
        t1 = best_test["median"] >= base_test["median"] * T1_KEEP
        t2 = best_test["share_gt_A"] >= T2_SHARE
        result |= {
            "test_base": base_test,
            "test_chosen": best_test,
            "T1": t1,
            "T2": t2,
            "verdict": "PASS" if t1 and t2 else "REJECT",
        }
    # 보고만: 2000~2002 매매 횟수 (각 N)
    first = min(d for d in days if d >= "2000-01-01")
    result["crash_trades"] = {
        str(n): simulate(
            days, ndx, tqqq, sigs[n], rf, mode="QS", start=first, end="2002-12-31"
        ).trades
        for n in (1, *CANDIDATES)
    }
    result["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_qs_confirm run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
