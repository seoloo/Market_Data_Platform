"""문서 138 — PG: 업비트 USDT/원화 프리미엄 그리드. 자료 받기 · 해시 · 1회 판정.

사용 예::

    python -m scripts.run_usdt_grid fetch   # 업비트 KRW-USDT 1분봉 + FRED DEXKOUS (공개, 읽기만)
    python -m scripts.run_usdt_grid hash    # 두 파일 SHA-256 (판정 전 고정용, 수익 계산 없음)
    python -m scripts.run_usdt_grid run     # 판정 (깨끗한 트리 · 해시 일치 · 판정 파일 없음)

기준은 문서 138 §5 그대로다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import sys
import time
from array import array
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from scripts.run_a1_train import git_state

from auto_trading.backtest.grid import MinuteBars, hold_trend
from auto_trading.backtest.premium_grid import (
    PremiumGridConfig,
    PremiumGridResult,
    centers,
    simulate_premium_grid,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "upbit_usdt"
MINUTES = DATA / "KRW-USDT-1m.csv"
FX = DATA / "DEXKOUS.csv"
OUTPUT = REPO / "reports" / "usdt_grid"
LISTED = "2024-06-07"
FETCH_END = "2026-10-01T00:00:00"
"""받는 마지막 분 바로 다음 시각(UTC, 포함 안 함) — 2026-09-30 23:59 까지."""
INITIAL = 10_000_000.0
EXPECTED = {
    "minutes": "a7e510af5038e0e0b33a4bf220787236a693026e324187af4120f8120f43dad6",
    "fx": "d9de5fd0954777e88b01181b2ed26f54d16fb2172b06d42a9365f62861864918",
}
"""문서 138 §8 에 고정한 SHA-256 (``hash`` 로 구해 판정 전에 커밋)."""


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


FROM, TO = _ms("2024-07-08"), _ms("2026-09-30")
HALVES = ((_ms("2024-07-08"), _ms("2025-08-19")), (_ms("2025-08-19"), _ms("2026-10-01")))
BASE = PremiumGridConfig()
HURDLE = 0.025
MIN_TRIPS = 200


# ------------------------------------------------------------------ 자료


def fetch() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=20.0, headers={"User-Agent": "auto-trading-research"})
    rows: dict[int, tuple[float, float, float, float, float]] = {}
    to = FETCH_END
    floor = _ms(LISTED)
    calls = 0
    try:
        while True:
            for attempt in range(5):
                resp = client.get(
                    "https://api.upbit.com/v1/candles/minutes/1",
                    params={"market": "KRW-USDT", "count": 200, "to": to},
                )
                if resp.status_code == 429:
                    time.sleep(1.0 + attempt)
                    continue
                resp.raise_for_status()
                break
            page = resp.json()
            calls += 1
            if not page:
                break
            for r in page:
                ms = int(
                    datetime.strptime(r["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S")
                    .replace(tzinfo=UTC)
                    .timestamp()
                    * 1000
                )
                rows[ms] = (
                    float(r["opening_price"]),
                    float(r["high_price"]),
                    float(r["low_price"]),
                    float(r["trade_price"]),
                    float(r["candle_acc_trade_volume"]),
                )
            oldest = page[-1]["candle_date_time_utc"]
            if calls % 500 == 0:
                print(f"  {calls}번 · {len(rows):,}분 · {oldest}")
            if _ms(oldest[:10]) < floor or len(page) < 200:
                break
            to = oldest
            time.sleep(0.12)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(("open_ms", "open", "high", "low", "close", "volume"))
        for ms in sorted(rows):
            w.writerow((ms, *rows[ms]))
        MINUTES.write_text(buf.getvalue(), encoding="utf-8", newline="\n")
        print(
            f"KRW-USDT 1분봉 {len(rows):,}개 · {calls}번 요청"
            f" · {_date(min(rows))} ~ {_date(max(rows))}"
        )
        resp = client.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": "DEXKOUS", "cosd": "2024-05-01", "coed": "2026-09-30"},
            timeout=120.0,
        )
        resp.raise_for_status()
        fx_rows = [
            (r["observation_date"], r["DEXKOUS"])
            for r in csv.DictReader(io.StringIO(resp.text))
            if r["DEXKOUS"] not in ("", ".")
        ]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(("date", "krw_per_usd"))
        w.writerows(fx_rows)
        FX.write_text(buf.getvalue(), encoding="utf-8", newline="\n")
        print(f"DEXKOUS {len(fx_rows)}일 · {fx_rows[0][0]} ~ {fx_rows[-1][0]}")
    finally:
        client.close()
    return 0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_minutes() -> MinuteBars:
    t, o, h, lo, c = array("q"), array("d"), array("d"), array("d"), array("d")
    with MINUTES.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t.append(int(r["open_ms"]))
            o.append(float(r["open"]))
            h.append(float(r["high"]))
            lo.append(float(r["low"]))
            c.append(float(r["close"]))
    return MinuteBars(t, o, h, lo, c)


def load_fx() -> dict[str, float]:
    with FX.open(encoding="utf-8") as fh:
        return {r["date"]: float(r["krw_per_usd"]) for r in csv.DictReader(fh)}


# ------------------------------------------------------------------ 통계


def stats(days: Sequence[int], equity: Sequence[float], start: float) -> dict[str, Any]:
    path = [start, *equity]
    rets = [path[i] / path[i - 1] - 1 for i in range(1, len(path))]
    mean, sd = statistics.fmean(rets), statistics.stdev(rets)
    peak, mdd = path[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    years = len(days) / 365.25
    months: dict[str, float] = {}
    for d, e in zip(days, equity, strict=True):
        months[_date(d)[:7]] = e
    monthly, prev = {}, start
    for m in sorted(months):
        monthly[m] = months[m] / prev - 1
        prev = months[m]
    return {
        "total": equity[-1] / start - 1,
        "cagr": (equity[-1] / start) ** (1 / years) - 1,
        "mdd": mdd,
        "vol": sd * math.sqrt(365),
        "sharpe": mean / sd * math.sqrt(365) if sd > 0 else math.nan,
        "monthly": monthly,
    }


def window(r: Any, lo: int, hi: int) -> dict[str, Any]:
    idx = [i for i, d in enumerate(r.days) if lo <= d < hi]
    base = r.equity[idx[0] - 1] if idx[0] > 0 else INITIAL
    return stats([r.days[i] for i in idx], [r.equity[i] for i in idx], base)


def grid_report(r: PremiumGridResult) -> dict[str, Any]:
    return {
        "round_trips": r.round_trips,
        "trip_mean": statistics.fmean(r.trip_pnl) if r.trip_pnl else None,
        "realized_grid": r.realized_grid,
        "actual_pnl": r.equity[-1] - INITIAL,
        "stops": r.stops,
        "stop_pnl": r.stop_pnl,
        "buys": r.buys,
        "sells": r.sells,
        "recenters": r.recenters,
        "fees": r.fees,
        "daily_turnover": r.turnover / len(r.days) / INITIAL,
        "avg_exposure": statistics.fmean(r.exposure),
    }


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
    got = {"minutes": _sha(MINUTES), "fx": _sha(FX)}
    if not all(EXPECTED.values()) or got != EXPECTED:
        print(f"자료 해시가 문서 138 §8 과 다르다 {got}. 판정하지 않는다.")
        return 3
    bars, fx = load_minutes(), load_fx()
    center_by_day, premium = centers(bars, fx)
    kw: dict[str, Any] = {"from_ms": FROM, "to_ms": TO, "initial": INITIAL}
    pg = simulate_premium_grid(bars, center_by_day, cfg=BASE, **kw)
    pa = simulate_premium_grid(
        bars, center_by_day, cfg=replace(BASE, recenter_daily=False, use_stop=False), **kw
    )
    uh = hold_trend(bars, None, fee=BASE.fee, **kw)
    full = {k: stats(r.days, r.equity, INITIAL) for k, r in (("PG", pg), ("PA", pa), ("UH", uh))}
    halves = [window(pg, lo, hi) for lo, hi in HALVES]
    criteria = {
        "U1": full["PG"]["cagr"] > HURDLE,
        "U2": full["PG"]["mdd"] < full["UH"]["mdd"] and full["PG"]["vol"] < full["UH"]["vol"],
        "U3": all(h["total"] > 0 for h in halves),
        "U4": pg.round_trips >= MIN_TRIPS
        and bool(pg.trip_pnl)
        and statistics.fmean(pg.trip_pnl) > 0,
    }
    status = "PASS" if all(criteria.values()) else "REJECT"
    refs = {
        "through_2won": replace(BASE, through=1.0),
        "fee_0.139": replace(BASE, fee=0.00139, stop_fee=0.00139),
        "fee_0": replace(BASE, fee=0.0),
    }
    reference = {}
    for name, cfg in refs.items():
        rr = simulate_premium_grid(bars, center_by_day, cfg=cfg, **kw)
        reference[name] = {**stats(rr.days, rr.equity, INITIAL), "round_trips": rr.round_trips}
    pvals = [v for d, v in premium.items() if FROM <= d <= TO]
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "data_sha256": got,
        "config": {k: str(v) for k, v in asdict(BASE).items()},
        "full": full,
        "pg_halves": halves,
        "criteria": criteria,
        "status": status,
        "pg": grid_report(pg),
        "pa": grid_report(pa),
        "premium": {
            "mean": statistics.fmean(pvals),
            "sd": statistics.stdev(pvals),
            "min": min(pvals),
            "max": max(pvals),
        },
        "reference": reference,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"PG: {status} {criteria}")
    for k, f in full.items():
        print(f"  {k}: CAGR {f['cagr']:.2%} · MDD {f['mdd']:.2%} · 변동성 {f['vol']:.2%}")
    print(f"  왕복 {pg.round_trips} · 손절 {pg.stops} · 기록 {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("fetch", "hash", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "fetch":
        return fetch()
    if args.stage == "hash":
        print(json.dumps({"minutes": _sha(MINUTES), "fx": _sha(FX)}, indent=1))
        return 0
    return run(args.output)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
