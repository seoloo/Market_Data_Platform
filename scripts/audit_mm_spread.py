"""IDEAS 17 Phase 0 — 바이낸스 최우선 호가 차이(스프레드)가 지정가 수수료를 넘는가 (조회만).

사용 예::

    python -m scripts.audit_mm_spread --out reports/audit_mm_spread.json   # 약 2 분 표본

* 공개 시세 ``bookTicker``(키 없음)를 2 초마다 60 번 읽어 종목별 스프레드(bp)를 잰다.
* 최우선 호가에 사고팔아 한 번 왕복하면 버는 것은 스프레드 하나(불리한 가격 변화가 없을 때의
  상한), 내는 것은 지정가 수수료 두 번. 스프레드 < 2 × 지정가 수수료면 호가 제공만으로는 비용을
  넘을 수 없다 — 더 넓게 호가를 내면 그것은 '가격이 되돌아온다' 에 거는 그리드(145 에서 자리 없음).
* 수수료: 바이낸스 기본 등급(VIP 0) 공개 요율 — 현물 지정가 0.10%(BNB 할인 0.075%),
  USD-M 지정가 0.02%(BNB 할인 0.018%). 리베이트는 없다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT",
           "LINKUSDT", "LTCUSDT", "TRXUSDT")  # fmt: skip
ENDPOINTS = {
    "spot": "https://api.binance.com/api/v3/ticker/bookTicker",
    "um": "https://fapi.binance.com/fapi/v1/ticker/bookTicker",
}
MAKER = {"spot": 0.0010, "um": 0.0002}
MAKER_BNB = {"spot": 0.00075, "um": 0.00018}
SAMPLES, PAUSE = 60, 2.0


def spread_bp(bid: float, ask: float) -> float | None:
    """(매도 호가 − 매수 호가) ÷ 중간가, bp (순수)."""
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (ask - bid) / ((ask + bid) / 2) * 1e4


def summarize(samples: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for market, by_sym in samples.items():
        fee_bp = 2 * MAKER[market] * 1e4
        out[market] = {
            "round_trip_maker_fee_bp": round(fee_bp, 2),
            "round_trip_maker_fee_bp_bnb": round(2 * MAKER_BNB[market] * 1e4, 2),
            "symbols": {
                s: {
                    "n": len(v),
                    "median_spread_bp": round(statistics.median(v), 4),
                    "p90_spread_bp": round(sorted(v)[int(0.9 * (len(v) - 1))], 4),
                    "spread_over_fee": round(statistics.median(v) / fee_bp, 4),
                }
                for s, v in sorted(by_sym.items())
                if v
            },
        }
    return out


def main(argv: list[str] | None = None) -> int:
    import httpx

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    samples: dict[str, dict[str, list[float]]] = {m: {s: [] for s in SYMBOLS} for m in ENDPOINTS}
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    with httpx.Client(timeout=10) as client:
        for _ in range(SAMPLES):
            for market, url in ENDPOINTS.items():
                try:
                    rows = client.get(url).json()
                except Exception:  # 한 번 실패는 그 표본만 건너뜀
                    continue
                for r in rows if isinstance(rows, list) else []:
                    if r.get("symbol") in SYMBOLS:
                        bp = spread_bp(float(r["bidPrice"]), float(r["askPrice"]))
                        if bp is not None:
                            samples[market][r["symbol"]].append(bp)
            time.sleep(PAUSE)
    result = {"started": started, "samples_per_symbol": SAMPLES, **summarize(samples)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
