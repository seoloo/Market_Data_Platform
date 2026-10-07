"""IDEAS 26 · 27 · 28 (통과 연구 후속) Phase 0 감사 — 검정력 대리 값. 새 후보 성과 없음.

사용 예::

    python -m scripts.audit_followup_26_28 --out reports/followup_26_28/audit.json

* 26: 이미 판정한 T/v2(T2_ALONE) · O/v2(O2_STOP) 를 그대로 재생해 '선물 추가분' 일간 수익
  a = r(O/v2) − r(T/v2) 를 얻는다. 방어 규칙이 추가분을 f 비율의 날에 끈다면 후보 − O/v2 의 일간
  차이 SD ≈ SD(a)·√f (대리). 그리고 O/v2 의 10% · 20% 낙폭 구간(독립 사건) 수를 센다.
* 27: ALLOC(127) 의 코인 T/v2 · QQQ 추세 일간 수익으로, 배분 비중을 Δw 만큼 바꾼 두 계좌의 월
  차이 SD ≈ Δw·SD(코인 − 주식 월 수익) (대리).
* 28: 한국 FLOW 는 이미 계산한 141 · 142 규칙 차이 SD(``reports/equity_ml_phase0/audit.json``) 를
  쓴다. 미국 13F 는 자료 관문만 문서로 본다.
* 이미 본 성과(판정이 끝난 규칙)만 쓰고, 26 · 27 · 28 의 새 규칙은 만들지 않는다.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.audit_dmn_phase0 import mde, newey_west_sd
from scripts.run_alloc import daily_returns
from scripts.run_o_overlay import FROM_MS, INITIAL, TO_MS, load

from auto_trading.backtest.overlay import O2_STOP, T2_ALONE, simulate_overlay

REPO = Path(__file__).resolve().parent.parent
Z80 = 0.8416
Z = 2.0 + Z80
OFF = (0.2, 0.35, 0.5)
EFFECTS = (0.01, 0.02, 0.04)
DELTA_W = (0.1, 0.2)


def returns(equity: Sequence[float]) -> list[float]:
    return [b / a - 1 for a, b in pairwise(equity) if a > 0]


def drawdown_episodes(equity: Sequence[float], depth: float) -> int:
    """고점 대비 depth 이상 빠졌다가 새 고점을 낸 (또는 끝까지 열린) 구간 수 (순수)."""
    peak, inside, n = equity[0], False, 0
    for e in equity:
        if e >= peak:
            peak, inside = e, False
            continue
        if not inside and e <= peak * (1 - depth):
            inside, n = True, n + 1
    return n


def years_needed(sd_daily: float, effect_annual: float, per_year: int) -> float:
    d = effect_annual / per_year
    return (Z * sd_daily / d) ** 2 / per_year


def idea26() -> dict[str, Any]:
    spot, perp, funding = load()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL}
    t2 = simulate_overlay(spot, perp, funding, cfg=T2_ALONE, **kw)
    o2 = simulate_overlay(spot, perp, funding, cfg=O2_STOP, **kw)
    rt, ro = returns(t2.equity), returns(o2.equity)
    n = min(len(rt), len(ro))
    addon = [b - a for a, b in zip(rt[:n], ro[:n], strict=True)]
    sd = statistics.pstdev(addon)
    sd_nw = newey_west_sd(addon, 5)
    rows = []
    for f in OFF:
        s = sd_nw * math.sqrt(f)
        rows.append(
            {
                "off_fraction": f,
                "sd_daily_diff": round(s, 5),
                "mde_annual_at_history": round(mde(s, n, Z80) * 365, 3),
                "years_needed": {f"{e:.0%}/yr": round(years_needed(s, e, 365), 1) for e in EFFECTS},
            }
        )
    return {
        "days": n,
        "period": [
            datetime.fromtimestamp(FROM_MS / 1000, UTC).date().isoformat(),
            datetime.fromtimestamp(TO_MS / 1000, UTC).date().isoformat(),
        ],
        "addon_sd_daily": round(sd, 5),
        "addon_sd_daily_nw5": round(sd_nw, 5),
        "addon_annual_vol": round(sd * math.sqrt(365), 3),
        "o2_drawdown_episodes": {
            f">={int(d * 100)}%": drawdown_episodes(o2.equity, d) for d in (0.1, 0.2)
        },
        "power": rows,
    }


def idea27() -> dict[str, Any]:
    days, c, s = daily_returns()
    month: dict[str, list[tuple[float, float]]] = {}
    for d, a, b in zip(days, c, s, strict=True):
        month.setdefault(d[:7], []).append((a, b))
    diffs = []
    for v in month.values():
        cm = math.prod(1 + a for a, _ in v) - 1
        sm = math.prod(1 + b for _, b in v) - 1
        diffs.append(cm - sm)
    sd = newey_west_sd(diffs, 3)
    rows = []
    for dw in DELTA_W:
        s_ = dw * sd
        rows.append(
            {
                "delta_weight": dw,
                "sd_monthly_diff": round(s_, 4),
                "mde_annual_at_history": round(12 * mde(s_, len(diffs), Z80), 3),
                "years_needed": {f"{e:.0%}/yr": round(years_needed(s_, e, 12), 1) for e in EFFECTS},
            }
        )
    return {
        "months": len(diffs),
        "from": min(month),
        "to": max(month),
        "sd_monthly_crypto_minus_stock_nw3": round(sd, 4),
        "power": rows,
    }


def idea28() -> dict[str, Any]:
    eq = json.loads((REPO / "reports/equity_ml_phase0/audit.json").read_text(encoding="utf-8"))
    pairs = eq["power_proxies_seen_141_142"]["pairs"]
    months = eq["power_proxies_seen_141_142"]["months"]
    out = {}
    for k, v in pairs.items():
        sd = v["sd_lr_lag3"]
        out[k] = {
            "sd_monthly": sd,
            "mde_annual_at_history": round(12 * mde(sd, months, Z80), 3),
        }
    return {"months": months, "pairs": out}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = {"idea26": idea26(), "idea27": idea27(), "idea28": idea28()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
