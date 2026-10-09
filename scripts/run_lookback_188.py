"""문서 188 — T 브레이크 재매수 추세 기간 20 · 50 · 100 vs 20 · 60 · 120.

사용 예::

    python -m scripts.run_lookback_188 run   # 1 회 판정 → reports/lookback_188/result.json

* 사전 등록: docs/research/188-t-brake-rebuy-lookback-20-50-100-preregistration.md.
* 나스닥 M3 틀(184) + 다른 시장 25 개(185 · 186 틀). 추세 기간만 바꾼다.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_brake_183 import CRISES, END, _daily, data_real, data_synth
from scripts.run_brake_184 import qqq_real, qqq_synth, simulate, summarize
from scripts.run_brake_185 import MARKETS as M185
from scripts.run_brake_185 import _index as index185
from scripts.run_brake_185 import _rates, lev3, rf_daily
from scripts.run_brake_186 import MARKETS as M186
from scripts.run_brake_186 import _index as index186
from scripts.run_brake_186 import simulate as sim3x
from scripts.run_div_mix_180 import DATA as DIV_DATA
from scripts.run_qs_dotcom import _series

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "lookback_188"
L120, L100 = (20, 60, 120), (20, 50, 100)
P_MDD = 0.05
NEED = 17


def trend_map(
    days: Sequence[str], px: Mapping[str, float], lbs: Sequence[int]
) -> dict[str, bool | None]:
    """날짜 → 추세 켜짐 (lbs 거래일 수익 부호 평균 > 0, 183 과 같은 셈) (순수)."""
    out: dict[str, bool | None] = {}
    for i, d in enumerate(days):
        if i < max(lbs):
            out[d] = None
            continue
        p = px[d]
        out[d] = (
            sum(1 if p > px[days[i - k]] else -1 if p < px[days[i - k]] else 0 for k in lbs) > 0
        )
    return out


def nasdaq() -> dict[str, Any]:
    res: dict[str, Any] = {}
    qqq = _daily(DIV_DATA / "QQQ.csv")
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    for name, data, sig in (("R", data_real(), qqq), ("S", data_synth(), ndx)):
        q = qqq_real() if name == "R" else qqq_synth(data["days"], data["rf"])
        sd = sorted(d for d in sig if d <= END)
        row: dict[str, Any] = {}
        for k, lbs in (("L120", L120), ("L100", L100)):
            d2 = dict(data)
            d2["trend"] = trend_map(sd, sig, lbs)
            row[k] = summarize(simulate(d2, q, ref="T", park="cash"), CRISES[name])
        res[name] = row
    return res


def markets() -> dict[str, Any]:
    long_m = _rates()
    out: dict[str, Any] = {}
    series: list[tuple[str, dict[str, float]]] = [(m, index185(m, *M185[m])) for m in M185] + [
        (m, index186(m)) for m in M186
    ]
    for name, idx in series:
        days = sorted(idx)
        lev = lev3(days, idx, rf_daily(days, long_m))
        t120, t100 = trend_map(days, idx, L120), trend_map(days, idx, L100)
        out[name] = {"L120": sim3x(days, lev, t120, t120), "L100": sim3x(days, lev, t120, t100)}
    return out


def verdict(nq: Mapping[str, Any], mk: Mapping[str, Any]) -> dict[str, Any]:
    nas_ok = {
        s: bool(
            nq[s]["L100"]["irr_liq"] > nq[s]["L120"]["irr_liq"]
            and nq[s]["L100"]["mdd"] <= nq[s]["L120"]["mdd"] + P_MDD
        )
        for s in ("R", "S")
    }
    dc = [m["L100"]["cagr"] - m["L120"]["cagr"] for m in mk.values()]
    wins = sum(x >= 0 for x in dc)
    mk_ok = wins >= NEED and statistics.median(dc) > 0
    return {
        "nasdaq_ok": nas_ok,
        "markets": len(mk),
        "wins": wins,
        "median_cagr_diff": round(statistics.median(dc), 4),
        "median_mdd_diff": round(
            statistics.median([m["L100"]["mdd"] - m["L120"]["mdd"] for m in mk.values()]), 4
        ),
        "markets_ok": mk_ok,
        "choice": "L100" if all(nas_ok.values()) and mk_ok else "L120",
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    nq, mk = nasdaq(), markets()
    res = {
        "nasdaq": nq,
        "markets": mk,
        "verdict": verdict(nq, mk),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_lookback_188 run")
    res = run()
    for s in ("R", "S"):
        for k in ("L120", "L100"):
            r = res["nasdaq"][s][k]
            sys.stdout.write(
                f"{s} {k} 세후 {r['irr_liq'] * 100:5.1f} 세전 {r['cagr'] * 100:5.1f} "
                f"MDD {r['mdd'] * 100:4.1f} "
                f"매도 {r['brake_sales']} 재매수 {r['rebuys']} 손해 {r['loss_rebuy_share']}\n"
            )
    for m, r in res["markets"].items():
        a, b = r["L120"], r["L100"]
        sys.stdout.write(
            f"{m:6s} L120 {a['cagr'] * 100:5.1f}/{a['mdd'] * 100:3.0f}/{a['rebuys']:3d}  "
            f"L100 {b['cagr'] * 100:5.1f}/{b['mdd'] * 100:3.0f}/{b['rebuys']:3d}\n"
        )
    sys.stdout.write(json.dumps(res["verdict"], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
