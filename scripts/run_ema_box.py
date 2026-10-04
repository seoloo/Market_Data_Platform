"""문서 139 — KB: EMA 10·20·50 정배열 + 박스 돌파, BTC·ETH 일봉·4시간봉 롱만. 1회 판정.

사용 예::

    python -m scripts.run_ema_box run   # 깨끗한 트리 · 1분봉 묶음 해시 일치 · 판정 파일 없음일 때만

자료는 GR(137)과 같은 Binance 현물 1분봉 zip 218 개. 기준은 문서 139 §5 그대로다.
"""

from __future__ import annotations

import argparse
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
from scripts.run_grid_upbit import EXPECTED_BUNDLE, VISION, bundle_hash

from auto_trading.backtest.ema_box import (
    Bars,
    BoxConfig,
    BoxResult,
    aggregate,
    hold_alignment,
    simulate_box,
)

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "ema_box"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
SLEEVE = 5_000.0
STEPS = {"1d": 86_400_000, "4h": 14_400_000}
_MICROS = 10**14


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


FROM, TO = _ms("2018-01-01"), _ms("2026-08-31")
HALVES = ((_ms("2018-01-01"), _ms("2022-01-01")), (_ms("2022-01-01"), _ms("2026-09-01")))
BASE = BoxConfig()


def load_bars(symbol: str) -> dict[str, Bars]:
    t, o, h, lo, c, v = (
        array("q"),
        array("d"),
        array("d"),
        array("d"),
        array("d"),
        array("d"),
    )
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
                    continue
                t.append(ms)
                o.append(float(cells[1]))
                h.append(float(cells[2]))
                lo.append(float(cells[3]))
                c.append(float(cells[4]))
                v.append(float(cells[5]))
    return {k: aggregate(t, o, h, lo, c, v, step) for k, step in STEPS.items()}


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
        "final": equity[-1],
        "worst_year": min(yearly.items(), key=lambda kv: kv[1]),
        "yearly": yearly,
    }


def view(results: Sequence[BoxResult]) -> dict[str, Any]:
    days = results[0].days
    equity = [sum(r.equity[i] for r in results) for i in range(len(days))]
    exposure = [
        sum(r.exposure[i] * r.equity[i] for r in results) / equity[i] if equity[i] else 0.0
        for i in range(len(days))
    ]
    start = SLEEVE * len(results)
    halves = []
    for lo, hi in HALVES:
        idx = [i for i, d in enumerate(days) if lo <= d < hi]
        base = equity[idx[0] - 1] if idx[0] > 0 else start
        halves.append(stats([days[i] for i in idx], [equity[i] for i in idx], base))
    return {
        "full": stats(days, equity, start),
        "halves": halves,
        "avg_exposure": statistics.fmean(exposure),
    }


def trade_report(r: BoxResult) -> dict[str, Any]:
    rs = r.r_multiples
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    return {
        "signals": r.signals,
        "entries": r.entries,
        "adds": r.adds,
        "missed": r.missed,
        "stops": r.stops,
        "half_exits": r.half_exits,
        "full_exits": r.full_exits,
        "closed_lots": len(rs),
        "win_rate": len(wins) / len(rs) if rs else None,
        "mean_r": statistics.fmean(rs) if rs else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "fees": r.fees,
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
    if digest != EXPECTED_BUNDLE:
        print(f"1분봉 묶음 해시가 문서 139 §3 과 다르다 ({count}개). 판정하지 않는다.")
        return 3
    bars = {s: load_bars(s) for s in SYMBOLS}
    kw: dict[str, Any] = {"from_ms": FROM, "to_ms": TO, "initial": SLEEVE}
    record: dict[str, Any] = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "bundle_sha256": digest,
        "config": {k: str(v) for k, v in asdict(BASE).items()},
        "timeframes": {},
    }
    for tf in STEPS:
        kb = [simulate_box(bars[s][tf], cfg=BASE, **kw) for s in SYMBOLS]
        ah = [hold_alignment(bars[s][tf], cfg=BASE, **kw) for s in SYMBOLS]
        bh = [hold_alignment(bars[s][tf], cfg=BASE, always=True, **kw) for s in SYMBOLS]
        v_kb, v_ah, v_bh = view(kb), view(ah), view(bh)
        all_r = [x for r in kb for x in r.r_multiples]
        entries = sum(r.entries + r.adds for r in kb)
        criteria = {
            "K1": v_kb["full"]["sharpe"] > v_ah["full"]["sharpe"],
            "K2": v_kb["full"]["mdd"] <= v_ah["full"]["mdd"],
            "K3": all(
                a["sharpe"] > b["sharpe"]
                for a, b in zip(v_kb["halves"], v_ah["halves"], strict=True)
            ),
            "K4": entries >= 30 and bool(all_r) and statistics.fmean(all_r) > 0,
        }
        refs = {}
        for label, fee in (("fee_0.1", 0.001), ("fee_0.05", 0.0005)):
            cfg = replace(BASE, fee=fee)
            refs[label] = {
                "KB": view([simulate_box(bars[s][tf], cfg=cfg, **kw) for s in SYMBOLS])["full"],
                "AH": view([hold_alignment(bars[s][tf], cfg=cfg, **kw) for s in SYMBOLS])["full"],
            }
        record["timeframes"][tf] = {
            "status": "PASS" if all(criteria.values()) else "REJECT",
            "criteria": criteria,
            "KB": v_kb,
            "AH": v_ah,
            "BH": v_bh,
            "trades": {s: trade_report(r) for s, r in zip(SYMBOLS, kb, strict=True)},
            "all_mean_r": statistics.fmean(all_r) if all_r else None,
            "entries_total": entries,
            "reference": refs,
        }
        print(f"[{tf}] {record['timeframes'][tf]['status']} {criteria}")
        for name, vw in (("KB", v_kb), ("AH", v_ah), ("BH", v_bh)):
            f = vw["full"]
            print(
                f"  {name}: 샤프 {f['sharpe']:.2f} · CAGR {f['cagr']:.1%} · MDD {f['mdd']:.1%}"
                f" · 노출 {vw['avg_exposure']:.2f}"
            )
        mean_r = record["timeframes"][tf]["all_mean_r"]
        print(f"  진입 {entries} · 평균 R {mean_r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"기록: {path}")
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
