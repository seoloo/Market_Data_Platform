#!/usr/bin/env python
"""A1 사전등록에 들어갈 수치를 산출한다 (읽기 전용, 평균 미열람).

앞선 두 스크립트와 다른 점은 **TRADING_CORE 의 빈도 한도와 손절 하한을 적용**한다는
것이다. 한도를 무시하고 잰 값은 실제로 거래할 수 없는 전략의 수치다.

실측으로 드러난 두 가지가 여기 반영돼 있다.

1. `min_stop_distance = 1%` 가 2h 손절의 **100%** 를 WIDEN 시킨다 (C1).
   그래서 하한을 적용한 손절로 잰다.
2. `같은 방향 연속 2회` 제한이 트리거의 **65%** 를 막는다. H1 을 막았던 것과 같은
   한도다. 그래서 한도를 통과한 부분집합으로 잰다.

읽는 값은 SD·표본 수·빈도뿐이다. 방향별 수익 평균은 계산하지 않는다.

사용 예::

    python scripts/measure_a1_design.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
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

from auto_trading.backtest.power import (  # noqa: E402
    DEFAULT_POWER,
    detectable_effect_bps,
    required_samples,
)
from auto_trading.backtest.triggers import (  # noqa: E402
    BARS_PER_HOUR,
    RANK_WINDOW_BARS,
    VOL_WINDOW_BARS,
    Trigger,
    breakout_triggers,
    contraction_flags,
    rolling_vol,
)
from auto_trading.config.loader import load_config  # noqa: E402

HORIZON_HOURS = (2, 4, 6)
STOP_MULTIPLE = 2.0
"""전략 손절 = 2.0 × σ5m × √봉수.

기본 측정은 이전 WIDEN 가정을 재현한다. A1 수정안은
``--preserve-stop`` 으로 실행해 전략 손절을 유지한다.

2.0 을 쓰는 이유: 하한이 지배하는 2h 에서는 배수 선택이 SD 를 거의 바꾸지 않으므로
(0.5σ 69.9 vs 2.0σ 72.5), 통계적으로 미세하게 유리한 쪽 대신 **고변동 국면에서
봉 잡음에 기계적으로 걸리지 않는** 쪽을 택한다.
"""

OOS_TRADES = 100
"""Phase 4 의 OOS 최소 실거래 수. 이 값이 필요 효과를 결정한다 (C8 선택지 1)."""

FEE_BPS = 13.0
SLIPPAGE_BPS = 5.0
TRIGGER_MOVE_RATIO = 1.381327932435883
"""고가/저가 트리거 실측 (reports/phase0/trigger_conditions_highlow_corrected.json)."""

MS_PER_YEAR = 365.25 * 24 * 3_600_000

SPLIT_BOUNDS_MS = (1_577_836_800_000, 1_672_531_200_000, 1_735_689_600_000)
"""2020-01-01, 2023-01-01, 2025-01-01 UTC. 결과 평균은 읽지 않는다."""

PROPOSED_TRAIN_START_MS = int(datetime(2021, 1, 3, tzinfo=UTC).timestamp() * 1000)
"""G5 8,824개 1h 봉 warm-up 이후의 첫 온전한 UTC 날짜."""


def cost_bps() -> float:
    return FEE_BPS + SLIPPAGE_BPS * TRIGGER_MOVE_RATIO


def annual_counts(times: list[int], triggers: list[Trigger]) -> dict[str, int]:
    """성과를 열지 않고 UTC 연도별 예상 거래 수만 센다."""
    boundaries = {
        year: int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000) for year in range(2020, 2028)
    }
    return {
        str(year): sum(
            boundaries[year] <= times[trigger.index] < boundaries[year + 1] for trigger in triggers
        )
        for year in range(2020, 2027)
    }


def load_bars(
    conn: sqlite3.Connection, exchange: str
) -> tuple[list[int], list[float], list[float], list[float]]:
    rows = conn.execute(
        "SELECT open_ms, close, high, low FROM candles"
        " WHERE exchange=? AND symbol='BTCUSDT' AND timeframe='5m' AND close > 0"
        " ORDER BY open_ms",
        (exchange,),
    ).fetchall()
    return (
        [int(r[0]) for r in rows],
        [float(r[1]) for r in rows],
        [float(r[2]) for r in rows],
        [float(r[3]) for r in rows],
    )


def apply_risk_limits(
    times: list[int],
    triggers: list[Trigger],
    horizon_bars: int,
    *,
    same_direction_limit: int,
    daily_limit: int,
    cooldown_bars: int,
) -> list[Trigger]:
    """TRADING_CORE 빈도 한도와 보유·대기 구간을 적용한 **실제 거래 가능** 부분집합.

    한도는 완화하지 않는다. 걸러진 트리거는 표본이 아니다. 한 거래가 점유하는 구간은
    보유 horizon + `reentry_cooldown_minutes` 다.
    """
    out: list[Trigger] = []
    streak = 0
    previous: int | None = None
    day: str | None = None
    today = 0
    busy_until = -1
    for trigger in triggers:
        if trigger.index <= busy_until:
            continue
        stamp = datetime.fromtimestamp(times[trigger.index] / 1000, UTC).strftime("%Y-%m-%d")
        if stamp != day:
            day, today = stamp, 0
        streak = streak + 1 if trigger.direction == previous else 1
        previous = trigger.direction
        if streak > same_direction_limit or today >= daily_limit:
            continue
        today += 1
        out.append(trigger)
        busy_until = trigger.index + horizon_bars + cooldown_bars
    return out


def trade_pnl_sd(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    vol: list[float | None],
    triggers: list[Trigger],
    horizon_bars: int,
    min_stop_distance: float,
    *,
    preserve_stop: bool = False,
) -> tuple[float, int, float]:
    """거래당 손익 SD, 표본 수, 손절 체결 비율. **평균은 돌려주지 않는다.**

    같은 봉 안의 선후를 알 수 없으므로 손절 우선. 목표가는 두지 않는다.
    """
    values: list[float] = []
    stopped = 0
    for trigger in triggers:
        index = trigger.index
        sigma = vol[index]
        if sigma is None or sigma <= 0.0 or index + horizon_bars >= len(closes):
            continue
        strategy_distance = STOP_MULTIPLE * sigma * horizon_bars**0.5
        distance = strategy_distance if preserve_stop else max(strategy_distance, min_stop_distance)
        entry = closes[index]
        stop = entry * (1 - distance) if trigger.direction > 0 else entry * (1 + distance)
        outcome: float | None = None
        for j in range(index + 1, index + horizon_bars + 1):
            hit = lows[j] <= stop if trigger.direction > 0 else highs[j] >= stop
            if hit:
                outcome = -distance * 10_000
                stopped += 1
                break
        if outcome is None:
            exit_price = closes[index + horizon_bars]
            outcome = trigger.direction * (exit_price - entry) / entry * 10_000
        values.append(outcome)
    if len(values) < 2:
        return 0.0, len(values), 0.0
    return statistics.stdev(values), len(values), stopped / len(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/phase0/a1_design.json")
    parser.add_argument("--preserve-stop", action="store_true")
    args = parser.parse_args()

    config = load_config().config
    risk = config.risk
    floor = float(risk.min_stop_distance)
    same_direction = int(risk.same_direction_limit)
    daily = int(risk.daily_trade_limit)
    cooldown_bars = int(risk.reentry_cooldown_minutes) // 5
    min_hold_bars = int(risk.min_hold_minutes) // 5

    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    times, closes, highs, lows = load_bars(conn, config.exchange)
    if not closes:
        print("5분봉이 없다.")
        return 1
    years = (times[-1] - times[0]) / MS_PER_YEAR
    cost = cost_bps()

    print(f"BTCUSDT 5분봉 {len(closes):,}개 · {years:.2f}년")
    print(f"비용 {cost:.1f}bps (수수료 {FEE_BPS} + 슬리피지 {SLIPPAGE_BPS}×{TRIGGER_MOVE_RATIO})")
    print(
        f"손절 하한 {floor:.1%} · 같은 방향 연속 {same_direction} · 하루 {daily}회"
        f" · 최소 보유 {risk.min_hold_minutes}분 · 재진입 대기 {risk.reentry_cooldown_minutes}분"
    )
    print(f"검정력 {DEFAULT_POWER:.0%} · OOS 최소 거래 {OOS_TRADES}\n")
    print("SD·표본·빈도만 읽는다. 방향별 수익 평균은 계산하지 않는다.\n")

    vol = rolling_vol(closes)
    main_flags = contraction_flags(vol)
    control_flags = [i >= VOL_WINDOW_BARS + RANK_WINDOW_BARS for i in range(len(vol))]
    raw = breakout_triggers(closes, highs, lows, main_flags)
    raw_control = breakout_triggers(closes, highs, lows, control_flags)
    print(f"한도 적용 전 트리거 {len(raw):,}회 (연 {len(raw) / years:.0f}회)\n")

    header = (
        f"{'horizon':>8} {'거래가능':>8} {'연':>5} {'거래당SD':>9}"
        f" {'손절율':>7} {'필요효과':>9} {'TRAIN필요':>10} {'OOS도달':>8}"
    )
    print(header)
    print("-" * len(header))
    rows = []
    control_rows = []
    for hours in HORIZON_HOURS:
        bars = hours * BARS_PER_HOUR
        if bars < min_hold_bars:
            print(f"{hours:>6}h  최소 보유 {risk.min_hold_minutes}분 미만 — 제외")
            continue
        usable = apply_risk_limits(
            times,
            raw,
            bars,
            same_direction_limit=same_direction,
            daily_limit=daily,
            cooldown_bars=cooldown_bars,
        )
        sd, n, stop_rate = trade_pnl_sd(
            closes, highs, lows, vol, usable, bars, floor, preserve_stop=args.preserve_stop
        )
        if n < 2:
            continue
        needed = detectable_effect_bps(sd, OOS_TRADES, cost)
        train_need = required_samples(sd, needed, cost)
        per_year = n / years
        print(
            f"{hours:>6}h {n:>8,} {per_year:>5.0f} {sd:>9.1f}"
            f" {stop_rate:>7.1%} {needed:>8.1f}b {train_need:>6,}/{n:<4,}"
            f" {OOS_TRADES / per_year:>6.1f}년"
        )
        rows.append(
            {
                "horizon_hours": hours,
                "tradeable": n,
                "per_year": per_year,
                "trade_pnl_sd_bps": sd,
                "stop_rate": stop_rate,
                "required_effect_bps": needed,
                "train_required_samples": train_need,
                "oos_years_to_100": OOS_TRADES / per_year,
            }
        )
        control = apply_risk_limits(
            times,
            raw_control,
            bars,
            same_direction_limit=same_direction,
            daily_limit=daily,
            cooldown_bars=cooldown_bars,
        )
        control_sd, control_n, control_stops = trade_pnl_sd(
            closes, highs, lows, vol, control, bars, floor, preserve_stop=args.preserve_stop
        )
        counts = []
        control_counts = []
        for start_ms, end_ms in zip(
            SPLIT_BOUNDS_MS, (*SPLIT_BOUNDS_MS[1:], times[-1] + 1), strict=True
        ):
            counts.append(sum(start_ms <= times[t.index] < end_ms for t in usable))
            control_counts.append(sum(start_ms <= times[t.index] < end_ms for t in control))
        train_main = [
            t for t in usable if SPLIT_BOUNDS_MS[0] <= times[t.index] < SPLIT_BOUNDS_MS[1]
        ]
        train_control = [
            t for t in control if SPLIT_BOUNDS_MS[0] <= times[t.index] < SPLIT_BOUNDS_MS[1]
        ]
        train_sd, _, _ = trade_pnl_sd(
            closes, highs, lows, vol, train_main, bars, floor, preserve_stop=args.preserve_stop
        )
        train_control_sd, _, _ = trade_pnl_sd(
            closes,
            highs,
            lows,
            vol,
            train_control,
            bars,
            floor,
            preserve_stop=args.preserve_stop,
        )
        difference_2se_bps = (
            2 * (train_sd**2 / len(train_main) + train_control_sd**2 / len(train_control)) ** 0.5
            if train_main and train_control
            else None
        )
        rows[-1]["split_counts"] = counts
        rows[-1]["annual_counts"] = annual_counts(times, usable)
        rows[-1]["post_warmup_train_count"] = sum(
            PROPOSED_TRAIN_START_MS <= times[t.index] < SPLIT_BOUNDS_MS[1] for t in usable
        )
        rows[-1]["train_trade_pnl_sd_bps"] = train_sd
        rows[-1]["control_difference_2se_bps"] = difference_2se_bps
        control_rows.append(
            {
                "horizon_hours": hours,
                "tradeable": control_n,
                "trade_pnl_sd_bps": control_sd,
                "stop_rate": control_stops,
                "split_counts": control_counts,
                "annual_counts": annual_counts(times, control),
                "post_warmup_train_count": sum(
                    PROPOSED_TRAIN_START_MS <= times[t.index] < SPLIT_BOUNDS_MS[1] for t in control
                ),
                "train_trade_pnl_sd_bps": train_control_sd,
            }
        )

    print("\n'필요효과' = OOS 100거래로 확인 가능한 최소 총우위. C8 선택지 1 에 따라")
    print("이 값을 사전등록 기준으로 쓴다. TRAIN 이 아니라 OOS 가 구속조건이다.")

    payload = {
        "symbol": "BTCUSDT",
        "years": years,
        "cost_bps": cost,
        "power": DEFAULT_POWER,
        "oos_min_trades": OOS_TRADES,
        "stop_multiple": STOP_MULTIPLE,
        "min_stop_distance": floor,
        "stop_action": "REDUCE_POSITION" if args.preserve_stop else "WIDEN",
        "same_direction_limit": same_direction,
        "daily_limit": daily,
        "raw_triggers": len(raw),
        "raw_control_triggers": len(raw_control),
        "breakout_reference": "prior_6h_high_low_not_close_extrema",
        "note": "위험 한도·손절 하한 적용. SD/빈도만 측정하고 방향별 평균은 미계산.",
        "horizons": rows,
        "control_horizons": control_rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
