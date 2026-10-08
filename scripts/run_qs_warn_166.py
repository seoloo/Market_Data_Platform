"""문서 166 — QS 하락 경고 세 가지: 경고가 켜진 동안 TQQQ 몫을 3 배 → 1 배(지수)로.

사용 예::

    python -m scripts.run_qs_warn_166 fetch    # FRED BAA10Y · Yahoo 업종 ETF 9 개
    python -m scripts.run_qs_warn_166 count    # 경고가 켜진 날 비율 (수익 없음)
    python -m scripts.run_qs_warn_166 run      # 세 경고 1 회 판정 → reports/qs_warn_166/result.json

* 사전 등록: docs/research/166-qs-decline-warnings.md. 틀은 120 · 124 · 165 와 같다
  (FRED 나스닥 100 ·
  DTB3, 110 의 ``simulate`` 그대로, TQQQ 자리에 넣는 가격 줄만 바꿈).
* 경고는 모두 **하나로 고정한 규칙**이다(고르는 후보 없음). t 일 종가까지의 자료로 정한
  경고를 다음 날
  배율에 쓴다. 배율이 바뀌는 날 |ΔL| × 0.25%.
  - CREDIT: BAA10Y(무디스 Baa − 10 년 국채)의 20 일 평균 > 200 일 평균 — 신용 위험이 커지는 중.
  - BREADTH: 업종 SPDR 9 개 중 200 일선 위가 4 개 이하 — 시장 폭이 무너짐(1999-10 부터).
  - VOTE: 나스닥 100 종가로 만든 약세 표시 10 개 중 6 개 이상(사용자 제안, 엘리엇 데모의 '10 중 6').
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_qs_confirm import _windows
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import FEE, TQQQ_FEE_YEAR, leveraged, simulate, trend_signals

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "qs_warn"
OUT = REPO / "reports" / "qs_warn_166"
SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
TRAIN = ("1987-01", "1997-12")
BREADTH_TRAIN = ("1999-12-01", "2007-12-31")
TEST = ("2008-01", "2016-08")
KEEP = 0.95
DD_GAIN = 0.05
SHARE_SLACK = 0.05
VOTE_K = 6


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    r = httpx.get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAA10Y&cosd=1986-01-01&coed=2026-10-07",
        timeout=120.0,
        headers={"User-Agent": "auto-trading-research"},
    )
    r.raise_for_status()
    (DATA / "BAA10Y.csv").write_text(r.text, encoding="utf-8")
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
    for s in SECTORS:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=900000000&period2=1791331200&interval=1d"
        res = httpx.get(url, timeout=60.0, headers={"User-Agent": ua}).json()["chart"]["result"][0]
        adj = res["indicators"]["adjclose"][0]["adjclose"]
        with (DATA / f"{s}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "adjclose"])
            for t, a in zip(res["timestamp"], adj, strict=True):
                if a:
                    w.writerow([datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d"), a])
        time.sleep(0.3)
    print("fetched")


# ------------------------------------------------------------------ 순수 계산


def _sma(xs: Sequence[float], i: int, n: int) -> float | None:
    return sum(xs[i - n + 1 : i + 1]) / n if i + 1 >= n else None


def credit_warning(days: Sequence[str], spread: Mapping[str, float]) -> dict[str, bool]:
    """BAA10Y 20 일 평균 > 200 일 평균 (그날까지의 값, 빈 날은 직전 값) (순수)."""
    out: dict[str, bool] = {}
    xs: list[float] = []
    last: float | None = None
    for d in days:
        last = spread.get(d, last)
        if last is None:
            out[d] = False
            continue
        xs.append(last)
        a, b = _sma(xs, len(xs) - 1, 20), _sma(xs, len(xs) - 1, 200)
        out[d] = bool(a is not None and b is not None and a > b)
    return out


def breadth_warning(
    days: Sequence[str], sectors: Mapping[str, Mapping[str, float]]
) -> dict[str, bool]:
    """업종 9 개 중 200 일선 위가 4 개 이하 (순수).

    9 개 모두 200 일 이력이 차기 전에는 경고 없음."""
    hist: dict[str, list[float]] = {s: [] for s in sectors}
    last: dict[str, float | None] = dict.fromkeys(sectors)
    out: dict[str, bool] = {}
    for d in days:
        above = ready = 0
        for s, px in sectors.items():
            last[s] = px.get(d, last[s])
            v = last[s]
            if v is None:
                continue
            hist[s].append(v)
            m = _sma(hist[s], len(hist[s]) - 1, 200)
            if m is not None:
                ready += 1
                above += v > m
        out[d] = ready == len(sectors) and above <= 4
    return out


def _ema(xs: Sequence[float], n: int) -> list[float]:
    a = 2 / (n + 1)
    out: list[float] = []
    for i, x in enumerate(xs):
        out.append(x if i == 0 else out[-1] + a * (x - out[-1]))
    return out


def _rsi(xs: Sequence[float], n: int = 14) -> list[float | None]:
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


def bear_marks(closes: Sequence[float]) -> list[int | None]:
    """종가만으로 만든 약세 표시 10 개의 개수 (그날까지, 252 일 이력 전에는 None) (순수).

    ① 종가 < 50 일선 ② 종가 < 200 일선 ③ 50 일선 < 200 일선 ④ MACD < 시그널 ⑤ MACD < 0
    ⑥ RSI14 < 50 ⑦ 63 일 수익 < 0 ⑧ 최근 5 일 안에 20 일 최저 종가 ⑨ 252 일 고점 대비 −10% 이하
    ⑩ 약세 다이버전스: 최근 20 일 안에 120 일 최고 종가(1% 안)를 다시 찍었는데 그때 RSI 가
       직전 최고점(그 전 100 일 안 최고 종가 날)의 RSI 보다 5 이상 낮다."""
    e12, e26 = _ema(closes, 12), _ema(closes, 26)
    macd = [a - b for a, b in zip(e12, e26, strict=True)]
    sig = _ema(macd, 9)
    rsi = _rsi(closes)
    out: list[int | None] = []
    for i in range(len(closes)):
        if i < 252 or rsi[i] is None:
            out.append(None)
            continue
        c = closes[i]
        m50, m200 = _sma(closes, i, 50), _sma(closes, i, 200)
        assert m50 is not None and m200 is not None
        r = rsi[i]
        assert r is not None
        lo20 = [min(closes[j - 19 : j + 1]) for j in range(i - 4, i + 1)]
        new_low = any(closes[j] <= lo20[k] for k, j in enumerate(range(i - 4, i + 1)))
        hi120 = max(closes[i - 119 : i + 1])
        div = False
        recent = max(range(i - 19, i + 1), key=lambda j: closes[j])
        if closes[recent] >= hi120 * 0.99:
            prior = max(range(recent - 120, recent - 19), key=lambda j: closes[j])
            rp, rr = rsi[prior], rsi[recent]
            div = rp is not None and rr is not None and rr < rp - 5
        marks = [
            c < m50,
            c < m200,
            m50 < m200,
            macd[i] < sig[i],
            macd[i] < 0,
            r < 50,
            c < closes[i - 63],
            new_low,
            c <= max(closes[i - 251 : i + 1]) * 0.90,
            div,
        ]
        out.append(sum(marks))
    return out


def warned_line(
    days: Sequence[str],
    base: Mapping[str, float],
    rf: Mapping[str, float],
    warn: Mapping[str, bool],
) -> tuple[dict[str, float], int]:
    """경고(전날 값)가 켜지면 1 배, 아니면 3 배인 가격 줄과 배율 바꾼 횟수 (순수).

    하루 비용은 110 의 가상 레버리지 식(차입 (L − 1) × 금리 + TQQQ 보수 × TQQQ 비중),
    바꾼 날 2 × 0.25%."""
    price = {days[0]: 100.0}
    cur, changes = 3.0, 0
    for prev, d in pairwise(days):
        want = 1.0 if warn.get(prev, False) else 3.0
        cost = 0.0
        if want != cur:
            cost = abs(want - cur) * FEE
            cur = want
            changes += 1
        r = base[d] / base[prev] - 1
        carry = ((cur - 1) * rf.get(prev, 0.0) / 100 + TQQQ_FEE_YEAR * (cur - 1) / 2) / 252
        price[d] = price[prev] * (1 + cur * r - carry) * (1 - cost)
    return price, changes


# ------------------------------------------------------------------ 실행


def _read(path: Path, col: str) -> dict[str, float]:
    with path.open(encoding="utf-8") as f:
        rows = csv.DictReader(f)
        return {r[next(iter(r))]: float(r[col]) for r in rows if r[col] not in ("", ".")}


def _inputs() -> tuple[list[str], dict[str, float], dict[str, float], dict[str, dict[str, bool]]]:
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate_raw = _series("DTB3_full.csv", "DTB3")
    days = sorted(d for d in ndx if d <= "2026-08-31")
    rf: dict[str, float] = {}
    last = 0.0
    for d in sorted(set(days) | set(rate_raw)):
        last = rate_raw.get(d, last)
        rf[d] = last
    spread = _read(DATA / "BAA10Y.csv", "BAA10Y")
    sectors = {s: _read(DATA / f"{s}.csv", "adjclose") for s in SECTORS}
    counts = bear_marks([ndx[d] for d in days])
    warns = {
        "CREDIT": credit_warning(days, spread),
        "BREADTH": breadth_warning(days, sectors),
        "VOTE": {d: (c is not None and c >= VOTE_K) for d, c in zip(days, counts, strict=True)},
    }
    return days, ndx, rf, warns


def count() -> dict[str, Any]:
    days, _ndx, _rf, warns = _inputs()
    out = {}
    for k, w in warns.items():
        on = [d for d in days if w[d]]
        spans = sum(1 for a, b in pairwise(days) if w[b] and not w[a])
        out[k] = {
            "first_on": on[0] if on else None,
            "share_on_1987_2007": round(
                sum(1 for d in days if "1987" <= d[:4] <= "2007" and w[d])
                / sum(1 for d in days if "1987" <= d[:4] <= "2007"),
                3,
            ),
            "share_on_2008_2026": round(
                sum(1 for d in days if d[:4] >= "2008" and w[d])
                / sum(1 for d in days if d[:4] >= "2008"),
                3,
            ),
            "episodes": spans,
        }
    return out


def _stats(ratios: Sequence[float], dds: Sequence[float]) -> dict[str, float]:
    return {
        "ratio_median": round(statistics.median(ratios), 4),
        "ratio_min": round(min(ratios), 4),
        "share_gt_A": round(sum(r > 1 for r in ratios) / len(ratios), 4),
        "dd_median": round(statistics.median(dds), 4),
        "dd_max": round(max(dds), 4),
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    days, ndx, rf, warns = _inputs()
    sig = trend_signals(days, ndx)
    lines = {"base": leveraged(days, ndx, rf, 3.0, TQQQ_FEE_YEAR)}
    changes = {}
    for k, w in warns.items():
        lines[k], changes[k] = warned_line(days, ndx, rf, w)

    def evaluate(key: str, wins: list[tuple[str, str]]) -> dict[str, float]:
        ratios, dds = [], []
        for s, e in wins:
            a = simulate(days, ndx, lines[key], sig, rf, mode="A", start=s, end=e)
            q = simulate(days, ndx, lines[key], sig, rf, mode="QS", start=s, end=e)
            ratios.append(q.final / a.final)
            dds.append(q.max_drawdown)
        return _stats(ratios, dds)

    def path(key: str, s: str, e: str) -> dict[str, float]:
        q = simulate(days, ndx, lines[key], sig, rf, mode="QS", start=s, end=e)
        return {"final": round(q.final), "dd": round(q.max_drawdown, 4)}

    def passes(base: Mapping[str, float], x: Mapping[str, float]) -> dict[str, bool]:
        return {
            "T1": x["ratio_median"] >= base["ratio_median"] * KEEP,
            "T2": base["dd_median"] - x["dd_median"] >= DD_GAIN,
            "T3": x["share_gt_A"] >= base["share_gt_A"] - SHARE_SLACK,
        }

    train_w, test_w = _windows(days, *TRAIN), _windows(days, *TEST)
    base_train, base_test = evaluate("base", train_w), evaluate("base", test_w)
    result: dict[str, Any] = {"base_train": base_train, "base_test": base_test, "changes": changes}
    for k in warns:
        r: dict[str, Any] = {}
        if k == "BREADTH":
            b, x = path("base", *BREADTH_TRAIN), path(k, *BREADTH_TRAIN)
            gate = x["final"] >= b["final"] * KEEP and b["dd"] - x["dd"] >= DD_GAIN
            r["train_path"] = {"base": b, "warn": x}
        else:
            x_train = evaluate(k, train_w)
            g = passes(base_train, x_train)
            gate = g["T1"] and g["T2"]
            r["train"] = x_train
        r["train_gate"] = gate
        if not gate:
            r["verdict"] = "REJECT"
            r["reason"] = "학습 문턱 미달"
        else:
            x_test = evaluate(k, test_w)
            t = passes(base_test, x_test)
            r |= {"test": x_test, **t, "verdict": "PASS" if all(t.values()) else "REJECT"}
        r["report"] = {
            name: {"base": path("base", s, e), "warn": path(k, s, e)}
            for name, (s, e) in {
                "2000-2002": ("2000-01-01", "2002-12-31"),
                "2008": ("2008-01-01", "2009-06-30"),
                "2022": ("2021-11-01", "2023-06-30"),
                "2008-06~": ("2008-06-01", "2026-08-31"),
            }.items()
        }
        result[k] = r
    a_full = simulate(
        days, ndx, lines["base"], sig, rf, mode="A", start="2008-06-01", end="2026-08-31"
    )
    result["A_2008-06~"] = {"final": round(a_full.final), "dd": round(a_full.max_drawdown, 4)}
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
        raise SystemExit("usage: python -m scripts.run_qs_warn_166 fetch|count|run")
    res = count() if mode == "count" else run()
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
