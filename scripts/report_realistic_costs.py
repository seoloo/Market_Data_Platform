"""판정된 T/v2·O 의 참고 줄 — 기준 비용(판정)과 현실 비용(docs/research/42 §8)을 나란히.

사용 예::

    python -m scripts.report_realistic_costs

판정을 바꾸지 않는다. 이미 판정·보고된 전략을 비용만 바꿔 다시 재생한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from typing import Any

from scripts.run_o_overlay import FROM_MS, INITIAL, TO_MS, describe, load

from auto_trading.backtest.overlay import O2_STOP, O3_2X, T2_ALONE, OverlayConfig, simulate_overlay
from auto_trading.backtest.trend import realistic_costs

STRATEGIES = {
    "T/v2 단독": T2_ALONE,
    "O/v1": OverlayConfig(),
    "O/v2": O2_STOP,
    "O/v3 (2배, 관찰)": O3_2X,
}


def main() -> int:
    spot, perp, funding = load()
    kwargs: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    out: dict[str, Any] = {}
    for name, cfg in STRATEGIES.items():
        row: dict[str, Any] = {}
        for label, c in (("기준", cfg), ("현실", replace(cfg, trend=realistic_costs(cfg.trend)))):
            rep = describe(simulate_overlay(spot, perp, funding, cfg=c, **kwargs))
            row[label] = {
                "cagr": round(rep["full"]["cagr"], 4),
                "mdd": round(rep["full"]["mdd"], 4),
                "sharpe": round(rep["full"]["annual_sharpe"], 3),
                "costs": round(rep["costs"]),
                "final_equity": round(rep["final_equity"]),
            }
        out[name] = row
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
