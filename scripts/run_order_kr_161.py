"""161 — 국장 일반 개별주: 최근 3 거래일 상태의 '순서' 가 다음 5 거래일 순위를 더 맞히나.

IDEAS 25 주식판. 사용 예::

    python -m scripts.run_order_kr_161 pilot     # 검정력 시험: S 와 P 만, O 는 만들지 않음
    python -m scripts.run_order_kr_161 verdict   # 1 회 판정: O − S (사전 등록 확정 뒤)

* 정보 연구다. 거래 성과는 계산하지 않는다(국장 왕복 비용 0.5% 이상, 감사 §6).
* 판단일: 2015-07-15 이후 거래일 5 일 간격(라벨이 겹치지 않게). 그날 종가까지 자료만 입력.
* 종목군: 유가 + 코스닥(시장을 옮긴 종목은 유가 파일), 판단일 20 일 평균 거래대금 ≥ 10 억,
  최근 23 거래일 수정 종가가 끊김 없음, 최근 3 거래일 KRX 원시 고가 · 저가 · 종가와
  거래대금 > 0, 최근 23 거래일에 G-KR1 위반 없음.
* 라벨: 판단일 종가 → 5 거래일 뒤 종가 수익(그 사이 폐지면 마지막 종가, 거래 정지면 직전 종가)의
  그날 횡단면 순위를 [−0.5, 0.5] 로. 라벨 구간에 G-KR1 위반이 있으면 뺀다(수를 따로 셈).
* 입력은 모두 그날 횡단면 순위([−0.5, 0.5]):
  - 기준선 B (3): 20 거래일 수익 · 20 거래일 일간 수익 표준편차 · log 20 일 평균 거래대금
  - 구간 (3 × 3): 하루마다 수익(전날 종가 대비) · 범위(고가 − 저가)/종가 ·
    거래대금 / 직전 20 일(t−22 ~ t−3) 평균
  - 상호작용 (2): 1 구간 수익 × 2 구간 거래대금 · 2 구간 수익 × 3 구간 거래대금
    (위치 기준, 순위값 곱)
* 모델(같은 Ridge, α = 1, 절편 포함):
  - O = B + 구간을 실제 순서(t−2, t−1, t) 로 + 상호작용
  - S = B + 구간을 수익 오름차순으로 정렬(구간의 세 값은 함께 이동) + 같은 위치 규칙의 상호작용
  - P = S 와 같되 구간을 종목마다 무작위 순서로(시드 고정) — 순서 정보가 없는 두 번째
    모델, 검정력 시험용
* 학습: 분기마다(1 · 4 · 7 · 10 월 첫 판단일) 직전 12 개월 판단일로 재학습, 라벨이 시험
  시작 전에 끝나는 날만(5 거래일 purge). 첫 시험 분기 2016-07.
* 주 평가량: 판단일 w 마다 D_w = 평균_i[(y − ŷ_S)² − (y − ŷ_O)²]. 판단일이 표본 하나
  (종목 사이 상관을 그대로 담는다), Newey–West(시차 2) SE.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import random
import statistics
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.count_kr_patterns import DIRS, _calendar, _load, violations  # noqa: E402

KRX = REPO_ROOT / "data/research/krx"
OUT = REPO_ROOT / "reports/order_kr_161"
START, END = "20150715", "20260831"
STEP, HORIZON, LOOK, BASE = 5, 5, 20, 20
MIN_VALUE = 1e9
RIDGE = 1.0
TRAIN_MONTHS = 12
FIRST_TEST = "201607"
SEED = 20261008
NW_LAG = 2
Z = 2.0 + 0.8416
RANK_VAR = 1 / 12  # [−0.5, 0.5] 균등 순위의 분산 — ΔR² = ΔMSE / RANK_VAR
EXPECTED_DATA_SHA = "04acc78a3ae3bbbe7dc89b9bd9d189199eaef72f4a73da3d0e7bcdda413540a8"

Row = tuple[str, list[float], list[list[float]], float]
"""(종목, 기준선 3, 구간 3 × [수익 · 범위 · 거래대금], 라벨 원 수익) — 순위 전."""


def rank_center(xs: Sequence[float]) -> list[float]:
    """동률 평균 순위를 [−0.5, 0.5] 로 (순수)."""
    n = len(xs)
    if n < 2:
        return [0.0] * n
    order = sorted(range(n), key=lambda i: xs[i])
    out = [0.0] * n
    k = 0
    while k < n:
        j = k
        while j + 1 < n and xs[order[j + 1]] == xs[order[k]]:
            j += 1
        r = (k + j) / 2 / (n - 1) - 0.5
        for m in range(k, j + 1):
            out[order[m]] = r
        k = j + 1
    return out


def design(
    base: Sequence[float], segs: Sequence[Sequence[float]], order: Sequence[int]
) -> list[float]:
    """입력 14 개: 기준선 3 + 구간 9(order 순서) + 상호작용 2 (순수). segs 는 이미 순위값."""
    s = [segs[k] for k in order]
    return [*base, *s[0], *s[1], *s[2], s[0][0] * s[1][2], s[1][0] * s[2][2]]


def sorted_order(raw_returns: Sequence[float]) -> list[int]:
    """원 수익 오름차순(동률은 시간 순) — S 의 구간 순서 (순수)."""
    return sorted(range(3), key=lambda k: (raw_returns[k], k))


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """가우스 소거 (순수, 작은 대칭 양정치 행렬)."""
    n = len(b)
    m = [[*row, b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c]
        for r in range(n):
            if r != c and m[r][c] != 0.0:
                f = m[r][c] / piv
                m[r] = [x - f * y for x, y in zip(m[r], m[c], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def xtx(
    rows: Sequence[Sequence[float]], ys: Sequence[float]
) -> tuple[list[list[float]], list[float]]:
    """절편을 붙인 X'X · X'y (순수)."""
    k = len(rows[0]) + 1
    a = [[0.0] * k for _ in range(k)]
    b = [0.0] * k
    for x, y in zip(rows, ys, strict=True):
        v = [1.0, *x]
        for i in range(k):
            vi = v[i]
            b[i] += vi * y
            ai = a[i]
            for j in range(i, k):
                ai[j] += vi * v[j]
    for i in range(k):
        for j in range(i):
            a[i][j] = a[j][i]
    return a, b


def ridge(a: list[list[float]], b: list[float], alpha: float = RIDGE) -> list[float]:
    """절편에는 벌점 없음."""
    m = [row[:] for row in a]
    for i in range(1, len(m)):
        m[i][i] += alpha
    return solve(m, b)


def predict(w: Sequence[float], x: Sequence[float]) -> float:
    return w[0] + sum(wi * xi for wi, xi in zip(w[1:], x, strict=True))


def nw_mean_se(xs: Sequence[float], lag: int = NW_LAG) -> tuple[float, float]:
    n = len(xs)
    m = statistics.fmean(xs)
    d = [x - m for x in xs]
    var = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * sum(d[i] * d[i - k] for i in range(k, n)) / n
    return m, math.sqrt(max(var, 0.0) / n)


# ---------------------------------------------------------------- 자료


def _krx_ranges(days: Sequence[str]) -> dict[str, dict[str, float]]:
    """{종목: {날짜: (고가 − 저가)/종가}} — KRX 원시(수정 전) 같은 날 비율."""
    out: dict[str, dict[str, float]] = {}
    for d in days:
        for mkt in ("stk", "ksq"):
            f = KRX / mkt / f"{d}.csv.gz"
            if not f.exists():
                continue
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    try:
                        hi, lo, c = (float(r[k]) for k in ("TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC"))
                    except ValueError:
                        continue
                    if c > 0 and hi >= lo > 0:
                        out.setdefault(r["ISU_CD"], {})[d] = (hi - lo) / c
    return out


def data_sha() -> tuple[str, int]:
    files = [f for d in DIRS.values() for f in sorted(d.glob("*.csv"))]
    files += [
        f
        for m in ("stk", "ksq")
        for f in sorted((KRX / m).glob("*.csv.gz"))
        if f.name[:8] >= "20150501"
    ]
    h = hashlib.sha256()
    for f in files:
        h.update(f.relative_to(REPO_ROOT).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest(), len(files)


def build_panel() -> tuple[list[str], list[list[Row]], dict[str, int]]:
    """판단일마다 (원값) 행 목록. 자료 품질로 뺀 수를 함께."""
    cal = _calendar()
    stocks: dict[str, tuple[list[float | None], list[float]]] = {}
    for d in DIRS.values():
        for f in sorted(d.glob("*.csv")):
            if f.stem in stocks:
                continue
            px, val = _load(f)
            stocks[f.stem] = ([px.get(x) for x in cal], [val.get(x, 0.0) for x in cal])
    ranges = _krx_ranges([d for d in cal if d >= "20150501"])
    grid = [i for i in range(len(cal)) if cal[i] >= START][::STEP]
    grid = [t for t in grid if cal[t] <= END and t + HORIZON < len(cal)]
    panel: list[list[Row]] = [[] for _ in grid]
    drop = {"label_violation": 0}
    for code, (p, v) in stocks.items():
        bad = violations(cal, p)
        last = max((i for i, x in enumerate(p) if x is not None), default=-1)
        rg = ranges.get(code, {})
        for g, t in enumerate(grid):
            lo = t - (LOOK + 2)
            if lo < 1 or t > last:
                continue
            win = p[lo : t + 1]
            if any(x is None for x in win) or any(bad[lo + 1 : t + 1]):
                continue
            if sum(v[t - BASE + 1 : t + 1]) / BASE < MIN_VALUE:
                continue
            seg_days = (t - 2, t - 1, t)
            if any(v[k] <= 0 or cal[k] not in rg for k in seg_days):
                continue
            base_val = sum(v[t - 22 : t - 2]) / 20
            if base_val <= 0:
                continue
            closes = [float(x) for x in win if x is not None]
            rets = [b / a - 1 for a, b in pairwise(closes)]
            r20 = closes[-1] / closes[-1 - LOOK] - 1
            vol20 = statistics.pstdev(rets[-LOOK:])
            adv = math.log(sum(v[t - BASE + 1 : t + 1]) / BASE)
            segs = [[p[k] / p[k - 1] - 1, rg[cal[k]], v[k] / base_val] for k in seg_days]  # type: ignore[operator]
            end = min(t + HORIZON, last) if last < len(cal) - 1 else t + HORIZON
            if any(bad[t + 1 : end + 1]):
                drop["label_violation"] += 1
                continue
            j = end
            while p[j] is None:
                j -= 1
            label = float(p[j]) / float(p[t]) - 1  # type: ignore[arg-type]
            panel[g].append((code, [r20, vol20, adv], segs, label))
    return [cal[t] for t in grid], panel, drop


def ranked(
    rows: Sequence[Row],
) -> tuple[list[list[float]], list[list[list[float]]], list[list[float]], list[float]]:
    """그날 횡단면 순위로 바꾼 (기준선, 구간, 구간 원 수익, 라벨)."""
    n = len(rows)
    base = [rank_center([r[1][k] for r in rows]) for k in range(3)]
    seg = [[rank_center([r[2][s][f] for r in rows]) for f in range(3)] for s in range(3)]
    y = rank_center([r[3] for r in rows])
    b_rows = [[base[k][i] for k in range(3)] for i in range(n)]
    s_rows = [[[seg[s][f][i] for f in range(3)] for s in range(3)] for i in range(n)]
    raw = [[r[2][s][0] for s in range(3)] for r in rows]
    return b_rows, s_rows, raw, y


def features(
    rows: Sequence[Row], rng: random.Random
) -> tuple[dict[str, list[list[float]]], list[float]]:
    b, s, raw, y = ranked(rows)
    out: dict[str, list[list[float]]] = {"B": b, "O": [], "S": [], "P": []}
    for i in range(len(rows)):
        out["O"].append(design(b[i], s[i], (0, 1, 2)))
        out["S"].append(design(b[i], s[i], sorted_order(raw[i])))
        perm = [0, 1, 2]
        rng.shuffle(perm)
        out["P"].append(design(b[i], s[i], perm))
    return out, y


def run(models: Sequence[str]) -> dict[str, Any]:
    dates, panel, drop = build_panel()
    rng = random.Random(SEED)
    feats: list[dict[str, list[list[float]]]] = []
    ys: list[list[float]] = []
    mats: dict[str, list[tuple[list[list[float]], list[float]]]] = {m: [] for m in models}
    for rows in panel:
        f, y = features(rows, rng)
        feats.append({m: f[m] for m in models})
        ys.append(y)
        for m in models:
            mats[m].append(xtx(f[m], y))
    quarters = sorted(
        {d[:6] for d in dates if d[:6] >= FIRST_TEST and d[4:6] in ("01", "04", "07", "10")}
    )
    losses: dict[str, list[float]] = {m: [] for m in models}
    test_dates: list[str] = []
    for qi, q in enumerate(quarters):
        nxt = quarters[qi + 1] if qi + 1 < len(quarters) else "999999"
        test = [g for g, d in enumerate(dates) if q <= d[:6] < nxt]
        if not test:
            continue
        start = test[0]
        y0, m0 = int(q[:4]) - 1, int(q[4:])
        train_from = f"{y0:04d}{m0:02d}"
        train = [
            g for g, d in enumerate(dates) if train_from <= d[:6] and g < start - 1
        ]  # purge 1 판단일
        w = {}
        for m in models:
            k = len(mats[m][0][1])
            a = [[sum(mats[m][g][0][i][j] for g in train) for j in range(k)] for i in range(k)]
            b = [sum(mats[m][g][1][i] for g in train) for i in range(k)]
            w[m] = ridge(a, b)
        for g in test:
            test_dates.append(dates[g])
            for m in models:
                sq = [(y - predict(w[m], x)) ** 2 for x, y in zip(feats[g][m], ys[g], strict=True)]
                losses[m].append(statistics.fmean(sq))
    return {
        "dates": test_dates,
        "losses": losses,
        "panel_dates": len(dates),
        "rows_per_date": round(statistics.fmean(len(r) for r in panel), 1),
        "rows_total": sum(len(r) for r in panel),
        "dropped": drop,
    }


def pilot() -> dict[str, Any]:
    """S 와 P(둘 다 순서 정보 없음)의 손실 차이 잡음으로 O − S 최소 검출 효과를 어림.

    평균 차이는 보지 않는다."""
    r = run(("S", "P"))
    d = [a - b for a, b in zip(r["losses"]["P"], r["losses"]["S"], strict=True)]
    _, se = nw_mean_se(d)
    sd = se * math.sqrt(len(d))
    mde = Z * se
    return {
        "test_dates": len(d),
        "first_test": r["dates"][0],
        "last_test": r["dates"][-1],
        "panel_dates": r["panel_dates"],
        "rows_per_date": r["rows_per_date"],
        "rows_total": r["rows_total"],
        "dropped": r["dropped"],
        "sd_weekly_loss_diff_nw": sd,
        "mde_mse": mde,
        "mde_delta_r2_pctpt": round(100 * mde / RANK_VAR, 4),
        "note": "평균 차이는 출력하지 않는다(S 의 성과 노출 최소화). O 는 만들지 않았다.",
    }


def verdict() -> dict[str, Any]:
    """O − S 1 회. T1: 평균 − 2·NW SE > 0 · T2: 두 반쪽 평균 > 0 → HISTORICAL_SUPPORT(정보)."""
    sha, nfiles = data_sha()
    if EXPECTED_DATA_SHA and sha != EXPECTED_DATA_SHA:
        raise SystemExit(f"자료 해시 불일치 {sha}")
    r = run(("B", "S", "O"))
    los, dates = r["losses"], r["dates"]
    d = [s_ - o for s_, o in zip(los["S"], los["O"], strict=True)]
    m, se = nw_mean_se(d)
    half = len(d) // 2
    h1, h2 = statistics.fmean(d[:half]), statistics.fmean(d[half:])
    t1, t2 = m - 2 * se > 0, h1 > 0 and h2 > 0

    def r2(x: float) -> float:
        return round(100 * x / RANK_VAR, 4)

    return {
        "verdict": "HISTORICAL_SUPPORT" if t1 and t2 else "REJECT",
        "T1_lower_2se_gt0": t1,
        "T2_both_halves_gt0": t2,
        "test_dates": len(d),
        "first_test": dates[0],
        "last_test": dates[-1],
        "split_date": dates[half],
        "O_minus_S": {
            "mean_mse": m,
            "se_nw": se,
            "delta_r2_pctpt": r2(m),
            "lower_2se_pctpt": r2(m - 2 * se),
            "halves_pctpt": [r2(h1), r2(h2)],
            "weeks_positive_share": round(sum(1 for x in d if x > 0) / len(d), 3),
        },
        "report_vs_B_pctpt": {
            "S_minus_B": r2(
                statistics.fmean(b - s_ for b, s_ in zip(los["B"], los["S"], strict=True))
            ),
            "O_minus_B": r2(
                statistics.fmean(b - o for b, o in zip(los["B"], los["O"], strict=True))
            ),
        },
        "oos_r2_pctpt_vs_zero_forecast": {
            k: r2(RANK_VAR - statistics.fmean(v)) for k, v in los.items()
        },
        "rows_total": r["rows_total"],
        "rows_per_date": r["rows_per_date"],
        "dropped": r["dropped"],
        "data_sha": sha,
        "data_files": nfiles,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("hash", "pilot", "verdict"))
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.mode == "hash":
        sha, n = data_sha()
        result: dict[str, Any] = {"data_sha": sha, "files": n}
    elif args.mode == "pilot":
        result = pilot()
    else:
        result = verdict()
    (OUT / f"{args.mode}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
