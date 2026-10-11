# ruff: noqa: E501, N806, RUF007 — 한국어 설명이 길고, 수식 변수(X · L · A · B · C)는 대문자로 둔다
"""문서 215 — USDT.D 하락 + BTC.D 상승(A) vs BTC 현물 4 시간 상승(B) vs TOTAL 상승 + BTC.D 상승(C) → 24 시간 BTC 선물 롱. 후향 파일럿. 주문 없음.

자료: 사용자가 TradingView 에서 직접 내보낸 CRYPTOCAP 4 시간봉 CSV(`data/external/tradingview/`), 바이낸스 현물 1 시간봉(DB), 선물 1 분봉 · funding · mark(210 캐시).

사용::

    python -m scripts.run_dominance_215 audit   # 자료 품질 · 신호 구조 (손익 없음)
    python -m scripts.run_dominance_215 run     # 파일럿 한 번 → reports/dominance_215/
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
TV = REPO / "data" / "external" / "tradingview"
REPORT = REPO / "reports" / "dominance_215"
H4 = 4 * 3_600_000
DAY = 86_400_000
START = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
END = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)  # 이 시각 이후는 읽지 않는다(보존)
FILES = {
    "USDT.D": "CRYPTOCAP_USDT.D, 240.csv",
    "BTC.D": "CRYPTOCAP_BTC.D, 240.csv",
    "TOTAL": "CRYPTOCAP_TOTAL, 240.csv",
    "USDT": "CRYPTOCAP_USDT, 240.csv",
    "BTC": "CRYPTOCAP_BTC, 240.csv",
}


def load_tv(name: str) -> dict[int, float]:
    """봉 시작 ms → 종가. 2026-09-01 이후 행은 버린다(읽은 즉시 버림, 계산에 안 씀)."""
    out: dict[int, float] = {}
    with (TV / FILES[name]).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            t = int(row["time"]) * 1000
            if t + H4 > END:
                continue
            out[t] = float(row["close"])
    return out


def spot_btc_4h() -> dict[int, float]:
    """바이낸스 현물 BTCUSDT 1 시간봉 → 4 시간봉 종가(그 4 시간의 마지막 1 시간봉 종가). 4 개가 다 있어야."""
    db = sqlite3.connect(f"file:{REPO / 'data' / 'trading.db'}?mode=ro", uri=True)
    h = {
        int(t): float(c)
        for t, c in db.execute(
            "select open_ms, close from candles where exchange='binance_spot' and symbol='BTCUSDT' and timeframe='1h' and open_ms >= ? and open_ms < ?",
            (START - 2 * DAY, END),
        )
    }
    out: dict[int, float] = {}
    for t in range(START - 2 * DAY, END, H4):
        if all(t + k * 3_600_000 in h for k in range(4)):
            out[t] = h[t + 3 * 3_600_000]
    return out


@dataclass(frozen=True, slots=True)
class Bar:
    t: int  # 봉 시작 ms (완성 = t + 4h)
    usdt_d: float
    btc_d: float
    total: float
    usdt: float
    btc: float
    spot: float


def aligned() -> list[Bar]:
    d = {k: load_tv(k) for k in FILES}
    sp = spot_btc_4h()
    ts = sorted(
        set(d["USDT.D"])
        & set(d["BTC.D"])
        & set(d["TOTAL"])
        & set(d["USDT"])
        & set(d["BTC"])
        & set(sp)
    )
    return [
        Bar(t, d["USDT.D"][t], d["BTC.D"][t], d["TOTAL"][t], d["USDT"][t], d["BTC"][t], sp[t])
        for t in ts
        if t >= START - H4
    ]


def signals(bars: Sequence[Bar]) -> list[tuple[int, bool, bool, bool]]:
    """완성 봉 k(직전 봉 k−1 대비) → (판단 시각 = 봉 k 완성 ms, A, B, C). 봉이 4 시간 간격으로 이어져야 계산(빠지면 그 봉 신호 없음)."""
    out = []
    for prev, cur in zip(bars[:-1], bars[1:], strict=True):
        if cur.t - prev.t != H4:
            continue
        a = cur.usdt_d < prev.usdt_d and cur.btc_d > prev.btc_d
        b = cur.spot > prev.spot
        c = cur.total > prev.total and cur.btc_d > prev.btc_d
        out.append((cur.t + H4, a, b, c))
    return out


def audit() -> dict[str, Any]:
    raw = {k: load_tv(k) for k in FILES}
    res: dict[str, Any] = {"files": {}}
    for k, v in raw.items():
        ts = sorted(v)
        gaps = [(b - a) // H4 for a, b in zip(ts[:-1], ts[1:], strict=True) if b - a != H4]
        off_grid = sum(1 for t in ts if t % H4)
        lr = [math.log(v[b] / v[a]) for a, b in zip(ts[:-1], ts[1:], strict=True) if b - a == H4]
        sd = statistics.stdev(lr)
        big = [
            (datetime.fromtimestamp(b / 1000, UTC).isoformat(), round(x * 100, 2))
            for (a, b), x in zip(zip(ts[:-1], ts[1:], strict=True), lr, strict=False)
            if abs(x) > 6 * sd
        ][:8]
        res["files"][k] = {
            "first": datetime.fromtimestamp(ts[0] / 1000, UTC).isoformat(),
            "last_used": datetime.fromtimestamp(ts[-1] / 1000, UTC).isoformat(),
            "bars": len(ts),
            "gaps": len(gaps),
            "gap_sizes": sorted(set(gaps))[:10],
            "off_4h_grid": off_grid,
            "logret_sd_pct": round(sd * 100, 3),
            "jumps_gt_6sd": big,
        }
    bars = aligned()
    # 정의 대조: USDT.D ≈ USDT / TOTAL × 100, BTC.D ≈ BTC / TOTAL × 100 (같은 봉)
    eu = [b.usdt_d / (b.usdt / b.total * 100) - 1 for b in bars if b.t >= START]
    eb = [b.btc_d / (b.btc / b.total * 100) - 1 for b in bars if b.t >= START]
    res["definition_check"] = {
        "usdt_d_vs_ratio_rel_err_median_pct": round(statistics.median(abs(x) for x in eu) * 100, 4),
        "usdt_d_vs_ratio_rel_err_max_pct": round(max(abs(x) for x in eu) * 100, 4),
        "btc_d_vs_ratio_rel_err_median_pct": round(statistics.median(abs(x) for x in eb) * 100, 4),
        "btc_d_vs_ratio_rel_err_max_pct": round(max(abs(x) for x in eb) * 100, 4),
    }
    # BTC 시총 / 현물 가격 = 암시 유통량 (안정적이어야)
    supply = [b.btc / b.spot for b in bars if b.t >= START]
    res["btc_implied_supply"] = {
        "min": round(min(supply)),
        "max": round(max(supply)),
        "median": round(statistics.median(supply)),
    }
    # 신호 구조 (손익 없음)
    sg = [s for s in signals(bars) if START <= s[0] - H4 < END]
    n = len(sg)
    A = [s[1] for s in sg]
    C = [s[3] for s in sg]
    B = [s[2] for s in sg]
    a_only = sum(1 for x, y in zip(A, C, strict=True) if x and not y)
    c_only = sum(1 for x, y in zip(A, C, strict=True) if y and not x)
    both = sum(1 for x, y in zip(A, C, strict=True) if x and y)
    # 원인: Δln USDT.D = Δln USDT − Δln TOTAL — A 와 C 가 갈리는 봉에서 USDT 시총 변화가 TOTAL 변화보다 큰가
    pairs = list(zip(bars[:-1], bars[1:], strict=True))
    diffs = []
    for prev, cur in pairs:
        if cur.t - prev.t != H4 or not (START <= cur.t < END):
            continue
        du, dt = math.log(cur.usdt / prev.usdt), math.log(cur.total / prev.total)
        a = cur.usdt_d < prev.usdt_d and cur.btc_d > prev.btc_d
        c = cur.total > prev.total and cur.btc_d > prev.btc_d
        if a != c:
            diffs.append((abs(du) > abs(dt), du, dt))

    def onset(xs: list[bool]) -> int:
        return sum(1 for p, q in zip([False, *xs[:-1]], xs, strict=True) if q and not p)

    res["signals"] = {
        "bars": n,
        "A_on": sum(A),
        "B_on": sum(B),
        "C_on": sum(C),
        "A_onsets": onset(A),
        "B_onsets": onset(B),
        "C_onsets": onset(C),
        "A_and_C": both,
        "A_only": a_only,
        "C_only": c_only,
        "agreement_pct": round(sum(1 for x, y in zip(A, C, strict=True) if x == y) / n * 100, 2),
        "jaccard_on": round(both / (both + a_only + c_only), 3) if both + a_only + c_only else None,
        "disagree_where_usdt_move_gt_total_move": sum(1 for d in diffs if d[0]),
        "disagree_total": len(diffs),
        "median_abs_dln_usdt_pct": round(
            statistics.median(
                abs(math.log(c.usdt / p.usdt))
                for p, c in pairs
                if c.t - p.t == H4 and START <= c.t < END
            )
            * 100,
            4,
        ),
        "median_abs_dln_total_pct": round(
            statistics.median(
                abs(math.log(c.total / p.total))
                for p, c in pairs
                if c.t - p.t == H4 and START <= c.t < END
            )
            * 100,
            4,
        ),
    }
    return res


def main(argv: list[str]) -> int:
    if argv[:1] == ["audit"]:
        r = audit()
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / "audit.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    if argv[:1] == ["run"]:
        from scripts.run_dominance_215_pilot import run

        return run()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["Bar", "aligned", "date", "signals"]
