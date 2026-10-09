"""문서 189 — M3 매달 비율 맞추기: 양쪽 사고팔기(F) vs SCHD 안 팔기(N) · 둘 다 안 팔기(N2).

사용 예::

    python -m scripts.run_rebal_189 run   # 1 회 판정 → reports/rebal_189/result.json

* 사전 등록: docs/research/189-m3-rebalance-schd-no-sell-preregistration.md.
* 184 틀 + T 브레이크(달러 보관). 월 30 만 원으로 판정, 100 만 원은 참고.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_brake_183 import CRISES, data_real, data_synth

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "rebal_189"
VARIANTS: dict[str, frozenset[str]] = {
    "F": frozenset(),
    "N": frozenset({"D"}),
    "N2": frozenset({"L", "D"}),
}
AMOUNTS = (300_000.0, 1_000_000.0)
P_IRR, P_MDD = 0.005, 0.05


def run_one(
    data: Mapping[str, Any],
    qqq: Mapping[str, float],
    name: str,
    no_sell: frozenset[str],
    krw: float,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    orig = m184.Book.sell

    def counting(self: m184.Book, sym: str, qty: float, px: float, year: str) -> float:
        got = orig(self, sym, qty, px, year)
        if got > 0:
            counts[sym] += 1
        return got

    m184.Book.sell = counting  # type: ignore[method-assign]
    try:
        sim = m184.simulate(data, qqq, ref="T", park="cash", no_sell=no_sell, monthly_krw=krw)
    finally:
        m184.Book.sell = orig  # type: ignore[method-assign]
    row = m184.summarize(sim, CRISES[name])
    row["sells_tqqq"], row["sells_schd"] = counts["L"], counts["D"]
    return row


def verdict(res: Mapping[str, Any]) -> dict[str, Any]:
    ok = {}
    for s in ("R", "S"):
        f, n = res[s]["300000"]["F"], res[s]["300000"]["N"]
        ok[s] = bool(n["irr_liq"] >= f["irr_liq"] - P_IRR and n["mdd"] <= f["mdd"] + P_MDD)
    return {"N_ok_[R,S]": [ok["R"], ok["S"]], "choice": "N" if all(ok.values()) else "F"}


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    res: dict[str, Any] = {}
    for name, data in (("R", data_real()), ("S", data_synth())):
        q = m184.qqq_real() if name == "R" else m184.qqq_synth(data["days"], data["rf"])
        res[name] = {
            f"{krw:.0f}": {k: run_one(data, q, name, ns, krw) for k, ns in VARIANTS.items()}
            for krw in AMOUNTS
        }
    out = {
        "results": res,
        "verdict": verdict(res),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_rebal_189 run")
    out = run()
    for s, by_krw in out["results"].items():
        for krw, rows in by_krw.items():
            for k, r in rows.items():
                head = f"{s} {krw:>7} {k:2s} 세후 {r['irr_liq'] * 100:5.1f}"
                mid = f"세전 {r['cagr'] * 100:5.1f} MDD {r['mdd'] * 100:4.1f}"
                body = f"세금/납입 {r['tax_paid_x']:6.2f} 최종/납입 {r['after_tax_liq_x']:8.2f}"
                tail = f"매도 {r['sells_tqqq']}/{r['sells_schd']}"
                sys.stdout.write(f"{head} {mid} {body} {tail}\n")
    sys.stdout.write(json.dumps(out["verdict"], ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
