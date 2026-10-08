"""IDEAS 5 누락 편향 점검 (관문 문서 §10). 수익 없음 — 그 월말에 알 수 있는 값만 본다.

사용 예::

    python -m scripts.fundamental_kr_missing run   # → reports/fundamental_kr/missing_bias.json

* C = 계산 가능, M = 계산 못 함(신규 상장 자격 전 · 금융 서식 제외). 구간은 §9 의 셋.
* B1 크기: M 시가총액 중앙값 ÷ C 중앙값 0.5 ~ 2.0.
* B2 큰 회사 쏠림: 시가총액 가중 커버리지(월 평균) ≥ 개수 커버리지(월 평균) − 5%p.
* B3 업종: 종목 · 달의 5% 이상 업종 중 업종 안 커버리지가 전체보다 20%p 넘게 낮은 업종이 없다.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from scripts.fundamental_kr import (
    KRX,
    NEW_LISTING,
    REPO,
    classify,
    liquid_top,
    load_all,
    month_ends,
)
from scripts.run_lt_kr import build_panel

OUT = REPO / "reports" / "fundamental_kr" / "missing_bias.json"
PERIODS = {
    "계획용 2018 ~ 2020": ("2018-01", "2020-12"),
    "TRAIN 2021 ~ 2023": ("2021-01", "2023-12"),
    "역사 확인 2024-01 ~ 2026-08": ("2024-01", "2026-08"),
}
SIZE_LO, SIZE_HI = 0.5, 2.0
CAP_SLACK = 0.05
SECTOR_MIN_SHARE, SECTOR_GAP = 0.05, 0.20


@dataclass(frozen=True, slots=True)
class Obs:
    month: str
    code: str
    ok: bool
    cap: float
    rank: int
    sector: str
    reason: str


def judge_period(obs: Sequence[Obs]) -> dict[str, Any]:
    """한 구간의 B1 · B2 · B3 (순수)."""
    c = [o.cap for o in obs if o.ok and o.cap > 0]
    m = [o.cap for o in obs if not o.ok and o.cap > 0]
    size_ratio = statistics.median(m) / statistics.median(c) if c and m else 1.0
    by_month: dict[str, list[Obs]] = defaultdict(list)
    for o in obs:
        by_month[o.month].append(o)
    cnt, capw = [], []
    for xs in by_month.values():
        cnt.append(sum(o.ok for o in xs) / len(xs))
        tot = sum(o.cap for o in xs)
        capw.append(sum(o.cap for o in xs if o.ok) / tot if tot > 0 else 0.0)
    cov_cnt = statistics.fmean(cnt) if cnt else 0.0
    cov_cap = statistics.fmean(capw) if capw else 0.0
    overall = sum(o.ok for o in obs) / len(obs) if obs else 0.0
    sec_n: Counter[str] = Counter(o.sector for o in obs)
    sec_ok: Counter[str] = Counter(o.sector for o in obs if o.ok)
    sectors = []
    bad = []
    for sec, n in sec_n.most_common():
        share = n / len(obs)
        cov = sec_ok[sec] / n
        row = {"sector": sec, "share": round(share, 3), "coverage": round(cov, 3)}
        if share >= SECTOR_MIN_SHARE:
            sectors.append(row)
            if cov < overall - SECTOR_GAP:
                bad.append(sec)
    b1 = SIZE_LO <= size_ratio <= SIZE_HI
    b2 = cov_cap >= cov_cnt - CAP_SLACK
    b3 = not bad
    return {
        "firm_months": len(obs),
        "missing": sum(not o.ok for o in obs),
        "median_cap_bn_krw": {
            "C": round(statistics.median(c) / 1e9, 0) if c else None,
            "M": round(statistics.median(m) / 1e9, 0) if m else None,
        },
        "B1_size_ratio_M_over_C": round(size_ratio, 3),
        "median_rank": {
            "C": statistics.median([o.rank for o in obs if o.ok]) if c else None,
            "M": statistics.median([o.rank for o in obs if not o.ok]) if m else None,
        },
        "B2_coverage_count": round(cov_cnt, 3),
        "B2_coverage_capweighted": round(cov_cap, 3),
        "overall_coverage": round(overall, 3),
        "B3_sectors_ge_5pct": sectors,
        "B3_bad_sectors": bad,
        "missing_reasons": dict(Counter(o.reason for o in obs if not o.ok).most_common()),
        "B1": b1,
        "B2": b2,
        "B3": b3,
        "low_bias": b1 and b2 and b3,
    }


def _krx(day: str) -> dict[str, tuple[float, str]]:
    """그날 KRX 유가증권 파일 → 종목 → (시가총액, 이름). 없으면 빈 dict."""
    path = KRX / "stk" / f"{day.replace('-', '')}.csv.gz"
    if not path.exists():
        return {}
    rows = csv.DictReader(io.StringIO(gzip.decompress(path.read_bytes()).decode("utf-8")))
    return {r["ISU_CD"]: (float(r["MKTCAP"] or 0), r["ISU_NM"]) for r in rows}


def run() -> dict[str, Any]:
    panel, _ = build_panel()
    hists, financial, _ = load_all(panel)
    obs: list[Obs] = []
    names: dict[str, str] = {}
    no_cap = 0
    for t in month_ends(panel.days):
        day = panel.days[t]
        ok, missing, _ = classify(panel, t, hists, financial)
        krx = _krx(day)
        names.update({c: v[1] for c, v in krx.items()})
        ranked = [s for s in liquid_top(panel, t) if s in ok or s in missing]
        for r, s in enumerate(ranked, 1):
            if missing.get(s) == NEW_LISTING:
                continue
            cap = krx.get(s, (0.0, ""))[0]
            no_cap += cap <= 0
            obs.append(
                Obs(day[:7], s, s in ok, cap, r, panel.sector.get(s, "?"), missing.get(s, ""))
            )
    res: dict[str, Any] = {"no_cap_rows": no_cap}
    for name, (a, b) in PERIODS.items():
        res[name] = judge_period([o for o in obs if a <= o.month <= b])
    miss = Counter(o.code for o in obs if not o.ok)
    res["longest_missing"] = [
        {
            "code": c,
            "name": names.get(c, ""),
            "months": n,
            "reasons": dict(Counter(o.reason for o in obs if o.code == c and not o.ok)),
            "median_rank": statistics.median(o.rank for o in obs if o.code == c),
        }
        for c, n in miss.most_common(25)
    ]
    res["low_bias_all_periods"] = all(res[p]["low_bias"] for p in PERIODS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.fundamental_kr_missing run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
