"""문서 225 — LT 그 시점 명단 2018 ~ 2026 검정력 관문.

계획 자료 2008-06 ~ 2017-12(113 지금 명단)로 LT − EW 월 수익 차이의 표준편차만 잰다.
평균은 쓰지 않는다.
사용: ``python -m scripts.power_lt_pit_225`` → ``reports/lt_pit_225/power.json``
"""

from __future__ import annotations

import json
import math
import sys
from itertools import pairwise
from pathlib import Path

import scripts.run_lt_leaders as lt

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "lt_pit_225"
PLAN_TO = "2017-12-31"
TEST_MONTHS = 104
Z = 1.96 + 0.84
SLACK = 1.2
GATE = 0.03


def main() -> int:
    panel = lt.build_panel()
    spy = lt.load(lt.SPY)
    sm = dict(zip(spy.dates, spy.close, strict=True))
    sc = [sm[d] for d in panel.days]
    ss = lt._sma(sc, lt.SPY_SMA)
    ok = [m is not None and c > m for c, m in zip(sc, ss, strict=True)]
    start = next(i for i, d in enumerate(panel.days) if d >= lt.FROM)
    span = panel.days[start:]
    run = lt.simulate(panel, ok)
    ew = lt.equal_weight(panel)
    month_end: dict[str, tuple[float, float]] = {}
    for d, a, b in zip(span, run.equity, ew, strict=True):
        if d <= PLAN_TO:
            month_end[d[:7]] = (a, b)
    ms = sorted(month_end)
    diff = [
        (month_end[m2][0] / month_end[m1][0]) - (month_end[m2][1] / month_end[m1][1])
        for m1, m2 in pairwise(ms)
    ]
    n = len(diff)
    mu = sum(diff) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in diff) / (n - 1))
    mde = Z * sd * 12 / math.sqrt(TEST_MONTHS) * SLACK
    res = {
        "plan_months": n,
        "plan_period": [ms[0], ms[-1]],
        "sd_monthly_diff": round(sd, 5),
        "tracking_error_annual": round(sd * math.sqrt(12), 4),
        "test_months": TEST_MONTHS,
        "mde_annual": round(mde, 4),
        "gate": GATE,
        "verdict": "POWER_OK" if mde <= GATE else "UNDERPOWERED",
        "months_needed_for_3pct": math.ceil((Z * sd * 12 * SLACK / GATE) ** 2),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "power.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(res, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
