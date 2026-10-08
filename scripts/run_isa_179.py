"""문서 179 — ISA(국내 상장 ETF) vs 토스 과세 계좌(미국 ETF) 세후 비교.

사용 예::

    python -m scripts.run_isa_179 run   # 1 회 판정 → reports/isa_179/result.json

* 사전 등록: docs/research/179-isa-vs-taxable-account.md.
* 기초 월 표는 176 · 178 과 같다(해시 그대로).
* 추적 차이 자료: data/research/isa_179/ (Yahoo 일봉 배당 조정 종가, 2026-09 까지, 해시 고정).
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_multi_asset_176 import (
    EXPECTED_DATA_SHA,
    SLEEVE_FEE,
    _fred_daily,
    _month_end,
    build_table,
    data_hash,
)

from auto_trading.research.account_tax import (
    GENERAL,
    ISA,
    TAXABLE,
    Account,
    simulate,
    tracking_gap,
)

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "isa_179"
KR_DATA = REPO / "data" / "research" / "isa_179"
KR_SHA = "a4596c8a17763cc3babd82dfb2699aaae5f3df85b0dac80c1ebad600a23d62af"
MONTHLY = 650_000.0
LUMP = 20_000_000.0
ISA_TOTAL = 100_000_000.0
TER = {"NDX": 0.0020, "UST10": 0.0015, "GOLD": 0.0040}
PAIRS = {"NDX": ("133690.KS", "QQQ"), "UST10": ("305080.KS", "IEF"), "GOLD": ("411060.KS", "GLD")}
NBG = {"NDX": 0.6, "UST10": 0.2, "GOLD": 0.2}
NDX_ONLY = {"NDX": 1.0}
B = {"NDX": 0.8, "TS": 0.2}


def kr_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(KR_DATA.glob("*.csv")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _daily(sym: str) -> dict[str, float]:
    with (KR_DATA / f"{sym}.csv").open(encoding="utf-8") as f:
        return {r["date"]: float(r["adjclose"]) for r in csv.DictReader(f)}


def gaps() -> dict[str, dict[str, Any]]:
    fx = _month_end(_daily("KRW=X"))
    out: dict[str, dict[str, Any]] = {}
    for a, (kr, us) in PAIRS.items():
        g = tracking_gap(_month_end(_daily(kr)), _month_end(_daily(us)), fx)
        out[a] = {
            "kr": kr,
            "us": us,
            **{k: v if isinstance(v, str) else round(v, 4) for k, v in g.items()},
        }
        out[a]["gap_raw"] = g["gap_annual"]
    return out


def _series(
    table: Mapping[str, Mapping[str, float]], drag: Mapping[str, float]
) -> tuple[list[str], list[dict[str, float]], list[dict[str, float]]]:
    """원화 월 수익(보수 · 추적 차이 반영) · 분배금 몫."""
    fx = _month_end(_fred_daily("DEXKOUS"))
    y = _month_end(_fred_daily("DGS10"))
    months, rets, inc = [], [], []
    for p, m in pairwise(sorted(table)):
        row = table[m]
        if not all(k in row for k in ("NDX", "UST10", "GOLD", "RF")) or p not in fx or m not in fx:
            continue
        f = fx[m] / fx[p]
        r = {a: (1 + row[a]) * f - 1 - drag.get(a, 0.0) / 12 for a in ("NDX", "UST10", "GOLD")}
        if "TSMOM_EX" in row:
            s = row["TSMOM_EX"] + row["RF"] - SLEEVE_FEE / 12
            r["TS"] = (1 + s) * f - 1
        months.append(m)
        rets.append(r)
        inc.append({"UST10": y[p] / 100 / 12} if p in y else {})
    return months, rets, inc


def _windows(
    months: Sequence[str],
    rets: Sequence[Mapping[str, float]],
    inc: Sequence[Mapping[str, float]],
    weights: Mapping[str, float],
    acct: Account,
    years: int,
    contrib: Sequence[float],
) -> list[tuple[str, dict[str, float]]]:
    n = 12 * years
    ok = [k for k in range(len(months)) if all(a in rets[k] for a in weights)]
    out = []
    for s in range(len(ok) - n + 1):
        idx = ok[s : s + n]
        if idx[-1] - idx[0] != n - 1:
            continue
        ms = [months[k] for k in idx]
        out.append(
            (
                ms[0],
                simulate(acct, ms, [rets[k] for k in idx], [inc[k] for k in idx], weights, contrib),
            )
        )
    return out


def _ratio(
    a: list[tuple[str, dict[str, float]]], b: list[tuple[str, dict[str, float]]]
) -> dict[str, Any]:
    bm = dict(b)
    rs = [x["final"] / bm[s]["final"] - 1 for s, x in a if s in bm]
    return {
        "windows": len(rs),
        "median": round(statistics.median(rs), 4),
        "share_positive": round(sum(r > 0 for r in rs) / len(rs), 3),
        "min": round(min(rs), 4),
        "max": round(max(rs), 4),
    }


def _summ(res: list[tuple[str, dict[str, float]]]) -> dict[str, Any]:
    fin = [x["final"] for _, x in res]
    return {
        "median_final_mkrw": round(statistics.median(fin) / 1e6, 1),
        "median_taxes_mkrw": round(statistics.median(x["taxes"] for _, x in res) / 1e6, 2),
        "median_fees_mkrw": round(statistics.median(x["fees"] for _, x in res) / 1e6, 2),
        "paid_in_mkrw": round(res[0][1]["paid_in"] / 1e6, 1),
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if data_hash() != EXPECTED_DATA_SHA:
        raise SystemExit("자료 해시 불일치 (176)")
    if kr_hash() != KR_SHA:
        raise SystemExit("자료 해시 불일치 (179 국내 ETF)")
    table = build_table()
    g = gaps()
    drag_t = dict(TER)
    drag_k = {a: TER[a] - g[a]["gap_raw"] for a in TER}
    t_ser = _series(table, drag_t)
    k_ser = _series(table, drag_k)
    k0_ser = _series(table, TER)
    m10 = [MONTHLY] * 120
    lump = [LUMP] + [0.0] * 119
    res: dict[str, Any] = {
        "tracking": {a: {k: v for k, v in x.items() if k != "gap_raw"} for a, x in g.items()},
        "drag_annual": {"T": drag_t, "I_G": {a: round(v, 4) for a, v in drag_k.items()}},
    }

    def trio(w: Mapping[str, float], contrib: Sequence[float], years: int = 10) -> dict[str, Any]:
        t = _windows(*t_ser, w, TAXABLE, years, contrib)
        i = _windows(*k_ser, w, ISA, years, contrib)
        gg = _windows(*k_ser, w, GENERAL, years, contrib)
        return {
            "T": _summ(t),
            "I": _summ(i),
            "G": _summ(gg),
            "I_vs_T": _ratio(i, t),
            "G_vs_T": _ratio(gg, t),
            "_t": t,
            "_i": i,
        }

    primary = trio(NBG, m10)
    p_t, p_i = primary.pop("_t"), primary.pop("_i")
    res["primary_60_20_20_monthly_10y"] = primary
    ndx = trio(NDX_ONLY, m10)
    for k in ("_t", "_i"):
        ndx.pop(k)
    res["report_ndx_only_monthly_10y"] = ndx
    lump_r = trio(NBG, lump)
    for k in ("_t", "_i"):
        lump_r.pop(k)
    res["report_60_20_20_lump20m_10y"] = lump_r
    no_ded = _windows(*t_ser, NBG, replace(TAXABLE, deduction=0.0), 10, m10)
    res["report_T_without_2_5m_deduction"] = {"T": _summ(no_ded), "I_vs_T": _ratio(p_i, no_ded)}
    i0 = _windows(*k0_ser, NBG, ISA, 10, m10)
    res["report_I_zero_tracking_gap"] = {"I": _summ(i0), "I_vs_T": _ratio(i0, p_t)}
    b_t = _windows(*t_ser, B, TAXABLE, 10, m10)
    res["report_176B_taxable_vs_60_20_20"] = {
        "B_T": _summ(b_t),
        "60_20_20_I_vs_B_T": _ratio(p_i, b_t),
        "60_20_20_T_vs_B_T": _ratio(p_t, b_t),
    }
    # 20 년: ISA 총 한도(1 억) 넘는 납입은 T 로
    m20 = [MONTHLY] * 240
    cut = int(ISA_TOTAL // MONTHLY)
    isa_part = [
        MONTHLY if k < cut else (ISA_TOTAL - cut * MONTHLY if k == cut else 0.0) for k in range(240)
    ]
    over = [a - b for a, b in zip(m20, isa_part, strict=True)]
    t20 = _windows(*t_ser, NBG, TAXABLE, 20, m20)
    i20a = dict(_windows(*k_ser, NBG, ISA, 20, isa_part))
    i20b = dict(_windows(*t_ser, NBG, TAXABLE, 20, over))
    i20 = [
        (s, {k: i20a[s][k] + i20b[s][k] for k in ("final", "paid_in", "taxes", "fees")})
        for s in i20a
        if s in i20b
    ]
    res["report_20y_isa_limit_then_T"] = {
        "T": _summ(t20),
        "I_then_T": _summ(i20),
        "ratio": _ratio(i20, t20),
    }
    pr = primary["I_vs_T"]
    if pr["median"] > 0 and pr["share_positive"] >= 0.8:
        verdict = "ISA_PREFERRED"
    elif pr["median"] < 0 and pr["share_positive"] <= 0.2:
        verdict = "TAXABLE_PREFERRED"
    else:
        verdict = "NO_CLEAR_EDGE"
    res["verdict"] = verdict
    res["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_isa_179 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
