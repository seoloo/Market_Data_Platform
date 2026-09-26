"""문서 86 — X: T/v2 추세 규칙을 금·은·SPY·QQQ 로 (Q1 자산별 재현 · Q2 6자산 분산). 1회 판정.

사용 예::

    python -m scripts.run_x_tradfi run

``run`` 은 작업 트리가 깨끗해야 하고, 판정 파일이 이미 있으면 거부한다(사전 등록상 1회).
자료: ``data/research/tradfi/*_adj.csv`` (KIS 수정주가, ``scripts/audit_x_data.py``,
감사 A1~A3 통과)
와 저장소 BTC·ETH 현물 일봉. 기준은 문서 86 §4 그대로다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_t_trend import load as load_crypto

from auto_trading.backtest.trend import TrendConfig
from auto_trading.backtest.xasset_trend import AssetSeries, MultiResult, simulate_multi

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "tradfi"
OUTPUT = REPO / "reports" / "x_tradfi"
TRADFI = ("GLD", "SLV", "SPY", "QQQ")
CRYPTO = ("BTCUSDT", "ETHUSDT")
INITIAL = 10_000.0
DAY = 86_400_000
CFG = replace(TrendConfig(), long_only=True)
ETF_REAL_COST, KIS_COST = 0.0005, 0.0025


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


Q1_FROM, Q1_TO = _ms("2008-06-01"), _ms("2026-08-31")
Q1_HALVES = ((_ms("2008-06-01"), _ms("2017-01-01")), (_ms("2017-01-01"), _ms("2026-09-01")))
Q2_FROM, Q2_TO = _ms("2018-01-01"), _ms("2026-08-31")
Q2_HALVES = ((_ms("2018-01-01"), _ms("2022-01-01")), (_ms("2022-01-01"), _ms("2026-09-01")))
X1_SE = 2.5


# ------------------------------------------------------------------ 자료


def load_tradfi() -> tuple[dict[str, dict[int, float]], dict[str, str]]:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    closes: dict[str, dict[int, float]] = {}
    digests: dict[str, str] = {}
    for s in TRADFI:
        path = DATA / f"{s}_adj.csv"
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest != manifest["files"][f"{s}_adj"]["sha256"]:
            raise SystemExit(f"{path.name} 이 적재 기록(sha256)과 다르다")
        digests[s] = digest
        closes[s] = {_ms(r["date"]): float(r["close"]) for r in csv.DictReader(text.splitlines())}
    return closes, digests


# ------------------------------------------------------------------ 통계


def summarize(
    days: Sequence[int], equity: Sequence[float], start_equity: float, periods_per_year: int
) -> dict[str, Any]:
    """``equity[i]`` 는 ``days[i]`` 의 자본. 수익은 앞 값 대비 (첫 값은 ``start_equity`` 대비)."""
    path = [start_equity, *equity]
    rets = [path[i] / path[i - 1] - 1 for i in range(1, len(path))]
    n = len(rets)
    mean = statistics.fmean(rets)
    sd = statistics.stdev(rets)
    se = sd / math.sqrt(n)
    peak, mdd = path[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    span_years = ((days[-1] - days[0]) // DAY + 1) / 365.25
    year_end: dict[str, float] = {}
    for d, e in zip(days, equity, strict=True):
        year_end[_date(d)[:4]] = e
    yearly, prev = {}, start_equity
    for y in sorted(year_end):
        yearly[y] = year_end[y] / prev - 1
        prev = year_end[y]
    return {
        "periods": n,
        "mean": mean,
        "se": se,
        "lower_2se": mean - 2 * se,
        "lower_2_5se": mean - X1_SE * se,
        "sharpe": mean / sd * math.sqrt(periods_per_year) if sd > 0 else math.nan,
        "vol": sd * math.sqrt(periods_per_year),
        "cagr": (equity[-1] / start_equity) ** (1 / span_years) - 1,
        "mdd": mdd,
        "worst_year": min(yearly.items(), key=lambda kv: kv[1]),
        "yearly": yearly,
        "longest_underwater_days": _longest_underwater(days, equity, start_equity),
        "final": equity[-1],
    }


def _longest_underwater(days: Sequence[int], equity: Sequence[float], start: float) -> int:
    """고점에서 그 고점을 다시 넘을 때까지 가장 긴 역일 수 (끝까지 못 넘으면 끝까지)."""
    peak, peak_day, longest = start, days[0] - DAY, 0
    for d, e in zip(days, equity, strict=True):
        if e > peak:
            longest = max(longest, (d - peak_day) // DAY)
            peak, peak_day = e, d
    return max(longest, (days[-1] - peak_day) // DAY)


def _equity_before(result: MultiResult, day: int) -> float:
    i = result.days.index(day)
    return result.equity[i - 1] if i > 0 else INITIAL


def asset_view(result: MultiResult, sym: str, lo: int, hi: int, ppy: int) -> dict[str, Any]:
    """자산 하나만 든 결과를 **그 자산의 거래일** 수익으로 본다 (Q1 통계)."""
    idx = {d: i for i, d in enumerate(result.days)}
    days = [d for d in result.trading_days[sym] if lo <= d < hi]
    eq = [result.equity[idx[d]] for d in days]
    start = _equity_before(result, days[0])
    return summarize(days, eq, start, ppy)


def calendar_view(result: MultiResult, lo: int, hi: int) -> dict[str, Any]:
    days = [d for d in result.days if lo <= d < hi]
    idx = {d: i for i, d in enumerate(result.days)}
    eq = [result.equity[idx[d]] for d in days]
    start = _equity_before(result, days[0])
    return summarize(days, eq, start, 365)


def buy_hold(closes: Mapping[int, float], lo: int, hi: int, ppy: int) -> dict[str, Any]:
    before = max(d for d in closes if d < lo)
    days = sorted(d for d in closes if lo <= d < hi)
    base = closes[before]
    eq = [INITIAL * closes[d] / base for d in days]
    return summarize(days, eq, INITIAL, ppy)


def extras(result: MultiResult) -> dict[str, Any]:
    years = len(result.days) / 365.25
    return {
        "costs": result.costs,
        "trades": result.trades,
        "turnover_per_year": result.traded_notional / INITIAL / years,
        "avg_exposure": result.exposure_sum / len(result.days),
        "gross_max": result.gross_max,
        "target_sum_max": result.target_sum_max,
    }


def weekly(result: MultiResult, lo: int, hi: int) -> dict[int, float]:
    """7일 간격(기간 첫 금요일부터) 자본으로 본 주간 수익."""
    idx = {d: i for i, d in enumerate(result.days)}
    first = lo
    while datetime.fromtimestamp(first / 1000, UTC).weekday() != 4:
        first += DAY
    points = [d for d in range(first, hi, 7 * DAY) if d in idx]
    return {
        points[i]: result.equity[idx[points[i]]] / result.equity[idx[points[i - 1]]] - 1
        for i in range(1, len(points))
    }


def weekly_hold(closes: Mapping[int, float], lo: int, hi: int) -> dict[int, float]:
    first = lo
    while datetime.fromtimestamp(first / 1000, UTC).weekday() != 4:
        first += DAY
    pts = list(range(first, hi, 7 * DAY))

    def last(d: int) -> float:
        return closes[max(k for k in closes if k <= d)]

    return {pts[i]: last(pts[i]) / last(pts[i - 1]) - 1 for i in range(1, len(pts))}


def corr(a: Mapping[int, float], b: Mapping[int, float], lo: int = 0, hi: int = 1 << 62) -> float:
    keys = sorted(k for k in set(a) & set(b) if lo <= k < hi)
    xa, xb = [a[k] for k in keys], [b[k] for k in keys]
    if len(keys) < 3 or statistics.pstdev(xa) == 0 or statistics.pstdev(xb) == 0:
        return math.nan
    return statistics.correlation(xa, xb)


def window_return(result: MultiResult, lo: int, hi: int) -> float:
    idx = {d: i for i, d in enumerate(result.days)}
    return result.equity[idx[hi]] / result.equity[idx[lo]] - 1


# ------------------------------------------------------------------ 판정


def run(output: Path) -> int:
    path = output / "run" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    tradfi, digests = load_tradfi()
    audit = json.loads((OUTPUT / "audit.json").read_text(encoding="utf-8"))
    if audit.get("verdict") != "PASS":
        print("자료 감사 A1~A3 가 통과하지 않았다 (문서 86 §3.1). 판정하지 않는다.")
        return 3
    crypto_bars, _ = load_crypto()
    crypto = {s: {d: b.close for d, b in crypto_bars[s].items()} for s in CRYPTO}
    series = {s: AssetSeries(tradfi[s], 252) for s in TRADFI} | {
        s: AssetSeries(crypto[s], 365) for s in CRYPTO
    }

    def sim(names: Sequence[str], lo: int, to: int, cost: float = CFG.spot_cost) -> MultiResult:
        return simulate_multi(
            {s: series[s] for s in names},
            from_ms=lo,
            to_ms=to,
            initial_equity=INITIAL,
            cfg=replace(CFG, spot_cost=cost),
        )

    # Q1 — 자산마다
    q1: dict[str, Any] = {}
    for s in TRADFI:
        r = sim([s], Q1_FROM, Q1_TO)
        full = asset_view(r, s, Q1_FROM, Q1_TO + DAY, 252)
        halves = [asset_view(r, s, lo, hi, 252) for lo, hi in Q1_HALVES]
        x1 = full["lower_2_5se"] > 0
        x2 = all(h["mean"] > 0 for h in halves)
        refs = {}
        for label, cost in (("etf_real_0.05pct", ETF_REAL_COST), ("kis_0.25pct", KIS_COST)):
            rr = sim([s], Q1_FROM, Q1_TO, cost)
            v = asset_view(rr, s, Q1_FROM, Q1_TO + DAY, 252)
            refs[label] = {k: v[k] for k in ("sharpe", "cagr", "mdd")}
        q1[s] = {
            "full": full,
            "half_2008_2016": halves[0],
            "half_2017_2026": halves[1],
            **extras(r),
            "buy_hold": buy_hold(tradfi[s], Q1_FROM, Q1_TO + DAY, 252),
            "criteria": {
                "X1": {"passed": x1, "lower_2_5se": full["lower_2_5se"], "mean": full["mean"]},
                "X2": {"passed": x2, "halves_mean": [h["mean"] for h in halves]},
            },
            "replicated": x1 and x2,
            "reference": refs,
        }
    replicated = [s for s in TRADFI if q1[s]["replicated"]]

    # Q2 — 포트폴리오
    a = sim(CRYPTO, Q2_FROM, Q2_TO)
    b = sim((*CRYPTO, *TRADFI), Q2_FROM, Q2_TO)
    views = {
        name: {
            "full": calendar_view(r, Q2_FROM, Q2_TO + DAY),
            "halves": [calendar_view(r, lo, hi) for lo, hi in Q2_HALVES],
            **extras(r),
        }
        for name, r in (("A_btc_eth", a), ("B_six", b))
    }
    va, vb = views["A_btc_eth"], views["B_six"]
    y = {
        "Y1": vb["full"]["sharpe"] > va["full"]["sharpe"],
        "Y2": vb["full"]["mdd"] <= va["full"]["mdd"],
        "Y3": all(
            hb["sharpe"] > ha["sharpe"] for ha, hb in zip(va["halves"], vb["halves"], strict=True)
        ),
        "Y4": b.target_sum_max <= 1 + 1e-12,
    }
    q2_status = "PASS" if all(y.values()) else "REJECT"
    refs2: dict[str, Any] = {}
    tradfi_only = sim(TRADFI, Q2_FROM, Q2_TO)
    refs2["tradfi_only_4"] = {
        "full": calendar_view(tradfi_only, Q2_FROM, Q2_TO + DAY),
        **extras(tradfi_only),
    }
    for label, cost in (("B_etf_real_0.05pct", ETF_REAL_COST), ("B_kis_0.25pct", KIS_COST)):
        rr = sim((*CRYPTO, *TRADFI), Q2_FROM, Q2_TO, cost)
        v = calendar_view(rr, Q2_FROM, Q2_TO + DAY)
        refs2[label] = {k: v[k] for k in ("sharpe", "cagr", "mdd")}

    # 상관과 위기 구간 (보고만)
    wa = weekly(a, Q2_FROM, Q2_TO + DAY)
    sleeves = {s: sim([s], Q2_FROM, Q2_TO) for s in TRADFI}
    y2022 = (_ms("2022-01-01"), _ms("2023-01-01"))
    btc_hold = weekly_hold(crypto["BTCUSDT"], Q2_FROM, Q2_TO + DAY)
    correlation = {
        s: {
            "strategy_vs_A": corr(weekly(sleeves[s], Q2_FROM, Q2_TO + DAY), wa),
            "strategy_vs_A_2022": corr(weekly(sleeves[s], Q2_FROM, Q2_TO + DAY), wa, *y2022),
            "hold_vs_btc_hold": corr(weekly_hold(tradfi[s], Q2_FROM, Q2_TO + DAY), btc_hold),
        }
        for s in TRADFI
    }
    crash = (_ms("2020-02-19"), _ms("2020-03-23"))
    crisis = {
        "2020-02-19_to_2020-03-23": {
            "A": window_return(a, *crash),
            "B": window_return(b, *crash),
            **{f"{s}_strategy": window_return(sleeves[s], *crash) for s in TRADFI},
        },
    }

    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(CFG).items()},
        "data_sha256": digests,
        "audit": audit.get("pass"),
        "q1": q1,
        "q1_replicated": replicated,
        "q2": {**views, "criteria": y, "status": q2_status, "reference": refs2},
        "correlation_weekly": correlation,
        "crisis": crisis,
        "note": (
            "Binance TradFi 무기한 비용 참고 줄은 뺐다: 사용자가 실행 경로를 KIS 로 바꿨고"
            " 무기한 이력은 2025-12 이후뿐이다"
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Q1 재현: {len(replicated)}/4 {replicated}")
    for s in TRADFI:
        f = q1[s]["full"]
        c = q1[s]["criteria"]
        print(
            f"  {s}: X1 {'통과' if c['X1']['passed'] else '실패'} (하한 {f['lower_2_5se']:.5f})"
            f" · X2 {'통과' if c['X2']['passed'] else '실패'}"
            f" · 샤프 {f['sharpe']:.2f} · CAGR {f['cagr']:.1%} · MDD {f['mdd']:.1%}"
        )
    print(f"Q2: {q2_status} {y}")
    for name in ("A_btc_eth", "B_six"):
        f = views[name]["full"]
        print(f"  {name}: 샤프 {f['sharpe']:.2f} · CAGR {f['cagr']:.1%} · MDD {f['mdd']:.1%}")
    print(f"  기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("run",))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return run(args.output)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
