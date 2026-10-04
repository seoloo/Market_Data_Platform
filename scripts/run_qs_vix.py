"""문서 124 — QS + VIX 회복 신호: 1991~1997 시작 창으로 고르고 2008~2016 시작 창으로 한 번 검증.

사용 예::

    python -m scripts.run_qs_vix run     # → reports/qs_vix/result.json
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_qs_confirm import _windows
from scripts.run_qs_dotcom import FRED, _series
from scripts.run_qs_switch import TQQQ_FEE_YEAR, leveraged, simulate, trend_signals

from scripts import run_qs_dotcom as dc

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "qs_vix"
VIX_SHA = "cc0b59b37b714477d604de46fba408fd888e82308561111a772f3aacf931387f"
dc.SHA["VIXCLS.csv"] = VIX_SHA  # _series 가 같은 해시 확인을 하게
XS = (25.0, 30.0, 40.0)
RS = (0.7, 0.8)
LOOKBACK = 60
TRAIN = ("1991-01", "1997-12")
TEST = ("2008-01", "2016-08")
TRAIN_GAIN = 0.05
T1_KEEP = 0.97
T2_SHARE = 0.90


def vix_signal(
    days: Sequence[str],
    trend: Mapping[str, bool | None],
    vix: Sequence[float],
    x: float,
    r: float,
) -> dict[str, bool | None]:
    """T 규칙 켜짐 또는 (지난 60 거래일 VIX 최고 ≥ x 이고 오늘 VIX ≤ r × 그 최고)."""
    out: dict[str, bool | None] = {}
    for i, d in enumerate(days):
        t = trend.get(d)
        if t is None:
            out[d] = None
            continue
        peak = max(vix[max(0, i - LOOKBACK + 1) : i + 1])
        out[d] = t or (peak >= x and vix[i] <= r * peak)
    return out


def _stats(ratios: list[float]) -> dict[str, float]:
    return {
        "min": round(min(ratios), 4),
        "median": round(statistics.median(ratios), 4),
        "max": round(max(ratios), 4),
        "share_gt_A": round(sum(v > 1 for v in ratios) / len(ratios), 4),
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    path = FRED / "VIXCLS.csv"
    if hashlib.sha256(path.read_bytes()).hexdigest() != VIX_SHA:
        raise SystemExit("VIXCLS.csv 해시가 고정값과 다르다 — 판정하지 않는다")
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate_raw = _series("DTB3_full.csv", "DTB3")
    vix_raw = _series("VIXCLS.csv", "VIXCLS")
    days = sorted(d for d in ndx if d >= min(vix_raw))
    rf: dict[str, float] = {}
    last = 0.0
    for d in sorted(set(ndx) | set(rate_raw)):
        last = rate_raw.get(d, last)
        rf[d] = last
    vix: list[float] = []
    lv = next(iter(vix_raw.values()))
    for d in days:
        lv = vix_raw.get(d, lv)
        vix.append(lv)
    all_days = sorted(ndx)
    trend_all = trend_signals(all_days, ndx)
    trend = {d: trend_all[d] for d in days}
    tqqq = leveraged(days, ndx, rf, 3.0, TQQQ_FEE_YEAR)
    sigs: dict[str, dict[str, bool | None]] = {"base": trend}
    for x in XS:
        for r in RS:
            sigs[f"X{x:g}_r{r:g}"] = vix_signal(days, trend, vix, x, r)

    def ratios(name: str, wins: list[tuple[str, str]]) -> list[float]:
        out = []
        for s, e in wins:
            a = simulate(days, ndx, tqqq, sigs[name], rf, mode="A", start=s, end=e)
            q = simulate(days, ndx, tqqq, sigs[name], rf, mode="QS", start=s, end=e)
            out.append(q.final / a.final)
        return out

    train_w = _windows(days, *TRAIN)
    test_w = _windows(days, *TEST)
    train = {k: _stats(ratios(k, train_w)) for k in sigs}
    cands = [k for k in sigs if k != "base"]

    def order(k: str) -> tuple[float, float, float, float]:
        x = float(k.split("_")[0][1:])
        r = float(k.split("_")[1][1:])
        return (-train[k]["min"], -train[k]["median"], -x, r)

    best = sorted(cands, key=order)[0]
    gain = train[best]["min"] - train["base"]["min"]
    result: dict[str, Any] = {
        "train_windows": len(train_w),
        "test_windows": len(test_w),
        "train": train,
        "chosen": best,
        "train_gain_min": round(gain, 4),
    }
    if gain < TRAIN_GAIN:
        result["verdict"] = "REJECT"
        result["reason"] = f"학습 문턱 미달 ({gain:.4f} < {TRAIN_GAIN})"
    else:
        base_t = _stats(ratios("base", test_w))
        best_t = _stats(ratios(best, test_w))
        t1 = best_t["median"] >= base_t["median"] * T1_KEEP
        t2 = best_t["share_gt_A"] >= T2_SHARE
        result |= {
            "test_base": base_t,
            "test_chosen": best_t,
            "T1": t1,
            "T2": t2,
            "verdict": "PASS" if t1 and t2 else "REJECT",
        }
    first = min(d for d in days if d >= "2000-01-01")
    result["crash_trades"] = {
        k: simulate(days, ndx, tqqq, v, rf, mode="QS", start=first, end="2002-12-31").trades
        for k, v in sigs.items()
    }
    result["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_qs_vix run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
