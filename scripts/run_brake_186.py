"""문서 186 — T 브레이크 재매수를 RSI · MACD · 50 일선으로, 처음 쓰는 시장 13 개로 판정.

사용 예::

    python -m scripts.run_brake_186 fetch   # Yahoo 일간 종가 → data/research/brake_186/
    python -m scripts.run_brake_186 run     # 1 회 판정 → reports/brake_186/result.json

* 사전 등록: docs/research/186-t-brake-indicator-rebuy-preregistration.md.
* 가상 3 배 · 금리 · 하루 손실 하한 · 추세 · 비용 · 시점은 185 와 같다.
* 매도도 T0 와 같고 재매수 신호만 바꾼다.
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.run_brake_183 import STAGE1, STAGE2, signals
from scripts.run_brake_185 import COST, P_MDD, _rates, lev3, rf_daily

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "brake_186"
OUT = REPO / "reports" / "brake_186"
MARKETS = {
    "MXX": "^MXX",
    "MERV": "^MERV",
    "JKSE": "^JKSE",
    "KLSE": "^KLSE",
    "STI": "^STI",
    "SSE": "000001.SS",
    "NZ50": "^NZ50",
    "SSMI": "^SSMI",
    "AEX": "^AEX",
    "IBEX": "^IBEX",
    "BFX": "^BFX",
    "ATX": "^ATX",
    "TA125": "^TA125.TA",
}
REBUYS = ("trend", "rsi", "macd", "ma50")
MIN_YEARS = 10


# ------------------------------------------------------------------ 지표 (순수)


def rsi_wilder(close: Sequence[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(close)
    if len(close) <= n:
        return out
    gains = [max(close[i] - close[i - 1], 0.0) for i in range(1, n + 1)]
    losses = [max(close[i - 1] - close[i], 0.0) for i in range(1, n + 1)]
    ag, al = sum(gains) / n, sum(losses) / n
    for i in range(n, len(close)):
        if i > n:
            ch = close[i] - close[i - 1]
            ag = (ag * (n - 1) + max(ch, 0.0)) / n
            al = (al * (n - 1) + max(-ch, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ema(xs: Sequence[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(out[-1] + k * (x - out[-1]))
    return out


def macd_above(close: Sequence[float]) -> list[bool | None]:
    line = [a - b for a, b in zip(ema(close, 12), ema(close, 26), strict=True)]
    sig = ema(line, 9)
    return [None if i < 35 else line[i] > sig[i] for i in range(len(close))]


def ma_above(close: Sequence[float], n: int = 50) -> list[bool | None]:
    out: list[bool | None] = [None] * len(close)
    s = 0.0
    for i, c in enumerate(close):
        s += c
        if i >= n:
            s -= close[i - n]
        if i >= n - 1:
            out[i] = c > s / n
    return out


def above_maps(days: Sequence[str], idx: Mapping[str, float]) -> dict[str, dict[str, bool | None]]:
    """재매수 신호마다 날짜 → '위' (True) · '아래' (False) · 모름 (None)."""
    close = [idx[d] for d in days]
    _, trend = signals(days, idx)
    rsi = rsi_wilder(close)
    return {
        "trend": dict(trend),
        "rsi": {d: (None if r is None else r > 50) for d, r in zip(days, rsi, strict=True)},
        "macd": dict(zip(days, macd_above(close), strict=True)),
        "ma50": dict(zip(days, ma_above(close), strict=True)),
    }


# ------------------------------------------------------------------ 모의 계좌 (순수)


def simulate(
    days: Sequence[str],
    lev: Mapping[str, float],
    start_ok: Mapping[str, bool | None],
    up: Mapping[str, bool | None] | None,
) -> dict[str, Any]:
    """가상 3 배 100%. ``up`` None 이면 브레이크 없음.

    재매수 = 판 뒤 '아래' 였다가 '위' 가 된 날.
    """
    i0 = next(i for i, d in enumerate(days) if start_ok.get(d) is not None) + 1
    q, cash = 1.0 / lev[days[i0 - 1]] * (1 - COST), 0.0
    stage, below_seen, peak, holding = 0, False, lev[days[i0 - 1]], False
    sales = rebuys = 0
    vals: list[float] = []
    for i in range(i0, len(days)):
        d, prev = days[i], days[i - 1]
        if up is not None and q > 0 and stage < 2:
            dd = 1 - lev[prev] / peak
            sell = q if dd >= STAGE2 else q / 2 if dd >= STAGE1 and stage == 0 else 0.0
            if sell > 0:
                if not holding:
                    below_seen, holding = False, True
                stage = 2 if dd >= STAGE2 else 1
                cash += sell * lev[d] * (1 - COST)
                q -= sell
                sales += 1
        if up is not None and holding:
            u = up.get(prev)
            if u is False:
                below_seen = True
            elif u and below_seen:
                q += cash * (1 - COST) / lev[d]
                cash, stage, below_seen, holding = 0.0, 0, False, False
                peak = lev[d]
                rebuys += 1
        vals.append(q * lev[d] + cash)
        peak = max(peak, lev[d])
    yrs = (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[i0])).days / 365.25
    top, mdd = vals[0], 0.0
    for v in vals:
        top = max(top, v)
        mdd = max(mdd, 1 - v / top)
    return {
        "cagr": round(vals[-1] ** (1 / yrs) - 1, 4),
        "mdd": round(mdd, 4),
        "sales": sales,
        "rebuys": rebuys,
        "period": [days[i0], days[-1]],
    }


def verdict(res: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    n = len(res)
    need = -(-2 * n // 3)
    out: dict[str, Any] = {"markets": n, "need_wins": need}
    for k in ("rsi", "macd", "ma50"):
        dc = [m[k]["cagr"] - m["trend"]["cagr"] for m in res.values()]
        dm = [m[k]["mdd"] - m["trend"]["mdd"] for m in res.values()]
        wins = sum(x >= 0 for x in dc)
        out[k] = {
            "wins": wins,
            "median_cagr_diff": round(statistics.median(dc), 4),
            "median_mdd_diff": round(statistics.median(dm), 4),
            "pass": wins >= need and statistics.median(dc) > 0 and statistics.median(dm) <= P_MDD,
        }
    passed = [k for k in ("rsi", "macd", "ma50") if out[k]["pass"]]
    out["choice"] = max(passed, key=lambda k: out[k]["median_cagr_diff"]) if passed else "T0"
    return out


# ------------------------------------------------------------------ 자료 · 실행


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    for name, sym in MARKETS.items():
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('^', '%5E')}"
            "?period1=-1325635200&period2=1791331200&interval=1d"
        )
        res = httpx.get(url, timeout=60.0, headers={"User-Agent": ua}).json()["chart"]["result"][0]
        closes = res["indicators"]["quote"][0]["close"]
        with (DATA / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            for t, c in zip(res["timestamp"], closes, strict=True):
                if c:
                    d = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=t)
                    w.writerow([d.strftime("%Y-%m-%d"), c])
        time.sleep(0.4)
    print("fetched")


def data_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.glob("*.csv")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _index(name: str) -> dict[str, float]:
    with (DATA / f"{name}.csv").open(encoding="utf-8") as f:
        rows = {r["date"]: float(r["close"]) for r in csv.DictReader(f) if r["close"]}
    return {d: v for d, v in rows.items() if v > 0}


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    long_m = _rates()
    res: dict[str, Any] = {}
    dropped: dict[str, str] = {}
    for name in MARKETS:
        path = DATA / f"{name}.csv"
        if not path.exists():
            dropped[name] = "자료 없음"
            continue
        idx = _index(name)
        days = sorted(idx)
        if (
            len(days) < 2
            or (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[0])).days
            < MIN_YEARS * 365
        ):
            dropped[name] = "10 년 미만"
            continue
        lev = lev3(days, idx, rf_daily(days, long_m))
        ups = above_maps(days, idx)
        row: dict[str, Any] = {"BH": simulate(days, lev, ups["trend"], None)}
        for k in REBUYS:
            row[k] = simulate(days, lev, ups["trend"], ups[k])
        res[name] = row
    full = {
        "markets": res,
        "dropped": dropped,
        "verdict": verdict(res),
        "data_sha256": data_hash(),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
    return full


def main(argv: list[str]) -> int:
    if argv == ["fetch"]:
        fetch()
        return 0
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_brake_186 fetch|run")
    res = run()
    for m, r in res["markets"].items():
        cells = " ".join(
            f"{k} {r[k]['cagr'] * 100:5.1f}/{r[k]['mdd'] * 100:4.0f}/{r[k]['rebuys']:3d}"
            for k in ("BH", *REBUYS)
        )
        sys.stdout.write(f"{m:6s} {r['trend']['period'][0][:4]}~ {cells}\n")
    sys.stdout.write(json.dumps(res["verdict"], ensure_ascii=False, indent=1) + "\n")
    sys.stdout.write(f"dropped {res['dropped']}\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
