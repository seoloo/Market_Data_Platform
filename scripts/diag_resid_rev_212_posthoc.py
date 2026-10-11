# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 212 §10 사후 진단 (판정에 쓰지 않음) — 기본 결과를 청산 사유별로, 같은 규칙을 계좌 100만 USDT 로(BTC 주문 단위 영향 제거).

사용: ``python -m scripts.diag_resid_rev_212_posthoc`` → reports/resid_rev_212/diag_posthoc.json
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any

from scripts.run_resid_rev_212 import OUT, REPORT, load_daily, signals
from scripts.run_resid_rev_212_pilot import LruIntraday, P, Sim, block_p, stats


def main() -> int:
    members = json.loads((OUT / "members.json").read_text(encoding="utf-8"))
    exinfo = json.loads((OUT / "exchange_info.json").read_text(encoding="utf-8"))
    sigs = signals(load_daily(), members)
    out: dict[str, Any] = {}
    for name, p in (("main_by_reason", P()), ("equity_1m_diag", P(equity0=1_000_000.0))):
        sim = Sim(p, sigs, LruIntraday(), exinfo)
        sim.run(date(2020, 3, 1), date(2026, 8, 31))
        st = stats(sim)
        st["bootstrap"] = block_p(st.pop("_r"))
        agg: dict[str, list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
        for b in sim.done:
            if b.coin.first_fill < 0:
                continue
            a = agg[b.reason]
            a[0] += 1
            a[1] += b.cash
            a[2] += b.fees
            a[3] += b.cash + b.fees + b.funding + b.extra
        st["by_reason"] = {
            k: {
                "n": int(v[0]),
                "net": round(v[1], 2),
                "fees": round(v[2], 2),
                "gross": round(v[3], 2),
            }
            for k, v in agg.items()
        }
        out[name] = st
        print(name, st["net"], st["exit_reasons"], st["by_reason"], st["bootstrap"], flush=True)
    (REPORT / "diag_posthoc.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
