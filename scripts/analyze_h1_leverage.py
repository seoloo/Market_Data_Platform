"""문서 82 — H1: 위험 한도를 올려 배율을 높일 때 받아들이게 되는 손실의 크기 (판정 아님).

사용 예::

    python -m scripts.analyze_h1_leverage

**판정·선택용이 아니다.** 문서 30 §3 C 대로, 이미 본 2020~2026 자료로 배율을 골라
실거래·Paper 를 정하지 않는다. 이 분석은 사용자가 위험 한도(계좌 정지 30% · 중단선 25%)를
올릴지 정할 때 **무엇을 받아들이는지**를 보여 준다.

1. 과거 경로 (계좌 정지를 끄고): O/v2 규칙(비상 스톱 −20%·선물 레버리지 ≤ 3 × 선물 지갑·
   일 손실 2%) 그대로 배수만 1.5 · 2.0 · 2.5 · 3.0.
   최악 낙폭(봉 안·종가)·물속 기간·청산·스톱 발동·funding.
   **"그 배수를 쓰려면 정지선이 최소 얼마여야 했나"** = 봉 안 최악 낙폭.
2. 하루 급락 스트레스 (산술): 과거 최대 노출 날의 구성으로 하루 −20~−50% 가 오면 계좌 손실.
   (a) 비상 스톱이 −20% 에서 1% 불리하게 체결 (b) 스톱이 체결되지 못하고 저가까지 (거래소 장애·갭).
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from scripts.run_o_overlay import FROM_MS, INITIAL, TO_MS, describe, load
from scripts.run_o_volcap import _worst

from auto_trading.backtest.overlay import O2_STOP, OverlayResult, simulate_overlay

EXPOSURES = (1.5, 2.0, 2.5, 3.0)
MAX_W = 0.86
"""T/v2 두 종목 합계 비중의 과거 최대 (문서 50 §1.1)."""
MEDIAN_W = 0.29
"""보유일의 합계 비중 중앙값 (문서 50 §1.1)."""
DROPS = (0.20, 0.30, 0.40, 0.50)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def underwater(result: OverlayResult) -> dict[str, Any]:
    """종가 기준 고점 아래에 머문 가장 긴 기간."""
    peak, start, longest, span = result.equity[0], 0, 0, (0, 0)
    for i, e in enumerate(result.equity):
        if e >= peak:
            peak, start = e, i
        elif i - start > longest:
            longest, span = i - start, (start, i)
    return {
        "longest_days": longest,
        "from": _date(result.days[span[0]]),
        "to": _date(result.days[span[1]]),
    }


def path(exposure: float) -> dict[str, Any]:
    spot, perp, funding = load()
    cfg = replace(O2_STOP, exposure=exposure, halt_drawdown=1.0)  # 정지를 끄고 경로만 본다
    r = simulate_overlay(
        spot, perp, funding, from_ms=FROM_MS, to_ms=TO_MS, initial_equity=INITIAL, cfg=cfg
    )
    d = describe(r)
    worst = _worst(r)
    return {
        "exposure": exposure,
        "cagr": d["full"]["cagr"],
        "sharpe": d["full"]["annual_sharpe"],
        "first_half_cagr": d["first_half"]["cagr"],
        "second_half_cagr": d["second_half"]["cagr"],
        "yearly": d["yearly"],
        "mdd_close": worst["mdd"],
        "mdd_span": [worst["mdd_peak"], worst["mdd_trough"]],
        "worst_intraday_drawdown": worst["worst_intraday_drawdown"],
        "min_halt_line_needed": worst["worst_intraday_drawdown"],
        "worst_day": worst["worst_day"],
        "worst_30d": worst["worst_30d"],
        "underwater": underwater(r),
        "liquidations": r.liquidations,
        "stop_hits": r.stop_hits,
        "deleverage_days": r.deleverage_days,
        "max_futures_leverage": r.max_futures_leverage,
        "mean_exposure": d["mean_exposure"],
        "max_exposure": d["max_exposure"],
        "funding": r.funding,
        "costs": r.costs,
        "final_equity": r.equity[-1],
    }


def book(exposure: float, w: float, max_leverage: float = 3.0) -> dict[str, float]:
    """O 구성 (overlay.split_weights 와 같은 식): 현물 k·w, 선물 (e − k)·w, 선물 지갑 = 1 − k·w."""
    k = 1.0
    if w > 0:
        k = min(1.0, (max_leverage - exposure * w) / ((max_leverage - 1) * w))
    spot = k * w
    fut = (exposure - k) * w
    wallet = 1 - spot
    lev = fut / wallet if wallet > 0 else float("inf")
    liq = 1 / lev - 0.005 if lev > 0 else float("inf")
    return {
        "spot": spot,
        "futures": fut,
        "futures_wallet": wallet,
        "futures_leverage": lev,
        "liquidation_distance": liq,
    }


def stress(exposure: float, w: float) -> dict[str, Any]:
    b = book(exposure, w)
    rows = {}
    for x in DROPS:
        stop_fill = 0.21  # −20% 스톱, 1% 불리
        if b["liquidation_distance"] <= stop_fill:
            fut_a = b["futures_wallet"]  # 스톱 전에 청산
        else:
            fut_a = b["futures"] * min(x, stop_fill)
        fut_b = min(
            b["futures"] * x, b["futures_wallet"]
        )  # 스톱 실패 — 저가까지, 지갑 이상은 못 잃는다
        rows[f"{x:.0%}"] = {
            "stop_works": b["spot"] * x + fut_a,
            "stop_fails": b["spot"] * x + fut_b,
            "liquidated_if_stop_fails": b["futures"] * x >= b["futures_wallet"] - 1e-12,
        }
    return {"book": b, "drops": rows}


def main() -> int:
    out: dict[str, Any] = {
        "note": "문서 82 — 판정·배율 선택용 아님. 한도 결정을 위한 손실 크기 분석.",
        "window": "2020-03-01 ~ 2026-08-31, 정지 끔, O/v2 규칙",
        "paths": [path(e) for e in EXPOSURES],
        "stress": {
            str(e): {"max_w": stress(e, MAX_W), "median_w": stress(e, MEDIAN_W)} for e in EXPOSURES
        },
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
