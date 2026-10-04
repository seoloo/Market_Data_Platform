"""문서 137 — GR: 업비트 현물 BTC·ETH 추세 켜짐 그리드 + 따라 올라가는 손절. 1회 판정.

사용 예::

    python -m scripts.run_grid_upbit hash   # 1분봉 zip 묶음 SHA-256 (판정 전 고정, 수익 계산 없음)
    python -m scripts.run_grid_upbit run    # 판정 (깨끗한 트리 · 해시 일치 · 판정 파일 없음)

자료: ``data/external/binance_vision/spot/{BTCUSDT,ETHUSDT}/1m/*.zip`` (업비트 원화 가격의 대리값).
기준은 문서 137 §5 그대로다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import zipfile
from array import array
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

from auto_trading.backtest.grid import (
    GridConfig,
    GridResult,
    MinuteBars,
    daily_closes,
    hold_trend,
    simulate_grid,
    trend_and_sigma,
)

REPO = Path(__file__).resolve().parent.parent
VISION = REPO / "data" / "external" / "binance_vision" / "spot"
OUTPUT = REPO / "reports" / "grid_upbit"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
SLEEVE = 5_000.0
EXPECTED_BUNDLE = "4c9895548b31c2c91c848eb6aed7e268a4b26e62607c207a4954548c0eac1801"
"""문서 137 §8 에 고정한 묶음 SHA-256 (``hash`` 로 구해 판정 전에 커밋)."""
_MICROS = 10**14


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


FROM, TO = _ms("2018-01-01"), _ms("2026-08-31")
HALVES = ((_ms("2018-01-01"), _ms("2022-01-01")), (_ms("2022-01-01"), _ms("2026-09-01")))
BASE = GridConfig()
TH_FEE = 0.0005


def _zips() -> list[Path]:
    return sorted(p for s in SYMBOLS for p in (VISION / s / "1m").glob(f"{s}-1m-*.zip"))


def bundle_hash() -> tuple[str, int]:
    parts = [hashlib.sha256(p.read_bytes()).hexdigest() for p in _zips()]
    return hashlib.sha256("".join(parts).encode()).hexdigest(), len(parts)


def load_minutes(symbol: str) -> MinuteBars:
    # array 로 담는다 — 종목당 약 460 만 줄이라 list[float] 이면 메모리가 몇 배로 든다
    t, o, h, lo, c = array("q"), array("d"), array("d"), array("d"), array("d")
    for path in sorted((VISION / symbol / "1m").glob(f"{symbol}-1m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            (name,) = archive.namelist()
            for line in archive.read(name).decode("ascii").splitlines():
                cells = line.split(",")
                if not cells[0].isdigit():
                    continue
                ms = int(cells[0])
                if ms > _MICROS:
                    ms //= 1000
                if t and ms <= t[-1]:
                    continue  # 겹치거나 거꾸로 된 줄은 버린다
                t.append(ms)
                o.append(float(cells[1]))
                h.append(float(cells[2]))
                lo.append(float(cells[3]))
                c.append(float(cells[4]))
    return MinuteBars(t, o, h, lo, c)


# ------------------------------------------------------------------ 통계


def combine(results: Sequence[GridResult]) -> tuple[list[int], list[float], list[float]]:
    days = results[0].days
    equity = [sum(r.equity[i] for r in results) for i in range(len(days))]
    exposure = [
        sum(r.exposure[i] * r.equity[i] for r in results) / equity[i] if equity[i] > 0 else 0.0
        for i in range(len(days))
    ]
    return days, equity, exposure


def stats(days: Sequence[int], equity: Sequence[float], start: float) -> dict[str, Any]:
    path = [start, *equity]
    rets = [path[i] / path[i - 1] - 1 for i in range(1, len(path))]
    mean, sd = statistics.fmean(rets), statistics.stdev(rets)
    peak, mdd = path[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    years = len(days) / 365.25
    year_end: dict[str, float] = {}
    for d, e in zip(days, equity, strict=True):
        year_end[_date(d)[:4]] = e
    yearly, prev = {}, start
    for y in sorted(year_end):
        yearly[y] = year_end[y] / prev - 1
        prev = year_end[y]
    return {
        "sharpe": mean / sd * math.sqrt(365) if sd > 0 else math.nan,
        "cagr": (equity[-1] / start) ** (1 / years) - 1 if equity[-1] > 0 else -1.0,
        "mdd": mdd,
        "vol": sd * math.sqrt(365),
        "final": equity[-1],
        "worst_year": min(yearly.items(), key=lambda kv: kv[1]),
        "yearly": yearly,
    }


def view(results: Sequence[GridResult]) -> dict[str, Any]:
    days, equity, exposure = combine(results)
    start = SLEEVE * len(results)
    out = {"full": stats(days, equity, start), "avg_exposure": statistics.fmean(exposure)}
    halves = []
    for lo, hi in HALVES:
        idx = [i for i, d in enumerate(days) if lo <= d < hi]
        first = idx[0]
        base = equity[first - 1] if first > 0 else start
        halves.append(stats([days[i] for i in idx], [equity[i] for i in idx], base))
    out["halves"] = halves
    return out


def grid_report(r: GridResult) -> dict[str, Any]:
    return {
        "activations": r.activations,
        "recenters": r.recenters,
        "stops": r.stops,
        "trend_exits": r.trend_exits,
        "round_trips": r.round_trips,
        "buys": r.buys,
        "sells": r.sells,
        "fees": r.fees,
        "realized_grid": r.realized_grid,
        "actual_pnl": r.equity[-1] - SLEEVE,
        "stop_pnl_mean": statistics.fmean(r.stop_pnl) if r.stop_pnl else None,
        "stop_pnl_worst": min(r.stop_pnl) if r.stop_pnl else None,
        "exit_pnl_sum": sum(r.exit_pnl),
        "avg_exposure": statistics.fmean(r.exposure),
        "alone": stats(r.days, r.equity, SLEEVE),
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
    digest, count = bundle_hash()
    if not EXPECTED_BUNDLE or digest != EXPECTED_BUNDLE:
        print(
            f"1분봉 묶음 해시가 문서 137 §8 과 다르다 ({count}개, {digest[:16]}…). 판정하지 않는다."
        )
        return 3
    variants: dict[str, list[GridResult]] = {k: [] for k in ("GR", "TH", "BH", "GA")}
    refs: dict[str, list[GridResult]] = {
        k: [] for k in ("GR_fee_0.25", "TH_fee_0.25", "GR_maker_0")
    }
    for symbol in SYMBOLS:
        bars = load_minutes(symbol)
        trend, sigma = trend_and_sigma(daily_closes(bars), BASE)
        kw: dict[str, Any] = {"from_ms": FROM, "to_ms": TO, "initial": SLEEVE}
        variants["GR"].append(simulate_grid(bars, trend, sigma, cfg=BASE, **kw))
        variants["TH"].append(hold_trend(bars, trend, fee=TH_FEE, **kw))
        variants["BH"].append(hold_trend(bars, None, fee=TH_FEE, **kw))
        ga = replace(BASE, use_trend=False, use_stop=False)
        variants["GA"].append(simulate_grid(bars, trend, sigma, cfg=ga, **kw))
        high = replace(BASE, grid_fee=0.0025, stop_fee=0.0025, exit_fee=0.0025)
        refs["GR_fee_0.25"].append(simulate_grid(bars, trend, sigma, cfg=high, **kw))
        refs["TH_fee_0.25"].append(hold_trend(bars, trend, fee=0.0025, **kw))
        refs["GR_maker_0"].append(
            simulate_grid(bars, trend, sigma, cfg=replace(BASE, grid_fee=0.0), **kw)
        )
        print(f"{symbol}: 1분봉 {len(bars.t):,}개 처리")
    views = {k: view(v) for k, v in variants.items()}
    gr, th, bh = views["GR"], views["TH"], views["BH"]
    criteria = {
        "G1": gr["full"]["sharpe"] > th["full"]["sharpe"],
        "G2": gr["full"]["mdd"] <= th["full"]["mdd"],
        "G3": all(
            a["sharpe"] > b["sharpe"] for a, b in zip(gr["halves"], th["halves"], strict=True)
        ),
        "G4": gr["full"]["sharpe"] > bh["full"]["sharpe"] and gr["full"]["cagr"] > 0,
    }
    status = "PASS" if all(criteria.values()) else "REJECT"
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "bundle_sha256": digest,
        "config": {k: str(v) for k, v in asdict(BASE).items()},
        "views": views,
        "criteria": criteria,
        "status": status,
        "per_symbol": {
            s: {
                "GR": grid_report(variants["GR"][i]),
                "GA": grid_report(variants["GA"][i]),
                "TH": stats(variants["TH"][i].days, variants["TH"][i].equity, SLEEVE),
                "BH": stats(variants["BH"][i].days, variants["BH"][i].equity, SLEEVE),
            }
            for i, s in enumerate(SYMBOLS)
        },
        "reference": {k: view(v)["full"] for k, v in refs.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"GR: {status} {criteria}")
    for k in ("GR", "TH", "BH", "GA"):
        f = views[k]["full"]
        print(
            f"  {k}: 샤프 {f['sharpe']:.2f} · CAGR {f['cagr']:.1%} · MDD {f['mdd']:.1%}"
            f" · 노출 {views[k]['avg_exposure']:.2f}"
        )
    print(f"  기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("hash", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "hash":
        digest, count = bundle_hash()
        print(f"zip {count}개 · 묶음 SHA-256 {digest}")
        return 0
    return run(args.output)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
