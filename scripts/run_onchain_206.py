"""문서 206 — BTC 거래소 흐름 충격 숏.

명령::

    python -m scripts.run_onchain_206 audit   # 공개 지연 · 라벨 소급(빈티지) · 사건 수 · 검정력

수익(사건 뒤 가격)은 계산하지 않는다 — 관문 미달로 DATA_INSUFFICIENT (문서 206 §6).

자료: ``data/research/onchain_206/`` (``scripts.fetch_coinmetrics_206``).
가격은 trading.db Binance 현물 BTCUSDT 1 시간봉 — 모든 날 3 일 수익 SD 만 검정력에 쓴다.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.research.exchange_flow import (
    event_days,
    forward_short,
    non_overlapping,
    series,
    zscores,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "onchain_206"
DB = REPO / "data" / "trading.db"
OUT = REPO / "reports" / "onchain_206"
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
ENTRY_HOUR = 6
SPLITS = {
    "TRAIN": ("2018-01-01", "2021-12-31"),
    "VALIDATION": ("2022-01-01", "2023-12-31"),
    "OOS": ("2024-01-01", "2026-08-31"),
}
KINDS = ("R", "I", "N")
WINDOW, THRESHOLD, HOLD = 60, 2.0, 3
THRESHOLDS = (1.5, 2.0, 2.5)
VINTAGES = ("20240525", "20260120", "20260921")


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def _f(v: str) -> float | None:
    return float(v) if v not in ("", None) else None


# ------------------------------------------------------------------ 자료


def load_flows(path: Path | None = None) -> tuple[list[date], list[dict[str, Any]]]:
    """날짜 순 행: in · out · sply · price · 상태."""
    path = path or DATA / "btc_daily.csv"
    rows: list[dict[str, Any]] = []
    days: list[date] = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if not r.get("time"):
                continue
            days.append(_d(r["time"]))
            rows.append(
                {
                    "in": _f(r.get("FlowInExNtv", "")),
                    "out": _f(r.get("FlowOutExNtv", "")),
                    "sply": _f(r.get("SplyExNtv", "")),
                    "price": _f(r.get("PriceUSD", "")),
                    "status": r.get("FlowInExNtv-status", ""),
                    "status_time": r.get("FlowInExNtv-status-time", ""),
                }
            )
    # 날짜가 하루씩 이어지는지
    for a, b in pairwise(days):
        if (b - a).days != 1:
            raise SystemExit(f"날짜가 끊긴다: {a} → {b}")
    return days, rows


def entry_prices(days: Sequence[date]) -> list[float | None]:
    """날짜 T 의 신호 → T+1 06:00 UTC 시간봉 시가 (Binance 현물). 없으면 None."""
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    with con:
        px = {
            int(t): float(o)
            for t, o in con.execute(
                "select open_ms, open from candles where exchange='binance_spot' and "
                "symbol='BTCUSDT' and timeframe='1h'"
            )
        }
    return [px.get(_ms(d + timedelta(days=1)) + ENTRY_HOUR * HOUR_MS) for d in days]


def in_split(days: Sequence[date], lo: str, hi: str) -> list[bool]:
    a, b = _d(lo), _d(hi)
    return [a <= d <= b for d in days]


# ------------------------------------------------------------------ 감사 (수익 없음)


def _vintage(day: str) -> dict[date, dict[str, float | None]]:
    path = DATA / "vintages" / f"btc_{day}.csv"
    out = {}
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[_d(r["time"])] = {
                "in": _f(r.get("FlowInExNtv", "")),
                "out": _f(r.get("FlowOutExNtv", "")),
                "sply": _f(r.get("SplyExNtv", "")),
            }
    return out


def _rel(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b)


def publication(rows: Sequence[dict[str, Any]], days: Sequence[date]) -> dict[str, Any]:
    status = Counter(r["status"] for r in rows if r["in"] is not None)
    delays = []
    for d, r in zip(days, rows, strict=True):
        if r["status"] == "flash" and r["status_time"]:
            st = datetime.fromisoformat(r["status_time"][:19]).replace(tzinfo=UTC)
            delays.append(
                (st - datetime(d.year, d.month, d.day, tzinfo=UTC)).total_seconds() / 3600 - 24
            )
    reviewed_lag = []
    for d, r in zip(days, rows, strict=True):
        if r["status"] in ("reviewed", "revised") and r["status_time"]:
            st = datetime.fromisoformat(r["status_time"][:19]).replace(tzinfo=UTC).date()
            reviewed_lag.append((st - d).days)
    return {
        "status_counts": dict(status),
        "flash_rows": len(delays),
        "flash_hours_after_day_end": {
            "min": round(min(delays), 2) if delays else None,
            "median": round(statistics.median(delays), 2) if delays else None,
            "max": round(max(delays), 2) if delays else None,
        },
        "reviewed_status_time_lag_days": {
            "min": min(reviewed_lag) if reviewed_lag else None,
            "median": statistics.median(reviewed_lag) if reviewed_lag else None,
            "max": max(reviewed_lag) if reviewed_lag else None,
        },
    }


def label_drift(days: Sequence[date], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """과거 공개본과 지금 값의 차이 · 충격 날짜 겹침 (라벨 소급의 크기)."""
    cur = dict(zip(days, rows, strict=True))
    out: dict[str, Any] = {}
    for v in VINTAGES:
        vin = _vintage(v)
        common = sorted(d for d in vin if d in cur and d >= date(2013, 1, 1))
        res: dict[str, Any] = {"days": len(common), "last": str(common[-1]) if common else None}
        for key in ("in", "out", "sply"):
            per_year: dict[str, Any] = {}
            for y in range(2013, 2027):
                diffs = [
                    x
                    for d in common
                    if d.year == y and (x := _rel(vin[d][key], cur[d][key])) is not None
                ]
                if diffs:
                    per_year[str(y)] = {
                        "median_abs_rel": round(statistics.median(diffs), 4),
                        "share_gt_1pct": round(sum(x > 0.01 for x in diffs) / len(diffs), 3),
                        "share_gt_10pct": round(sum(x > 0.10 for x in diffs) / len(diffs), 3),
                    }
            res[key] = per_year
        # 충격 날짜 겹침: 같은 날짜 구간에서 빈티지 값으로 만든 사건 vs 지금 값
        rows_v = [
            {"in": vin[d]["in"], "out": vin[d]["out"], "sply": vin[d]["sply"]} for d in common
        ]
        rows_c = [
            {"in": cur[d]["in"], "out": cur[d]["out"], "sply": cur[d]["sply"]} for d in common
        ]
        overlap: dict[str, Any] = {}
        for kind in KINDS:
            zv = zscores(series(rows_v, kind), WINDOW)
            zc = zscores(series(rows_c, kind), WINDOW)
            for y0, y1 in ((2013, 2017), (2018, 2021), (2022, 2023), (2024, 2026)):
                ev_v = {common[t] for t in event_days(zv, THRESHOLD) if y0 <= common[t].year <= y1}
                ev_c = {common[t] for t in event_days(zc, THRESHOLD) if y0 <= common[t].year <= y1}
                if ev_v or ev_c:
                    overlap[f"{kind} {y0}-{y1}"] = {
                        "vintage_events": len(ev_v),
                        "current_events": len(ev_c),
                        "jaccard": round(len(ev_v & ev_c) / len(ev_v | ev_c), 3),
                    }
        res["event_overlap_z2_w60"] = overlap
        out[v] = res
    return out


def reserve_vs_netflow(days: Sequence[date], rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    r = series(rows, "R")
    n = series(rows, "N")
    out: dict[str, Any] = {}
    for y0, y1 in ((2013, 2017), (2018, 2021), (2022, 2023), (2024, 2026)):
        pairs = [
            (a, b)
            for d, a, b in zip(days, r, n, strict=True)
            if a is not None and b is not None and y0 <= d.year <= y1
        ]
        if len(pairs) < 3:
            continue
        resid = [a - b for a, b in pairs]
        out[f"{y0}-{y1}"] = {
            "days": len(pairs),
            "corr_R_N": round(
                statistics.correlation([a for a, _ in pairs], [b for _, b in pairs]), 3
            ),
            "median_abs_residual_btc": round(statistics.median(abs(x) for x in resid), 1),
            "median_abs_netflow_btc": round(statistics.median(abs(b) for _, b in pairs), 1),
            "days_abs_residual_gt_10k": sum(abs(x) > 10_000 for x in resid),
        }
    return out


def counts(
    days: Sequence[date], rows: Sequence[dict[str, Any]], entry: Sequence[float | None]
) -> dict[str, Any]:
    """사건 · 겹치지 않는 거래 수와 무조건 3 일 숏 수익 SD (기준선만) → 최소 검출 효과."""
    out: dict[str, Any] = {}
    uncond = [forward_short(entry, t, HOLD) for t in range(len(days))]
    for name, (lo, hi) in SPLITS.items():
        mask = in_split(days, lo, hi)
        u = [x for x, m in zip(uncond, mask, strict=True) if m and x is not None]
        sd = statistics.stdev(u)
        row: dict[str, Any] = {"days": sum(mask), "sd_3d_short_uncond": round(sd, 4)}
        for kind in KINDS:
            z = zscores(series(rows, kind), WINDOW)
            for th in THRESHOLDS:
                ev = [t for t in event_days(z, th) if mask[t]]
                tr = non_overlapping(ev, entry, HOLD, 0.0)
                row[f"{kind} z>{th}"] = {"events": len(ev), "trades": len(tr)}
            ev = [t for t in event_days(z, THRESHOLD) if mask[t]]
            n_tr = len(non_overlapping(ev, entry, HOLD, 0.0))
            row[f"{kind} MDE_trades_pct"] = (
                round(2.8 * sd / math.sqrt(n_tr) * 100, 2) if n_tr else None
            )
        out[name] = row
    return out


def audit() -> dict[str, Any]:
    days, rows = load_flows()
    entry = entry_prices(days)
    return {
        "data_sha256": hashlib.sha256((DATA / "btc_daily.csv").read_bytes()).hexdigest(),
        "first_flow_day": str(
            next(d for d, r in zip(days, rows, strict=True) if r["in"] is not None)
        ),
        "last_day": str(days[-1]),
        "publication": publication(rows, days),
        "label_drift": label_drift(days, rows),
        "reserve_vs_netflow": reserve_vs_netflow(days, rows),
        "counts": counts(days, rows, entry),
        "entry_price_missing_2018_2026": sum(
            1
            for d, p in zip(days, entry, strict=True)
            if date(2018, 1, 1) <= d <= date(2026, 8, 31) and p is None
        ),
    }


def main(argv: list[str]) -> int:
    if argv != ["audit"]:
        raise SystemExit("usage: python -m scripts.run_onchain_206 audit")
    res = audit()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
