#!/usr/bin/env python
"""후보 A·B 의 **트리거 시점** 잡음과 비용 대리지표 측정.

`docs/research/01-candidate-comparison.md` §4 가 요구한 재측정이다. 비교표의 검정력
판정은 근사 SD 를 썼고 **낙관적**이다 — A 가 진입하는 순간은 돌파(확장의 시작)이고
B 는 캐스케이드 직후(고변동)라 실제 SD 는 더 높을 수 있다.

같은 실수를 이미 한 번 했다. archive/research/19 는 **무작위 시점**에서 maker 체결을
재 "순개선 4bps" 를 얻었지만, 20 에서 **신호 시점**으로 다시 재니 역선택이 27배였고
결론이 뒤집혔다. 그래서 트리거 시점에서 다시 잰다.

**평균(효과)은 읽지 않는다.**

- 선행수익은 **부호 없이** SD 만 쓴다. 방향을 곱해도 분산은 사실상 같고
  (평균이 작으므로), 방향별 수익을 보면 사전등록 전에 효과를 엿보는 것이 된다.
- 비용 대리지표도 **부호 없는 크기**만 쓴다 — 1분봉 절대 변화와 5분봉 고저 레인지.
  시장가 체결 슬리피지는 방향이 아니라 그 순간의 움직임 크기에 붙는다.

사용 예::

    python scripts/measure_trigger_conditions.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.backtest.power import DEFAULT_POWER, detectable_effect_bps  # noqa: E402
from auto_trading.backtest.triggers import (  # noqa: E402
    BARS_PER_HOUR,
    Trigger,
    breakout_triggers,
    cascade_triggers,
    contraction_flags,
    rolling_vol,
)
from auto_trading.config.loader import load_config  # noqa: E402

HORIZON_HOURS = (2, 4, 6, 12, 24)
COST_BPS = 18.0
MS_PER_YEAR = 365.25 * 24 * 3_600_000
STEP_MS = 300_000


def load_5m(conn: sqlite3.Connection, exchange: str) -> tuple[list[int], list[float], list[float]]:
    rows = conn.execute(
        "SELECT open_ms, close, (high - low) / close FROM candles"
        " WHERE exchange=? AND symbol='BTCUSDT' AND timeframe='5m' AND close > 0"
        " ORDER BY open_ms",
        (exchange,),
    ).fetchall()
    return [r[0] for r in rows], [float(r[1]) for r in rows], [float(r[2]) for r in rows]


def load_1m_moves(conn: sqlite3.Connection, exchange: str) -> dict[int, float]:
    """``{봉 시각: |1분 변화율|}``. 진입 직후 1분의 움직임 크기 = 슬리피지 대리지표."""
    rows = conn.execute(
        "SELECT open_ms, open, close FROM candles"
        " WHERE exchange=? AND symbol='BTCUSDT' AND timeframe='1m' AND open > 0"
        " ORDER BY open_ms",
        (exchange,),
    ).fetchall()
    return {int(r[0]): abs(float(r[2]) - float(r[1])) / float(r[1]) for r in rows}


def load_open_interest(conn: sqlite3.Connection, exchange: str) -> dict[int, float]:
    rows = conn.execute(
        "SELECT at_ms, open_interest FROM perp_metrics WHERE exchange=? AND symbol='BTCUSDT'",
        (exchange,),
    ).fetchall()
    return {int(r[0]): float(r[1]) for r in rows if r[1] is not None}


def forward_sd(
    closes: list[float], triggers: list[Trigger], horizon_bars: int
) -> tuple[float, int]:
    """트리거 시점 선행수익의 SD 와 표본 수. **부호 없이, 평균은 돌려주지 않는다.**

    중첩을 제거한다 — 앞 트리거의 보유 구간과 겹치는 트리거는 독립 표본이 아니다.
    """
    values: list[float] = []
    last_used = -1
    for trigger in triggers:
        i = trigger.index
        if i <= last_used or i + horizon_bars >= len(closes):
            continue
        entry, exit_ = closes[i], closes[i + horizon_bars]
        if entry > 0.0 and exit_ > 0.0:
            values.append((exit_ - entry) / entry * 10_000)
            last_used = i + horizon_bars
    if len(values) < 2:
        return 0.0, len(values)
    return statistics.stdev(values), len(values)


def cost_proxy(
    times: list[int],
    ranges: list[float],
    minute_moves: dict[int, float],
    triggers: list[Trigger],
) -> dict[str, float]:
    """트리거 시점의 움직임 크기 대 전체 평균. 1.0 이면 평소와 같다."""
    idx = [t.index for t in triggers if t.index < len(ranges)]
    if not idx:
        return {}
    base_range = statistics.fmean(ranges) * 10_000
    at_range = statistics.fmean(ranges[i] for i in idx) * 10_000
    out = {
        "range_5m_bps_all": base_range,
        "range_5m_bps_at_trigger": at_range,
        "range_ratio": at_range / base_range if base_range else 0.0,
    }
    if minute_moves:
        base_minute = statistics.fmean(minute_moves.values()) * 10_000
        after = [
            minute_moves[times[i] + STEP_MS] for i in idx if times[i] + STEP_MS in minute_moves
        ]
        if after:
            at_minute = statistics.fmean(after) * 10_000
            out |= {
                "minute_move_bps_all": base_minute,
                "minute_move_bps_after_trigger": at_minute,
                "minute_ratio": at_minute / base_minute if base_minute else 0.0,
            }
    return out


def report(
    name: str,
    closes: list[float],
    triggers: list[Trigger],
    years: float,
    costs: dict[str, float],
) -> dict[str, object]:
    print(f"\n── {name} · 트리거 {len(triggers):,}회 (연 {len(triggers) / years:.0f}회)")
    if costs:
        print(
            f"   5분 레인지 {costs['range_5m_bps_at_trigger']:.0f}bps"
            f" vs 전체 {costs['range_5m_bps_all']:.0f}bps"
            f" → {costs['range_ratio']:.2f}배"
        )
        if "minute_ratio" in costs:
            print(
                f"   진입 직후 1분 {costs['minute_move_bps_after_trigger']:.1f}bps"
                f" vs 전체 {costs['minute_move_bps_all']:.1f}bps"
                f" → {costs['minute_ratio']:.2f}배"
            )
    print(f"   {'horizon':>8} {'SD':>8} {'표본':>7} {'증명가능':>9}")
    rows = []
    for hours in HORIZON_HOURS:
        sd, n = forward_sd(closes, triggers, hours * BARS_PER_HOUR)
        edge = detectable_effect_bps(sd, n, COST_BPS) if n > 1 else float("nan")
        print(f"   {hours:>6}h  {sd:8.1f} {n:7,} {edge:8.1f}b")
        rows.append({"horizon_hours": hours, "sd_bps": sd, "samples": n, "detectable_bps": edge})
    return {
        "triggers": len(triggers),
        "per_year": len(triggers) / years,
        "cost": costs,
        "horizons": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "reports/phase0/trigger_conditions.json"
    )
    args = parser.parse_args()

    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    times, closes, ranges = load_5m(conn, config.exchange)
    if not closes:
        print("5분봉이 없다.")
        return 1
    years = (times[-1] - times[0]) / MS_PER_YEAR
    print(
        f"BTCUSDT 5분봉 {len(closes):,}개 · {years:.1f}년 · 비용 {COST_BPS}bps"
        f" · 검정력 {DEFAULT_POWER:.0%}"
    )
    print("SD 와 크기만 읽는다. 방향별 수익(효과)은 계산하지 않는다.")

    minute_moves = load_1m_moves(conn, config.exchange)
    vol = rolling_vol(closes)

    a = breakout_triggers(closes, contraction_flags(vol))
    oi = load_open_interest(conn, config.exchange)
    b, usable = cascade_triggers(times, closes, vol, oi)
    b_years = usable / (BARS_PER_HOUR * 24 * 365.25) if usable else years

    payload = {
        "symbol": "BTCUSDT",
        "years": years,
        "cost_bps": COST_BPS,
        "power": DEFAULT_POWER,
        "note": "트리거 시점 SD·비용 대리지표만 측정. 방향별 수익 미계산.",
        "A_volatility_contraction_breakout": report(
            "A 변동성 수축 돌파",
            closes,
            a,
            years,
            cost_proxy(times, ranges, minute_moves, a),
        ),
        "B_liquidation_cascade": report(
            "B 청산 캐스케이드 반전",
            closes,
            b,
            b_years,
            cost_proxy(times, ranges, minute_moves, b),
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
