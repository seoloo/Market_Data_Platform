#!/usr/bin/env python
"""post-only 진입의 체결률과 **역선택**을 실측한다 (D16-2).

18-h2-validation-result.md §3.3 의 선택지 B 는 "비용을 18 → 12.5bps 로 줄이면
판정 가능해진다" 는 것이다. 그 전제가 맞는지 데이터로 확인하는 것이 이 스크립트다.

수수료 절감만 세면 안 된다. 지정가는 **내 판단이 틀렸을 때 더 잘 체결된다**.
그래서 체결된 시점과 놓친 시점의 선행수익을 따로 재서, 순비용을 비교한다.

사용 예::

    python scripts/measure_maker_fills.py --years 2021 2023 --timeout 15
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

from auto_trading.backtest.maker import TimeoutPolicy, simulate_post_only  # noqa: E402
from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.config.schema import CostConfig  # noqa: E402
from auto_trading.core.enums import PositionSide, Timeframe  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402

HORIZON_MS = 21_600_000
"""6시간. F5 의 효과가 가장 컸던 horizon 이라 비교 기준을 맞춘다."""

BPS = Decimal("10000")


Row = dict[str, float | int | str | None]


def num(row: Row, key: str) -> float:
    """숫자 필드 추출. 값이 없으면 호출 자체가 잘못된 것이므로 드러낸다."""
    value = row[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{key} 가 숫자가 아니다: {value!r}")
    return float(value)


def year_bounds(year: int) -> tuple[int, int]:
    start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1
    return start, end


def collect(args: argparse.Namespace, cost: CostConfig) -> list[Row]:
    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    repo = CandleRepository(conn, exchange=config.exchange)
    rows: list[Row] = []

    for year in args.years:
        start, end = year_bounds(year)
        minutes = repo.get_range(
            config.symbol, Timeframe.M1, from_ms=start, to_ms=end + HORIZON_MS + 3_600_000
        )
        by_open = {c.open_ms: c for c in minutes}
        index = {c.open_ms: i for i, c in enumerate(minutes)}
        step = args.stride_min * 60_000
        cursor = start
        while cursor <= end:
            bar = by_open.get(cursor)
            position = index.get(cursor)
            exit_bar = by_open.get(cursor + HORIZON_MS)
            if bar is None or position is None or exit_bar is None:
                cursor += step
                continue
            following = minutes[position + 1 : position + 1 + args.timeout + 10]
            if not following:
                cursor += step
                continue
            for side in (PositionSide.LONG, PositionSide.SHORT):
                sign = Decimal(side.sign)
                taker_entry = following[0].open
                taker_ret = float((exit_bar.open - taker_entry) / taker_entry * BPS * sign)
                for policy in (TimeoutPolicy.CANCEL, TimeoutPolicy.CHASE):
                    attempt = simulate_post_only(
                        side,
                        bar.close,
                        following,
                        maker_fee_rate=cost.maker_fee_rate,
                        taker_fee_rate=cost.taker_fee_rate,
                        chase_slippage_bps=cost.slippage_entry_rate * BPS,
                        timeout_minutes=args.timeout,
                        policy=policy,
                    )
                    maker_ret = None
                    if attempt.fill_price is not None:
                        maker_ret = float(
                            (exit_bar.open - attempt.fill_price) / attempt.fill_price * BPS * sign
                        )
                    rows.append(
                        {
                            "year": year,
                            "side": side.value,
                            "policy": policy.value,
                            "outcome": attempt.outcome.value,
                            "wait": attempt.wait_minutes,
                            "entry_cost_bps": float(attempt.cost_bps),
                            "maker_ret_bps": maker_ret,
                            "taker_ret_bps": taker_ret,
                        }
                    )
            cursor += step
    return rows


def report(rows: list[Row], cost: CostConfig, timeout: int) -> None:
    taker_entry = float((cost.taker_fee_rate + cost.slippage_entry_rate) * BPS)
    exit_cost = taker_entry  # 청산은 어느 쪽이든 시장가 (보호주문)
    cancel = [r for r in rows if r["policy"] == "cancel"]
    filled = [r for r in cancel if r["outcome"] == "filled"]
    missed = [r for r in cancel if r["outcome"] == "missed"]

    print(f"\n지정가 대기 {timeout}분 · 보유 6시간 · 시도 {len(cancel):,}건")
    rate = len(filled) / len(cancel) if cancel else 0.0
    print(f"체결률: {len(filled):,}/{len(cancel):,} = {rate:.1%}")
    if filled:
        print(f"  평균 대기: {stats.mean(num(r, 'wait') for r in filled):.1f}분")

    print("\n── 역선택 검정: 체결된 시점 vs 놓친 시점의 선행수익 (비용 차감 전)")
    stats_by_label: dict[str, tuple[float, float, int]] = {}
    for label, group in (("체결됨", filled), ("놓침", missed)):
        if len(group) < 30:
            print(f"  {label:<8} 표본 부족 (n={len(group)})")
            continue
        values = [num(r, "taker_ret_bps") for r in group]
        mean = stats.mean(values)
        stderr = stats.stdev(values) / len(values) ** 0.5
        stats_by_label[label] = (mean, stderr, len(values))
        print(f"  {label:<8} n={len(values):<6} {mean:+7.2f} ± {stderr:5.2f} bps")
    if len(stats_by_label) == 2:
        (m_a, s_a, _), (m_b, s_b, _) = stats_by_label["체결됨"], stats_by_label["놓침"]
        diff = m_a - m_b
        stderr = (s_a**2 + s_b**2) ** 0.5
        t_stat = diff / stderr if stderr else 0.0
        verdict = "역선택 확인 (체결된 쪽이 유의하게 나쁨)" if t_stat < -2 else "유의한 역선택 없음"
        print(f"  차이 {diff:+7.2f} ± {stderr:5.2f}  t={t_stat:+.2f} → {verdict}")

    print("\n── 순수익 비교 (진입+청산 비용 차감)")
    print(f"{'전략':<24}{'거래':>9}{'평균 순수익':>14}{'거래당 비용':>13}")
    taker_net = [num(r, "taker_ret_bps") - taker_entry - exit_cost for r in cancel]
    print(
        f"{'taker 진입':<24}{len(taker_net):>9,}{stats.mean(taker_net):>+13.2f}"
        f"{taker_entry + exit_cost:>13.1f}"
    )
    for policy, name in (("cancel", "maker (미체결 취소)"), ("chase", "maker (미체결 추격)")):
        group = [r for r in rows if r["policy"] == policy and r["maker_ret_bps"] is not None]
        if not group:
            continue
        net = [num(r, "maker_ret_bps") - num(r, "entry_cost_bps") - exit_cost for r in group]
        avg_cost = stats.mean(num(r, "entry_cost_bps") for r in group) + exit_cost
        print(f"{name:<24}{len(net):>9,}{stats.mean(net):>+13.2f}{avg_cost:>13.1f}")

    print(
        "\n주의: 방향을 무작위로 절반씩 잡았으므로 순수익 자체는 0 근처가 정상이다."
        "\n      읽어야 할 것은 **taker 와 maker 의 차이**와 위의 역선택 크기다."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2021, 2023])
    parser.add_argument("--timeout", type=int, default=15, help="지정가 대기 시간(분)")
    parser.add_argument("--stride-min", type=int, default=360, help="표본 간격(분)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/phase4/maker_fills.json")
    args = parser.parse_args()

    cost = load_config().config.risk.cost
    rows = collect(args, cost)
    report(rows, cost, args.timeout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows[:3000], ensure_ascii=False), encoding="utf-8")
    print(f"\n기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
