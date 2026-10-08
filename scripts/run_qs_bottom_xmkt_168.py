"""문서 168 — 167 '바닥 근처 나눠 사기'를 안 본 지수 12 개에서 검증.

가격 지표만 쓰고, 같은 시기 하락은 한 묶음으로 센다.

사용 예::

    python -m scripts.run_qs_bottom_xmkt_168 fetch   # Yahoo 지수 일봉 12 개 (로그인 없음)
    python -m scripts.run_qs_bottom_xmkt_168 count   # 시장별 꺼짐 구간 · 묶음 수 (수익 없음)
    python -m scripts.run_qs_bottom_xmkt_168 run     # 1 회 판정 → reports/qs_bottom_xmkt_168/

* 사전 등록: docs/research/168-qs-bottom-ladder-cross-market.md.
* 나스닥 100 은 쓰지 않는다(167 에서 봤다). 지표는 종가만으로 계산되는 것(과매도 10 · 되돌기 8).
* 꺼짐 구간 하나의 성적 E = 그 구간에서 미리 산 몫의 (QS 재진입 날 가치 ÷ 산 돈 − 1) × 몫 비중 합.
  E > 0 이면 미리 산 쪽이 재진입 때 한꺼번에 사는 지금 QS 보다 낫다.
  재진입 뒤에는 둘이 같은 노출이다.
* 시장끼리 꺼짐 기간이 겹치면 한 묶음(같은 시기 하락)으로 보고, 묶음 평균 E 로 검정한다.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.run_qs_bottom_167 import (
    TRANCHE_FRACTIONS,
    TRANCHE_GAP,
    TRANCHE_STOP,
    _ema_series,
    _rsi_series,
    _sma,
)
from scripts.run_qs_switch import LOOKBACKS

from auto_trading.features.tsmom import trend_direction

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "qs_bottom_xmkt"
OUT = REPO / "reports" / "qs_bottom_xmkt_168"
MARKETS = {
    "^GSPC": "S&P 500",
    "^N225": "닛케이 225",
    "^FTSE": "FTSE 100",
    "^GSPTSE": "캐나다 TSX",
    "^GDAXI": "독일 DAX",
    "^HSI": "홍콩 항셍",
    "^FCHI": "프랑스 CAC 40",
    "^AXJO": "호주 ASX 200",
    "^BVSP": "브라질 보베스파",
    "^KS11": "한국 KOSPI",
    "^TWII": "대만 가권",
    "^BSESN": "인도 센섹스",
}
END = "2026-08-31"
RATE = 0.03
"""가상 3 배의 차입 금리(연, 모든 시장 · 시대 같음).

비교는 같은 시장 안 QS 대 나눠 사기라 영향이 작다."""
FEE_YEAR = 0.0084
FEE = 0.0025
GRID_A = (3, 4, 5)
GRID_B = (2, 3, 4)
"""167 의 a ∈ {4, 6, 8}/15 · b ∈ {2, 3, 4}/10 를 지표 수(10 · 8)에 맞춘 값."""
PASS_COMBOS = 7
SHARE_POS = 0.60
OVERSOLD_C = (
    "RSI14<30",
    "종가Stoch%K14<20",
    "종가Williams%R14<-80",
    "종가CCI20<-100",
    "5일안_볼린저하단아래",
    "200일SMA보다-10%",
    "50일EMA보다-8%",
    "252일고점대비-20%",
    "ROC20<-10%",
    "종가변동폭14/종가>=252일중앙x1.5",
)
TURNS_C = (
    "RSI강세다이버전스",
    "MACD<0이고_히스토그램3일상승",
    "종가Stoch%K가%D위로(20아래에서)",
    "10일EMA위로복귀",
    "직전3일종가최고돌파",
    "볼린저하단안으로복귀",
    "종가Williams%R이-80위로",
    "5일ROC>0(ROC20<-10%뒤)",
)


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    for sym in MARKETS:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('^', '%5E')}"
            "?period1=-1325635200&period2=1791331200&interval=1d"
        )
        res = httpx.get(url, timeout=60.0, headers={"User-Agent": ua}).json()["chart"]["result"][0]
        closes = res["indicators"]["quote"][0]["close"]
        with (DATA / f"{sym[1:]}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            for t, c in zip(res["timestamp"], closes, strict=True):
                if c:
                    d = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=t)
                    w.writerow([d.strftime("%Y-%m-%d"), c])
        time.sleep(0.4)
    print("fetched")


# ------------------------------------------------------------------ 순수 계산


def close_marks(close: Sequence[float]) -> list[tuple[tuple[bool, ...], tuple[bool, ...]] | None]:
    """167 지표 가운데 종가만으로 되는 것(과매도 10 · 되돌기 8) (순수).

    고가 · 저가 자리에 종가를 쓴다."""
    n = len(close)
    rsi = _rsi_series(close)
    e12, e26 = _ema_series(close, 12), _ema_series(close, 26)
    macd = [a - b for a, b in zip(e12, e26, strict=True)]
    sig = _ema_series(macd, 9)
    hist = [m - s for m, s in zip(macd, sig, strict=True)]
    e10, e50 = _ema_series(close, 10), _ema_series(close, 50)
    rng = [0.0] + [abs(close[i] - close[i - 1]) for i in range(1, n)]
    atr = _ema_series(rng, 27)
    k_line = []
    for i in range(n):
        w = close[max(0, i - 13) : i + 1]
        hh, ll = max(w), min(w)
        k_line.append(100 * (close[i] - ll) / (hh - ll) if hh > ll else 50.0)
    d_line = [statistics.fmean(k_line[max(0, i - 2) : i + 1]) for i in range(n)]
    out: list[tuple[tuple[bool, ...], tuple[bool, ...]] | None] = []
    for i in range(n):
        if i < 252 or rsi[i] is None:
            out.append(None)
            continue
        c = close[i]
        lower = [
            (_sma(close, j, 20) or 0.0) - 2 * statistics.pstdev(close[j - 19 : j + 1])
            for j in range(i - 4, i + 1)
        ]
        m200 = _sma(close, i, 200)
        assert m200 is not None
        w20 = close[i - 19 : i + 1]
        mean20 = statistics.fmean(w20)
        md = statistics.fmean(abs(x - mean20) for x in w20)
        cci = (c - mean20) / (0.015 * md) if md else 0.0
        w14, w14p = close[i - 13 : i + 1], close[i - 14 : i]
        wr = -100 * (max(w14) - c) / (max(w14) - min(w14)) if max(w14) > min(w14) else -50.0
        wrp = (
            -100 * (max(w14p) - close[i - 1]) / (max(w14p) - min(w14p))
            if max(w14p) > min(w14p)
            else -50.0
        )
        roc20 = c / close[i - 20] - 1
        roc20_recent = min(close[j] / close[j - 20] - 1 for j in range(i - 9, i + 1))
        atr_med = statistics.median(atr[j] / close[j] for j in range(i - 251, i + 1))
        lo_recent = min(range(i - 9, i + 1), key=lambda j: close[j])
        lo_prior = min(range(i - 70, i - 10), key=lambda j: close[j])
        rr, rp = rsi[lo_recent], rsi[lo_prior]
        r = rsi[i]
        assert r is not None
        oversold = (
            r < 30,
            k_line[i] < 20,
            wr < -80,
            cci < -100,
            any(close[j] < lower[k] for k, j in enumerate(range(i - 4, i + 1))),
            c < m200 * 0.90,
            c < e50[i] * 0.92,
            c <= max(close[i - 251 : i + 1]) * 0.80,
            roc20 < -0.10,
            atr[i] / c >= 1.5 * atr_med,
        )
        turns = (
            close[lo_recent] < close[lo_prior]
            and rr is not None
            and rp is not None
            and rr > rp + 3,
            macd[i] < 0 and hist[i] > hist[i - 1] > hist[i - 2] > hist[i - 3],
            k_line[i] > d_line[i]
            and k_line[i - 1] <= d_line[i - 1]
            and min(k_line[i - 3 : i + 1]) < 20,
            c > e10[i] and close[i - 1] <= e10[i - 1],
            c > max(close[i - 3 : i]),
            close[i - 1] < lower[-2] and c >= lower[-1],
            wr > -80 >= wrp,
            roc20_recent < -0.10 and c > close[i - 5],
        )
        out.append((oversold, turns))
    return out


def lev3(close: Sequence[float]) -> list[float]:
    """가상 3 배: 일간 3 × 수익 − (2 × 3% + 0.84%)/252 (순수)."""
    out = [100.0]
    for i in range(1, len(close)):
        out.append(out[-1] * (1 + 3 * (close[i] / close[i - 1] - 1) - (2 * RATE + FEE_YEAR) / 252))
    return out


def off_episodes(on: Sequence[bool | None]) -> list[tuple[int, int]]:
    """꺼짐 구간 (첫 꺼짐 날, 다시 켜진 날). 끝까지 꺼져 있으면 넣지 않는다 (순수)."""
    eps, start = [], None
    for i, s in enumerate(on):
        if s is False and start is None:
            start = i
        elif s is True and start is not None:
            eps.append((start, i))
            start = None
    return eps


def episode_edge(
    close: Sequence[float], lev: Sequence[float], bottom: Sequence[bool], start: int, reentry: int
) -> tuple[float, list[dict[str, Any]]]:
    """167 의 몫 규칙을 한 꺼짐 구간에 적용한 E (순수).

    신호는 전날 값을 오늘 종가에 실행한다. 몫 1 = 모은 돈의 1/3, 몫 2 = 남은 돈의 1/2(= 처음의 1/3).
    재진입 날(그날 종가에 QS 가 나머지를 산다) 가치로 잰다. 손절하면 사고팔기 수수료 0.25% 두 번."""
    weights = (TRANCHE_FRACTIONS[0], (1 - TRANCHE_FRACTIONS[0]) * TRANCHE_FRACTIONS[1])
    lots: list[dict[str, Any]] = []
    last = -(10**9)
    for i in range(start + 1, reentry):
        for lot in lots:
            if lot["exit"] is None and close[i] <= lot["index"] * TRANCHE_STOP:
                lot["exit"], lot["exit_i"] = lev[i], i
        if bottom[i - 1] and len(lots) < 2 and i - last >= TRANCHE_GAP:
            lots.append({"i": i, "buy": lev[i], "index": close[i], "exit": None, "exit_i": None})
            last = i
    e = 0.0
    log = []
    for k, lot in enumerate(lots):
        if lot["exit"] is not None:
            # 손절: 사고 팔고(수수료 두 번) 재진입 때 다시 산다 — 재진입 매수는 QS 와 같다
            ret = lot["exit"] / lot["buy"] * (1 - FEE) ** 2 - 1
        else:
            ret = (
                lev[reentry] / lot["buy"] - 1
            )  # 미리 한 번 사기 vs 재진입 때 한 번 사기 — 수수료 같음
        e += weights[k] * ret
        log.append({"buy_i": lot["i"], "stopped": lot["exit"] is not None, "ret": round(ret, 4)})
    return e, log


def cluster(intervals: Sequence[tuple[str, str, str]]) -> list[list[int]]:
    """(시장, 시작일, 끝일) 구간들을 겹침으로 묶는다 (순수).

    서로 겹치면 같은 묶음이고, 겹침이 이어지면 계속 묶는다."""
    order = sorted(range(len(intervals)), key=lambda k: intervals[k][1])
    groups: list[list[int]] = []
    end = ""
    for k in order:
        _m, s, e = intervals[k]
        if groups and s <= end:
            groups[-1].append(k)
            end = max(end, e)
        else:
            groups.append([k])
            end = e
    return groups


# ------------------------------------------------------------------ 실행


def _load(sym: str) -> tuple[list[str], list[float]]:
    with (DATA / f"{sym[1:]}.csv").open(encoding="utf-8") as f:
        rows = [(r["date"], float(r["close"])) for r in csv.DictReader(f) if r["date"] <= END]
    rows = [r for r in rows if r[1] > 0]
    return [d for d, _ in rows], [c for _, c in rows]


def _prepare() -> dict[str, Any]:
    out = {}
    for sym in MARKETS:
        days, close = _load(sym)
        from decimal import Decimal

        hist: list[Decimal] = []
        on: list[bool | None] = []
        for c in close:
            hist.append(Decimal(str(c)))
            v = trend_direction(hist, LOOKBACKS)
            on.append(None if v is None else v > 0)
        out[sym] = {
            "days": days,
            "close": close,
            "on": on,
            "marks": close_marks(close),
            "lev": lev3(close),
        }
    return out


def _bottom(marks: Sequence[Any], a: int, b: int) -> list[bool]:
    return [m is not None and sum(m[0]) >= a and sum(m[1]) >= b for m in marks]


def count() -> dict[str, Any]:
    prep = _prepare()
    res: dict[str, Any] = {}
    for sym, p in prep.items():
        eps = off_episodes(p["on"])
        big = [
            (s, e)
            for s, e in eps
            if min(p["close"][s:e]) <= max(p["close"][max(0, s - 252) : s + 1]) * 0.80
        ]
        res[MARKETS[sym]] = {"first": p["days"][0], "episodes": len(eps), "episodes_dd20": len(big)}
    return res


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    prep = _prepare()
    combos: dict[str, Any] = {}
    for a in GRID_A:
        for b in GRID_B:
            rows = []
            for sym, p in prep.items():
                bottom = _bottom(p["marks"], a, b)
                for s, e in off_episodes(p["on"]):
                    edge, log = episode_edge(p["close"], p["lev"], bottom, s, e)
                    if log:
                        rows.append(
                            {
                                "market": MARKETS[sym],
                                "start": p["days"][s],
                                "reentry": p["days"][e],
                                "edge": edge,
                                "buys": len(log),
                                "stops": sum(x["stopped"] for x in log),
                                "days_off": e - s,
                            }
                        )
            groups = cluster([(r["market"], r["start"], r["reentry"]) for r in rows])
            cl = []
            for g in groups:
                es = [rows[k]["edge"] for k in g]
                cl.append(
                    {
                        "from": min(rows[k]["start"] for k in g),
                        "to": max(rows[k]["reentry"] for k in g),
                        "markets": sorted({rows[k]["market"] for k in g}),
                        "episodes": len(g),
                        "edge": statistics.fmean(es),
                        "stops": sum(rows[k]["stops"] for k in g),
                        "buys": sum(rows[k]["buys"] for k in g),
                    }
                )
            ce = [c["edge"] for c in cl]
            m = statistics.fmean(ce)
            se = statistics.stdev(ce) / math.sqrt(len(ce)) if len(ce) > 1 else float("inf")
            share = sum(1 for x in ce if x > 0) / len(ce)
            combos[f"a{a}_b{b}"] = {
                "episodes_with_buys": len(rows),
                "clusters": len(cl),
                "cluster_mean_edge": round(m, 4),
                "se": round(se, 4),
                "lower": round(m - 2 * se, 4),
                "share_pos": round(share, 3),
                "pass": m - 2 * se > 0 and share >= SHARE_POS,
                "worst_clusters": sorted(
                    (
                        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()}
                        for c in cl
                    ),
                    key=lambda c: c["edge"],
                )[:5],
                "best_clusters": sorted(
                    (
                        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()}
                        for c in cl
                    ),
                    key=lambda c: -c["edge"],
                )[:5],
                "named": {
                    name: [
                        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items()}
                        for c in cl
                        if c["from"] <= hi and c["to"] >= lo
                    ]
                    for name, (lo, hi) in {
                        "1929~32": ("1929-09-01", "1932-12-31"),
                        "1973~74": ("1973-01-01", "1974-12-31"),
                        "1987": ("1987-09-01", "1988-03-31"),
                        "일본1990~2003": ("1990-01-01", "2003-06-30"),
                        "2000~02": ("2000-03-01", "2002-12-31"),
                        "2008~09": ("2008-01-01", "2009-06-30"),
                        "2020": ("2020-02-15", "2020-06-30"),
                        "2022": ("2022-01-01", "2022-12-31"),
                        "2025": ("2025-02-15", "2025-06-30"),
                    }.items()
                },
            }
    passed = sum(1 for v in combos.values() if v["pass"])
    result = {
        "combos": combos,
        "combos_passed": passed,
        "verdict": "HISTORICAL_SUPPORT" if passed >= PASS_COMBOS else "REJECT",
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    if mode == "fetch":
        fetch()
        return 0
    if mode not in ("count", "run"):
        raise SystemExit("usage: python -m scripts.run_qs_bottom_xmkt_168 fetch|count|run")
    res = count() if mode == "count" else run()
    if mode == "run":
        res = {k: v for k, v in res.items() if k != "combos"} | {
            "summary": {
                k: {
                    kk: v[kk]
                    for kk in ("clusters", "cluster_mean_edge", "se", "lower", "share_pos", "pass")
                }
                for k, v in res["combos"].items()
            }
        }
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
