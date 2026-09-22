#!/usr/bin/env python
"""F5 신호 시점에서 post-only 체결률·역선택을 재측정한다 (19 §5 1단계).

19 의 측정은 **무작위 시점** 기준이었다. 그게 그 문서의 가장 큰 한계다 —
신호가 뜨는 순간은 시장이 빠르게 움직이는 구간일 수 있고, 그러면 체결 양상이
완전히 다르다. 신호 시점으로 한정해 같은 측정을 반복해야 비용 기준을 확정할 수 있다.

전략을 만들지 않는다. F5 하위 decile 시점에 롱 post-only 를 걸었다고 가정하고
체결 여부와 그 뒤 수익만 본다.

사용 예::

    python scripts/measure_signal_fills.py --years 2021 2023 --timeout 15
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as stats
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.backtest.maker import (  # noqa: E402
    MakerOutcome,
    TimeoutPolicy,
    simulate_post_only,
)
from auto_trading.backtest.signals_h2 import align_inputs, build_signals  # noqa: E402
from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.config.schema import CostConfig  # noqa: E402
from auto_trading.core.enums import PositionSide, Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402
from auto_trading.storage.repositories.funding import FundingRateRepository  # noqa: E402
from auto_trading.storage.repositories.metrics import PerpMetricsRepository  # noqa: E402

SIGNAL = "F5_crowd_divergence"
"""선별에서 유일하게 구조를 보인 신호 (17 §2). 하위 decile → 롱."""

HORIZON_MS = 21_600_000
"""6시간. 효과가 가장 컸던 horizon."""

EMBARGO_MS = HORIZON_MS
"""중첩 제거는 horizon 과 같게 (15 §4)."""

DECILE = 0.10
BPS = Decimal("10000")
STEP_MS = 300_000


def year_bounds(year: int) -> tuple[int, int]:
    start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)
    return start, int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1


def signal_times(years: list[int]) -> tuple[list[int], int]:
    """F5 하위 decile 시점 (중첩 제거 후)과 전체 관측 수."""
    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    candles_repo = CandleRepository(conn, exchange=config.exchange)
    metrics_repo = PerpMetricsRepository(conn, exchange=config.exchange)
    funding_repo = FundingRateRepository(conn, exchange=config.exchange)

    pairs: list[tuple[int, float]] = []
    for year in years:
        start, end = year_bounds(year)
        metrics = metrics_repo.get_range(config.symbol, start, end)
        if not metrics:
            continue
        candles = candles_repo.get_range(
            config.symbol, Timeframe.M5, from_ms=start, to_ms=end + HORIZON_MS + STEP_MS
        )
        funding = funding_repo.get_range(config.symbol, start - 40 * 86_400_000, end)
        inputs = align_inputs(metrics, candles, funding)
        values = build_signals(inputs)[SIGNAL]
        pairs.extend(
            (at_ms, value)
            for at_ms, value in zip(inputs.at_ms, values, strict=True)
            if value is not None
        )

    pairs.sort()
    ordered = sorted(value for _, value in pairs)
    edge = ordered[max(0, int(len(ordered) * DECILE) - 1)]
    picked: list[int] = []
    last = -(10**18)
    for at_ms, value in pairs:
        if value <= edge and at_ms - last >= EMBARGO_MS:
            picked.append(at_ms)
            last = at_ms
    return picked, len(pairs)


def measure(
    times: list[int], years: list[int], cost: CostConfig, timeout: int
) -> list[dict[str, float | str]]:
    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    repo = CandleRepository(conn, exchange=config.exchange)
    minutes: list[Candle] = []
    for year in years:
        start, end = year_bounds(year)
        minutes.extend(
            repo.get_range(
                config.symbol, Timeframe.M1, from_ms=start, to_ms=end + HORIZON_MS + 3_600_000
            )
        )
    by_open = {c.open_ms: c for c in minutes}
    index = {c.open_ms: i for i, c in enumerate(minutes)}

    rows: list[dict[str, float | str]] = []
    for at_ms in times:
        entry_bar_ms = at_ms + STEP_MS  # 신호 봉 다음 5분봉 (R-1)
        anchor = by_open.get(entry_bar_ms)
        position = index.get(entry_bar_ms)
        exit_bar = by_open.get(entry_bar_ms + HORIZON_MS)
        if anchor is None or position is None or exit_bar is None:
            continue
        following = minutes[position : position + timeout + 10]
        if not following:
            continue
        reference = by_open[at_ms].close if at_ms in by_open else anchor.open
        taker_ret = float((exit_bar.open - anchor.open) / anchor.open * BPS)
        attempt = simulate_post_only(
            PositionSide.LONG,
            reference,
            following,
            maker_fee_rate=cost.maker_fee_rate,
            taker_fee_rate=cost.taker_fee_rate,
            chase_slippage_bps=cost.slippage_entry_rate * BPS,
            timeout_minutes=timeout,
            policy=TimeoutPolicy.CANCEL,
        )
        maker_ret = (
            float((exit_bar.open - attempt.fill_price) / attempt.fill_price * BPS)
            if attempt.fill_price is not None
            else None
        )
        rows.append(
            {
                "at_ms": at_ms,
                "outcome": attempt.outcome.value,
                "wait": attempt.wait_minutes,
                "entry_cost_bps": float(attempt.cost_bps),
                "taker_ret_bps": taker_ret,
                "maker_ret_bps": maker_ret if maker_ret is not None else float("nan"),
            }
        )
    return rows


def report(rows: list[dict[str, float | str]], cost: CostConfig, total: int, timeout: int) -> None:
    exit_cost = float((cost.taker_fee_rate + cost.slippage_entry_rate) * BPS)
    taker_entry = exit_cost
    filled = [r for r in rows if r["outcome"] == MakerOutcome.FILLED.value]
    missed = [r for r in rows if r["outcome"] == MakerOutcome.MISSED.value]

    print(f"\nF5 하위 decile · 대기 {timeout}분 · 보유 6시간")
    print(f"관측 {total:,} → 중첩 제거 후 신호 {len(rows):,}건")
    rate = len(filled) / len(rows) if rows else 0.0
    print(f"체결률: {len(filled):,}/{len(rows):,} = {rate:.1%}")
    if filled:
        print(f"  평균 대기: {stats.mean(float(r['wait']) for r in filled):.1f}분")

    print("\n── 역선택 (taker 기준가 선행수익, 비용 차감 전)")
    summary: dict[str, tuple[float, float]] = {}
    for label, group in (("체결됨", filled), ("놓침", missed)):
        if len(group) < 10:
            print(f"  {label:<8} 표본 부족 (n={len(group)})")
            continue
        values = [float(r["taker_ret_bps"]) for r in group]
        stderr = stats.stdev(values) / len(values) ** 0.5
        summary[label] = (stats.mean(values), stderr)
        print(f"  {label:<8} n={len(values):<5} {stats.mean(values):+8.2f} ± {stderr:5.2f} bps")
    if len(summary) == 2:
        (m_a, s_a), (m_b, s_b) = summary["체결됨"], summary["놓침"]
        diff, stderr = m_a - m_b, (s_a**2 + s_b**2) ** 0.5
        t_stat = diff / stderr if stderr else 0.0
        print(f"  차이 {diff:+8.2f} ± {stderr:5.2f}  t={t_stat:+.2f}")

    print("\n── F5 롱 진입의 순수익 (진입+청산 비용 차감)")
    print(f"{'방식':<22}{'거래':>7}{'평균 순수익':>15}{'하한(−2SE)':>13}")
    for label, values, entry_cost in (
        ("taker 진입", [float(r["taker_ret_bps"]) for r in rows], taker_entry),
        (
            "maker 진입 (취소)",
            [float(r["maker_ret_bps"]) for r in filled],
            stats.mean(float(r["entry_cost_bps"]) for r in filled) if filled else 0.0,
        ),
    ):
        if len(values) < 10:
            continue
        net = [v - entry_cost - exit_cost for v in values]
        stderr = stats.stdev(net) / len(net) ** 0.5
        mean = stats.mean(net)
        print(f"{label:<22}{len(net):>7}{mean:>+14.2f}{mean - 2 * stderr:>+13.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2021, 2023])
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/phase4/signal_fills.json")
    args = parser.parse_args()

    cost = load_config().config.risk.cost
    times, total = signal_times(args.years)
    rows = measure(times, args.years, cost, args.timeout)
    report(rows, cost, total, args.timeout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"\n기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
