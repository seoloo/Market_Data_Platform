"""IDEAS 5 한국 실적 개선형 — 계획용 구간(2018 ~ 2020) 분산 · 80% 검정력 (초안 §7, 평균 미열람).

사용::

    python -m scripts.fundamental_kr_power plan   # 계획용 구간: 월 차이 SD · 장기 SD · 필요 월 수

* 초안(FUNDAMENTAL_KR_PREREGISTRATION_DRAFT) §4 ~ §7 을 그대로 옮긴다.
  - 월말: 유가증권시장 비금융 거래대금 상위 200 → 계산 가능 집합 E(A · M · F).
  - 후보: g · A · M · F · C 모두 > 0 인 종목을 A · M · F 백분위 평균으로 상위 20, 5% 씩.
  - EW-E: E 를 고르게 100%. EW-match: E 를 고르게, 총노출 = 선정 수 × 5%.
  - 체결: :mod:`research.kr_portfolio` (지정가 일봉 대리 모형, 다음 거래일부터, 배당 · 분할 · 폐지).
  - 비용: 매수 · 매도 각각 max(0.25%, 수수료 0.015% + 매도 세금) — 관문 문서 §4 의 기간별 표.
* **평균 · 누적 곡선 · 종목별 수익은 출력하지 않는다.** 출력은 월 수 · 표준편차 · Newey–West(3) 장기
  표준편차 · 1 차 자기상관 · 필요 월 수 · TRAIN(36 달) · 역사 확인(2024-01 ~ 2026-08 · 32 달)과의
  비교뿐이다. 검출 대상은 월 0.25%p(연 산술 3%p), 동시 관문 z_β = Φ⁻¹(1 − 0.20/3) ≈ 1.501.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from scripts.fundamental_kr import computable, coverage_share, load_all, month_ends  # noqa: E402
from scripts.run_lt_kr import build_panel  # noqa: E402

from auto_trading.research.fundamental_kr import Inputs  # noqa: E402
from auto_trading.research.kr_portfolio import StockDays, simulate  # noqa: E402

KR = REPO / "data" / "research" / "kr_stocks"
OUT = REPO / "reports" / "fundamental_kr_power"
PLAN_END = "2020-12-31"
TOP, WEIGHT = 20, 0.05
CAPITAL = 10_000_000.0
EFFECT = 0.0025
Z_ALPHA, Z_BETA_ONE, Z_BETA_JOINT = 2.0, 0.8416, 1.501
NW_LAG = 3
TRAIN_MONTHS, HISTORY_MONTHS = 36, 32
COVERAGE_SHARE, COVERAGE_MIN = 0.85, 100
"""2026-10-09 사용자 결정 "85로 진행해줘" (관문 문서 §11). 초안 제안값 0.90 → 0.85."""


def sell_tax(day: str) -> float:
    """증권거래세 + 농어촌특별세 (관문 문서 §4). ``day`` = YYYYMMDD."""
    if day < "20190530":
        return 0.0030
    if day <= "20201231":
        return 0.0025
    return {"2021": 0.0023, "2022": 0.0023, "2023": 0.0020, "2024": 0.0018, "2025": 0.0015}.get(
        day[:4], 0.0020
    )


def cost(day: str, side: str) -> float:
    actual = 0.00015 + (sell_tax(day) if side == "sell" else 0.0)
    return max(0.0025, actual)


def pct_rank(values: Mapping[str, float]) -> dict[str, float]:
    """오름차순 평균 순위 백분위 (평균순위 − 1)/(N − 1), 동점은 같은 평균 순위."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[items[k][0]] = (avg - 1) / (n - 1) if n > 1 else 0.0
        i = j + 1
    return out


def select(ok: Mapping[str, Inputs]) -> list[str]:
    """초안 §4: 점수 = A · M · F 백분위 평균(E 전체에서), 자격 = g · A · M · F · C > 0, 상위 20."""
    if not ok:
        return []
    ra = pct_rank({s: x.accel for s, x in ok.items()})
    rm = pct_rank({s: x.margin_chg for s, x in ok.items()})
    rf = pct_rank({s: x.cf_chg for s, x in ok.items()})
    eligible = [
        s
        for s, x in ok.items()
        if x.g > 0 and x.accel > 0 and x.margin_chg > 0 and x.cf_chg > 0 and x.cf_ttm > 0
    ]
    score = {s: (ra[s] + rm[s] + rf[s]) / 3 for s in eligible}
    return sorted(eligible, key=lambda s: (-score[s], s))[:TOP]


def stock_days(
    sym: str, days: Sequence[str], adj: Sequence[float | None], last: int, delisted: bool
) -> StockDays | None:
    """비수정 OHLCV(raw/) · 수정 계수 · 배당을 계좌 날짜 축에 맞춘다. raw 가 없으면 None."""
    path = KR / "raw" / f"{sym}.csv"
    if not path.exists():
        return None
    n = len(days)
    at = {d.replace("-", ""): i for i, d in enumerate(days)}
    o, h, lo, c, v, val = ([0.0] * n for _ in range(6))
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        p = line.split(",")
        i = at.get(p[0])
        if i is None:
            continue
        o[i], h[i], lo[i], c[i], v[i], val[i] = (float(x) for x in p[1:7])
    factor = [0.0] * n
    f = 0.0
    for i in range(n):
        a = adj[i]
        if c[i] > 0 and a:
            f = c[i] / a
        factor[i] = f
    divs: dict[int, tuple[float, int]] = {}
    dpath = KR / "dividends" / f"{sym}.json"
    if dpath.exists():
        keys = sorted(at)
        import bisect

        for ev in json.loads(dpath.read_text(encoding="utf-8")):
            dps = float(ev.get("dps") or 0)
            rec, pay = ev.get("record_date") or "", ev.get("pay_date") or ""
            if dps <= 0 or not rec:
                continue
            k = bisect.bisect_right(keys, rec) - 1  # 기준일까지 마지막 거래일
            if k < 1:
                continue
            right = at[keys[k - 1]]  # 그 전 거래일 종가에 들고 있으면 받는다 (T+2)
            pi = bisect.bisect_left(keys, pay) if pay else len(keys)
            divs[right] = (dps, at[keys[pi]] if pi < len(keys) else n)
    return StockDays(o, h, lo, c, v, val, factor, last, delisted, divs)


def nw_sd(xs: Sequence[float], lag: int = NW_LAG) -> float:
    n = len(xs)
    m = statistics.mean(xs)
    d = [x - m for x in xs]
    g0 = sum(x * x for x in d) / n
    s = g0
    for k in range(1, lag + 1):
        gk = sum(d[i] * d[i - k] for i in range(k, n)) / n
        s += 2 * (1 - k / (lag + 1)) * gk
    return math.sqrt(max(s, 0.0))


def acf1(xs: Sequence[float]) -> float:
    m = statistics.mean(xs)
    d = [x - m for x in xs]
    den = sum(x * x for x in d)
    return sum(d[i] * d[i - 1] for i in range(1, len(d))) / den if den else 0.0


def power_row(xs: Sequence[float]) -> dict[str, Any]:
    sd_lr = nw_sd(xs)
    need_one = ((Z_ALPHA + Z_BETA_ONE) * sd_lr / EFFECT) ** 2
    need_joint = ((Z_ALPHA + Z_BETA_JOINT) * sd_lr / EFFECT) ** 2
    return {
        "months": len(xs),
        "sd": round(statistics.pstdev(xs), 5),
        "sd_lr_nw3": round(sd_lr, 5),
        "acf1": round(acf1(xs), 3),
        "need_months_80": math.ceil(need_one),
        "need_months_joint": math.ceil(need_joint),
        "mde_train_joint_annual": round(
            (Z_ALPHA + Z_BETA_JOINT) * sd_lr / math.sqrt(TRAIN_MONTHS) * 12, 4
        ),
        "mde_history_joint_annual": round(
            (Z_ALPHA + Z_BETA_JOINT) * sd_lr / math.sqrt(HISTORY_MONTHS) * 12, 4
        ),
    }


def plan() -> dict[str, Any]:
    panel, _ = build_panel()
    hists, financial, _ = load_all(panel)
    ends = [t for t in month_ends(panel.days) if panel.days[t] <= PLAN_END]
    targets_c: dict[int, dict[str, float]] = {}
    targets_e: dict[int, dict[str, float]] = {}
    targets_m: dict[int, dict[str, float]] = {}
    started = None
    cov_rows = []
    for t in ends:
        ok, why, n, _ = computable(panel, t, hists, financial)
        share = coverage_share(len(ok), n, why)
        cov_rows.append((panel.days[t], round(share, 3), len(ok)))
        if started is None:
            if share < COVERAGE_SHARE or len(ok) < COVERAGE_MIN:
                continue
            started = t
        picks = select(ok)
        targets_c[t] = dict.fromkeys(picks, WEIGHT)
        if ok:
            targets_e[t] = {s: 1.0 / len(ok) for s in ok}
            targets_m[t] = {s: len(picks) * WEIGHT / len(ok) for s in ok}
    if started is None:
        return {
            "status": "DATA_NOT_READY",
            "reason": "계획용 구간에 커버리지 관문을 넘는 달이 없다",
            "coverage": cov_rows,
        }
    syms = {s for d in (targets_c, targets_e) for tg in d.values() for s in tg}
    stocks = {}
    missing = []
    for s in sorted(syms):
        sd = stock_days(s, panel.days, panel.close[s], panel.last[s], s in panel.delisted)
        if sd is None:
            missing.append(s)
        else:
            stocks[s] = sd
    for d in (targets_c, targets_e, targets_m):
        for t in d:
            d[t] = {s: w for s, w in d[t].items() if s in stocks}
    days8 = [d.replace("-", "") for d in panel.days]
    start = started + 1
    end = max(i for i, d in enumerate(panel.days) if d <= PLAN_END)
    eq = {
        k: simulate(days8, stocks, tg, cost, CAPITAL, start=start, end=end).equity
        for k, tg in (("cand", targets_c), ("ew_e", targets_e), ("ew_match", targets_m))
    }
    month_idx = [t - start for t in ends if start <= t <= end] + [end - start]
    month_idx = sorted(set(month_idx))

    def monthly(series: Sequence[float]) -> list[float]:
        pts = [CAPITAL] + [series[i] for i in month_idx]
        return [b / a - 1 for a, b in pairwise(pts)]

    rc, re_, rm = monthly(eq["cand"]), monthly(eq["ew_e"]), monthly(eq["ew_match"])
    d_main = [a - b for a, b in zip(rc, re_, strict=True)]
    d_match = [a - b for a, b in zip(rc, rm, strict=True)]
    out: dict[str, Any] = {
        "status": "MEASURED",
        "plan_period": [panel.days[start], panel.days[end]],
        "first_signal": panel.days[started],
        "missing_raw": missing,
        "K1_net_return": power_row(rc),
        "K2_vs_ew_e": power_row(d_main),
        "K3_vs_ew_match": power_row(d_match),
        "note": "평균 · 누적 곡선 · 종목별 수익은 계산해도 내보내지 않는다 (초안 §7)",
    }
    rows = [out["K1_net_return"], out["K2_vs_ew_e"], out["K3_vs_ew_match"]]
    worst = max(r["need_months_joint"] for r in rows)
    out["joint_need_months_max"] = worst
    out["train_ok"] = worst <= TRAIN_MONTHS
    out["history_ok"] = worst <= HISTORY_MONTHS
    out["power_verdict"] = "OK" if out["train_ok"] and out["history_ok"] else "DATA_INSUFFICIENT"
    return out


def main(argv: list[str]) -> int:
    if argv != ["plan"]:
        raise SystemExit(__doc__)
    target = OUT / "plan.json"
    if target.exists():
        raise SystemExit(f"이미 측정했다: {target}")
    out = plan()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    _ = date
    raise SystemExit(main(sys.argv[1:]))
