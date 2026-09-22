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


Bars = tuple[list[int], list[float], list[float], list[float], list[float]]


def load_5m(conn: sqlite3.Connection, exchange: str) -> Bars:
    """``(시각, 종가, 고가, 저가, 고저레인지비율)``."""
    rows = conn.execute(
        "SELECT open_ms, close, high, low FROM candles"
        " WHERE exchange=? AND symbol='BTCUSDT' AND timeframe='5m' AND close > 0"
        " ORDER BY open_ms",
        (exchange,),
    ).fetchall()
    times = [int(r[0]) for r in rows]
    closes = [float(r[1]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    ranges = [(h - low) / c for c, h, low in zip(closes, highs, lows, strict=True)]
    return times, closes, highs, lows, ranges


STOP_MULTIPLES = (0.5, 1.0, 2.0)
"""손절 거리 = 배수 × (그 시점 변동성으로 본 horizon 기대 이동폭).

절대 %가 아니라 변동성 배수를 쓰는 이유는 국면마다 적정 손절폭이 다르기 때문이다.
이 배수들은 **사전등록이 아니라 민감도**다 — 손절이 거래당 손익 SD 를 얼마나
줄이는지 자릿수를 보려는 것이다.
"""


def stopped_pnl_sd(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    vol: list[float | None],
    triggers: list[Trigger],
    horizon_bars: int,
    stop_multiple: float,
) -> tuple[float, int]:
    """손절을 적용한 **거래당 손익**의 SD. 평균은 돌려주지 않는다.

    같은 봉 안에서 손절과 목표의 선후를 알 수 없으므로 **손절 우선**으로 본다
    (BACKTEST_AND_TESTING §시간·체결·비용). 목표가는 두지 않고 horizon 에 청산한다.
    """
    values: list[float] = []
    last_used = -1
    for trigger in triggers:
        i = trigger.index
        sigma = vol[i]
        if i <= last_used or sigma is None or sigma <= 0.0:
            continue
        if i + horizon_bars >= len(closes):
            continue
        entry = closes[i]
        distance = stop_multiple * sigma * horizon_bars**0.5
        if distance <= 0.0:
            continue
        stop = entry * (1 - distance) if trigger.direction > 0 else entry * (1 + distance)
        outcome = None
        for j in range(i + 1, i + horizon_bars + 1):
            hit = lows[j] <= stop if trigger.direction > 0 else highs[j] >= stop
            if hit:
                outcome = -distance * 10_000  # 손절가 체결 가정
                break
        if outcome is None:
            outcome = trigger.direction * (closes[i + horizon_bars] - entry) / entry * 10_000
        values.append(outcome)
        last_used = i + horizon_bars
    if len(values) < 2:
        return 0.0, len(values)
    return statistics.stdev(values), len(values)


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
    bars: Bars,
    vol: list[float | None],
    triggers: list[Trigger],
    years: float,
    costs: dict[str, float],
    cost_bps: float,
) -> dict[str, object]:
    _, closes, highs, lows, _ = bars
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

    # 손절을 넣으면 거래당 손익 SD 가 얼마나 줄고, OOS 100거래로 무엇을 확인할 수 있는가
    print(f"   손절 적용 거래당 손익 SD · 비용 {cost_bps:.1f}bps · OOS 100거래 확인 한계")
    stops = []
    for hours in (2, 6):
        bars_n = hours * BARS_PER_HOUR
        line = [f"   {hours:>6}h"]
        for k in STOP_MULTIPLES:
            sd, n = stopped_pnl_sd(closes, highs, lows, vol, triggers, bars_n, k)
            oos = detectable_effect_bps(sd, 100, cost_bps) if n > 1 else float("nan")
            line.append(f" {k}σ SD {sd:6.1f} → OOS100 {oos:5.1f}b")
            stops.append(
                {
                    "horizon_hours": hours,
                    "stop_multiple": k,
                    "sd_bps": sd,
                    "samples": n,
                    "oos_100_detectable_bps": oos,
                }
            )
        print("".join(line))
    return {
        "stopped": stops,
        "triggers": len(triggers),
        "per_year": len(triggers) / years,
        "cost": costs,
        "horizons": rows,
    }


FEE_BPS = 13.0
SLIPPAGE_BPS = 5.0
"""18bps = 수수료 13 + 슬리피지 여유 5. 수수료는 고정, 슬리피지만 실측 배율로 조정한다."""


def cost_of(costs: dict[str, float]) -> float:
    """트리거 시점 움직임 배율을 반영한 왕복 비용."""
    ratio = costs.get("minute_ratio", 1.0)
    return FEE_BPS + SLIPPAGE_BPS * ratio


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "reports/phase0/trigger_conditions.json"
    )
    args = parser.parse_args()

    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    bars = load_5m(conn, config.exchange)
    times, closes, _highs, _lows, ranges = bars
    if not closes:
        print("5분봉이 없다.")
        return 1
    years = (times[-1] - times[0]) / MS_PER_YEAR
    print(
        f"BTCUSDT 5분봉 {len(closes):,}개 · {years:.1f}년 · 기준비용 {COST_BPS}bps"
        f" · 검정력 {DEFAULT_POWER:.0%}"
    )
    print("SD 와 크기만 읽는다. 방향별 수익(효과)은 계산하지 않는다.")

    minute_moves = load_1m_moves(conn, config.exchange)
    vol = rolling_vol(closes)

    a = breakout_triggers(closes, contraction_flags(vol))
    oi = load_open_interest(conn, config.exchange)
    b, usable = cascade_triggers(times, closes, vol, oi)
    b_years = usable / (BARS_PER_HOUR * 24 * 365.25) if usable else years

    cost_a = cost_proxy(times, ranges, minute_moves, a)
    cost_b = cost_proxy(times, ranges, minute_moves, b)

    payload = {
        "symbol": "BTCUSDT",
        "years": years,
        "base_cost_bps": COST_BPS,
        "power": DEFAULT_POWER,
        "stop_multiples": list(STOP_MULTIPLES),
        "note": "트리거 시점 SD·비용 대리지표·손절 적용 손익 SD 만 측정. 방향별 수익 미계산.",
        "A_volatility_contraction_breakout": report(
            "A 변동성 수축 돌파", bars, vol, a, years, cost_a, cost_of(cost_a)
        ),
        "B_liquidation_cascade": report(
            "B 청산 캐스케이드 반전", bars, vol, b, b_years, cost_b, cost_of(cost_b)
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
