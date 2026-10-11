"""문서 221 — O 선물 추가분 × funding 과열 끄기, 후향 파일럿 1 회. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_funding_gate_221      # reports/funding_gate_221/result.json · 화면
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

from scripts.run_o_overlay import FROM_MS, INITIAL, SPLIT_MS, TO_MS, describe, load

from auto_trading.backtest.overlay import O2_STOP, O3_2X_LIMIT40, OverlayResult
from auto_trading.research.funding_gate_221 import (
    DAY_MS,
    GATE,
    block_bootstrap_sharpe_diff,
    funding_hot,
    sharpe,
    simulate_gated,
)

OUT = Path("reports/funding_gate_221")
SENS = (0.0002, 0.0005)


def _halves(r: OverlayResult) -> tuple[list[float], list[float]]:
    pairs = list(zip(r.days, r.returns, strict=True))
    return [x for d, x in pairs if d < SPLIT_MS], [x for d, x in pairs if d >= SPLIT_MS]


def compare(g: OverlayResult, b: OverlayResult) -> dict[str, Any]:
    dg, db = describe(g), describe(b)
    g1, g2 = _halves(g)
    b1, b2 = _halves(b)
    point, lo, hi = block_bootstrap_sharpe_diff(g.returns, b.returns)
    out = {
        "sharpe": [dg["full"]["annual_sharpe"], db["full"]["annual_sharpe"]],
        "cagr": [dg["full"]["cagr"], db["full"]["cagr"]],
        "mdd": [dg["full"]["mdd"], db["full"]["mdd"]],
        "funding": [dg["funding"], db["funding"]],
        "final": [dg["final_equity"], db["final_equity"]],
        "halted": [dg["halted_day"], db["halted_day"]],
        "d_sharpe": point,
        "d_sharpe_ci90": [lo, hi],
        "d_sharpe_halves": [sharpe(g1) - sharpe(b1), sharpe(g2) - sharpe(b2)],
        "d_cagr": dg["full"]["cagr"] - db["full"]["cagr"],
        "d_mdd": dg["full"]["mdd"] - db["full"]["mdd"],
    }
    ok = (
        point > 0
        and all(x > 0 for x in out["d_sharpe_halves"])
        and out["d_cagr"] >= -0.01
        and out["d_mdd"] <= 0.01
        and lo > 0
    )
    out["verdict"] = "불리" if point <= 0 else "지지" if ok else "불명"
    return out


def after_gate(spot: dict[str, Any], funding: dict[str, list[tuple[int, float]]]) -> dict[str, Any]:
    """끈 날 다음 7 · 30 일 현물 수익 (끈 날 vs 전체 날, 종목별)."""
    fund_t = {s: [t for t, _ in v] for s, v in funding.items()}
    fund_r = {s: [r for _, r in v] for s, v in funding.items()}
    out: dict[str, Any] = {}
    for s in sorted(spot):
        rows: dict[str, list[list[float]]] = {"hot": [[], []], "all": [[], []]}
        day = FROM_MS
        while day + 30 * DAY_MS <= TO_MS:
            p0 = spot[s][day].close
            r7 = spot[s][day + 7 * DAY_MS].close / p0 - 1
            r30 = spot[s][day + 30 * DAY_MS].close / p0 - 1
            keys = ["all"] + (["hot"] if s in funding_hot(fund_t, fund_r, day, GATE) else [])
            for k in keys:
                rows[k][0].append(r7)
                rows[k][1].append(r30)
            day += DAY_MS
        out[s] = {
            k: {
                "days": len(v[0]),
                "fwd7": statistics.fmean(v[0]) if v[0] else None,
                "fwd30": statistics.fmean(v[1]) if v[1] else None,
            }
            for k, v in rows.items()
        }
    return out


def main() -> int:
    spot, perp, funding = load()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    res: dict[str, Any] = {"gate": GATE}
    n_days = (TO_MS - FROM_MS) // DAY_MS + 1
    for name, cfg in (("O2", O2_STOP), ("O3", O3_2X_LIMIT40)):
        base, _ = simulate_gated(spot, perp, funding, cfg=cfg, gate=float("inf"), **kw)
        g, gated = simulate_gated(spot, perp, funding, cfg=cfg, gate=GATE, **kw)
        c = compare(g, base)
        c["gated_share"] = gated / (n_days * len(spot))
        c["sensitivity(참고)"] = {}
        for th in SENS:
            gs, _ = simulate_gated(spot, perp, funding, cfg=cfg, gate=th, **kw)
            d = compare(gs, base)
            c["sensitivity(참고)"][str(th)] = {
                k: d[k] for k in ("d_sharpe", "d_cagr", "d_mdd", "verdict")
            }
        res[name] = c
    res["after_gate(참고)"] = after_gate(spot, funding)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
