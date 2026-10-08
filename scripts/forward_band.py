"""통과 전략의 '기대 범위' — 앞으로의 성과가 백테스트 범위 밖으로 나가면 검토 (보완 3 번).

사용 예::

    python -m scripts.forward_band bands --out reports/forward_band/bands.json   # 범위표만
    python -m scripts.forward_band check                                          # Paper 원장 점검

* 범위: 판정한 규칙을 그대로 재생한 백테스트(2020-03 ~ 2026-08) 일간 자산에서, 길이 L 일인 모든
  구간의 수익과 그 구간 안 최대 낙폭의 분포. 앞으로의 성과는 같은 길이 L 의 분포와 비교한다.
* 등급(docs/research/FORWARD_BAND.md 에 고정):
  - REVIEW: L 일 누적 수익 < 백테스트 5% 분위, 또는 L 일 최대 낙폭 > 95% 분위
  - ALERT: 1% 분위 아래, 또는 99% 분위 위
  - OK: 그 밖
* 원장은 읽기 전용(``data/trading.db`` mode=ro). 주문 · 설정 · 운영을 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
HORIZONS = (7, 14, 30, 60, 90, 180, 365)


def window_stats(equity: Sequence[float], length: int) -> dict[str, float] | None:
    """길이 length 일 모든 구간의 수익 1 · 5 · 50% 분위와 최대 낙폭 95 · 99% 분위 (순수)."""
    if length < 1 or len(equity) <= length:
        return None
    rets: list[float] = []
    dds: list[float] = []
    for i in range(len(equity) - length):
        seg = equity[i : i + length + 1]
        rets.append(seg[-1] / seg[0] - 1)
        peak, mdd = seg[0], 0.0
        for x in seg:
            peak = max(peak, x)
            mdd = max(mdd, 1 - x / peak)
        dds.append(mdd)
    rets.sort()
    dds.sort()

    def q(xs: list[float], p: float) -> float:
        return xs[min(len(xs) - 1, int(p * len(xs)))]

    return {
        "ret_p01": q(rets, 0.01),
        "ret_p05": q(rets, 0.05),
        "ret_p50": q(rets, 0.50),
        "mdd_p95": q(dds, 0.95),
        "mdd_p99": q(dds, 0.99),
        "windows": len(rets),
    }


def grade(forward: Sequence[float], backtest: Sequence[float]) -> dict[str, Any]:
    """앞으로의 자산 경로를 같은 길이 백테스트 분포와 비교 (순수)."""
    length = len(forward) - 1
    if length < 1:
        return {"grade": "NA", "reason": "기록 1 일 이하"}
    st = window_stats(backtest, length)
    if st is None:
        return {"grade": "NA", "reason": "백테스트보다 김"}
    ret = forward[-1] / forward[0] - 1
    peak, mdd = forward[0], 0.0
    for x in forward:
        peak = max(peak, x)
        mdd = max(mdd, 1 - x / peak)
    if ret < st["ret_p01"] or mdd > st["mdd_p99"]:
        g = "ALERT"
    elif ret < st["ret_p05"] or mdd > st["mdd_p95"]:
        g = "REVIEW"
    else:
        g = "OK"
    return {"grade": g, "days": length, "ret": ret, "mdd": mdd, **st}


def backtests() -> dict[str, list[float]]:
    from scripts.run_o_overlay import FROM_MS, INITIAL, TO_MS, load
    from scripts.run_paper_o1 import LEDGERS

    from auto_trading.backtest.overlay import T2_ALONE, simulate_overlay

    spot, perp, funding = load()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    cfgs = {"t2_long": T2_ALONE, **{led.strategy_id: led.cfg for led in LEDGERS.values()}}
    return {
        sid: list(simulate_overlay(spot, perp, funding, cfg=c, **kw).equity)
        for sid, c in cfgs.items()
    }


def forward_paths() -> dict[str, list[float]]:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    out: dict[str, list[float]] = {}
    for table in ("paper_days", "paper_overlay_days"):
        for sid, eq in con.execute(
            f"select strategy_id, equity from {table} order by strategy_id, decision_ms"
        ):
            out.setdefault(sid, []).append(float(eq))
    con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("bands", "check"))
    parser.add_argument(
        "--out", type=Path, default=REPO / "reports" / "forward_band" / "latest.json"
    )
    args = parser.parse_args(argv)
    bt = backtests()
    if args.mode == "bands":
        result: dict[str, Any] = {
            sid: {str(h): window_stats(eq, h) for h in HORIZONS} for sid, eq in bt.items()
        }
    else:
        fw = forward_paths()
        result = {sid: grade(fw[sid], bt[sid]) for sid in bt if sid in fw}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
