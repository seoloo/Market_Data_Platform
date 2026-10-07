"""IDEAS 25 를 국장 주식에 옮기면 — 종목군별 무조건 5 일 수익 분포 · 검정력 (모델 · 성과 없음).

사용 예::

    python -m scripts.audit_order_stocks_phase0 --out reports/order_phase0/stocks.json

* 코인판(``audit_order_phase0``)과 같은 진단을 국장 일봉에 한다. 주식은 4 시간봉 과거 자료가 없어
  '최근 72 시간 · 24 시간 세 구간' 은 '최근 3 거래일 · 하루씩' 이 된다(감사 문서 §6).
* 종목군:
  - 일반 개별주: 유가 + 코스닥, 창 시작일 20 일 평균 거래대금 ≥ 10 억(158 과 같음)
  - 주도주: 141 LT-KR 의 그 시점 주도주(유가 상위 200 → 주도 섹터 3 · 종목 10, 매달 말 갱신)
  - 테마 주도주: 그 시점 테마 명부가 없어 과거 시험 불가 — 계산하지 않는다
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.audit_order_phase0 import power_rows, window_moments
from scripts.count_kr_patterns import MIN_VALUE
from scripts.run_lt_kr import build_panel, leaders_pit, members
from scripts.verdict_kr_patterns import Market

FROM, TO = "20100104", "20260831"
PER_YEAR = 252 / 5  # 5 거래일 창이 1 년에 약 50 개


def general(mk: Market) -> tuple[dict[str, dict[str, float]], list[str], Callable[[str], set[str]]]:
    days = [d for d in mk.cal if FROM <= d <= TO]
    pos = {d: i for i, d in enumerate(mk.cal)}
    series = {
        s: {d: x for d, x in zip(mk.cal, p, strict=True) if x is not None} for s, p in mk.p.items()
    }

    def allow(d: str) -> set[str]:
        i = pos[d]
        return {s for s in mk.p if mk.p[s][i] is not None and mk.liquid[s][i] >= MIN_VALUE}

    return series, days, allow


def leaders() -> tuple[dict[str, dict[str, float]], list[str], Callable[[str], set[str]]]:
    panel, _ = build_panel()
    days = [d for d in panel.days if FROM <= d <= TO]
    month_end: dict[str, set[str]] = {}
    for t, d in enumerate(panel.days):
        nxt = panel.days[t + 1] if t + 1 < len(panel.days) else ""
        if d < "20091201" or nxt[:6] == d[:6]:
            continue
        picks = leaders_pit(panel, t, members(panel, t))
        month_end[d[:6]] = {s for s, _ in picks}
    months = sorted(month_end)

    def allow(d: str) -> set[str]:
        prev = [m for m in months if m < d[:6]]
        return month_end[prev[-1]] if prev else set()

    series = {
        s: {d: x for d, x in zip(panel.days, xs, strict=True) if x is not None}
        for s, xs in panel.close.items()
    }
    return series, days, allow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result: dict[str, Any] = {}
    for name, build in (("general_10e8", lambda: general(Market())), ("leaders_lt_kr", leaders)):
        series, days, allow = build()
        m = window_moments(series, days, allow)
        result[name] = {
            **m,
            "label_raw_excess": power_rows(m, m["kurtosis_excess"], PER_YEAR),
            "label_clipped_p1_p99": power_rows(m, m["kurtosis_clipped_p1_p99"], PER_YEAR),
            "label_cross_sectional_rank": power_rows(m, 1.8, PER_YEAR),
        }
    result["theme_leaders"] = "그 시점 테마 명부가 없어 과거 시험 불가 (143 · 147 은 앞으로 기록만)"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
