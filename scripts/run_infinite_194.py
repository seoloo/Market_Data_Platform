# ruff: noqa: E501 — 결과 출력 문자열이 길다
"""문서 194 — 라오어 무한매수법 V2.2 (TQQQ · SOXL 실제 + 합성, 빗썸 BTC · ETH).

사용 예::

    python -m scripts.run_infinite_194 fetch   # Yahoo · 빗썸 일봉 → data/research/infinite_194/
    python -m scripts.run_infinite_194 run     # 1 회 판정 → reports/infinite_194/result.json

* 사전 등록: docs/research/194-infinite-buying-preregistration.md.
* 규칙: 40 분할 · 별% = 10 − T/2 · 1/4 LOC @ 별지점 + 3/4 지정가 @ 평단 +10% · 원금 소진 H(버티기) · Q(쿼터 손절).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_brake_183 import _rf_daily

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "infinite_194"
NDX_FILE = REPO / "data" / "research" / "brake_187" / "NDX.csv"
OUT = REPO / "reports" / "infinite_194"
END = "2026-08-31"
SPLITS = 40
TP = 0.10
EXHAUST_T = 39.0
COST_ETF, COST_COIN = 0.0015, 0.0010
FLOOR = -0.99
DOTCOM = ("2000-03-10", "2002-10-09")
NDX_DIV = 0.008
FEE_TQQQ, FEE_SOXL = 0.0084, 0.0095
YAHOO = {"TQQQ": "TQQQ", "SOXL": "SOXL", "SOX": "%5ESOX"}
BITHUMB = ("BTC", "ETH")


@dataclass(frozen=True, slots=True)
class Bar:
    d: str
    o: float
    h: float
    low: float
    c: float


# ------------------------------------------------------------------ 엔진 (순수)


def t_value(invested: float, unit: float) -> float:
    """T = 들어간 원가 ÷ 1 회분, 소수 둘째 자리에서 올림."""
    return math.ceil(round(invested / unit, 9) * 10) / 10


def run_infinite(bars: Sequence[Bar], *, cost: float, mode: str = "H") -> dict[str, Any]:
    """원금 1 로 V2.2 를 돌린다. ``mode`` H(버티기) · Q(쿼터 손절)."""
    unit = 1.0 / SPLITS
    cash, qty, basis = 1.0, 0.0, 0.0
    vals: list[float] = []
    expo: list[float] = []
    realized_days = exhausted = 0
    cycles: list[dict[str, Any]] = []
    cyc: dict[str, Any] | None = None

    def buy(amount: float, px: float) -> None:
        nonlocal cash, qty, basis
        spend = min(amount, cash)
        if spend <= 1e-12:
            return
        qty += spend * (1 - cost) / px
        basis += spend
        cash -= spend

    def sell(q: float, px: float) -> float:
        nonlocal cash, qty, basis
        q = min(q, qty)
        if q <= 1e-15:
            return 0.0
        avg = basis / qty
        proceeds = q * px * (1 - cost)
        cash += proceeds
        basis -= avg * q
        qty -= q
        if qty <= 1e-12:
            qty, basis = 0.0, 0.0
        return proceeds - avg * q

    for b in bars:
        pnl = 0.0
        if qty == 0:
            buy(unit, b.c)
            cyc = {"start": b.d, "pnl": 0.0, "max_t": 1.0}
        else:
            avg = basis / qty
            t = t_value(basis, unit)
            star_px = avg * (1 + (10 - t / 2) / 100)
            if cyc is not None:
                cyc["max_t"] = max(cyc["max_t"], t)
            if t >= EXHAUST_T:
                exhausted += 1
            if mode == "Q" and t >= EXHAUST_T:
                pnl += sell(qty / 4, b.c)
            else:
                q14, q34 = qty / 4, qty * 3 / 4
                tp_px = avg * (1 + TP)
                if b.h >= tp_px:
                    pnl += sell(q34, max(b.o, tp_px))
                if qty > 0 and b.c >= star_px:
                    pnl += sell(min(q14, qty), b.c)
                if qty > 0:
                    orders = [(unit / 2, avg), (unit / 2, star_px)] if t < 20 else [(unit, star_px)]
                    for amount, px in orders:
                        if b.c < px or (px == avg and b.c <= px):
                            buy(amount, b.c)
            if cyc is not None:
                cyc["pnl"] += pnl
            if qty == 0 and cyc is not None:
                cyc["end"] = b.d
                cycles.append(cyc)
                cyc = None
        if pnl > 0:
            realized_days += 1
        v = cash + qty * b.c
        vals.append(v)
        expo.append(qty * b.c / v if v > 0 else 0.0)
    return {
        "vals": vals,
        "days": [b.d for b in bars],
        "exposure": sum(expo) / len(expo),
        "realized_day_share": realized_days / len(bars),
        "exhausted_days": exhausted,
        "cycles": cycles,
        "open_cycle": cyc,
    }


def buy_hold(bars: Sequence[Bar], *, cost: float) -> dict[str, Any]:
    q = (1 - cost) / bars[0].c
    return {"vals": [q * b.c for b in bars], "days": [b.d for b in bars]}


def stats(vals: Sequence[float], days: Sequence[str]) -> dict[str, float]:
    yrs = (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[0])).days / 365.25
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak if peak > 0 else 0.0)
    cagr = (vals[-1] / vals[0]) ** (1 / yrs) - 1 if vals[-1] > 0 else -1.0
    return {
        "cagr": round(cagr, 4),
        "mdd": round(mdd, 4),
        "calmar": round(cagr / mdd, 3) if mdd > 0 else 0.0,
    }


def window_dd(
    vals: Sequence[float], days: Sequence[str], a: str, b: str
) -> dict[str, float] | None:
    pts = [v for d, v in zip(days, vals, strict=True) if a <= d <= b]
    if len(pts) < 2:
        return None
    peak, mdd = pts[0], 0.0
    for v in pts:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak if peak > 0 else 0.0)
    return {"ret": round(pts[-1] / pts[0] - 1, 4), "mdd": round(mdd, 4)}


def summarize(res: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(stats(res["vals"], res["days"]))
    cyc = res.get("cycles")
    if cyc is not None:
        lens = [
            (datetime.fromisoformat(c["end"]) - datetime.fromisoformat(c["start"])).days
            for c in cyc
        ]
        out.update(
            exposure=round(res["exposure"], 3),
            realized_day_share=round(res["realized_day_share"], 3),
            exhausted_days=res["exhausted_days"],
            cycles=len(cyc),
            win_cycles=round(sum(c["pnl"] > 0 for c in cyc) / len(cyc), 3) if cyc else None,
            longest_cycle_days=max(lens) if lens else None,
            open_cycle_since=(res["open_cycle"] or {}).get("start"),
        )
    dc = window_dd(res["vals"], res["days"], *DOTCOM)
    if dc:
        out["dotcom"] = dc
    return out


# ------------------------------------------------------------------ 자료


def _yahoo(sym: str) -> list[Bar]:
    import httpx

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        "?period1=-1325635200&period2=1791331200&interval=1d"
    )
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    res = httpx.get(url, timeout=60.0, headers={"User-Agent": ua}).json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    out = []
    for t, o, h, lo, c, a in zip(
        res["timestamp"], q["open"], q["high"], q["low"], q["close"], adj, strict=True
    ):
        if None in (o, h, lo, c, a) or not c:
            continue
        f = a / c
        d = (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=t)).strftime("%Y-%m-%d")
        out.append(Bar(d, o * f, h * f, lo * f, a))
    return out


def _bithumb(sym: str) -> list[Bar]:
    import httpx

    """빗썸 v1 일봉(00:00 KST 시작)을 200 개씩 거슬러 받는다 (공개 API, 키 없음)."""
    seen: dict[str, Bar] = {}
    to = ""
    for _ in range(100):
        params: dict[str, str | int] = {"market": f"KRW-{sym}", "count": 200}
        if to:
            params["to"] = to
        rows = httpx.get(
            "https://api.bithumb.com/v1/candles/days", params=params, timeout=60.0
        ).json()
        if not rows:
            break
        for r in rows:
            d = str(r["candle_date_time_kst"])[:10]
            seen[d] = Bar(
                d,
                float(r["opening_price"]),
                float(r["high_price"]),
                float(r["low_price"]),
                float(r["trade_price"]),
            )
        oldest = min(str(r["candle_date_time_kst"]) for r in rows)
        to = oldest.replace("T", " ")
        if len(rows) < 200:
            break
        time.sleep(0.15)
    return [seen[d] for d in sorted(seen)]


def _write(name: str, bars: Sequence[Bar]) -> None:
    with (DATA / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close"])
        for b in bars:
            w.writerow([b.d, b.o, b.h, b.low, b.c])


def fetch() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for name, sym in YAHOO.items():
        _write(name, _yahoo(sym))
        time.sleep(0.4)
    for sym in BITHUMB:
        _write(f"{sym}_KRW", _bithumb(sym))
        time.sleep(0.4)
    print("fetched")


def load(path: Path) -> list[Bar]:
    with path.open(encoding="utf-8") as f:
        rows = [
            Bar(r["date"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in csv.DictReader(f)
        ]
    seen: dict[str, Bar] = {}
    for b in rows:
        if b.d <= END and b.c > 0:
            seen[b.d] = b
    return [seen[d] for d in sorted(seen)]


def synth3x(bars: Sequence[Bar], fee: float, div: float) -> list[Bar]:
    """지수 일봉 → 가상 3 배 OHLC (전날 종가 대비 변화 × 3 − 금리 · 보수)."""
    rf = _rf_daily([b.d for b in bars])
    out = [Bar(bars[0].d, 100.0, 100.0, 100.0, 100.0)]
    for p, b in pairwise(bars):
        prev = out[-1].c
        carry = (2 * rf.get(p.d, 0.0) / 100 + fee) / 252 - div / 252 * 3

        def lev(x: float, prev: float = prev, base: float = p.c, carry: float = carry) -> float:
            return prev * (1 + max(3 * (x / base - 1) - carry, FLOOR))

        c = lev(b.c)
        o, h, lo = lev(b.o), lev(b.h), lev(b.low)
        out.append(Bar(b.d, o, max(h, o, c), min(lo, o, c), c))
    return out


def data_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.glob("*.csv")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ 판정


def judge(rows: Mapping[str, Any], synth: bool) -> dict[str, Any]:
    keys = ("real", "synth") if synth else ("real",)
    p1 = all(rows[k]["H"]["mdd"] < rows[k]["BH"]["mdd"] for k in keys)
    p2 = all(rows[k]["H"]["calmar"] >= rows[k]["BH"]["calmar"] for k in keys)
    out: dict[str, Any] = {"P1_mdd": p1, "P2_calmar": p2}
    ok = p1 and p2
    if synth:
        dc = rows["synth"]["H"].get("dotcom", {})
        p3 = bool(dc) and dc["mdd"] < 0.60
        out["P3_dotcom"] = p3
        ok = ok and p3
    out["pass"] = ok
    q_keys = all(
        rows[k]["Q"]["mdd"] < rows[k]["BH"]["mdd"]
        and rows[k]["Q"]["calmar"] >= rows[k]["BH"]["calmar"]
        for k in keys
    )
    out["Q_same_test"] = q_keys and (
        not synth or rows["synth"]["Q"].get("dotcom", {}).get("mdd", 1) < 0.60
    )
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    res: dict[str, Any] = {"data_sha256": data_hash()}
    sets: dict[str, dict[str, list[Bar]]] = {
        "TQQQ": {
            "real": load(DATA / "TQQQ.csv"),
            "synth": synth3x(load(NDX_FILE), FEE_TQQQ, NDX_DIV),
        },
        "SOXL": {
            "real": load(DATA / "SOXL.csv"),
            "synth": synth3x(load(DATA / "SOX.csv"), FEE_SOXL, 0.0),
        },
        "BTC": {"real": load(DATA / "BTC_KRW.csv")},
        "ETH": {"real": load(DATA / "ETH_KRW.csv")},
    }
    for name, by in sets.items():
        cost = COST_COIN if name in BITHUMB else COST_ETF
        rows: dict[str, Any] = {}
        for k, bars in by.items():
            rows[k] = {
                "period": [bars[0].d, bars[-1].d],
                "BH": summarize(buy_hold(bars, cost=cost)),
                "H": summarize(run_infinite(bars, cost=cost, mode="H")),
                "Q": summarize(run_infinite(bars, cost=cost, mode="Q")),
            }
        rows["verdict"] = judge(rows, "synth" in by)
        res[name] = rows
    res["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv == ["fetch"]:
        fetch()
        return 0
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_infinite_194 fetch|run")
    res = run()
    for name in ("TQQQ", "SOXL", "BTC", "ETH"):
        for k in ("real", "synth"):
            if k not in res[name]:
                continue
            r = res[name][k]
            for m in ("BH", "H", "Q"):
                s = r[m]
                extra = (
                    f" 노출 {s['exposure']:.0%} 사이클 {s['cycles']} 승 {s['win_cycles']} 최장 {s['longest_cycle_days']}일"
                    f" 소진일 {s['exhausted_days']} 실현일 {s['realized_day_share']:.0%}"
                    if "cycles" in s
                    else ""
                )
                dc = f" 닷컴 {s['dotcom']}" if "dotcom" in s else ""
                sys.stdout.write(
                    f"{name} {k} {r['period'][0]}~ {m:2s} CAGR {s['cagr'] * 100:6.1f} MDD {s['mdd'] * 100:5.1f}"
                    f" Calmar {s['calmar']:5.2f}{extra}{dc}\n"
                )
        sys.stdout.write(f"{name} 판정 {json.dumps(res[name]['verdict'], ensure_ascii=False)}\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
