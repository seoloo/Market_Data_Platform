"""문서 180 — 나스닥 100 + 배당주 섞기.

사용 예::

    python -m scripts.run_div_mix_180 run   # 1 회 판정 → reports/div_mix_180/result.json

* 사전 등록: docs/research/180-nasdaq-dividend-mix-preregistration.md.
* A(판정): 나스닥 100 총수익 + French 배당 수익률 상위 30%(시가총액 가중), 60/40 하나.
* B(기록): QQQ + 배당 ETF 11 개, 비율 여섯 가지. 판정 없음.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_multi_asset_176 import (
    EXPECTED_DATA_SHA,
    MDD_GAIN,
    Z,
    _month_end,
    _st,
    build_table,
    data_hash,
)
from scripts.run_static_mix_178 import _static

from auto_trading.research.multi_asset import sharpe_diff_z, stats

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "div_mix_180"
OUT = REPO / "reports" / "div_mix_180"
DATA_SHA = "6217d2c761e11c45ec3e5c347f3c5de96326344aa39743e27dea3c506da72cb0"
NDX_DIV = 0.008
QQQ_FROM = "1999-05"
"""등록문은 1999-04 — QQQ 상장 달(1999-03)을 빼면 첫 월 수익이 1999-05 라 한 달 늦춤(결과 전)."""
PRIMARY = {"NDXT": 0.6, "HI30": 0.4}
REPORT_A = {
    "80/20": {"NDXT": 0.8, "HI30": 0.2},
    "70/30": {"NDXT": 0.7, "HI30": 0.3},
    "50/50": {"NDXT": 0.5, "HI30": 0.5},
    "고배당만": {"HI30": 1.0},
}
NBG = {"NDXT": 0.6, "UST10": 0.2, "GOLD": 0.2}
DIV_ETFS = {
    "SCHD": "배당 성장 · Schwab US Dividend Equity (다우존스 US 배당 100)",
    "VIG": "배당 성장 · Vanguard Dividend Appreciation (10 년 연속 증배)",
    "DGRO": "배당 성장 · iShares Core Dividend Growth (5 년 연속 증배)",
    "NOBL": "배당 성장 · ProShares S&P 500 배당 귀족 (25 년 연속 증배)",
    "SDY": "배당 성장 · SPDR S&P 고배당 귀족 (20 년 연속 증배)",
    "VYM": "고배당 · Vanguard High Dividend Yield (고배당 절반)",
    "HDV": "고배당 · iShares Core High Dividend (재무 건전 고배당 75)",
    "DVY": "고배당 · iShares Select Dividend (고배당 100)",
    "SPYD": "고배당 · SPDR S&P 500 고배당 80",
    "JEPI": "커버드콜 인컴 · JPMorgan Equity Premium Income",
    "458730.KS": "국내 상장 · TIGER 미국배당다우존스 (SCHD 지수 복제, 원화)",
}
RATIOS = {"100/0": 1.0, "80/20": 0.8, "70/30": 0.7, "60/40": 0.6, "50/50": 0.5, "0/100": 0.0}
COMMON = ("2016-01", "2026-09")
CRISES = {
    "2008 금융위기": ("2007-11", "2009-02"),
    "2020 코로나": ("2020-02", "2020-03"),
    "2022 금리 인상": ("2022-01", "2022-12"),
}
COST = 0.0015


def own_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.glob("*")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def french_hi30() -> dict[str, float]:
    """French D/P 파일 → 월 → 시가총액 가중 '배당 수익률 상위 30%' 수익."""
    with zipfile.ZipFile(DATA / "french_dp.zip") as z:
        text = z.read(z.namelist()[0]).decode("latin1")
    lines = text.splitlines()
    i = next(k for k, line in enumerate(lines) if "Value Weight Returns -- Monthly" in line)
    head = [c.strip() for c in lines[i + 1].split(",")]
    col = head.index("Hi 30")
    out: dict[str, float] = {}
    for line in lines[i + 2 :]:
        parts = [c.strip() for c in line.split(",")]
        if len(parts) < 2 or not parts[0].isdigit():
            break
        v = float(parts[col])
        if v > -99:
            out[f"{parts[0][:4]}-{parts[0][4:6]}"] = v / 100
    return out


def _etf(sym: str) -> dict[str, float]:
    with (DATA / f"{sym}.csv").open(encoding="utf-8") as f:
        daily = {r["date"]: float(r["adjclose"]) for r in csv.DictReader(f)}
    me = _month_end(daily)
    ms = sorted(me)[1:]  # 상장 달은 뺀다(달 중간 시작)
    return {b: me[b] / me[a] - 1 for a, b in pairwise(ms)}


def ndx_total(
    table: Mapping[str, Mapping[str, float]], qqq: Mapping[str, float], div: float
) -> dict[str, float]:
    """나스닥 100 총수익: QQQ_FROM 전은 가격 지수 + 배당 가정, 그 뒤는 QQQ."""
    out = {m: row["NDX"] + div / 12 for m, row in table.items() if "NDX" in row and m < QQQ_FROM}
    out.update({m: r for m, r in qqq.items() if m >= QQQ_FROM})
    return out


def _judge(
    table: Mapping[str, Mapping[str, float]], months: list[str], w: Mapping[str, float]
) -> dict[str, Any]:
    rf = [table[m]["RF"] for m in months]
    base = [table[m]["NDXT"] for m in months]
    port = _static(table, months, w)
    st, sb = stats(port, rf), stats(base, rf)
    half = len(months) // 2
    diff, z = sharpe_diff_z(port, base, rf)
    d1, _ = sharpe_diff_z(port[:half], base[:half], rf[:half])
    d2, _ = sharpe_diff_z(port[half:], base[half:], rf[half:])
    red = sb.mdd - st.mdd
    return {
        "stats": _st(st),
        "sharpe_diff_monthly": round(diff, 4),
        "z": round(z, 2),
        "halves": [round(d1, 4), round(d2, 4), months[half]],
        "mdd_reduction": round(red, 4),
        "pass": z >= Z and red >= MDD_GAIN and d1 > 0 and d2 > 0,
    }


def part_a(table_in: Mapping[str, Mapping[str, float]], qqq: Mapping[str, float]) -> dict[str, Any]:
    hi = french_hi30()
    res: dict[str, Any] = {}
    for label, div in (("main", NDX_DIV), ("ndx_div_0", 0.0), ("ndx_div_1.5", 0.015)):
        nt = ndx_total(table_in, qqq, div)
        table = {
            m: {**row, "NDXT": nt[m], "HI30": hi[m]}
            for m, row in table_in.items()
            if m in nt and m in hi and "RF" in row and m >= "1986-02"
        }
        months = sorted(m for m in table if all(k in table[m] for k in ("UST10", "GOLD")))
        rf = [table[m]["RF"] for m in months]
        out: dict[str, Any] = {
            "period": [months[0], months[-1]],
            "NDX_only": _st(stats([table[m]["NDXT"] for m in months], rf)),
            "corr_ndx_hi30": round(
                _corr([table[m]["NDXT"] for m in months], [table[m]["HI30"] for m in months]), 3
            ),
            "60/40": _judge(table, months, PRIMARY),
        }
        if label == "main":
            for name, w in REPORT_A.items():
                out[name] = _judge(table, months, w)
            out["178_60/20/20_same_data"] = _judge(table, months, NBG)
        res[label] = out
    return res


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = (sum((x - ma) ** 2 for x in a) / n) ** 0.5
    sb = (sum((y - mb) ** 2 for y in b) / n) ** 0.5
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / n
    return float(cov / (sa * sb))


def _mix(
    series: Mapping[str, Mapping[str, float]], months: list[str], w: Mapping[str, float]
) -> list[float]:
    tab = {m: {k: series[k][m] for k in w} for m in months}
    return _static(tab, months, w) if len(w) > 1 else [series[next(iter(w))][m] for m in months]


def _window_stats(rets: Sequence[float]) -> dict[str, float]:
    v, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        v *= 1 + r
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"total": round(v - 1, 4), "mdd": round(mdd, 4)}


def part_b(table: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    series = {s: _etf(s) for s in ("QQQ", "SPY", "IEF", "GLD", *DIV_ETFS)}
    last_rf = table[max(m for m in table if "RF" in table[m])]["RF"]

    def rf_of(ms: list[str]) -> list[float]:
        return [table.get(m, {}).get("RF", last_rf) for m in ms]

    def ratios(d: str, ms: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, q in RATIOS.items():
            w = {k: v for k, v in (("QQQ", q), (d, 1 - q)) if v > 0}
            out[name] = _st(stats(_mix(series, ms, w), rf_of(ms)))
        return out

    res: dict[str, Any] = {"etfs": DIV_ETFS, "own_window": {}, "common_window": {}, "crises": {}}
    for d in DIV_ETFS:
        ms = sorted(set(series[d]) & set(series["QQQ"]))
        res["own_window"][d] = {
            "period": [ms[0], ms[-1]],
            "corr_with_qqq": round(
                _corr([series[d][m] for m in ms], [series["QQQ"][m] for m in ms]), 3
            ),
            **ratios(d, ms),
        }
    common = sorted(
        m
        for m in series["QQQ"]
        if COMMON[0] <= m <= COMMON[1]
        and all(m in series[d] for d in DIV_ETFS if d not in ("JEPI", "458730.KS"))
    )
    res["common_window"]["period"] = [common[0], common[-1]]
    for d in [*DIV_ETFS, "SPY"]:
        if d in ("JEPI", "458730.KS"):
            continue
        res["common_window"][d] = ratios(d, common)
    res["common_window"]["ref_QQQ60_IEF20_GLD20"] = _st(
        stats(_mix(series, common, {"QQQ": 0.6, "IEF": 0.2, "GLD": 0.2}), rf_of(common))
    )
    for cname, (a, b) in CRISES.items():
        row: dict[str, Any] = {}
        for d in ["QQQ", "SPY", *DIV_ETFS]:
            ms = [m for m in sorted(series[d]) if a <= m <= b and m in series["QQQ"]]
            if not ms or ms[0] != a:
                continue
            row[d] = {
                "alone": _window_stats([series[d][m] for m in ms]),
                "QQQ60_this40": _window_stats(_mix(series, ms, {"QQQ": 0.6, d: 0.4}))
                if d != "QQQ"
                else None,
            }
        ms = [m for m in sorted(series["QQQ"]) if a <= m <= b]
        row["ref_QQQ60_IEF20_GLD20"] = _window_stats(
            _mix(series, ms, {"QQQ": 0.6, "IEF": 0.2, "GLD": 0.2})
        )
        res["crises"][cname] = row
    return res


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if data_hash() != EXPECTED_DATA_SHA:
        raise SystemExit("자료 해시 불일치 (176)")
    if own_hash() != DATA_SHA:
        raise SystemExit("자료 해시 불일치 (180)")
    table = build_table()
    qqq = _etf("QQQ")
    res: dict[str, Any] = {"A": part_a(table, qqq), "B": part_b(table)}
    res["verdict"] = "HISTORICAL_SUPPORT" if res["A"]["main"]["60/40"]["pass"] else "REJECT"
    res["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_div_mix_180 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
