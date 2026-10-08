"""문서 181 — 나스닥 레버리지(3 배 · 2 배) + 배당주 섞기.

사용 예::

    python -m scripts.run_lev_div_181 run   # 1 회 판정 → reports/lev_div_181/result.json

* 사전 등록: docs/research/181-leveraged-nasdaq-dividend-mix-preregistration.md.
* A(판정): 합성 3 배 나스닥 100 60 + French 배당 수익률 상위 30% 40 vs 3 배만.
* B(기록): QLD · TQQQ + 배당 ETF 11 개(180 자료), 비율 여섯 가지. 판정 없음.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_div_mix_180 import (
    CRISES,
    DIV_ETFS,
    RATIOS,
    _corr,
    _etf,
    _mix,
    _window_stats,
    french_hi30,
    own_hash,
)
from scripts.run_div_mix_180 import DATA_SHA as DIV_SHA
from scripts.run_multi_asset_176 import (
    EXPECTED_DATA_SHA,
    MDD_GAIN,
    Z,
    _month_end,
    _st,
    build_table,
    data_hash,
)
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import leveraged
from scripts.run_static_mix_178 import _static

from auto_trading.research.multi_asset import sharpe_diff_z, stats

REPO = Path(__file__).resolve().parent.parent
LEV_DATA = REPO / "data" / "research" / "lev_div_181"
OUT = REPO / "reports" / "lev_div_181"
LEV_SHA = "2fba751c1b296fc2ecb8810db694400ceb522c52a46d6dd6a4b1af69455015c0"
NDX_DIV = 0.008
FEES = {3.0: 0.0084, 2.0: 0.0095}
KEYS = {3.0: "L3", 2.0: "L2"}
MIXES = {"80/20": 0.8, "70/30": 0.7, "60/40": 0.6, "50/50": 0.5}
COMMON = ("2016-01", "2026-09")


def lev_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(LEV_DATA.glob("*")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def total_return_index(
    days: Sequence[str], px: Mapping[str, float], div: float
) -> dict[str, float]:
    """가격 지수 → 일간 배당(연 ``div`` / 252)을 더한 총수익 지수 (순수)."""
    out = {days[0]: px[days[0]]}
    for prev, cur in pairwise(days):
        out[cur] = out[prev] * (px[cur] / px[prev] + div / 252)
    return out


def monthly(level: Mapping[str, float]) -> dict[str, float]:
    me = _month_end(level)
    ms = sorted(me)
    return {b: me[b] / me[a] - 1 for a, b in pairwise(ms)}


def synthetic() -> dict[str, dict[str, float]]:
    """월 → {L3, L2, N1} 합성 월 수익 (FRED 나스닥 100 · DTB3)."""
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate_raw = _series("DTB3_full.csv", "DTB3")
    days = sorted(d for d in ndx if d <= "2026-08-31")
    rf: dict[str, float] = {}
    lv = 0.0
    for d in sorted(set(days) | set(rate_raw)):
        lv = rate_raw.get(d, lv)
        rf[d] = lv
    tr = total_return_index(days, ndx, NDX_DIV)
    out: dict[str, dict[str, float]] = {"N1": monthly(tr)}
    for k, key in KEYS.items():
        out[key] = monthly(leveraged(days, tr, rf, k, FEES[k]))
    return out


def _judge(
    table: Mapping[str, Mapping[str, float]],
    months: list[str],
    base_key: str,
    w: Mapping[str, float],
) -> dict[str, Any]:
    rf = [table[m]["RF"] for m in months]
    base = [table[m][base_key] for m in months]
    port = _static(table, months, w)
    st, sb = stats(port, rf), stats(base, rf)
    half = len(months) // 2
    diff, z = sharpe_diff_z(port, base, rf)
    d1, _ = sharpe_diff_z(port[:half], base[:half], rf[:half])
    d2, _ = sharpe_diff_z(port[half:], base[half:], rf[half:])
    red = sb.mdd - st.mdd
    n1 = [table[m]["N1"] for m in months]
    _, z_n1 = sharpe_diff_z(port, n1, rf)
    return {
        "stats": _st(st),
        "sharpe_diff_monthly": round(diff, 4),
        "z": round(z, 2),
        "halves": [round(d1, 4), round(d2, 4), months[half]],
        "mdd_reduction": round(red, 4),
        "z_vs_ndx_1x": round(z_n1, 2),
        "pass": z >= Z and red >= MDD_GAIN and d1 > 0 and d2 > 0,
    }


def part_a(table_in: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    syn = synthetic()
    hi = french_hi30()
    table = {
        m: {**row, "HI30": hi[m], **{k: syn[k][m] for k in syn}}
        for m, row in table_in.items()
        if m >= "1986-02" and m in hi and "RF" in row and all(m in syn[k] for k in syn)
    }
    months = sorted(m for m in table if all(k in table[m] for k in ("UST10", "GOLD")))
    rf = [table[m]["RF"] for m in months]
    res: dict[str, Any] = {
        "period": [months[0], months[-1]],
        "NDX_1x": _st(stats([table[m]["N1"] for m in months], rf)),
    }
    for k, key in KEYS.items():
        res[f"{int(k)}x_only"] = _st(stats([table[m][key] for m in months], rf))
        res[f"{int(k)}x_corr_hi30"] = round(
            _corr([table[m][key] for m in months], [table[m]["HI30"] for m in months]), 3
        )
        for name, q in MIXES.items():
            res[f"{int(k)}x {name}"] = _judge(table, months, key, {key: q, "HI30": 1 - q})
        res[f"{int(k)}x 60 + UST10 20 + GOLD 20"] = _judge(
            table, months, key, {key: 0.6, "UST10": 0.2, "GOLD": 0.2}
        )
    return res


def _lev(sym: str) -> dict[str, float]:
    with (LEV_DATA / f"{sym}.csv").open(encoding="utf-8") as f:
        daily = {r["date"]: float(r["adjclose"]) for r in csv.DictReader(f)}
    me = _month_end(daily)
    ms = sorted(me)[1:]
    return {b: me[b] / me[a] - 1 for a, b in pairwise(ms)}


def part_b(table: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    series = {s: _etf(s) for s in ("IEF", "GLD", *DIV_ETFS)}
    series |= {s: _lev(s) for s in ("QLD", "TQQQ")}
    last_rf = table[max(m for m in table if "RF" in table[m])]["RF"]

    def rf_of(ms: list[str]) -> list[float]:
        return [table.get(m, {}).get("RF", last_rf) for m in ms]

    def ratios(lev: str, d: str, ms: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, q in RATIOS.items():
            w = {k: v for k, v in ((lev, q), (d, 1 - q)) if v > 0}
            out[name] = _st(stats(_mix(series, ms, w), rf_of(ms)))
        return out

    res: dict[str, Any] = {}
    for lev in ("QLD", "TQQQ"):
        own: dict[str, Any] = {}
        for d in DIV_ETFS:
            ms = sorted(set(series[d]) & set(series[lev]))
            own[d] = {
                "period": [ms[0], ms[-1]],
                "corr": round(_corr([series[d][m] for m in ms], [series[lev][m] for m in ms]), 3),
                **ratios(lev, d, ms),
            }
        common = sorted(
            m
            for m in series[lev]
            if COMMON[0] <= m <= COMMON[1]
            and all(m in series[d] for d in DIV_ETFS if d not in ("JEPI", "458730.KS"))
        )
        com: dict[str, Any] = {"period": [common[0], common[-1]]}
        for d in DIV_ETFS:
            if d not in ("JEPI", "458730.KS"):
                com[d] = ratios(lev, d, common)
        com["ref_lev60_IEF20_GLD20"] = _st(
            stats(_mix(series, common, {lev: 0.6, "IEF": 0.2, "GLD": 0.2}), rf_of(common))
        )
        crises: dict[str, Any] = {}
        for cname, (a, b) in CRISES.items():
            ms = [m for m in sorted(series[lev]) if a <= m <= b]
            if not ms or ms[0] != a:
                continue
            row: dict[str, Any] = {"alone": _window_stats([series[lev][m] for m in ms])}
            for d in DIV_ETFS:
                if all(m in series[d] for m in ms):
                    row[d] = _window_stats(_mix(series, ms, {lev: 0.6, d: 0.4}))
            row["ref_lev60_IEF20_GLD20"] = _window_stats(
                _mix(series, ms, {lev: 0.6, "IEF": 0.2, "GLD": 0.2})
            )
            crises[cname] = row
        res[lev] = {"own_window": own, "common_window": com, "crises": crises}
    return res


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if data_hash() != EXPECTED_DATA_SHA:
        raise SystemExit("자료 해시 불일치 (176)")
    if own_hash() != DIV_SHA:
        raise SystemExit("자료 해시 불일치 (180)")
    if lev_hash() != LEV_SHA:
        raise SystemExit("자료 해시 불일치 (181)")
    table = build_table()
    res: dict[str, Any] = {"A": part_a(table), "B": part_b(table)}
    res["verdict"] = "HISTORICAL_SUPPORT" if res["A"]["3x 60/40"]["pass"] else "REJECT"
    res["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_lev_div_181 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
