"""문서 196 — Glide 를 원화로 보면 (환율 포함) — 기록, 판정 없음.

사용 예::

    python -m scripts.run_glide_krw_196 run   # reports/glide_krw_196/result.json

* 183 · 184 · 195 는 달러 가격 그대로 돌렸다(매달 '30 만' 이 사실상 달러 고정).
* 여기서는 TQQQ · SCHD · QQQ 가격에 날마다 원/달러(FRED DEXKOUS, 빈 날은 앞 값)를 곱해
  매달 원화 30 만 원 · 원화 원가 · 원화 양도 차익(환차익 포함, 실제 세법과 같음)으로 계산한다.
* 신호(변동성 · QQQ 추세)와 T 브레이크 고점은 실거래처럼 달러 가격으로 잰다(``ref_lev``).
* 비교: U(달러 그대로) vs K(원화) × M0(브레이크 없음) · T(실거래 규칙).
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_brake_183 import CRISES, data_real, data_synth

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "glide_krw_196"
FX_CSV = REPO / "data" / "research" / "multi_asset" / "DEXKOUS.csv"
VARIANTS: dict[str, dict[str, Any]] = {"M0": {"ref": None}, "T": {"ref": "T", "park": "cash"}}


def fx_daily() -> dict[str, float]:
    with FX_CSV.open(encoding="utf-8") as f:
        return {
            r["observation_date"]: float(r["DEXKOUS"])
            for r in csv.DictReader(f)
            if r["DEXKOUS"] not in ("", ".")
        }


def to_krw(
    px: Mapping[str, float], days: Sequence[str], fx: Mapping[str, float]
) -> dict[str, float]:
    """달러 가격 × 그날 환율 (환율 빈 날은 앞 값, 순수)."""
    fx_days = sorted(fx)
    out: dict[str, float] = {}
    k, last = 0, None
    for d in sorted(set(days) & set(px)):
        while k < len(fx_days) and fx_days[k] <= d:
            last = fx[fx_days[k]]
            k += 1
        if last is not None:
            out[d] = px[d] * last
    return out


def run_set(
    name: str, data: Mapping[str, Any], qqq: Mapping[str, float], fx: Mapping[str, float]
) -> dict[str, Any]:
    days = data["days"]
    k_data = {
        **data,
        "lev": to_krw(data["lev"], days, fx),
        "div": to_krw(data["div"], days, fx),
    }
    k_data["days"] = [d for d in days if d in k_data["lev"] and d in k_data["div"]]
    k_qqq = to_krw(qqq, days, fx)
    out: dict[str, Any] = {}
    for v, kw in VARIANTS.items():
        u = m184.simulate(data, qqq, **kw)
        k = m184.simulate(k_data, k_qqq, ref_lev=data["lev"], **kw)
        out[f"U_{v}"] = m184.summarize(u, CRISES[name])
        out[f"K_{v}"] = m184.summarize(k, CRISES[name])
        out[f"U_{v}"]["yearly"] = m184.yearly(u)
        out[f"K_{v}"]["yearly"] = m184.yearly(k)
        out[f"K_{v}"]["sale_days_same_as_U"] = [s["day"] for s in k["sales"]] == [
            s["day"] for s in u["sales"]
        ]
    out["period"] = [days[0], days[-1]]
    fx_y = {}
    for d in k_data["days"]:
        fx_y.setdefault(d[:4], [None, None])
        if fx_y[d[:4]][0] is None:
            fx_y[d[:4]][0] = k_data["lev"][d] / data["lev"][d]
        fx_y[d[:4]][1] = k_data["lev"][d] / data["lev"][d]
    out["fx_year_end"] = {y: round(v[1], 1) for y, v in fx_y.items()}
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    fx = fx_daily()
    dr, ds = data_real(), data_synth()
    res = {
        "R": run_set("R", dr, m184.qqq_real(), fx),
        "S": run_set("S", ds, m184.qqq_synth(ds["days"], ds["rf"]), fx),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_glide_krw_196 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
