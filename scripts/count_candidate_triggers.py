#!/usr/bin/env python
"""후보 전략의 **트리거 빈도**만 센다 — 검정력 판정의 남은 입력.

`measure_noise_floor.py` 가 SD 를 줬고, 검정력 게이트에 남은 미지수는 표본 수다.
표본 수는 트리거 빈도 × 기간이므로 여기서 빈도를 실측한다.

**수익률을 계산하지 않는다.** 세는 것은 "조건이 몇 번 성립했는가" 뿐이다.
사전등록 전에 성과를 보면 그 뒤 모든 판정이 오염된다.

여기 쓰인 임계값은 **사전등록이 아니다.** 빈도의 자릿수를 보기 위한 기본값이고,
후보가 선택되면 사전등록에서 다시 고정한다. 그 사실을 결과에도 적어 남긴다.

사용 예::

    python scripts/count_candidate_triggers.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.backtest.triggers import (  # noqa: E402
    BARS_PER_HOUR,
    CASCADE_MOVE_SD,
    CASCADE_OI_DROP,
    CONTRACTION_QUANTILE,
    RANGE_BARS,
    TREND_BARS,
    breakout_triggers,
    cascade_triggers,
    contraction_flags,
    rolling_vol,
)
from auto_trading.config.loader import load_config  # noqa: E402

MS_PER_YEAR = 365.25 * 24 * 3_600_000

# --- 후보 C: 다일 추세 추종 (트리거 모듈이 아니라 보유기간으로 상한을 잡는다) ---
MOMENTUM_HOLD_DAYS = 14


def load_closes(conn: sqlite3.Connection, exchange: str) -> tuple[list[int], list[float]]:
    rows = conn.execute(
        "SELECT open_ms, close FROM candles"
        " WHERE exchange=? AND symbol='BTCUSDT' AND timeframe='5m' ORDER BY open_ms",
        (exchange,),
    ).fetchall()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def load_open_interest(conn: sqlite3.Connection, exchange: str) -> dict[int, float]:
    rows = conn.execute(
        "SELECT at_ms, open_interest FROM perp_metrics WHERE exchange=? AND symbol='BTCUSDT'",
        (exchange,),
    ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows if r[1] is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "reports/phase0/candidate_triggers.json"
    )
    args = parser.parse_args()

    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    times, closes = load_closes(conn, config.exchange)
    if not closes:
        print("5분봉이 없다.")
        return 1
    years = (times[-1] - times[0]) / MS_PER_YEAR
    fmt = "%Y-%m-%d"
    span = f"{datetime.fromtimestamp(times[0] / 1000, UTC):{fmt}}"
    span += f" ~ {datetime.fromtimestamp(times[-1] / 1000, UTC):{fmt}}"
    print(f"BTCUSDT 5분봉 {len(closes):,}개 · {span} ({years:.1f}년)")
    print("세는 것은 트리거 횟수뿐이다. 수익률은 계산하지 않는다.\n")

    vol = rolling_vol(closes)

    a = len(breakout_triggers(closes, contraction_flags(vol)))
    print(f"A 변동성 수축 후 구조 돌파 : {a:,}회 · 연 {a / years:.0f}회")

    oi = load_open_interest(conn, config.exchange)
    triggers_b, usable = cascade_triggers(times, closes, vol, oi)
    b = len(triggers_b)
    oi_years = usable / (BARS_PER_HOUR * 24 * 365.25) if usable else 0.0
    rate_b = b / oi_years if oi_years else 0.0
    print(f"B 청산 캐스케이드 반전    : {b:,}회 · 연 {rate_b:.0f}회 (지표 가용 {oi_years:.1f}년)")

    c_hold_bars = MOMENTUM_HOLD_DAYS * 24 * BARS_PER_HOUR
    c = len(closes) // c_hold_bars
    print(
        f"C 다일 추세 추종          : 최대 {c:,}회 · 연 {c / years:.0f}회"
        f" (보유 {MOMENTUM_HOLD_DAYS}일 상한)"
    )

    print("\n임계값은 사전등록이 아니라 자릿수 확인용 기본값이다.")
    payload = {
        "symbol": "BTCUSDT",
        "span": span,
        "years": years,
        "note": "트리거 횟수만 측정. 수익률 미계산. 임계값은 사전등록 아님.",
        "parameters": {
            "contraction_quantile": CONTRACTION_QUANTILE,
            "range_bars": RANGE_BARS,
            "trend_bars": TREND_BARS,
            "cascade_move_sd": CASCADE_MOVE_SD,
            "cascade_oi_drop": CASCADE_OI_DROP,
            "momentum_hold_days": MOMENTUM_HOLD_DAYS,
        },
        "candidates": {
            "A_volatility_contraction_breakout": {"triggers": a, "per_year": a / years},
            "B_liquidation_cascade": {
                "triggers": b,
                "per_year": rate_b,
                "metrics_years": oi_years,
            },
            "C_multiday_momentum": {"max_triggers": c, "per_year": c / years},
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
