"""문서 178 — 나스닥 100 + 미국채 10 년 + 금 고정 비중(추세 규칙 없음) vs 나스닥만 · 176 B.

사용 예::

    python -m scripts.run_static_mix_178 run   # 1 회 판정 → reports/static_mix_178/result.json

* 사전 등록: docs/research/178-nasdaq-bond-gold-preregistration.md. 자료는 176 과 같다(해시 그대로).
* 판정은 60/20/20 하나. 40/30/30 · 1/3 씩 · 176 B(나스닥 80 + TSMOM 20)는 보고만.
* 판정은 60/20/20 하나. 40/30/30 · 1/3 씩 · 176 B(나스닥 80 + TSMOM 소매 20, 2026-05 까지)는 보고만.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_multi_asset_176 import (
    COST,
    EXPECTED_DATA_SHA,
    MDD_GAIN,
    SLEEVE_FEE,
    Z,
    _st,
    build_table,
    data_hash,
)

from auto_trading.research.multi_asset import sharpe_diff_z, stats, turnover

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "static_mix_178"
MIXES = {
    "60/20/20": {"NDX": 0.6, "UST10": 0.2, "GOLD": 0.2},
    "40/30/30": {"NDX": 0.4, "UST10": 0.3, "GOLD": 0.3},
    "1/3 씩": {"NDX": 1 / 3, "UST10": 1 / 3, "GOLD": 1 / 3},
}
PRIMARY = "60/20/20"


def _static(
    table: Mapping[str, Mapping[str, float]], months: list[str], target: Mapping[str, float]
) -> list[float]:
    out = []
    w_old: dict[str, float] = {}
    for k, m in enumerate(months):
        r = sum(w * table[m][a] for a, w in target.items()) - COST * (
            1.0 if k == 0 else turnover(w_old, target)
        )
        out.append(r)
        grown = {a: w * (1 + table[m][a]) for a, w in target.items()}
        tot = sum(grown.values())
        w_old = {a: v / tot for a, v in grown.items()}
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if data_hash() != EXPECTED_DATA_SHA:
        raise SystemExit("자료 해시 불일치")
    table = build_table()
    months = sorted(m for m in table if all(a in table[m] for a in ("NDX", "UST10", "GOLD", "RF")))
    rf = [table[m]["RF"] for m in months]
    ndx = [table[m]["NDX"] for m in months]
    half = len(months) // 2
    res: dict[str, Any] = {"period": [months[0], months[-1]], "NDX_only": _st(stats(ndx, rf))}
    for name, w in MIXES.items():
        port = _static(table, months, w)
        st = stats(port, rf)
        diff, z = sharpe_diff_z(port, ndx, rf)
        d1, _ = sharpe_diff_z(port[:half], ndx[:half], rf[:half])
        d2, _ = sharpe_diff_z(port[half:], ndx[half:], rf[half:])
        mdd_red = stats(ndx, rf).mdd - st.mdd
        res[name] = {
            "stats": _st(st),
            "sharpe_diff_monthly": round(diff, 4),
            "z": round(z, 2),
            "halves": [round(d1, 4), round(d2, 4), months[half]],
            "mdd_reduction": round(mdd_red, 4),
            "pass": z >= Z and mdd_red >= MDD_GAIN and d1 > 0 and d2 > 0,
        }
    # 보고: 176 B (나스닥 80 + TSMOM 20) 같은 기간(TSMOM 끝 2026-05 까지)
    bm = [m for m in months if "TSMOM_EX" in table[m]]
    brf = [table[m]["RF"] for m in bm]
    b_port = []
    w_old: dict[str, float] = {}
    tgt = {"E": 0.8, "S": 0.2}
    for k, m in enumerate(bm):
        e, s = table[m]["NDX"], table[m]["TSMOM_EX"] + table[m]["RF"] - SLEEVE_FEE / 12
        b_port.append(0.8 * e + 0.2 * s - COST * (1.0 if k == 0 else turnover(w_old, tgt)))
        ge, gs = 0.8 * (1 + e), 0.2 * (1 + s)
        w_old = {"E": ge / (ge + gs), "S": gs / (ge + gs)}
    p_same = _static(table, bm, MIXES[PRIMARY])
    res["vs_176B_same_period"] = {
        "period": [bm[0], bm[-1]],
        "176B_ndx80_tsmom20": _st(stats(b_port, brf)),
        "60/20/20": _st(stats(p_same, brf)),
        "both_mixed_50_50": _st(
            stats([0.5 * a + 0.5 * b for a, b in zip(b_port, p_same, strict=True)], brf)
        ),
    }
    res["verdict"] = "HISTORICAL_SUPPORT" if res[PRIMARY]["pass"] else "REJECT"
    res["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_static_mix_178 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
