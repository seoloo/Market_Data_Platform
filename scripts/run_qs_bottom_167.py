"""문서 167 — QS '바닥 근처 나눠 사기'.

꺼진 동안 지표 다수가 바닥 근처라고 하면 모은 돈 일부를 미리 TQQQ 로 산다.

사용 예::

    python -m scripts.run_qs_bottom_167 fetch    # Yahoo ^NDX 일봉 · FRED VXO · VIX
    python -m scripts.run_qs_bottom_167 count    # 과매도 · 되돌기 지표가 켜진 날 수 (수익 없음)
    python -m scripts.run_qs_bottom_167 run      # 1 회 판정 → reports/qs_bottom_167/result.json

* 사전 등록: docs/research/167-qs-bottom-ladder.md. 틀은 120 · 124 · 165 · 166 과 같다
  (FRED 나스닥 100 · DTB3, 1987 ~ 1997 시작 10 년 창으로 고르고 2008 ~ 2016 시작 창으로 한 번 검증).
* 지표는 가중치 없이 **켜짐 개수만** 센다. 과매도(수준) 15 개 · 되돌기(전환) 10 개.
  바닥 근처 = 과매도 ≥ a · 되돌기 ≥ b.
* 나눠 사기는 QS 추세가 **꺼진 동안만**. 한 번에 그때 현금의 1/3(첫 번) · 1/2(두 번째),
  꺼짐 한 번에 최대 2 번, 20 거래일 간격. 산 뒤 아직 꺼짐인데 지수가 산 날보다 15% 더 내리면
  그 몫을 판다. 추세가 켜지면 나머지 현금을 지금처럼 TQQQ 로 넣고 몫을 합친다.
  꺼질 때 TQQQ 전부 판다(110 그대로).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_qs_confirm import _windows
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import (
    CONTRIBUTION,
    FEE,
    TAX,
    TQQQ_FEE_YEAR,
    Result,
    leveraged,
    simulate,
    trend_signals,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "qs_bottom"
WARN = REPO / "data" / "research" / "qs_warn"
OUT = REPO / "reports" / "qs_bottom_167"
TRAIN = ("1987-01", "1997-12")
TEST = ("2008-01", "2016-08")
GRID_A = (4, 6, 8)
GRID_B = (2, 3, 4)
TRANCHE_FRACTIONS = (1 / 3, 1 / 2)
TRANCHE_GAP = 20
TRANCHE_STOP = 0.85
TRAIN_GAIN = 1.03
TRAIN_FLOOR = 0.90
TEST_GAIN = 1.03
TEST_SHARE = 0.60
DD_SLACK = 0.05
OVERSOLD = (
    "RSI14<30",
    "Stoch%K14<20",
    "Williams%R14<-80",
    "CCI20<-100",
    "5일안_볼린저하단(20,2σ)아래",
    "200일SMA보다-10%",
    "50일EMA보다-8%",
    "252일고점대비-20%",
    "ROC20<-10%",
    "공포지수>30",
    "공포지수>=60일평균x1.5",
    "신용스프레드>=252일최저x1.3",
    "ATR14/종가>=252일중앙x1.5",
    "5일안_거래량>=50일평균x2",
    "MFI14<20",
)
TURNS = (
    "RSI강세다이버전스",
    "MACD<0이고_히스토그램3일상승",
    "Stoch%K가%D위로(20아래에서)",
    "10일EMA위로복귀",
    "공포지수20일고점대비-20%",
    "신용스프레드20일변화<0(스트레스중)",
    "직전3일고가돌파",
    "볼린저하단안으로복귀",
    "Williams%R이-80위로",
    "5일ROC>0(ROC20<-10%뒤)",
)


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENDX?period1=473385600&period2=1791331200&interval=1d"
    res = httpx.get(url, timeout=60.0, headers={"User-Agent": ua}).json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    with (DATA / "NDX.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "high", "low", "close", "volume"])
        for t, h, lo, c, v in zip(
            res["timestamp"], q["high"], q["low"], q["close"], q["volume"], strict=True
        ):
            if h and lo and c:
                w.writerow([datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d"), h, lo, c, v or 0])
    for sid in ("VXOCLS", "VIXCLS"):
        r = httpx.get(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=1986-01-01&coed=2026-10-07",
            timeout=120.0,
            headers={"User-Agent": "auto-trading-research"},
        )
        r.raise_for_status()
        (DATA / f"{sid}.csv").write_text(r.text, encoding="utf-8")
        time.sleep(0.5)
    print("fetched")


# ------------------------------------------------------------------ 지표 (순수, t 일까지의 값만)


def _sma(xs: Sequence[float], i: int, n: int) -> float | None:
    return sum(xs[i - n + 1 : i + 1]) / n if i + 1 >= n else None


def _ema_series(xs: Sequence[float], n: int) -> list[float]:
    a = 2 / (n + 1)
    out: list[float] = []
    for i, x in enumerate(xs):
        out.append(x if i == 0 else out[-1] + a * (x - out[-1]))
    return out


def _rsi_series(xs: Sequence[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None]
    up = dn = 0.0
    for i in range(1, len(xs)):
        ch = xs[i] - xs[i - 1]
        g, lo = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            up += g / n
            dn += lo / n
            out.append(None if i < n else (100 - 100 / (1 + up / dn) if dn else 100.0))
        else:
            up = (up * (n - 1) + g) / n
            dn = (dn * (n - 1) + lo) / n
            out.append(100 - 100 / (1 + up / dn) if dn else 100.0)
    return out


def indicator_marks(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float],
    fear: Sequence[float | None],
    spread: Sequence[float | None],
) -> list[tuple[tuple[bool, ...], tuple[bool, ...]] | None]:
    """날마다 (과매도 15 개, 되돌기 10 개). 252 일 이력 전에는 None (순수)."""
    n = len(close)
    rsi = _rsi_series(close)
    e12, e26 = _ema_series(close, 12), _ema_series(close, 26)
    macd = [a - b for a, b in zip(e12, e26, strict=True)]
    sig = _ema_series(macd, 9)
    hist = [m - s for m, s in zip(macd, sig, strict=True)]
    e10, e50 = _ema_series(close, 10), _ema_series(close, 50)
    tr = [high[0] - low[0]] + [
        max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        for i in range(1, n)
    ]
    atr = _ema_series(tr, 27)  # Wilder 14 ≈ EMA 27
    k_line: list[float] = []
    for i in range(n):
        hh, ll = max(high[max(0, i - 13) : i + 1]), min(low[max(0, i - 13) : i + 1])
        k_line.append(100 * (close[i] - ll) / (hh - ll) if hh > ll else 50.0)
    d_line = [statistics.fmean(k_line[max(0, i - 2) : i + 1]) for i in range(n)]
    tp = [(h + lo + c) / 3 for h, lo, c in zip(high, low, close, strict=True)]
    out: list[tuple[tuple[bool, ...], tuple[bool, ...]] | None] = []
    last_f: float | None = None
    last_s: float | None = None
    fears: list[float] = []
    spreads: list[float] = []
    for i in range(n):
        last_f = fear[i] if fear[i] is not None else last_f
        last_s = spread[i] if spread[i] is not None else last_s
        if last_f is not None:
            fears.append(last_f)
        if last_s is not None:
            spreads.append(last_s)
        if (
            i < 252
            or rsi[i] is None
            or last_f is None
            or last_s is None
            or len(fears) < 60
            or len(spreads) < 252
        ):
            out.append(None)
            continue
        c = close[i]
        m20 = _sma(close, i, 20)
        assert m20 is not None
        lower = [
            (_sma(close, j, 20) or 0) - 2 * statistics.pstdev(close[j - 19 : j + 1])
            for j in range(i - 4, i + 1)
        ]
        below_band = any(close[j] < lower[k] for k, j in enumerate(range(i - 4, i + 1)))
        m200 = _sma(close, i, 200)
        assert m200 is not None
        tp20 = tp[i - 19 : i + 1]
        tp_mean = statistics.fmean(tp20)
        md = statistics.fmean(abs(x - tp_mean) for x in tp20)
        cci = (tp[i] - tp_mean) / (0.015 * md) if md else 0.0
        hh14, ll14 = max(high[i - 13 : i + 1]), min(low[i - 13 : i + 1])
        wr = -100 * (hh14 - c) / (hh14 - ll14) if hh14 > ll14 else -50.0
        wr_prev_hh, wr_prev_ll = max(high[i - 14 : i]), min(low[i - 14 : i])
        wr_prev = (
            -100 * (wr_prev_hh - close[i - 1]) / (wr_prev_hh - wr_prev_ll)
            if wr_prev_hh > wr_prev_ll
            else -50.0
        )
        roc20 = c / close[i - 20] - 1
        roc20_recent = min(close[j] / close[j - 20] - 1 for j in range(i - 9, i + 1))
        atr_ratio = atr[i] / c
        atr_med = statistics.median(atr[j] / close[j] for j in range(i - 251, i + 1))
        vol_spike = any(
            volume[j] >= 2 * statistics.fmean(volume[j - 50 : j]) > 0 for j in range(i - 4, i + 1)
        )
        pos = neg = 0.0
        for j in range(i - 13, i + 1):
            flow = tp[j] * volume[j]
            if tp[j] > tp[j - 1]:
                pos += flow
            elif tp[j] < tp[j - 1]:
                neg += flow
        mfi = 100 - 100 / (1 + pos / neg) if neg > 0 else 100.0
        f = fears[-1]
        f60 = statistics.fmean(fears[-60:])
        f20max = max(fears[-20:])
        s = spreads[-1]
        s252min = min(spreads[-252:])
        s20chg = s - spreads[-21] if len(spreads) > 20 else 0.0
        # RSI 강세 다이버전스: 최근 10 일 안 새 저점(직전 60 일 최저보다 낮음)인데 RSI 는 더 높음
        lo_recent = min(range(i - 9, i + 1), key=lambda j: close[j])
        lo_prior = min(range(i - 70, i - 10), key=lambda j: close[j])
        rr, rp = rsi[lo_recent], rsi[lo_prior]
        bull_div = (
            close[lo_recent] < close[lo_prior] and rr is not None and rp is not None and rr > rp + 3
        )
        oversold = (
            rsi[i] < 30,  # type: ignore[operator]
            k_line[i] < 20,
            wr < -80,
            cci < -100,
            below_band,
            c < m200 * 0.90,
            c < e50[i] * 0.92,
            c <= max(close[i - 251 : i + 1]) * 0.80,
            roc20 < -0.10,
            f > 30,
            f >= 1.5 * f60,
            s >= 1.3 * s252min,
            atr_ratio >= 1.5 * atr_med,
            vol_spike,
            mfi < 20,
        )
        turns = (
            bull_div,
            macd[i] < 0 and hist[i] > hist[i - 1] > hist[i - 2] > hist[i - 3],
            k_line[i] > d_line[i]
            and k_line[i - 1] <= d_line[i - 1]
            and min(k_line[i - 3 : i + 1]) < 20,
            c > e10[i] and close[i - 1] <= e10[i - 1],
            f <= 0.8 * f20max,
            s20chg < 0 and s >= 1.3 * s252min,
            c > max(high[i - 3 : i]),
            close[i - 1] < lower[-2] and c >= lower[-1],
            wr > -80 >= wr_prev,
            roc20_recent < -0.10 and c > close[i - 5],
        )
        out.append((oversold, turns))
    return out


# ----------------------------------------------- 나눠 사기 계산 (110 simulate 에 몫만 더함)


def simulate_ladder(
    days: Sequence[str],
    qqq: Mapping[str, float],
    lev: Mapping[str, float],
    signal: Mapping[str, bool | None],
    rf: Mapping[str, float],
    bottom: Mapping[str, bool],
    *,
    start: str,
    end: str,
) -> tuple[Result, list[dict[str, Any]]]:
    """110 의 QS 에 꺼짐 동안 '바닥 근처' 나눠 사기를 더한다 (순수).

    ``bottom`` 이 늘 거짓이면 QS 와 같다."""
    span = [d for d in days if start <= d <= end]
    index = {d: i for i, d in enumerate(days)}
    res = Result()
    q_sh = l_sh = cash = basis = year_gain = units = peak_nav = 0.0
    lots: list[dict[str, float]] = []  # 꺼짐 동안 산 몫 (수량 · 원가 · 산 날 지수)
    log: list[dict[str, Any]] = []
    used = 0
    last_buy = -(10**9)
    prev_state: bool | None = None
    last_month = ""
    for n, d in enumerate(span):
        i = index[d]
        value_before = q_sh * qqq[d] + l_sh * lev[d] + sum(x["qty"] for x in lots) * lev[d] + cash
        if d[:7] != last_month:
            last_month = d[:7]
            nav = value_before / units if units > 0 else 1.0
            units += CONTRIBUTION / nav
            cash += CONTRIBUTION
            res.invested += CONTRIBUTION
            res.flows.append((d, -CONTRIBUTION))
        state = bool(signal.get(days[i - 1])) if i > 0 else False
        if prev_state is None:
            prev_state = state
        if state and not prev_state:  # 꺼짐 → 켜짐: 나머지 현금 전부 TQQQ, 몫 합치기
            if cash > 0:
                res.fees += cash * FEE
                l_sh += cash * (1 - FEE) / lev[d]
                basis += cash
                cash = 0.0
                res.trades += 1
            for x in lots:
                l_sh += x["qty"]
                basis += x["cost"]
            lots = []
            used = 0
        elif not state and prev_state:  # 켜짐 → 꺼짐: TQQQ 전부 매도
            if l_sh > 0:
                gross = l_sh * lev[d]
                proceeds = gross * (1 - FEE)
                res.fees += gross * FEE
                year_gain += proceeds - basis
                res.round_trips.append(round((proceeds - basis) / basis, 4) if basis > 0 else 0.0)
                cash += proceeds
                l_sh = basis = 0.0
                res.trades += 1
            used = 0
        elif state and cash > 0:  # 켜짐: 입금은 지수
            res.fees += cash * FEE
            q_sh += cash * (1 - FEE) / qqq[d]
            cash = 0.0
        if not state:
            res.off_days += 1
            keep = []
            for x in lots:  # 몫 손절: 산 날 지수보다 15% 더 내림
                if qqq[d] <= x["index"] * TRANCHE_STOP:
                    gross = x["qty"] * lev[d]
                    proceeds = gross * (1 - FEE)
                    res.fees += gross * FEE
                    year_gain += proceeds - x["cost"]
                    cash += proceeds
                    res.trades += 1
                    log.append(
                        {"date": d, "action": "STOP", "ret": round(proceeds / x["cost"] - 1, 4)}
                    )
                else:
                    keep.append(x)
            lots = keep
            fire = i > 0 and bottom.get(days[i - 1], False)
            if fire and used < len(TRANCHE_FRACTIONS) and i - last_buy >= TRANCHE_GAP and cash > 0:
                amt = cash * TRANCHE_FRACTIONS[used]
                res.fees += amt * FEE
                lots.append({"qty": amt * (1 - FEE) / lev[d], "cost": amt, "index": qqq[d]})
                cash -= amt
                used += 1
                last_buy = i
                res.trades += 1
                log.append({"date": d, "action": f"BUY{used}", "amount": round(amt)})
        prev_state = state
        last_of_year = n == len(span) - 1 or span[n + 1][:4] != d[:4]
        if last_of_year:
            if year_gain > 0:
                t = TAX * year_gain
                cash -= t
                res.tax_paid += t
                res.taxes_by_year[d[:4]] = round(t, 2)
            year_gain = 0.0
        value = q_sh * qqq[d] + l_sh * lev[d] + sum(x["qty"] for x in lots) * lev[d] + cash
        nav = value / units if units > 0 else 1.0
        peak_nav = max(peak_nav, nav)
        if peak_nav > 0:
            res.max_drawdown = max(res.max_drawdown, 1 - nav / peak_nav)
        res.days += 1
        res.final = value
    res.flows.append((span[-1], res.final))
    return res, log


# ------------------------------------------------------------------ 실행


def _read_csv(path: Path, key: str, col: str) -> dict[str, float]:
    with path.open(encoding="utf-8") as f:
        return {r[key]: float(r[col]) for r in csv.DictReader(f) if r[col] not in ("", ".")}


def _inputs() -> dict[str, Any]:
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate_raw = _series("DTB3_full.csv", "DTB3")
    days = sorted(d for d in ndx if d <= "2026-08-31")
    rf: dict[str, float] = {}
    last = 0.0
    for d in sorted(set(days) | set(rate_raw)):
        last = rate_raw.get(d, last)
        rf[d] = last
    with (DATA / "NDX.csv").open(encoding="utf-8") as f:
        ohlc = {
            r["date"]: (float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
            for r in csv.DictReader(f)
        }
    vxo = _read_csv(DATA / "VXOCLS.csv", "observation_date", "VXOCLS")
    vix = _read_csv(DATA / "VIXCLS.csv", "observation_date", "VIXCLS")
    spread = _read_csv(WARN / "BAA10Y.csv", "observation_date", "BAA10Y")
    # 지표 날짜 = FRED 지수 날짜 중 ^NDX 일봉이 있는 날 (없으면 FRED 종가 · 거래량 0)
    hi, lo, cl, vo, fe, sp = [], [], [], [], [], []
    for d in days:
        h, low_, c, v = ohlc.get(d, (ndx[d], ndx[d], ndx[d], 0.0))
        hi.append(max(h, c))
        lo.append(min(low_, c))
        cl.append(ndx[d])
        vo.append(v)
        fe.append(vix.get(d) if d >= "1990-01-02" else vxo.get(d))
        sp.append(spread.get(d))
    marks = indicator_marks(hi, lo, cl, vo, fe, sp)
    return {"days": days, "ndx": ndx, "rf": rf, "marks": dict(zip(days, marks, strict=True))}


def bottom_days(marks: Mapping[str, Any], a: int, b: int) -> dict[str, bool]:
    return {d: (m is not None and sum(m[0]) >= a and sum(m[1]) >= b) for d, m in marks.items()}


def count() -> dict[str, Any]:
    x = _inputs()
    marks, days = x["marks"], x["days"]
    sig = trend_signals(days, x["ndx"])
    off = [d for d in days if sig[d] is False and marks[d] is not None]
    on_counts = dict.fromkeys((*OVERSOLD, *TURNS), 0)
    for d in off:
        for name, v in zip((*OVERSOLD, *TURNS), (*marks[d][0], *marks[d][1]), strict=True):
            on_counts[name] += int(v)
    grid = {}
    for a in GRID_A:
        for b in GRID_B:
            bd = bottom_days(marks, a, b)
            grid[f"a{a}_b{b}"] = {
                "days_1987_2007": sum(1 for d in off if "1987" <= d[:4] <= "2007" and bd[d]),
                "days_2008_2026": sum(1 for d in off if d[:4] >= "2008" and bd[d]),
            }
    return {
        "off_days_with_marks": len(off),
        "indicator_on_days_while_off": on_counts,
        "grid_signal_days": grid,
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    x = _inputs()
    days, ndx, rf, marks = x["days"], x["ndx"], x["rf"], x["marks"]
    sig = trend_signals(days, ndx)
    tqqq = leveraged(days, ndx, rf, 3.0, TQQQ_FEE_YEAR)

    def evaluate(
        bd: Mapping[str, bool] | None, wins: Sequence[tuple[str, str]]
    ) -> dict[str, float]:
        rel, dds, vs_a = [], [], []
        for s, e in wins:
            base = simulate(days, ndx, tqqq, sig, rf, mode="QS", start=s, end=e)
            a = simulate(days, ndx, tqqq, sig, rf, mode="A", start=s, end=e)
            if bd is None:
                r = base
            else:
                r, _ = simulate_ladder(days, ndx, tqqq, sig, rf, bd, start=s, end=e)
            rel.append(r.final / base.final)
            vs_a.append(r.final / a.final)
            dds.append(r.max_drawdown)
        return {
            "vs_QS_median": round(statistics.median(rel), 4),
            "vs_QS_min": round(min(rel), 4),
            "beats_QS_share": round(sum(v > 1 for v in rel) / len(rel), 4),
            "vs_A_median": round(statistics.median(vs_a), 4),
            "dd_median": round(statistics.median(dds), 4),
        }

    train_w, test_w = _windows(days, *TRAIN), _windows(days, *TEST)
    base_train, base_test = evaluate(None, train_w), evaluate(None, test_w)
    grid = {
        f"a{a}_b{b}": evaluate(bottom_days(marks, a, b), train_w) for a in GRID_A for b in GRID_B
    }
    ok = [k for k, v in grid.items() if v["dd_median"] <= base_train["dd_median"] + DD_SLACK]
    result: dict[str, Any] = {"base_train": base_train, "base_test": base_test, "train_grid": grid}
    if not ok:
        result |= {"verdict": "REJECT", "reason": "학습: 낙폭 조건을 지키는 조합 없음"}
    else:
        best = sorted(ok, key=lambda k: (-grid[k]["vs_QS_median"], grid[k]["dd_median"]))[0]
        g = grid[best]
        result["chosen"] = best
        if g["vs_QS_median"] < TRAIN_GAIN or g["vs_QS_min"] < TRAIN_FLOOR:
            result |= {
                "verdict": "REJECT",
                "reason": "학습 문턱 미달 (중앙 ≥ 1.03 · 최솟값 ≥ 0.90)",
            }
        else:
            a, b = (int(p[1:]) for p in best.split("_"))
            t = evaluate(bottom_days(marks, a, b), test_w)
            t1, t2 = t["vs_QS_median"] >= TEST_GAIN, t["beats_QS_share"] >= TEST_SHARE
            t3 = t["dd_median"] <= base_test["dd_median"] + DD_SLACK
            result |= {
                "test": t,
                "T1": t1,
                "T2": t2,
                "T3": t3,
                "verdict": "PASS" if t1 and t2 and t3 else "REJECT",
            }
    # 보고: 고른 (또는 학습 1 위) 조합의 전 구간 한 경로 · 산 날 · 그날 켜진 지표
    show = result.get("chosen") or sorted(grid, key=lambda k: -grid[k]["vs_QS_median"])[0]
    a, b = (int(p[1:]) for p in show.split("_"))
    bd = bottom_days(marks, a, b)
    paths = {}
    for name, (s, e) in {
        "1990-01~2007-12": ("1990-01-01", "2007-12-31"),
        "2008-06~2026-08": ("2008-06-01", "2026-08-31"),
    }.items():
        base = simulate(days, ndx, tqqq, sig, rf, mode="QS", start=s, end=e)
        lad, log = simulate_ladder(days, ndx, tqqq, sig, rf, bd, start=s, end=e)
        used_marks: Counter[str] = Counter()
        for ev in log:
            if ev["action"].startswith("BUY"):
                prev = days[days.index(ev["date"]) - 1]
                m = marks[prev]
                for nm, v in zip((*OVERSOLD, *TURNS), (*m[0], *m[1]), strict=True):
                    used_marks[nm] += int(v)
        paths[name] = {
            "QS": {"final": round(base.final), "dd": round(base.max_drawdown, 4)},
            "ladder": {"final": round(lad.final), "dd": round(lad.max_drawdown, 4)},
            "events": log,
            "marks_on_buy_days": used_marks.most_common(),
        }
    result["shown_combo"] = show
    result["paths"] = paths
    result["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    if mode == "fetch":
        fetch()
        return 0
    if mode not in ("count", "run"):
        raise SystemExit("usage: python -m scripts.run_qs_bottom_167 fetch|count|run")
    res = count() if mode == "count" else run()
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
