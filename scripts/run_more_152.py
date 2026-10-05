"""문서 152 — 아직 시험하지 않은 '몇 달 단위' 수익 방법 넷. 각각 1회 판정.

사용 예::

    python -m scripts.run_more_152 audit   # 자료 해시·개수 (수익 없음)
    python -m scripts.run_more_152 run     # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음

* **HFEA** 3 배 S&P 500 55% + 3 배 미국 장기채(TLT) 45%, 분기마다 비중 맞춤 (Reddit·Bogleheads).
* **KR-TREND2** KOSPI 가 200 일선 위면 2 배 KOSPI, 아래면 현금 (QS·T 추세 규칙의 국장판).
* **KR-FLOWTIME** 시장 전체 외국인 + 기관 20 일 순매수가 + 면 KOSPI, − 면 현금.
* **KR-LOWVOL** 매달 그 시점 거래대금 상위 200 중 1 년 변동성 낮은 20 종목을 똑같이.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_community_150 import KR, TRADFI, equity_stats, load_close
from scripts.run_kr_flow import FLOWS, load_flows
from scripts.run_lt_kr import KrPanel, build_panel, equal_weight_pit, members
from scripts.run_lt_leaders import stats

REPO = Path(__file__).resolve().parent.parent
TLT = TRADFI / "hfea" / "TLT_tiingo.csv"
OUTPUT = REPO / "reports" / "more_152"
EXPECTED_DATA: str | None = "b64c51aa7eecc43ea6105e30b6e5231dd58b200ccea2f59e83bade7ede21d64f"
"""자료 묶음 해시 — audit 뒤 문서 152 에 적고 여기에 고정한다."""

FROM, SPLIT, TO = "2008-06-01", "2017-01-01", "2026-08-31"
US_FEE, US_FEE_REF = 0.0025, 0.0010
ETF_FEE, ETF_FEE_REF = 0.0010, 0.0003
LEV_FEE_US = 0.0091
"""UPRO·TMF 보수 (연)."""
LEV_FEE_KR = 0.0064
"""KODEX 레버리지 보수 (연)."""
KR_FUNDING = 0.02
"""2 배의 빌린 1 배 몫 금리 (연, 가정 — 국내 단기 금리 자료가 없다)."""
W_SPY = 0.55
INDEX_SMA = 200
FLOW_DAYS = 20
LOWVOL_N, VOL_DAYS, VOL_MIN_OBS, MIN_PRICE = 20, 252, 200, 1000.0


def lagged_equity(rets: Sequence[float], on: Sequence[bool], fee: float) -> list[float]:
    """``on[t]`` 은 t 종가에 정하고 t+1 종가에 실행. ``rets[t]`` = t−1 → t 종가 수익(보유 때).
    현금 수익 0."""
    eq = [1.0]
    pos = False
    for t in range(1, len(rets)):
        v = eq[-1]
        if pos:
            v *= 1 + rets[t]
        if on[t - 1] != pos:
            v *= 1 - fee
            pos = on[t - 1]
        eq.append(v)
    return eq


def lev(r: float, k: float, funding_year: float, fee_year: float) -> float:
    """k 배 하루 수익: k·r − (k−1)·자금 금리 − 보수."""
    return k * r - (k - 1) * funding_year / 252 - fee_year / 252


def sma(xs: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    acc = 0.0
    for i, x in enumerate(xs):
        acc += x
        if i >= n:
            acc -= xs[i - n]
        out.append(acc / n if i >= n - 1 else None)
    return out


def _seg_stats(days: Sequence[str], eq: Sequence[float], lo: str, hi: str) -> dict[str, float]:
    idx = [i for i, d in enumerate(days) if lo <= d <= hi]
    return equity_stats([eq[i] for i in idx])


def _three(days: Sequence[str], eq: Sequence[float]) -> list[dict[str, float]]:
    """전 기간 · 앞 반쪽 · 뒤 반쪽."""
    return [
        _seg_stats(days, eq, FROM, TO),
        _seg_stats(days, eq, FROM, "2016-12-31"),
        _seg_stats(days, eq, SPLIT, TO),
    ]


def _beats(s: Sequence[dict[str, float]], b: Sequence[dict[str, float]], key: str) -> list[bool]:
    return [x[key] > y[key] for x, y in zip(s, b, strict=True)]


# ---------------------------------------------------------------- HFEA


def hfea_equity(
    spy: Sequence[float],
    tlt: Sequence[float],
    cash: Sequence[float],
    days: Sequence[str],
    fee: float,
) -> list[float]:
    """3 배 SPY · 3 배 TLT (자금 금리 = DTB3) 55/45. 분기 첫 거래일 종가에 비중 맞춤."""
    a, b = W_SPY * (1 - fee), (1 - W_SPY) * (1 - fee)
    eq = [a + b]
    for t in range(1, len(days)):
        a *= 1 + lev(spy[t] / spy[t - 1] - 1, 3, cash[t] * 252, LEV_FEE_US)
        b *= 1 + lev(tlt[t] / tlt[t - 1] - 1, 3, cash[t] * 252, LEV_FEE_US)
        a, b = max(a, 0.0), max(b, 0.0)
        v = a + b
        if days[t][:7] != days[t - 1][:7] and days[t][5:7] in ("01", "04", "07", "10"):
            moved = abs(a - W_SPY * v) + abs(b - (1 - W_SPY) * v)
            v -= moved * fee
            a, b = W_SPY * v, (1 - W_SPY) * v
        eq.append(a + b)
    return eq


def us_aligned() -> tuple[list[str], list[float], list[float], list[float], list[float]]:
    """(날짜, SPY, TLT, QQQ, 현금 하루 수익) — SPY 거래일, FROM ~ TO."""
    sd, sc = load_close(TRADFI / "SPY_adj.csv")
    td, tc = load_close(TLT)
    qd, qc = load_close(TRADFI / "QQQ_adj.csv")
    rd, rc = load_close(TRADFI / "DTB3.csv")
    tm, qm = dict(zip(td, tc, strict=True)), dict(zip(qd, qc, strict=True))
    rm = dict(zip(rd, rc, strict=True))
    days = [d for d in sd if FROM <= d <= TO and d in tm and d in qm]
    smap = dict(zip(sd, sc, strict=True))
    cash: list[float] = []
    last = 0.0
    for d in days:
        last = rm.get(d, last)
        cash.append(last / 100 / 252)
    return days, [smap[d] for d in days], [tm[d] for d in days], [qm[d] for d in days], cash


def run_hfea() -> dict[str, object]:
    days, spy, tlt, qqq, cash = us_aligned()
    eq = hfea_equity(spy, tlt, cash, days, US_FEE)
    s = _three(days, eq)
    b = _three(days, [x / spy[0] for x in spy])
    q = _three(days, [x / qqq[0] for x in qqq])
    checks = {
        "a_cagr_gt_spy_full": s[0]["cagr"] > b[0]["cagr"],
        "b_sharpe_gt_spy_all_three": all(_beats(s, b, "sharpe")),
    }
    by_year: dict[str, float] = {}
    for y in sorted({d[:4] for d in days}):
        idx = [i for i, d in enumerate(days) if d[:4] == y]
        by_year[y] = round(eq[idx[-1]] / eq[max(idx[0] - 1, 0)] - 1, 4)
    return {
        "verdict": "PASS" if all(checks.values()) else "REJECT",
        "checks": checks,
        "hfea": s,
        "spy": b,
        "qqq": q,
        "post_2019_02": {
            "hfea": _seg_stats(days, eq, "2019-02-01", TO),
            "spy": _seg_stats(days, spy, "2019-02-01", TO),
        },
        "ref_cost": _seg_stats(days, hfea_equity(spy, tlt, cash, days, US_FEE_REF), FROM, TO),
        "by_year": by_year,
    }


# ---------------------------------------------------------------- KOSPI 타이밍


def kospi_frame() -> tuple[list[str], list[float]]:
    d, c = load_close(KR / "kospi.csv")
    return d, c


def run_trend2() -> dict[str, object]:
    d, c = kospi_frame()
    m = sma(c, INDEX_SMA)
    on_all = [mm is not None and x > mm for x, mm in zip(c, m, strict=True)]
    i0 = next(i for i, x in enumerate(d) if x >= FROM)
    i1 = max(i for i, x in enumerate(d) if x <= TO)
    days, closes = d[i0 : i1 + 1], c[i0 : i1 + 1]
    on = on_all[i0 : i1 + 1]
    r1 = [0.0] + [b / a - 1 for a, b in pairwise(closes)]
    r2 = [0.0] + [lev(r, 2, KR_FUNDING, LEV_FEE_KR) for r in r1[1:]]
    eq2 = lagged_equity(r2, on, ETF_FEE)
    eq1 = lagged_equity(r1, on, ETF_FEE)
    s = _three(days, eq2)
    b = _three(days, [x / closes[0] for x in closes])
    checks = {
        "a_cagr_gt_kospi_full": s[0]["cagr"] > b[0]["cagr"],
        "b_sharpe_gt_kospi_all_three": all(_beats(s, b, "sharpe")),
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "REJECT",
        "checks": checks,
        "trend_2x": s,
        "trend_1x": _three(days, eq1),
        "kospi": b,
        "buy_hold_2x": _three(days, _hold(r2)),
        "ref_cost": _seg_stats(days, lagged_equity(r2, on, ETF_FEE_REF), FROM, TO),
        "switches_per_year": round(sum(x != y for x, y in pairwise(on)) / (len(on) / 252), 1),
        "exposure": round(sum(on) / len(on), 3),
    }


def _hold(rets: Sequence[float]) -> list[float]:
    eq = [1.0]
    for r in rets[1:]:
        eq.append(eq[-1] * (1 + r))
    return eq


def market_flow(panel: KrPanel) -> list[float]:
    """날마다 모든 종목의 (외국인 + 기관) 순매수 합 (원)."""
    f = load_flows(panel)
    total = [0.0] * len(panel.days)
    for xs in f.net.values():
        for i, x in enumerate(xs):
            total[i] += x
    return total


def run_flowtime(panel: KrPanel) -> dict[str, object]:
    flow = market_flow(panel)
    d, c = kospi_frame()
    kmap = dict(zip(d, c, strict=True))
    closes_all = [kmap[x] for x in panel.days]
    roll: list[float] = []
    acc = 0.0
    for i, x in enumerate(flow):
        acc += x
        if i >= FLOW_DAYS:
            acc -= flow[i - FLOW_DAYS]
        roll.append(acc)
    on_all = [i >= FLOW_DAYS - 1 and r > 0 for i, r in enumerate(roll)]
    i0 = next(i for i, x in enumerate(panel.days) if x >= FROM)
    i1 = max(i for i, x in enumerate(panel.days) if x <= TO)
    days, closes, on = panel.days[i0 : i1 + 1], closes_all[i0 : i1 + 1], on_all[i0 : i1 + 1]
    r1 = [0.0] + [b / a - 1 for a, b in pairwise(closes)]
    eq = lagged_equity(r1, on, ETF_FEE)
    s = _three(days, eq)
    b = _three(days, [x / closes[0] for x in closes])
    checks = {
        "a_sharpe_gt_kospi_all_three": all(_beats(s, b, "sharpe")),
        "b_mdd_lt_kospi": s[0]["mdd"] < b[0]["mdd"],
    }
    zero_days = sum(1 for x in flow[i0 : i1 + 1] if x == 0)
    return {
        "verdict": "PASS" if all(checks.values()) else "REJECT",
        "checks": checks,
        "flow_timing": s,
        "kospi": b,
        "ref_cost": _seg_stats(days, lagged_equity(r1, on, ETF_FEE_REF), FROM, TO),
        "switches_per_year": round(sum(x != y for x, y in pairwise(on)) / (len(on) / 252), 1),
        "exposure": round(sum(on) / len(on), 3),
        "days_without_flow": zero_days,
    }


# ---------------------------------------------------------------- KR-LOWVOL


def volatility(xs: Sequence[float | None], t: int) -> float | None:
    """t 까지 252 거래일 안에서 이틀 연속 거래한 날의 일 수익 표준편차 (200 개 이상일 때)."""
    lo = max(1, t - VOL_DAYS + 1)
    rets = [
        b / a - 1
        for a, b in ((xs[i - 1], xs[i]) for i in range(lo, t + 1))
        if a is not None and b is not None and a > 0
    ]
    return statistics.stdev(rets) if len(rets) >= VOL_MIN_OBS else None


def lowvol_pick(panel: KrPanel, t: int, pool: Sequence[str]) -> list[str]:
    scored: list[tuple[float, str]] = []
    for s in pool:
        c = panel.close[s][t]
        if c is None or c < MIN_PRICE:
            continue
        v = volatility(panel.close[s], t)
        if v is not None:
            scored.append((v, s))
    scored.sort()
    return [s for _, s in scored[:LOWVOL_N]]


def month_ends(panel: KrPanel) -> list[int]:
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    return [t for t in range(start - 1, len(days) - 1) if days[t + 1][:7] != days[t][:7]]


def run_lowvol(panel: KrPanel) -> dict[str, object]:
    ew_log: dict[str, list[str]] = {}
    lv_log: dict[str, list[str]] = {}
    for t in month_ends(panel):
        pool = members(panel, t)
        ew_log[panel.days[t]] = pool
        lv_log[panel.days[t]] = lowvol_pick(panel, t, pool)
    lv = equal_weight_pit(panel, lv_log)
    ew = equal_weight_pit(panel, ew_log)
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    span = panel.days[start:]
    d, c = kospi_frame()
    kmap = dict(zip(d, c, strict=True))
    kos = [kmap[x] / kmap[span[0]] for x in span]
    end = span[-1]
    rows = {
        name: [
            stats(span, eq, FROM, end),
            stats(span, eq, FROM, "2016-12-31"),
            stats(span, eq, SPLIT, end),
        ]
        for name, eq in (("lowvol", lv), ("ew", ew), ("kospi", kos))
    }
    checks = {
        "a_sharpe_gt_ew_all_three": all(_beats(rows["lowvol"], rows["ew"], "sharpe")),
        "b_cagr_gt_ew_all_three": all(_beats(rows["lowvol"], rows["ew"], "cagr")),
        "c_cagr_gt_kospi_full": rows["lowvol"][0]["cagr"] > rows["kospi"][0]["cagr"],
    }
    sizes = [len(v) for v in lv_log.values()]
    return {
        "verdict": "PASS" if all(checks.values()) else "REJECT",
        "checks": checks,
        **rows,
        "picks_min_mean": [min(sizes), round(sum(sizes) / len(sizes), 1)],
        "last_picks": lv_log[max(lv_log)],
    }


# ---------------------------------------------------------------- 실행


def data_files() -> list[Path]:
    files = [TRADFI / n for n in ("SPY_adj.csv", "QQQ_adj.csv", "DTB3.csv")]
    files += [TLT, KR / "kospi.csv", KR / "info.json"]
    files += sorted((KR / "prices").glob("*.csv"))
    files += sorted(FLOWS.glob("*.csv"))
    return files


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def audit() -> dict[str, object]:
    """개수만 (수익 없음)."""
    digest, n = data_hash()
    days, *_ = us_aligned()
    panel, _ = build_panel()
    flow = market_flow(panel)
    i0 = next(i for i, x in enumerate(panel.days) if x >= FROM)
    me = month_ends(panel)
    sizes = [len(lowvol_pick(panel, t, members(panel, t))) for t in me[:: max(1, len(me) // 12)]]
    return {
        "data_sha256": digest,
        "files": n,
        "us_days": len(days),
        "us_first_last": [days[0], days[-1]],
        "kr_days": len(panel.days) - i0,
        "flow_zero_days": sum(1 for x in flow[i0:] if x == 0),
        "month_ends": len(me),
        "lowvol_pick_sizes_sample": sizes,
    }


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    digest, _ = data_hash()
    if EXPECTED_DATA is None or digest != EXPECTED_DATA:
        raise SystemExit(f"자료 해시가 다르다: {digest}")
    panel, _ = build_panel()
    result: dict[str, object] = {
        "commit": sha,
        "data_sha256": digest,
        "HFEA": run_hfea(),
        "KR_TREND2": run_trend2(),
        "KR_FLOWTIME": run_flowtime(panel),
        "KR_LOWVOL": run_lowvol(panel),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
    elif cmd == "run":
        print(json.dumps(run(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit(f"알 수 없는 명령: {cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
