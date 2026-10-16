"""문서 249 — 💹 −3% 손절은 그대로, 한 종목 비중(5 · 10 · 15 · 20 · 25%)만 바꾸면? (설명용 계산)

사용 예::

    python -m scripts.run_size_249

242 의 판(113 미국 · 141 국장 주도주 진입, 추매 없음, 최대 10 종목, 진입가 −3% 손절만)을 그대로 쓰고
한 종목 비중만 바꾼다. 비중 w = 한 번에 잃는 돈 3w (+ 갭 · 비용). 판정이 아니라 사용자 결정용 표.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from scripts.run_lt_leaders import FROM

from scripts import run_exit_242 as base

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "size_249"
WEIGHTS = (0.05, 0.10, 0.15, 0.20, 0.25)
S3 = base.ARMS[0]


def one(mk: base.Market, w: float) -> dict[str, Any]:
    old = (base.RISK, base.CAP)
    base.RISK, base.CAP = w * S3.fixed, w  # type: ignore[operator]
    try:
        run = base.simulate_exit(mk, S3)
    finally:
        base.RISK, base.CAP = old
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    span = mk.days[start:]
    row = base._row(span, run.equity)
    stops = [x for x in run.trades if x["reason"] == "stop"]
    hit = [x["ret"] * x["w"] for x in stops]  # 손절 한 번에 계좌가 줄어든 비율
    years = (len(run.equity) - 1) / 252
    worst_year = 0.0
    by_year: dict[str, float] = {}
    for x in stops:
        y = mk.days[x["exit_i"]][:4]
        by_year[y] = by_year.get(y, 0.0) + x["ret"] * x["w"]
    if by_year:
        worst_year = min(by_year.values())
    return {
        **row,
        "stops_per_year": round(len(stops) / years, 1),
        "hit_avg": round(statistics.fmean(hit), 4) if hit else 0.0,
        "hit_worst": round(min(hit), 4) if hit else 0.0,
        "stop_loss_worst_year": round(worst_year, 4),
        "trades": len(run.trades),
    }


def main() -> int:
    out: dict[str, Any] = {}
    for name, (mk, _eq) in (("kr", base.kr_market()), ("us", base.us_market())):
        out[name] = {f"{w:.0%}": one(mk, w) for w in WEIGHTS}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for name, rows in out.items():
        print("==", name)
        for w, r in rows.items():
            f = r["full"]
            print(
                f"{w:>4} 연 {f['cagr']:+.1%} 샤프 {f['sharpe']:.2f} 낙폭 {f['mdd']:.0%}"
                f" | 앞 {r['first']['sharpe']:.2f} 뒤 {r['second']['sharpe']:.2f}"
                f" | 손절 {r['stops_per_year']}/년 · 한 번 평균 {r['hit_avg']:.2%}"
                f" · 최악 {r['hit_worst']:.2%} · 최악 해 손절 합 {r['stop_loss_worst_year']:.1%}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
