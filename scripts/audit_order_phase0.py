"""IDEAS 25 (차트 상태 변화 순서) Phase 0 감사 — 종목군 · 무조건 5 일 수익 분포 · 검정력. 성과 없음.

사용 예::

    python -m scripts.audit_order_phase0 --out reports/order_phase0/audit.json

* 순서 보존(O) · 순서 제거(S) · 기준선(B) 어느 것도 계산하지 않는다. 종목군의 **무조건** 5 일
  초과 수익(같은 날 종목군 평균을 뺀 값)의 표준편차 · 첨도 · 원 수익 상관 · 동시 종목 수만 잰다.
* 예측 손실 차이의 검정력은 근사식으로 어림한다: O 가 S 보다 증분 R² = r 만큼 낫다면 손실 차이
  d 의 평균 ≈ r·σ², 분산 ≈ 4·r·σ⁴·h (h = 변동성 군집에 따른 부풀림, 첨도/3 로 보수적으로 잡음).
  2SE 판정을 80% 로 통과하려면 r ≥ 4·h·(2 + 0.84)² / N_eff.
* N_eff 는 두 극단으로 보고한다: 하한 = 겹치지 않는 5 일 묶음 수(같은 날 종목들이 완전히 같이
  움직이는 경우), 상한 = 묶음 수 × 평균 동시 종목 수(종목끼리 독립).
* 종목군: (A) 157 그대로(그달 거래대금 상위 20, 폐지 포함) · (B) 같은 규칙으로 상위 100. 둘 다
  ``fetch_alt_universe`` 의 빼는 규칙(스테이블 · 연동 · 레버리지 토큰 · KB 로 본 10 종목)을 따른다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.audit_indicator_phase0 import _alt_daily
from scripts.fetch_alt_universe import OUT, _month_ends, daily_quote_volume, monthly_top

Z = 2.0 + 0.8416
H_DAYS = 5
FROM, TO = "2018-01-01", "2026-08-31"
R_LEVELS = (0.0025, 0.005, 0.01, 0.02)


def min_r(n_eff: float, h: float) -> float:
    """2SE 판정 80% 검정력의 최소 증분 R² (순수 근사)."""
    return 4 * h * Z * Z / n_eff


def needed_n(r: float, h: float) -> float:
    return 4 * h * Z * Z / r


def window_moments(
    series: Mapping[str, Mapping[str, float]],
    days: Sequence[str],
    allow: Callable[[str], set[str]],
    h: int = H_DAYS,
) -> dict[str, Any]:
    """겹치지 않는 h 일 창의 초과 수익 SD · 첨도 · 원 수익 상관 · 동시 종목 수 (순수)."""
    ex: list[float] = []
    means: list[float] = []
    ns: list[int] = []
    for k in range(0, len(days) - h, h):
        a, b = days[k], days[k + h]
        ok = allow(a)
        rets = [px[b] / px[a] - 1 for s, px in series.items() if s in ok and a in px and b in px]
        if len(rets) < 5:
            continue
        m = statistics.fmean(rets)
        ex.extend(r - m for r in rets)
        means.append(m)
        ns.append(len(rets))
    sd = statistics.pstdev(ex)
    kurt = statistics.fmean([(x / sd) ** 4 for x in ex])
    q = sorted(ex)
    lo_q, hi_q = q[int(0.01 * len(q))], q[int(0.99 * len(q))]
    cl = [min(max(x, lo_q), hi_q) for x in ex]
    cm, csd = statistics.fmean(cl), statistics.pstdev(cl)
    kurt_clip = statistics.fmean([((x - cm) / csd) ** 4 for x in cl])
    n_bar = statistics.fmean(ns)
    idio = sd * sd * n_bar / (n_bar - 1)
    common = max(0.0, statistics.pvariance(means) - idio / n_bar)
    return {
        "windows": len(ns),
        "pairs_per_window": round(n_bar, 1),
        "sd_excess": round(sd, 4),
        "kurtosis_excess": round(kurt, 1),
        "kurtosis_clipped_p1_p99": round(kurt_clip, 1),
        "clip_bounds": [round(lo_q, 3), round(hi_q, 3)],
        "rho_raw": round(common / (common + idio), 3),
    }


def power_rows(m: Mapping[str, Any], kurtosis: float) -> dict[str, Any]:
    """첨도로 부풀림 h 를 잡아 두 극단 N_eff 의 최소 증분 R² 와 필요 연수 (순수)."""
    h = max(1.0, kurtosis / 3)
    lo, hi = m["windows"], m["windows"] * m["pairs_per_window"]
    per_year_lo, per_year_hi = 365 / H_DAYS, 365 / H_DAYS * m["pairs_per_window"]
    return {
        "inflation_h": round(h, 2),
        "n_eff_low_dates_only": lo,
        "n_eff_high_independent": round(hi),
        "min_incremental_r2_low": round(min_r(lo, h), 4),
        "min_incremental_r2_high": round(min_r(hi, h), 4),
        "years_needed_low": {f"{r:.2%}": round(needed_n(r, h) / per_year_lo, 1) for r in R_LEVELS},
        "years_needed_high": {f"{r:.2%}": round(needed_n(r, h) / per_year_hi, 1) for r in R_LEVELS},
    }


def _membership(members: Mapping[str, Sequence[str]]) -> Callable[[str], set[str]]:
    by_month: dict[str, set[str]] = {}
    for me, syms in members.items():
        nxt = (date.fromisoformat(me) + timedelta(days=1)).strftime("%Y-%m")
        by_month[nxt] = set(syms)
    return lambda d: by_month.get(d[:7], set())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    days = []
    d = date.fromisoformat(FROM)
    while d.isoformat() <= TO:
        days.append(d.isoformat())
        d += timedelta(days=1)
    top20 = json.loads((OUT / "members.json").read_text(encoding="utf-8"))
    elig = json.loads((OUT / "eligible.json").read_text(encoding="utf-8"))
    vols = {s: v for s in elig if (v := daily_quote_volume(s))}
    top100 = monthly_top(vols, _month_ends(), top=100)
    result: dict[str, Any] = {}
    for name, members in (("top20_157", top20), ("top100", top100)):
        union = sorted({s for v in members.values() for s in v})
        series = {s: _alt_daily(s) for s in union}
        m = window_moments(series, days, _membership(members))
        result[name] = {
            "pairs_ever": len(union),
            **m,
            "label_raw_excess": power_rows(m, m["kurtosis_excess"]),
            "label_clipped_p1_p99": power_rows(m, m["kurtosis_clipped_p1_p99"]),
            "label_cross_sectional_rank": power_rows(m, 1.8),  # 균등 분포 첨도
        }
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
