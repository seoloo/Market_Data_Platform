"""문서 267 — V 당일 변동성 돌파(18)를 선물 1 분 봉 실제 체결 · 메이커 청산으로 (참고 계산 1 회).

python -m scripts.run_v_exec_267

신호는 18 그대로(k = 0.5, 당일 종가 청산). 일봉은 선물 1 분 봉에서 만든다(00:00 UTC).
진입 = 처음 돌파한 분, 체결가 = max(위, 그 분 시가)(롱) — 갭 미끄럼을 실제로 잰다.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
import subprocess
import sys
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
UM = ROOT / "data" / "external" / "binance_vision" / "um"
OUT = ROOT / "reports" / "v_exec_267"
FILES = ("docs/research/267-v-breakout-real-execution.md", "scripts/run_v_exec_267.py")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
K = 0.5
HALF = "2023-01-01"
MIN_MINUTES = 1000
DAY_MS = 86_400_000
# 비용 줄 (bp): (진입 수수료, 진입 추가 미끄럼, 청산 수수료, 청산 미끄럼, 펀딩 넣기)
LINES: dict[str, tuple[float, float, float, float, bool]] = {
    "main": (5.0, 1.0, 2.0, 0.0, True),
    "orig24": (4.5, 7.5, 4.5, 7.5, False),
    "taker_exit": (5.0, 1.0, 5.0, 1.0, True),
    "slip5": (5.0, 5.0, 2.0, 0.0, True),
    "bnb": (4.5, 1.0, 1.8, 0.0, True),
}

Minute = tuple[int, float, float, float, float]  # open_ms, o, h, l, c


def minutes(sym: str) -> dict[str, list[Minute]]:
    days: dict[str, list[Minute]] = defaultdict(list)
    for p in sorted((UM / sym / "1m").glob("*.zip")):
        with zipfile.ZipFile(p) as zf:
            text = zf.read(zf.namelist()[0]).decode("ascii")
        for row in csv.reader(io.StringIO(text)):
            if not row or not row[0].isdigit():
                continue
            ms = int(row[0])
            d = datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")
            days[d].append((ms, float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    for v in days.values():
        v.sort()
    return days


def funding(sym: str) -> list[tuple[int, float]]:
    con = sqlite3.connect(ROOT / "data" / "trading.db")
    try:
        rows = con.execute(
            "select funding_ms, rate from funding_rates where exchange='binance_usdm' and symbol=?"
            " order by funding_ms",
            (sym,),
        ).fetchall()
    finally:
        con.close()
    return [(int(t), float(r)) for t, r in rows]


def trade(
    day: list[Minute], up: float, down: float
) -> tuple[int, float, float, int, float, bool] | None:
    """(방향, 체결가, 기준가, 진입 분 ms, 청산가, 같은 분 양쪽).

    같은 분에 위 · 아래 둘 다 닿으면 둘 다 계산해 나쁜 쪽."""
    close = day[-1][4]
    for ms, o, h, lo, _c in day:
        hit_up, hit_dn = h >= up, lo <= down
        if not (hit_up or hit_dn):
            continue
        cands = []
        if hit_up:
            cands.append((1, max(up, o), up))
        if hit_dn:
            cands.append((-1, min(down, o), down))
        side, fill, level = min(cands, key=lambda x: x[0] * (close / x[1] - 1))
        return side, fill, level, ms, close, len(cands) == 2
    return None


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise SystemExit("267 문서 · 스크립트를 먼저 커밋")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    trades: list[dict[str, Any]] = []
    skipped = 0
    for sym in SYMBOLS:
        days = minutes(sym)
        fund = funding(sym)
        first_fund = fund[0][0] if fund else None
        keys = sorted(days)
        for prev, d in pairwise(keys):
            pd, dd = days[prev], days[d]
            if len(pd) < MIN_MINUTES or len(dd) < MIN_MINUTES or d > "2026-08-31":
                skipped += 1
                continue
            rng = max(m[2] for m in pd) - min(m[3] for m in pd)
            o = dd[0][1]
            t = trade(dd, o + K * rng, o - K * rng)
            if t is None:
                continue
            side, fill, level, ms, close, both = t
            end_ms = dd[-1][0] + 60_000
            f_rate = sum(r for fm, r in fund if ms < fm <= end_ms)
            trades.append(
                {
                    "sym": sym,
                    "day": d,
                    "side": side,
                    "gross_bps": side * (close / fill - 1) * 1e4,
                    "gap_slip_bps": side * (fill / level - 1) * 1e4,
                    "funding_bps": -side * f_rate * 1e4,
                    "funding_known": first_fund is not None and ms >= first_fund,
                    "both": both,
                }
            )

    def stats(xs: list[float]) -> dict[str, Any]:
        n = len(xs)
        if n < 2:
            return {"n": n}
        m = sum(xs) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
        return {"n": n, "mean": m, "se": sd / math.sqrt(n), "lower": m - 2 * sd / math.sqrt(n)}

    def net(t: dict[str, Any], line: str) -> float:
        fe, se, fx, sx, use_f = LINES[line]
        return float(t["gross_bps"] - fe - se - fx - sx + (t["funding_bps"] if use_f else 0.0))

    out: dict[str, Any] = {"doc": FILES[0], "code_commit": commit, "trades": len(trades)}
    out["skipped_days"] = skipped
    out["lines"] = {}
    for line in LINES:
        allx = [net(t, line) for t in trades]
        out["lines"][line] = {
            "all": stats(allx),
            "h1": stats([net(t, line) for t in trades if t["day"] < HALF]),
            "h2": stats([net(t, line) for t in trades if t["day"] >= HALF]),
        }
    m = out["lines"]["main"]
    out["checks"] = {
        "G1": m["all"].get("lower", -1) > 0,
        "G2": m["h1"].get("mean", -1) > 0 and m["h2"].get("mean", -1) > 0,
    }
    out["verdict"] = "PAPER_후보" if all(out["checks"].values()) else "닫음"
    slips = sorted(t["gap_slip_bps"] for t in trades)
    out["report"] = {
        "gross": stats([t["gross_bps"] for t in trades]),
        "gap_slip": {
            "mean": sum(slips) / len(slips),
            "median": slips[len(slips) // 2],
            "p95": slips[int(0.95 * (len(slips) - 1))],
            "share_zero": sum(1 for s in slips if s == 0) / len(slips),
        },
        "funding_mean_bps": sum(t["funding_bps"] for t in trades) / len(trades),
        "funding_unknown": sum(1 for t in trades if not t["funding_known"]),
        "both_same_minute": sum(1 for t in trades if t["both"]),
        "long": stats([net(t, "main") for t in trades if t["side"] > 0]),
        "short": stats([net(t, "main") for t in trades if t["side"] < 0]),
        "by_sym": {s: stats([net(t, "main") for t in trades if t["sym"] == s]) for s in SYMBOLS},
        "by_year": {
            y: stats([net(t, "main") for t in trades if t["day"][:4] == y])
            for y in sorted({t["day"][:4] for t in trades})
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def main() -> int:
    out = run()
    print(json.dumps({k: out[k] for k in ("trades", "checks", "verdict")}, ensure_ascii=False))
    for line, v in out["lines"].items():
        a, h1, h2 = v["all"], v["h1"], v["h2"]
        head = f"{line:10} 평균 {a['mean']:+.1f}bp (하한 {a['lower']:+.1f})"
        print(f"{head} 앞 {h1['mean']:+.1f} 뒤 {h2['mean']:+.1f}")
    print(json.dumps(out["report"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
