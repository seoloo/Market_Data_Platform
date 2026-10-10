"""문서 203 — IDEAS 31 (BTC · ETH 독립 ML/DL) G0 관문 감사. 학습 · 후보 성과 없음.

사용 예::

    python -m scripts.run_ml_g0_203 run   # → reports/ml_g0_203/result.json

하는 것 (읽기 전용, ``data/trading.db`` mode=ro):

1. 자료 품질 — 1 시간봉 연속성 · 중복 · OHLC 모순 · 4 시간 완성봉 비율, funding 행.
2. 워밍업 — 120 일 수익 · 365 일 RV 백분위 · 42 개 4 시간봉이 처음 다 갖춰지는 시각.
3. 정답표(label) 분포 — 4 시간 마감마다 24 시간 선물 수익 · funding · 비용 18bp · 문턱 20bp 로
   LONG / FLAT / SHORT 비율 (구간 · 자산별). **모델 성과가 아니다.**
4. 검정력 대리 — 학습 구간(2020-04 ~ 2023-12) **기준선만**으로:
   A: 단순 20 일 추세 LONG/SHORT 24 시간 보유 vs 그 규칙과 일부만 다른 무작위 후보의 월 차이,
   B: T/v2(롱만) vs 무작위 위험 필터(0.5 · 0 배율, 같은 빈도 · 지속)의 월 차이.
   후보 모델의 실측 분산이 아니라 '이 설계가 어떤 크기의 개선을 잡을 수 있나' 의 규모 추정이다.
5. 필요한 월수 · 정해진 기간으로 잡을 수 있는 최소 효과(MDE).
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.backtest.trend import DailyBar, TrendConfig, simulate_trend

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
OUT = REPO / "reports" / "ml_g0_203"
H = 3_600_000
DAY = 86_400_000
ASSETS = ("BTCUSDT", "ETHUSDT")
COST_BP = 18.0
THRESH_BP = 20.0
DELTA_MONTH = 0.0025
"""IDEAS 31 계획용 최소 유용 증분 — 연 산술 3%p = 월 0.25%p."""
TRAIN = ("2020-04", "2023-12")
PERIODS = {"train": TRAIN, "validation": ("2024-01", "2024-12"), "test": ("2025-01", "2026-08")}
SEED = 11
SIMS = 300


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


Bar = tuple[int, float, float, float, float, float]
"""(open_ms, open, high, low, close, volume)."""


def load(conn: sqlite3.Connection, exchange: str, symbol: str, tf: str) -> list[Bar]:
    rows = conn.execute(
        "select open_ms, open, high, low, close, volume from candles "
        "where exchange=? and symbol=? and timeframe=? order by open_ms",
        (exchange, symbol, tf),
    ).fetchall()
    return [(int(a), float(b), float(c), float(d), float(e), float(f)) for a, b, c, d, e, f in rows]


def quality(bars: Sequence[Bar], step: int) -> dict[str, Any]:
    """연속성 · OHLC 모순 · 4 시간 완성 비율 (순수)."""
    if not bars:
        return {"rows": 0}
    gaps = [(b[0] - a[0]) // step - 1 for a, b in pairwise(bars)]
    missing = sum(g for g in gaps if g > 0)
    bad = sum(
        1
        for _, o, h, lo, c, v in bars
        if h < max(o, c) or lo > min(o, c) or v < 0 or min(o, h, lo, c) <= 0
    )
    have = {b[0] for b in bars}
    blocks = defaultdict(int)
    for ms in have:
        blocks[ms - ms % (4 * H)] += 1
    full = sum(1 for n in blocks.values() if n == 4)
    return {
        "rows": len(bars),
        "first": _iso(bars[0][0]),
        "last": _iso(bars[-1][0]),
        "missing_bars": missing,
        "gap_events": sum(1 for g in gaps if g > 0),
        "max_gap_bars": max(gaps, default=0),
        "ohlc_violations": bad,
        "complete_4h_share": round(full / len(blocks), 4) if blocks else None,
    }


def first_ready(daily_first_ms: int) -> str:
    """일봉 시작 + 385 일(RV20 21 개 + 365 순위 · 120 일 수익 포함)."""
    return _iso(daily_first_ms + 385 * DAY)


def labels(
    hourly: Sequence[Bar],
    funding: Sequence[tuple[int, float]],
) -> list[tuple[int, float, float, str]]:
    """4 시간 마감 t 마다 (t, R bp, funding bp, 분류). R = t 시작 1h 시가 → t+24h 시가 (순수)."""
    opens = {b[0]: b[1] for b in hourly}
    f_ms = [ms for ms, _ in funding]
    f_rate = [r for _, r in funding]
    import bisect

    out = []
    for ms in sorted(opens):
        if ms % (4 * H):
            continue
        a, b = opens.get(ms), opens.get(ms + 24 * H)
        if a is None or b is None:
            continue
        r_bp = 10_000 * math.log(b / a)
        lo, hi = bisect.bisect_right(f_ms, ms), bisect.bisect_right(f_ms, ms + 24 * H)
        f_bp = 10_000 * sum(f_rate[lo:hi])
        edge_l = r_bp - COST_BP - f_bp
        edge_s = -r_bp - COST_BP + f_bp
        cls = "LONG" if edge_l > THRESH_BP else "SHORT" if edge_s > THRESH_BP else "FLAT"
        out.append((ms, r_bp, f_bp, cls))
    return out


def lr_sd(xs: Sequence[float], lag: int = 3) -> float:
    """장기 표준편차 (Bartlett lag 3) — 월 계열의 자기상관을 반영."""
    n = len(xs)
    if n < lag + 2:
        return float("nan")
    m = statistics.fmean(xs)
    d = [x - m for x in xs]
    g0 = sum(v * v for v in d) / n
    s = g0
    for k in range(1, lag + 1):
        gk = sum(d[i] * d[i - k] for i in range(k, n)) / n
        s += 2 * (1 - k / (lag + 1)) * gk
    return math.sqrt(max(s, 0.0))


def months_needed(sd: float, delta: float = DELTA_MONTH, z: float = 2.0) -> float:
    return ((z + 0.8416) * sd / delta) ** 2


def mde_year(sd: float, months: int, z: float = 2.0) -> float:
    return 12 * (z + 0.8416) * sd / math.sqrt(months)


def _monthly(daily: Mapping[int, float], lo: str, hi: str) -> dict[str, float]:
    by: dict[str, float] = defaultdict(float)
    for ms, r in daily.items():
        mon = _month(ms)
        if lo <= mon <= hi:
            by[mon] += r
    return dict(sorted(by.items()))


def proxy_a(
    daily_close: Mapping[str, Mapping[int, float]],
    fut_open: Mapping[str, Mapping[int, float]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
    rng: random.Random,
) -> dict[str, Any]:
    """A 대리: 매일 00:00 UTC, 20 일 추세 부호로 자산당 ±0.5 를 24 시간.

    왕복 비용 18bp · funding 반영."""
    import bisect

    days = sorted(set.intersection(*(set(d) for d in daily_close.values())))
    rule: dict[str, dict[int, int]] = {s: {} for s in ASSETS}
    ret: dict[str, dict[int, float]] = {s: {} for s in ASSETS}
    for s in ASSETS:
        f_ms = [m for m, _ in funding[s]]
        f_rate = [r for _, r in funding[s]]
        closes = daily_close[s]
        for i, d in enumerate(days):
            if i < 21:
                continue
            prev20 = closes.get(days[i - 21])
            last = closes.get(days[i - 1])  # 어제 마감 일봉
            a, b = fut_open[s].get(d), fut_open[s].get(d + DAY)
            if None in (prev20, last, a, b):
                continue
            rule[s][d] = 1 if last > prev20 else -1  # type: ignore[operator]
            lo, hi = bisect.bisect_right(f_ms, d), bisect.bisect_right(f_ms, d + DAY)
            ret[s][d] = (b / a - 1, sum(f_rate[lo:hi]))  # type: ignore[assignment,operator]
    cost = COST_BP / 10_000

    def book(pos: Mapping[str, Mapping[int, int]]) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for s in ASSETS:
            for d, p in pos[s].items():
                if d not in ret[s] or p == 0:
                    continue
                r, f = ret[s][d]  # type: ignore[misc]
                out[d] += 0.5 * (p * r - cost - p * f)
        return out

    base = _monthly(book(rule), *TRAIN)
    res: dict[str, Any] = {
        "months": len(base),
        "rule_monthly_sd_lr": round(lr_sd(list(base.values())), 4),
    }
    for q in (0.1, 0.3, 0.5):
        sds = []
        for _ in range(SIMS):
            alt = {
                s: {
                    d: (rng.choice((-1, 0, 1)) if rng.random() < q else p)
                    for d, p in rule[s].items()
                }
                for s in ASSETS
            }
            alt_m = _monthly(book(alt), *TRAIN)
            diffs = [alt_m[m] - base[m] for m in base if m in alt_m]
            sds.append(lr_sd(diffs))
        res[f"diff_sd_q{int(q * 100)}"] = round(statistics.median(sds), 4)
    return res


def proxy_b(spot_daily: Mapping[str, Mapping[int, DailyBar]], rng: random.Random) -> dict[str, Any]:
    """B 대리: T/v2(롱만) 일 수익 × 무작위 위험 배율(마르코프: 위험 비중 p · 평균 지속 L 일)."""
    cfg = TrendConfig(long_only=True)
    lo, hi = _ms("2020-04-01"), _ms("2023-12-31")
    res_t = simulate_trend(spot_daily, {}, from_ms=lo, to_ms=hi, initial_equity=1.0, cfg=cfg)
    daily = dict(zip(res_t.days, res_t.returns, strict=False))
    base = _monthly(daily, *TRAIN)
    out: dict[str, Any] = {
        "months": len(base),
        "tv2_monthly_sd_lr": round(lr_sd(list(base.values())), 4),
    }
    gross = 0.5  # 평균 노출 근사 (비용 계산용)
    for p in (0.1, 0.2, 0.3):
        for length in (1, 3):
            exit_p = 1 / length
            enter_p = p * exit_p / (1 - p)
            sds = []
            for _ in range(SIMS):
                state, prev_m = 0, 1.0
                filt: dict[int, float] = {}
                for d in sorted(daily):
                    state = (
                        (0 if rng.random() < exit_p else 1)
                        if state
                        else (1 if rng.random() < enter_p else 0)
                    )
                    m = rng.choice((0.5, 0.0)) if state else 1.0
                    filt[d] = m * daily[d] - 0.0015 * abs(m - prev_m) * gross
                    prev_m = m
                b = _monthly(filt, *TRAIN)
                diffs = [b[k] - base[k] for k in base if k in b]
                sds.append(lr_sd(diffs))
            out[f"diff_sd_p{int(p * 100)}_L{length}"] = round(statistics.median(sds), 4)
    return out


def power_table(sd: float) -> dict[str, Any]:
    return {
        "sd": sd,
        "months_needed_z2": round(months_needed(sd)),
        "months_needed_holm6_z2.64": round(months_needed(sd, z=2.64)),
        "mde_year_12m": round(mde_year(sd, 12), 4),
        "mde_year_24m": round(mde_year(sd, 24), 4),
        "mde_year_36m": round(mde_year(sd, 36), 4),
        "mde_year_60m": round(mde_year(sd, 60), 4),
    }


def run() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.execute("pragma query_only=1")
    res: dict[str, Any] = {"computed": datetime.now(UTC).isoformat(timespec="seconds")}
    spot_h = {s: load(conn, "binance_spot", s, "1h") for s in ASSETS}
    fut_h = {s: load(conn, "binance_usdm", s, "1h") for s in ASSETS}
    spot_d = {s: load(conn, "binance_spot", s, "1d") for s in ASSETS}
    funding = {
        s: [
            (int(a), float(b))
            for a, b in conn.execute(
                "select funding_ms, rate from funding_rates where exchange='binance_usdm' "
                "and symbol=? order by funding_ms",
                (s,),
            )
        ]
        for s in ASSETS
    }
    conn.close()
    res["quality"] = {
        **{f"spot_1h_{s}": quality(spot_h[s], H) for s in ASSETS},
        **{f"usdm_1h_{s}": quality(fut_h[s], H) for s in ASSETS},
        **{f"spot_1d_{s}": quality(spot_d[s], DAY) for s in ASSETS},
        "funding_rows": {s: len(funding[s]) for s in ASSETS},
    }
    res["warmup_first_ready_spot_features"] = {s: first_ready(spot_d[s][0][0]) for s in ASSETS}
    res["label_first_futures"] = {s: _iso(fut_h[s][0][0]) for s in ASSETS}
    # 정답표 분포
    dist: dict[str, Any] = {}
    for s in ASSETS:
        lab = labels(fut_h[s], funding[s])
        for name, (lo, hi) in PERIODS.items():
            rows = [x for x in lab if lo <= _month(x[0]) <= hi]
            n = len(rows)
            cnt = {c: sum(1 for x in rows if x[3] == c) for c in ("LONG", "FLAT", "SHORT")}
            dist[f"{s}_{name}"] = {
                "decisions_4h": n,
                "non_overlapping_24h": n // 6,
                **{c: round(v / n, 3) if n else None for c, v in cnt.items()},
                "abs_return_bp_median": round(statistics.median(abs(x[1]) for x in rows), 1)
                if rows
                else None,
            }
    res["labels"] = dist
    # 검정력 대리
    rng = random.Random(SEED)
    daily_close = {s: {b[0]: b[4] for b in spot_d[s]} for s in ASSETS}
    fut_open = {s: {b[0]: b[1] for b in fut_h[s] if b[0] % DAY == 0} for s in ASSETS}
    a = proxy_a(daily_close, fut_open, funding, rng)
    spot_bars = {s: {b[0]: DailyBar(b[2], b[4]) for b in spot_d[s]} for s in ASSETS}
    b = proxy_b(spot_bars, rng)
    res["proxy_a"] = a
    res["proxy_b"] = b
    res["power"] = {
        "A_rule_itself": power_table(a["rule_monthly_sd_lr"]),
        "A_vs_rule_q30": power_table(a["diff_sd_q30"]),
        "A_vs_rule_q10": power_table(a["diff_sd_q10"]),
        "B_vs_tv2_p20_L3": power_table(b["diff_sd_p20_L3"]),
        "B_vs_tv2_p10_L1": power_table(b["diff_sd_p10_L1"]),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    return res


def kr_cross() -> dict[str, Any]:
    """대안 규모 추정: 국장 횡단면(141 자료, 상장폐지 포함 · 그 시점 상위 200).

    매달 말 12-1 수익 상위 20% 고르게 vs 종목군 고르게 — 두 '순위 규칙' 의 월 차이 분산을
    모델 후보 차이의 대리로 쓴다(성과 판정 아님, 141 · 122 에서 이미 본 자료)."""
    from scripts.run_lt_kr import build_panel, members

    panel, _ = build_panel()
    month_end = [t for t in range(1, len(panel.days)) if panel.days[t][:7] != panel.days[t - 1][:7]]
    month_end = [t - 1 for t in month_end]
    diffs, ews = [], []
    for i, t in enumerate(month_end[:-1]):
        nxt = month_end[i + 1]
        if t < 260:
            continue
        pool = members(panel, t)

        def fwd(s: str, t: int = t, nxt: int = nxt) -> float | None:
            xs = panel.close[s]
            a = xs[t]
            b = next((xs[k] for k in range(nxt, t, -1) if xs[k] is not None), None)
            return None if a is None or b is None or a <= 0 else b / a - 1

        mom = []
        for s in pool:
            xs = panel.close[s]
            a, b = xs[t - 21], xs[t - 252] if t >= 252 else None
            if a is not None and b is not None and b > 0:
                mom.append((a / b - 1, s))
        if len(mom) < 50:
            continue
        mom.sort(reverse=True)
        top = [s for _, s in mom[: len(mom) // 5]]
        r_top = [x for s in top if (x := fwd(s)) is not None]
        r_all = [x for s in pool if (x := fwd(s)) is not None]
        if r_top and r_all:
            diffs.append(statistics.fmean(r_top) - statistics.fmean(r_all))
            ews.append(statistics.fmean(r_all))
    sd = lr_sd(diffs)
    return {
        "months": len(diffs),
        "ew_monthly_sd_lr": round(lr_sd(ews), 4),
        "rank_diff_sd_lr": round(sd, 4),
        "power": power_table(round(sd, 4)),
    }


def qqq_proxy() -> dict[str, Any]:
    """QQQ(나스닥100 지수, 1985 ~) 대리 — 사용자 2026-10-11 "코인 자료 모자라면 qqq 도".

    A형: 20 일 추세 롱/현금 매일 vs 일부만 다른 무작위 후보, B형: T 규칙(20/60/120 부호 평균 ·
    변동성 15% 목표, 롱만) vs 무작위 위험 필터. 지수 가격(배당 없음) · 비용 편도 5bp.
    """
    from scripts.run_qs_dotcom import _series

    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    days = sorted(ndx)
    px = [ndx[d] for d in days]
    rets = [px[i] / px[i - 1] - 1 for i in range(1, len(px))]
    cost = 0.0005
    rng = random.Random(SEED)
    # A형 규칙
    rule: dict[int, int] = {}
    for i in range(21, len(px)):
        rule[i] = 1 if px[i - 1] > px[i - 21] else 0

    def book_a(pos: Mapping[int, int]) -> dict[str, float]:
        by: dict[str, float] = defaultdict(float)
        prev = 0
        for i in sorted(pos):
            p = pos[i]
            by[days[i][:7]] += p * rets[i - 1] - cost * abs(p - prev)
            prev = p
        return dict(by)

    base_a = book_a(rule)
    out: dict[str, Any] = {"first": days[0], "last": days[-1], "months": len(base_a)}
    for q in (0.1, 0.3):
        sds = []
        for _ in range(SIMS // 3):
            alt = {i: (rng.choice((0, 1)) if rng.random() < q else p) for i, p in rule.items()}
            b = book_a(alt)
            sds.append(lr_sd([b[m] - base_a[m] for m in base_a if m in b]))
        out[f"A_diff_sd_q{int(q * 100)}"] = round(statistics.median(sds), 4)
    # B형 T 규칙
    w: dict[int, float] = {}
    for i in range(121, len(px)):
        sign = statistics.fmean(
            (1 if px[i - 1] > px[i - 1 - k] else -1 if px[i - 1] < px[i - 1 - k] else 0)
            for k in (20, 60, 120)
        )
        steps = [math.log(px[i - j] / px[i - j - 1]) for j in range(1, 61)]
        vol = statistics.stdev(steps) * math.sqrt(252)
        w[i] = max(sign, 0.0) * min(0.15 / vol, 1.0) if vol > 0 else 0.0
    daily_b = {i: w[i] * rets[i - 1] for i in w}
    base_b: dict[str, float] = defaultdict(float)
    for i, r in daily_b.items():
        base_b[days[i][:7]] += r
    out["T_rule_monthly_sd_lr"] = round(lr_sd(list(base_b.values())), 4)
    for p, length in ((0.1, 1), (0.2, 3)):
        exit_p = 1 / length
        enter_p = p * exit_p / (1 - p)
        sds = []
        for _ in range(SIMS // 3):
            state, prev_m = 0, 1.0
            filt: dict[str, float] = defaultdict(float)
            for i in sorted(daily_b):
                state = (
                    (0 if rng.random() < exit_p else 1)
                    if state
                    else (1 if rng.random() < enter_p else 0)
                )
                m = rng.choice((0.5, 0.0)) if state else 1.0
                filt[days[i][:7]] += m * daily_b[i] - cost * abs(m - prev_m) * w[i]
                prev_m = m
            sds.append(lr_sd([filt[k] - base_b[k] for k in base_b]))
        out[f"B_diff_sd_p{int(p * 100)}_L{length}"] = round(statistics.median(sds), 4)
    out["power"] = {
        "A_vs_rule_q10": power_table(out["A_diff_sd_q10"]),
        "B_vs_T_p10_L1": power_table(out["B_diff_sd_p10_L1"]),
        "B_vs_T_p20_L3": power_table(out["B_diff_sd_p20_L3"]),
    }
    return out


def main(argv: list[str]) -> int:
    extra = {"kr": ("kr_cross.json", kr_cross), "qqq": ("qqq.json", qqq_proxy)}
    if argv and argv[0] in extra and len(argv) == 1:
        name, fn = extra[argv[0]]
        res = fn()
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
        sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
        return 0
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_ml_g0_203 run|kr|qqq")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
