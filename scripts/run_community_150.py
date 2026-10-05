"""문서 150 — 커뮤니티에서 수익을 낸다고 알려진, 아직 시험하지 않은 방법 5 가지. 각각 1회 판정.

사용 예::

    python -m scripts.run_community_150 audit   # 자료 해시·표본 수 (수익 없음)
    python -m scripts.run_community_150 run     # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음

시험(규칙·기준은 문서 150 에 결과 전에 고정):

* **TOM** 월말·월초(마지막 거래일 + 첫 3 거래일)에만 TQQQ, 나머지는 현금.
* **FOMC** 정례회의 발표일 하루(전날 종가 → 당일 종가)만 TQQQ.
* **BTC-HOUR** 매일 22:00~24:00 UTC 만 BTC 롱 (Quantpedia 2023, 표본 끝 2023-06-30 뒤만 판정).
* **CME-GAP** BTC 주말 CME 갭(금 16:00 → 일 17:00 시카고)이 0.5% 이상이면 메우는 쪽으로,
  금요일 가격에 닿으면 이익 실현, 아니면 다음 금요일 16:00 에 정리.
* **KR-LIMITUP** 유가증권시장(그 시점 거래대금 상위 200) 상한가 마감 다음 날 시가에 사서
  5 거래일째 종가에 판다.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.run_a1_train import git_state

REPO = Path(__file__).resolve().parent.parent
TRADFI = REPO / "data" / "research" / "tradfi"
KR = REPO / "data" / "research" / "kr_stocks"
BTC15 = REPO / "data" / "external" / "derived" / "15m" / "BTCUSDT.csv"
OUTPUT = REPO / "reports" / "community_150"
EXPECTED_DATA: str | None = "8528fc2947714fb5d661e107a5dde28c8e39ee26546401c756d64252b8c9f208"
"""자료 묶음 해시 — audit 뒤 문서 150 에 적고 여기에 고정한다."""

US_FEE = 0.0025
"""미국 ETF 편도 (QS·LT 와 같은 판정 비용). 참고 줄 0.10%."""
US_FEE_REF = 0.0010
PERP_FEE = 0.00095
"""코인 무기한 편도 판정 비용 (TrendConfig.perp_cost). 참고 줄 0.023%."""
PERP_FEE_REF = 0.00023
KR_FEE = 0.0025
"""국장 편도 (141 과 같음: 수수료·거래세·불리폭)."""
KR_FEE_REF = 0.0012
MIN_N = 30
MIN_N_GAP = 100
SEEDS = 1000
TOM_FROM, TOM_TO, TOM_SPLIT = "2010-03-01", "2026-08-31", "2018-01-01"
FOMC_SPLIT = "2019-01-01"
HOUR_OOS_FROM, HOUR_SPLIT, HOUR_TO = "2023-07-01", "2025-01-01", "2026-08-31"
GAP_FROM, GAP_SPLIT, GAP_TO = "2018-01-05", "2022-01-01", "2026-08-28"
GAP_MIN = 0.005
KR_SPLIT = "20220101"
KR_TO = "20260831"
LIMIT = 0.29
HOLD = 5
UNIVERSE = 200
LIQ_DAYS = 63
CHICAGO = ZoneInfo("America/Chicago")
_M15 = 15 * 60 * 1000

FOMC: tuple[str, ...] = (
    # 정례회의 발표일(회의 마지막 날). 출처: federalreserve.gov fomchistorical2012~2020 ·
    # fomccalendars (2026-10-06 열람). 2020-03 정례회의는 취소(긴급 회의는 넣지 않음).
    "2012-01-25", "2012-03-13", "2012-04-25", "2012-06-20", "2012-08-01", "2012-09-13",
    "2012-10-24", "2012-12-12",
    "2013-01-30", "2013-03-20", "2013-05-01", "2013-06-19", "2013-07-31", "2013-09-18",
    "2013-10-30", "2013-12-18",
    "2014-01-29", "2014-03-19", "2014-04-30", "2014-06-18", "2014-07-30", "2014-09-17",
    "2014-10-29", "2014-12-17",
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17", "2015-07-29", "2015-09-17",
    "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27", "2016-09-21",
    "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26", "2017-09-20",
    "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26",
    "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18",
    "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05",
    "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22",
    "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21",
    "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20",
    "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18",
    "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17",
    "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
)  # fmt: skip


# ---------------------------------------------------------------- 공통


def summary(xs: Sequence[float], keys: Sequence[str] | None = None) -> dict[str, float]:
    """평균 · 표준오차(``keys`` 가 있으면 같은 키끼리 평균 낸 뒤의 군집 표준오차) · 2SE 하한."""
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": statistics.fmean(xs) if xs else 0.0, "se": 0.0, "lo2": 0.0}
    mean = statistics.fmean(xs)
    if keys is None:
        se = statistics.stdev(xs) / math.sqrt(n)
    else:
        groups: dict[str, list[float]] = defaultdict(list)
        for x, k in zip(xs, keys, strict=True):
            groups[k].append(x - mean)
        g = len(groups)
        var = sum(sum(v) ** 2 for v in groups.values()) / (n * n) * (g / (g - 1) if g > 1 else 1)
        se = math.sqrt(var)
    return {"n": n, "mean": round(mean, 6), "se": round(se, 6), "lo2": round(mean - 2 * se, 6)}


def diff_summary(a: Sequence[float], b: Sequence[float]) -> dict[str, float]:
    """평균 차이 a − b 와 웰치 표준오차."""
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    d = ma - mb
    return {"diff": round(d, 6), "se": round(se, 6), "lo2": round(d - 2 * se, 6)}


def equity_stats(eq: Sequence[float], per_year: int = 252) -> dict[str, float]:
    rets = [b / a - 1 for a, b in pairwise(eq)]
    years = len(rets) / per_year
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 else 0.0
    sd = statistics.stdev(rets) if len(rets) > 1 else 0.0
    sharpe = statistics.fmean(rets) / sd * math.sqrt(per_year) if sd > 0 else 0.0
    peak, mdd = 0.0, 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"cagr": round(cagr, 4), "sharpe": round(sharpe, 3), "mdd": round(mdd, 4)}


def trade_verdict(
    nets: Sequence[float],
    dates: Sequence[str],
    split: str,
    min_n: int,
    keys: Sequence[str] | None = None,
) -> dict[str, object]:
    """거래 단위 공통 기준: n ≥ min_n · 평균 > 0 · 2SE 하한 > 0 · 두 반쪽 평균 > 0."""
    if len(nets) < min_n:
        return {"verdict": "DATA_INSUFFICIENT", "n": len(nets)}
    s = summary(nets, keys)
    first = [x for x, d in zip(nets, dates, strict=True) if d < split]
    second = [x for x, d in zip(nets, dates, strict=True) if d >= split]
    halves = [round(statistics.fmean(h), 6) if h else 0.0 for h in (first, second)]
    ok = s["mean"] > 0 and s["lo2"] > 0 and all(h > 0 for h in halves)
    return {
        "verdict": "PASS" if ok else "REJECT",
        **s,
        "halves": halves,
        "halves_n": [len(first), len(second)],
        "win": round(sum(x > 0 for x in nets) / len(nets), 3),
    }


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_close(path: Path) -> tuple[list[str], list[float]]:
    rows = _read(path)
    if rows and "-" not in rows[0]["date"]:
        for r in rows:
            d = r["date"]
            r["date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return [r["date"] for r in rows], [float(r["close"]) for r in rows]


# ---------------------------------------------------------------- TOM


def tom_days(dates: Sequence[str]) -> list[bool]:
    """각 거래일이 월말·월초 창(그달 마지막 거래일 또는 첫 3 거래일)인가."""
    out = [False] * len(dates)
    month_start: int | None = None  # 자료 첫 달은 월초를 모른다
    for i, d in enumerate(dates):
        if i > 0 and d[:7] != dates[i - 1][:7]:
            month_start = i
            out[i - 1] = True  # 앞 달 마지막 거래일
        if month_start is not None and i - month_start < 3:
            out[i] = True
    return out


def switch_equity(
    rets: Sequence[float], cash: Sequence[float], held: Sequence[bool], fee: float
) -> list[float]:
    """``held`` 인 날 ``rets``, 아닌 날 ``cash``. 들어갈 때·나올 때 각각 ``fee``."""
    eq = [1.0]
    for i, r in enumerate(rets):
        v = eq[-1]
        if held[i]:
            if i == 0 or not held[i - 1]:
                v *= 1 - fee
            v *= 1 + r
            if i == len(rets) - 1 or not held[i + 1]:
                v *= 1 - fee
        else:
            v *= 1 + cash[i]
        eq.append(v)
    return eq


def random_blocks(held: Sequence[bool], rng: random.Random) -> list[bool]:
    """창마다 같은 길이의 블록을 창 밖(앞 창과 이 창 사이) 아무 곳에나 옮긴다."""
    n = len(held)
    out = [False] * n
    i = 0
    prev_end = 0
    while i < n:
        if held[i]:
            j = i
            while j < n and held[j]:
                j += 1
            length = j - i
            lo, hi = prev_end, i - length  # 블록 시작 후보 [lo, hi]
            if hi >= lo:
                s = rng.randint(lo, hi)
                for k in range(s, s + length):
                    out[k] = True
            prev_end = j
            i = j
        else:
            i += 1
    return out


def _aligned(dates: Sequence[str], series: Mapping[str, float]) -> list[float]:
    out: list[float] = []
    last = None
    for d in dates:
        last = series.get(d, last)
        if last is None:
            raise SystemExit(f"자료 없음: {d}")
        out.append(last)
    return out


def _daily_rets(xs: Sequence[float]) -> list[float]:
    return [b / a - 1 for a, b in pairwise(xs)]


def us_frame(lo: str, hi: str) -> tuple[list[str], list[float], list[float], list[float]]:
    """QQQ 거래일 기준 [lo, hi] 의 (날짜, QQQ 수익, TQQQ 수익, 현금 수익). 수익 i 는 날짜 i
    종가까지(전날 종가 → 그날 종가)."""
    qd, qc = load_close(TRADFI / "QQQ_adj.csv")
    td, tc = load_close(TRADFI / "TQQQ_adj.csv")
    rd, rc = load_close(TRADFI / "DTB3.csv")
    tmap, rmap = dict(zip(td, tc, strict=True)), dict(zip(rd, rc, strict=True))
    idx = [i for i, d in enumerate(qd) if lo <= d <= hi]
    i0 = idx[0] - 1
    dates = qd[i0 : idx[-1] + 1]
    q = qc[i0 : idx[-1] + 1]
    t = [tmap[d] for d in dates]
    rate = _aligned(dates, rmap)
    cash = [rate[i] / 100 / 252 for i in range(1, len(dates))]
    return dates[1:], _daily_rets(q), _daily_rets(t), cash


def tom_gap(dates: Sequence[str], closes: Sequence[float]) -> dict[str, float]:
    """창 안 일 수익 평균 − 창 밖 평균 (일관성 보고용)."""
    flags = tom_days(dates)[1:]
    rets = _daily_rets(closes)
    return diff_summary(
        [x for x, f in zip(rets, flags, strict=True) if f],
        [x for x, f in zip(rets, flags, strict=True) if not f],
    )


def run_tom() -> dict[str, object]:
    dates, q, t, cash = us_frame(TOM_FROM, TOM_TO)
    full_dates, _ = load_close(TRADFI / "QQQ_adj.csv")
    flag_all = dict(zip(full_dates, tom_days(full_dates), strict=True))
    held = [flag_all[d] for d in dates]
    eq = switch_equity(t, cash, held, US_FEE)
    eq_ref = switch_equity(t, cash, held, US_FEE_REF)
    qqq_eq = [1.0]
    for r in q:
        qqq_eq.append(qqq_eq[-1] * (1 + r))
    cash_eq = [1.0]
    for r in cash:
        cash_eq.append(cash_eq[-1] * (1 + r))
    rng = random.Random(150)
    rand_cagr = sorted(
        equity_stats(switch_equity(t, cash, random_blocks(held, rng), US_FEE))["cagr"]
        for _ in range(SEEDS)
    )
    p95 = rand_cagr[int(0.95 * SEEDS) - 1]
    in_q = [r for r, h in zip(q, held, strict=True) if h]
    out_q = [r for r, h in zip(q, held, strict=True) if not h]
    gap = diff_summary(in_q, out_q)
    s, sq = equity_stats(eq), equity_stats(qqq_eq)
    split = next(i for i, d in enumerate(dates) if d >= TOM_SPLIT)
    halves = []
    for lo, hi in ((0, split), (split, len(dates))):
        e = switch_equity(t[lo:hi], cash[lo:hi], held[lo:hi], US_FEE)
        c = [1.0]
        for r in cash[lo:hi]:
            c.append(c[-1] * (1 + r))
        halves.append({"cagr": equity_stats(e)["cagr"], "cash_cagr": equity_stats(c)["cagr"]})
    checks = {
        "a_tom_minus_rest_lo2_gt0": gap["lo2"] > 0,
        "b_sharpe_gt_qqq": s["sharpe"] > sq["sharpe"],
        "c_beats_random_p95": s["cagr"] > p95,
        "d_halves_beat_cash": all(h["cagr"] > h["cash_cagr"] for h in halves),
    }
    consistency = {
        "SPY": tom_gap(*load_close(TRADFI / "SPY_adj.csv")),
        "KOSPI": tom_gap(*load_close(KR / "kospi.csv")),
        "BTC": tom_gap(*btc_daily()),
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "REJECT",
        "checks": checks,
        "qqq_tom_vs_rest_daily": gap,
        "qqq_tom_mean": round(statistics.fmean(in_q), 6),
        "qqq_rest_mean": round(statistics.fmean(out_q), 6),
        "exposure": round(sum(held) / len(held), 3),
        "strategy": s,
        "strategy_ref_cost": equity_stats(eq_ref),
        "qqq_buy_hold": sq,
        "cash": equity_stats(cash_eq),
        "random_cagr_p50_p95": [rand_cagr[SEEDS // 2], p95],
        "halves": halves,
        "consistency_tom_minus_rest": consistency,
    }


# ---------------------------------------------------------------- FOMC


def run_fomc() -> dict[str, object]:
    dates, q, t, _cash = us_frame("2012-01-01", TOM_TO)
    at = {d: i for i, d in enumerate(dates)}
    missing = [d for d in FOMC if d not in at]
    if missing:
        raise SystemExit(f"FOMC 날짜가 거래일이 아님: {missing}")
    ev = [d for d in FOMC if d <= TOM_TO]
    nets = [t[at[d]] - 2 * US_FEE for d in ev]
    nets_ref = [t[at[d]] - 2 * US_FEE_REF for d in ev]
    fset = set(ev)
    gap = diff_summary(
        [q[at[d]] for d in ev], [r for d, r in zip(dates, q, strict=True) if d not in fset]
    )
    out = trade_verdict(nets, ev, FOMC_SPLIT, MIN_N)
    return {
        **out,
        "qqq_fomc_minus_rest_daily": gap,
        "tqqq_gross_mean": round(statistics.fmean(t[at[d]] for d in ev), 6),
        "ref_cost": summary(nets_ref),
    }


# ---------------------------------------------------------------- 코인 15 분봉


@dataclass(frozen=True, slots=True)
class Bars:
    open_ms: list[int]
    o: list[float]
    h: list[float]
    low: list[float]
    c: list[float]
    at: dict[int, int]


def load_bars(path: Path = BTC15) -> Bars:
    om: list[int] = []
    o: list[float] = []
    h: list[float] = []
    lo: list[float] = []
    c: list[float] = []
    with path.open(encoding="utf-8") as f:
        next(f)
        for line in f:
            a = line.rstrip().split(",")
            om.append(int(a[0]))
            o.append(float(a[1]))
            h.append(float(a[2]))
            lo.append(float(a[3]))
            c.append(float(a[4]))
    return Bars(om, o, h, lo, c, {m: i for i, m in enumerate(om)})


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def btc_daily() -> tuple[list[str], list[float]]:
    """UTC 하루 마지막 15 분봉 종가 (TOM 일관성 보고용)."""
    b = load_bars()
    out: dict[str, float] = {}
    for m, c in zip(b.open_ms, b.c, strict=True):
        out[datetime.fromtimestamp(m / 1000, UTC).strftime("%Y-%m-%d")] = c
    ds = sorted(out)
    return ds, [out[d] for d in ds]


def hour_trades(b: Bars, lo: str, hi: str) -> list[tuple[str, float]]:
    """날마다 22:00 UTC 봉 시가 → 다음 날 00:00 UTC 봉 시가의 수익."""
    out: list[tuple[str, float]] = []
    d = date.fromisoformat(lo)
    end = date.fromisoformat(hi)
    while d <= end:
        start = _ms(datetime(d.year, d.month, d.day, 22, tzinfo=UTC))
        stop = start + 8 * _M15
        if start in b.at and stop in b.at:
            out.append((d.isoformat(), b.o[b.at[stop]] / b.o[b.at[start]] - 1))
        d += timedelta(days=1)
    return out


def run_hour() -> dict[str, object]:
    b = load_bars()
    oos = hour_trades(b, HOUR_OOS_FROM, HOUR_TO)
    ins = hour_trades(b, "2017-08-18", "2023-06-30")
    nets = [r - 2 * PERP_FEE for _, r in oos]
    out = trade_verdict(nets, [d for d, _ in oos], HOUR_SPLIT, MIN_N)
    return {
        **out,
        "oos_gross": summary([r for _, r in oos]),
        "in_sample_gross": summary([r for _, r in ins]),
        "ref_cost": summary([r - 2 * PERP_FEE_REF for _, r in oos]),
    }


@dataclass(frozen=True, slots=True)
class GapTrade:
    friday: str
    gap: float
    side: int
    """+1 롱(갭 하락을 메움), −1 숏."""
    ret: float
    filled: bool


def gap_trade(b: Bars, friday: date) -> GapTrade | None:
    """금 16:00 시카고(마지막 봉 종가 = 목표) → 일 17:00 시카고 봉 시가에 진입 → 다음 금 16:00.

    시카고 벽시계로 더하므로 서머타임이 바뀌는 주말도 맞다."""
    fri = datetime(friday.year, friday.month, friday.day, 16, tzinfo=CHICAGO)
    fri_ms = _ms(fri)
    sun_ms = _ms(fri + timedelta(days=2, hours=1))
    nxt_ms = _ms(fri + timedelta(days=7))
    last_fri = fri_ms - _M15
    last_nxt = nxt_ms - _M15
    if last_fri not in b.at or sun_ms not in b.at or last_nxt not in b.at:
        return None
    target = b.c[b.at[last_fri]]
    i0 = b.at[sun_ms]
    entry = b.o[i0]
    gap = entry / target - 1
    if abs(gap) < GAP_MIN:
        return None
    side = -1 if gap > 0 else 1
    i1 = b.at[last_nxt]
    exit_px = b.c[i1]
    filled = False
    for i in range(i0, i1 + 1):
        if (side == -1 and b.low[i] <= target) or (side == 1 and b.h[i] >= target):
            exit_px, filled = target, True
            break
    ret = side * (exit_px / entry - 1)
    return GapTrade(friday.isoformat(), round(gap, 6), side, ret, filled)


def gap_trades(b: Bars, lo: str = GAP_FROM, hi: str = GAP_TO) -> list[GapTrade]:
    d = date.fromisoformat(lo)
    while d.weekday() != 4:
        d += timedelta(days=1)
    out: list[GapTrade] = []
    end = date.fromisoformat(hi)
    while d <= end:
        g = gap_trade(b, d)
        if g is not None:
            out.append(g)
        d += timedelta(days=7)
    return out


def run_gap() -> dict[str, object]:
    trades = gap_trades(load_bars())
    nets = [g.ret - 2 * PERP_FEE for g in trades]
    out = trade_verdict(nets, [g.friday for g in trades], GAP_SPLIT, MIN_N_GAP)
    long_ = [g.ret - 2 * PERP_FEE for g in trades if g.side == 1]
    short = [g.ret - 2 * PERP_FEE for g in trades if g.side == -1]
    return {
        **out,
        "fill_rate": round(sum(g.filled for g in trades) / len(trades), 3) if trades else 0.0,
        "long": summary(long_) if long_ else {},
        "short": summary(short) if short else {},
        "ref_cost": summary([g.ret - 2 * PERP_FEE_REF for g in trades]) if trades else {},
    }


# ---------------------------------------------------------------- KR-LIMITUP


@dataclass(slots=True)
class KrStock:
    dates: list[str]
    o: list[float]
    c: list[float]
    vol: list[float]
    value: list[float]
    lock: list[str]
    adj: dict[str, float]


def load_kr() -> dict[str, KrStock]:
    out: dict[str, KrStock] = {}
    for p in sorted((KR / "raw").glob("*.csv")):
        rows = _read(p)
        adj_rows = _read(KR / "prices" / p.name) if (KR / "prices" / p.name).exists() else []
        out[p.stem] = KrStock(
            [r["date"] for r in rows],
            [float(r["open"] or 0) for r in rows],
            [float(r["close"] or 0) for r in rows],
            [float(r["volume"] or 0) for r in rows],
            [float(r["value"] or 0) for r in rows],
            [r["lock"] for r in rows],
            {r["date"]: float(r["close"]) for r in adj_rows if r["close"]},
        )
    return out


def top_by_value(stocks: Mapping[str, KrStock]) -> dict[str, set[str]]:
    """날짜별 그 시점 종목군: 그날 거래했고 직전 63 거래일 자료가 있는 종목 중
    63 일 평균 거래대금 상위 200."""
    days = sorted({d for s in stocks.values() for d in s.dates})
    at = {d: i for i, d in enumerate(days)}
    pre: dict[str, list[float]] = {}
    first: dict[str, int] = {}
    for sym, s in stocks.items():
        v = [0.0] * len(days)
        for d, x in zip(s.dates, s.value, strict=True):
            v[at[d]] = x
        acc = [0.0]
        for x in v:
            acc.append(acc[-1] + x)
        pre[sym] = acc
        first[sym] = at[s.dates[0]] if s.dates else len(days)
    traded = {
        sym: {at[d] for d, x in zip(s.dates, s.vol, strict=True) if x > 0}
        for sym, s in stocks.items()
    }
    out: dict[str, set[str]] = {}
    for t, d in enumerate(days):
        if t < LIQ_DAYS - 1:
            continue
        ranked = sorted(
            ((pre[s][t + 1] - pre[s][t + 1 - LIQ_DAYS]) / LIQ_DAYS, s)
            for s in stocks
            if t in traded[s] and first[s] <= t + 1 - LIQ_DAYS
        )
        out[d] = {s for _, s in ranked[-UNIVERSE:]}
    return out


@dataclass(frozen=True, slots=True)
class KrEvent:
    sym: str
    date: str
    ret: float
    """다음 날 시가 → 5 거래일째 종가 (비용 전)."""
    ret1: float
    """다음 날 시가 → 그날 종가."""
    gap_next: float
    """상한가 종가 → 다음 날 시가 (사지 못하는 구간, 보고용)."""


def _consistent(s: KrStock, i: int) -> bool:
    """i 일 원래 수익과 수정 수익이 1%p 안 — 액면 분할·병합·권리락이 없다."""
    a0, a1 = s.adj.get(s.dates[i - 1]), s.adj.get(s.dates[i])
    if not a0 or not a1 or s.c[i - 1] <= 0:
        return False
    return abs((s.c[i] / s.c[i - 1]) - (a1 / a0)) < 0.01


def limitup_events(
    stocks: Mapping[str, KrStock], universe: Mapping[str, set[str]]
) -> tuple[list[KrEvent], dict[str, int]]:
    events: list[KrEvent] = []
    drop: dict[str, int] = defaultdict(int)
    for sym, s in stocks.items():
        for i in range(1, len(s.dates)):
            if s.dates[i] > KR_TO or s.c[i - 1] <= 0 or s.c[i] < s.c[i - 1] * (1 + LIMIT):
                continue
            if sym not in universe.get(s.dates[i], ()):
                continue
            if s.lock[i] != "00" or not _consistent(s, i):
                drop["corporate_action_on_signal"] += 1
                continue
            j = i + 1
            if j >= len(s.dates) or s.vol[j] <= 0 or s.o[j] <= 0:
                drop["no_next_day"] += 1
                continue
            if s.o[j] >= s.c[i] * (1 + LIMIT):
                drop["opens_limit_up_unfillable"] += 1
                continue
            k = min(i + HOLD, len(s.dates) - 1)
            while k < len(s.dates) - 1 and s.vol[k] <= 0:
                k += 1  # 거래정지면 다음 거래일
            while k > j and s.vol[k] <= 0:
                k -= 1  # 끝까지 정지(상장폐지)면 마지막 거래일
            if any(s.lock[m] != "00" or not _consistent(s, m) for m in range(j, k + 1)):
                drop["corporate_action_in_hold"] += 1
                continue
            events.append(
                KrEvent(
                    sym,
                    s.dates[i],
                    s.c[k] / s.o[j] - 1,
                    s.c[j] / s.o[j] - 1,
                    s.o[j] / s.c[i] - 1,
                )
            )
    events.sort(key=lambda e: (e.date, e.sym))
    return events, dict(drop)


def run_limitup(
    stocks: Mapping[str, KrStock] | None = None,
) -> dict[str, object]:
    stocks = stocks or load_kr()
    events, drop = limitup_events(stocks, top_by_value(stocks))
    nets = [e.ret - 2 * KR_FEE for e in events]
    dates = [e.date for e in events]
    out = trade_verdict(nets, dates, KR_SPLIT, MIN_N, keys=dates)
    return {
        **out,
        "dropped": drop,
        "same_day_close": summary([e.ret1 - 2 * KR_FEE for e in events], dates),
        "gap_next_open_gross": summary([e.gap_next for e in events], dates),
        "ref_cost": summary([e.ret - 2 * KR_FEE_REF for e in events], dates),
    }


# ---------------------------------------------------------------- 실행


def data_files() -> list[Path]:
    files = [TRADFI / n for n in ("QQQ_adj.csv", "TQQQ_adj.csv", "DTB3.csv", "SPY_adj.csv")]
    files += [KR / "kospi.csv", BTC15]
    files += sorted((KR / "raw").glob("*.csv"))
    files += sorted((KR / "prices").glob("*.csv"))
    return files


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def audit() -> dict[str, object]:
    """표본 수만 센다 (수익 없음)."""
    digest, n = data_hash()
    b = load_bars()
    gaps = gap_trades(b)
    stocks = load_kr()
    events, drop = limitup_events(stocks, top_by_value(stocks))
    dates, *_ = us_frame(TOM_FROM, TOM_TO)
    full, _ = load_close(TRADFI / "QQQ_adj.csv")
    fl = dict(zip(full, tom_days(full), strict=True))
    return {
        "data_sha256": digest,
        "files": n,
        "tom_days": sum(fl[d] for d in dates),
        "us_days": len(dates),
        "fomc_events": len([d for d in FOMC if d <= TOM_TO]),
        "hour_oos_days": len(hour_trades(b, HOUR_OOS_FROM, HOUR_TO)),
        "gap_trades": len(gaps),
        "gap_halves": [
            sum(g.friday < GAP_SPLIT for g in gaps),
            sum(g.friday >= GAP_SPLIT for g in gaps),
        ],
        "limitup_events": len(events),
        "limitup_halves": [
            sum(e.date < KR_SPLIT for e in events),
            sum(e.date >= KR_SPLIT for e in events),
        ],
        "limitup_dropped": drop,
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
    result: dict[str, object] = {
        "commit": sha,
        "data_sha256": digest,
        "TOM": run_tom(),
        "FOMC": run_fomc(),
        "BTC_HOUR": run_hour(),
        "CME_GAP": run_gap(),
        "KR_LIMITUP": run_limitup(),
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
