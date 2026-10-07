"""IDEAS 22 (차트·보조지표 조합) Phase 0 감사 — 자료 · 무조건 수익 분산 · 검정력. 성과 없음.

사용 예::

    python -m scripts.audit_indicator_phase0 --out reports/indicator_phase0/audit.json

* 어떤 지표·패턴 신호도 계산하지 않는다. 종목군의 **무조건** h 일 수익(같은 날 종목군 동일 가중
  평균을 뺀 초과 수익)의 표준편차와 종목 사이 상관만 잰다 — 사건 단위 검정의 필요 표본을 어림하는
  자료 진단이다(초안 §3 "자료 진단 목적 기록"). 진단 구간은 자료 전체이고, 신호를 고르는 데
  쓰지 않는다.
* 국장: KIS 수정 종가(``kr_stocks`` 유가증권 · ``kq_stocks`` 코스닥, 폐지 포함 종목군).
  고가·저가 없음.
* 코인: 157 의 폐지 포함 USDT 종목군(``alt_universe``) 일봉, 그달 종목군에 든 페어만.
* 추세 강도 관문(후보에서 뺌)은 신호 없이, 관문이 끄는 날 비율 f 를 가정해 차이 표준편차를
  σ·√f 로 어림한다(대리).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import statistics
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.audit_dmn_phase0 import mde, required_months

REPO = Path(__file__).resolve().parent.parent
KR_DIRS = {
    "KOSPI": REPO / "data/research/kr_stocks/prices",
    "KOSDAQ": REPO / "data/research/kq_stocks/prices",
}
KR_CAL = REPO / "data/research/kr_stocks/kospi.csv"
ALT = REPO / "data/research/alt_universe"
SPOT = REPO / "data/external/binance_vision/spot"
Z80 = 0.8416
KR_FROM, KR_TO = "20100104", "20260831"
CR_FROM, CR_TO = "2018-01-01", "2026-08-31"
KR_H = (5, 10, 20, 40)
CR_H = (3, 5, 10, 20)
EVENT_EFFECTS = (0.005, 0.01, 0.02)
GATE_EFFECTS = (0.02, 0.04)
GATE_OFF = (0.2, 0.35, 0.5)

Series = Mapping[str, Mapping[str, float]]


def window_stats(
    series: Series,
    calendar: Sequence[str],
    h: int,
    eligible: Callable[[str], set[str] | None] = lambda _d: None,
    daily_limit: float | None = None,
) -> dict[str, Any]:
    """겹치지 않는 h 칸 창마다 종목 수익 → 초과 수익 SD · 평균 상관 (순수).

    창 시작 칸과 끝 칸 모두 종가가 있는 종목만 센다(창 안 폐지는 빠진다 — 보고서에 적는 편향).
    상관은 동일 가중 평균의 분산 = σ²(1 + (n−1)ρ)/n 에서 거꾸로 푼 어림이다.
    ``daily_limit`` 이 있으면 가격 제한폭으로 불가능한 수익(수정 주가 오류)을 빼고 그 수를 센다.
    """
    lo, hi = (
        ((1 - daily_limit) ** h - 1, (1 + daily_limit) ** h - 1)
        if daily_limit is not None
        else (-1.0, math.inf)
    )
    bad = 0
    excess: list[float] = []
    means: list[float] = []
    ns: list[int] = []
    for k in range(0, len(calendar) - h, h):
        a, b = calendar[k], calendar[k + h]
        allow = eligible(a)
        rets = [
            px[b] / px[a] - 1
            for sym, px in series.items()
            if (allow is None or sym in allow) and a in px and b in px and px[a] > 0
        ]
        ok = [r for r in rets if lo <= r <= hi]
        bad += len(rets) - len(ok)
        rets = ok
        if len(rets) < 5:
            continue
        m = statistics.fmean(rets)
        excess.extend(r - m for r in rets)
        means.append(m)
        ns.append(len(rets))
    if not excess:
        return {"h": h, "windows": 0}
    sd_x = statistics.pstdev(excess)
    sd_m = statistics.pstdev(means)
    n_bar = statistics.fmean(ns)
    idio = sd_x**2 * n_bar / (n_bar - 1)
    common = max(0.0, sd_m**2 - idio / n_bar)
    rho = common / (common + idio)
    med = statistics.median(excess)
    mad = statistics.median(abs(x - med) for x in excess)
    return {
        "h": h,
        "windows": len(ns),
        "stocks_per_window": round(n_bar, 1),
        "sd_excess": round(sd_x, 4),
        "sd_excess_robust_mad": round(1.4826 * mad, 4),
        "implausible_dropped": bad,
        "sd_market": round(sd_m, 4),
        "rho_raw_approx": round(rho, 3),
    }


def event_power(sd: float, effects: Sequence[float] = EVENT_EFFECTS) -> dict[str, Any]:
    """사건 하나 = 독립 표본일 때 순효과별 필요 사건 수 (순수). 군집은 보고서에서 따로 다룬다."""
    return {f"{e:.1%}": required_months(sd, e, Z80) for e in effects}


def gate_power(
    sigma_annual: float,
    days_per_year: int,
    off: Sequence[float] = GATE_OFF,
    effects: Sequence[float] = GATE_EFFECTS,
) -> list[dict[str, Any]]:
    """관문이 f 비율의 날을 끌 때 일간 차이 SD ≈ σ_d·√f → 연 효과별 필요 연수 (순수, 대리)."""
    sd_d = sigma_annual / math.sqrt(days_per_year)
    rows = []
    for f in off:
        sd = sd_d * math.sqrt(f)
        rows.append(
            {
                "off_fraction": f,
                "sd_daily_diff": round(sd, 5),
                "years_needed": {
                    f"{int(e * 100)}%/yr": round(
                        required_months(sd, e / days_per_year, Z80) / days_per_year, 1
                    )
                    for e in effects
                },
                "mde_annual_at_8.7y": round(
                    mde(sd, int(8.7 * days_per_year), Z80) * days_per_year, 3
                ),
            }
        )
    return rows


def _kr_series(folder: Path) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for f in sorted(folder.glob("*.csv")):
        px: dict[str, float] = {}
        with f.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                d, c = r["date"], r["close"]
                if KR_FROM <= d <= KR_TO and c:
                    px[d] = float(c)
        if px:
            out[f.stem] = px
    return out


def _kr_calendar() -> list[str]:
    with KR_CAL.open(encoding="utf-8") as fh:
        return [r["date"] for r in csv.DictReader(fh) if KR_FROM <= r["date"] <= KR_TO]


def _alt_daily(sym: str) -> dict[str, float]:
    px: dict[str, float] = {}
    for z in sorted((SPOT / sym / "1d").glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                for r in csv.reader(io.TextIOWrapper(zf.open(name), encoding="utf-8")):
                    t = int(r[0])
                    if t > 10**14:
                        t //= 1000
                    d = datetime.fromtimestamp(t / 1000, UTC).strftime("%Y-%m-%d")
                    if CR_FROM <= d <= CR_TO:
                        px[d] = float(r[4])
    return px


def _alt_membership() -> Callable[[str], set[str] | None]:
    members: dict[str, list[str]] = json.loads((ALT / "members.json").read_text(encoding="utf-8"))
    by_month: dict[str, set[str]] = {}
    for month_end, syms in members.items():
        nxt = (date.fromisoformat(month_end) + timedelta(days=1)).strftime("%Y-%m")
        by_month[nxt] = set(syms)

    def eligible(d: str) -> set[str] | None:
        return by_month.get(d[:7], set())

    return eligible


def inventory() -> dict[str, Any]:
    krx = {
        m: sorted(p.name[:8] for p in (REPO / "data/research/krx" / m).glob("*.csv.gz"))
        for m in ("stk", "ksq")
    }
    union = json.loads((ALT / "union.json").read_text(encoding="utf-8"))
    ended = sum(1 for v in union.values() if v["last_day"] < CR_TO)
    return {
        "kis_adjusted_close": {m: len(list(p.glob("*.csv"))) for m, p in KR_DIRS.items()},
        "kis_fields": "date, close, volume, value (고가·저가·시가 없음)",
        "krx_raw_ohlcv_days": {m: [len(v), v[0], v[-1]] for m, v in krx.items() if v},
        "krx_fields": "시가·고가·저가·종가·거래량·거래대금·시총 (수정 전 가격)",
        "alt_union_pairs": len(union),
        "alt_ended_pairs": ended,
        "alt_4h_files": sum(1 for s in union for _ in (SPOT / s / "4h").glob("*.zip")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cal = _kr_calendar()
    kr: dict[str, Any] = {}
    for m, folder in KR_DIRS.items():
        s = _kr_series(folder)
        kr[m] = [window_stats(s, cal, h, daily_limit=0.30) for h in KR_H]
    union = json.loads((ALT / "union.json").read_text(encoding="utf-8"))
    alt = {sym: _alt_daily(sym) for sym in union}
    days = []
    d = date.fromisoformat(CR_FROM)
    while d.isoformat() <= CR_TO:
        days.append(d.isoformat())
        d += timedelta(days=1)
    elig = _alt_membership()
    cr = [window_stats(alt, days, h, elig) for h in CR_H]
    for rows in (*kr.values(), cr):
        for r in rows:
            if r.get("windows"):
                r["events_needed_80pct"] = event_power(r["sd_excess"])
    result = {
        "inventory": inventory(),
        "kr_unconditional": kr,
        "crypto_unconditional": cr,
        "trend_gate_proxy": {
            "crypto_vol_target_15pct_365d": gate_power(0.15, 365),
            "qqq_vol_target_15pct_252d": gate_power(0.15, 252),
        },
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
