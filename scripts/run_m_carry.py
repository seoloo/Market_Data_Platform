"""M/v1 다중 종목 캐리를 사전등록(docs/research/13)대로 실행·판정한다.

사용 예::

    python scripts/run_m_carry.py coverage   # 데이터 적재율·SD만 (수익 평균 없음)
    python scripts/run_m_carry.py train      # TRAIN 1회
    python scripts/run_m_carry.py validate   # TRAIN PASS 뒤 1회: VALIDATION + STRESS

train·validate는 작업 트리가 깨끗해야 하고, 이미 판정 파일이 있으면 거부한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

from auto_trading.backtest.carry import (
    annualized,
    judge_carry,
    max_drawdown,
    monthly_returns,
    yearly_returns,
)
from auto_trading.backtest.multi_carry import (
    MultiBar,
    MultiCarryConfig,
    MultiCarryResult,
    simulate_multi_carry,
)

DB = Path("data/trading.db")
UNIVERSE = Path("reports/m/universe.json")
UNIVERSE_SHA256 = "b4aa8d9133691de13707f32bcf61c5ff628073d7b329f3d4ddbda5317b0fd2fd"
OUTPUT = Path("reports/m")
INITIAL_EQUITY = Decimal(10_000)
_HOUR = 3_600_000


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


PERIODS = {
    "train": (_ms(2023, 1, 1), _ms(2025, 1, 1) - 1, (2023, 2024)),
    "validation": (_ms(2025, 1, 1), _ms(2026, 9, 1) - 1, (2025, 2026)),
    "stress": (_ms(2020, 6, 1), _ms(2023, 1, 1) - 1, (2020, 2021, 2022)),
}

Bars = dict[str, dict[int, MultiBar]]
Funding = dict[str, list[tuple[int, Decimal]]]


def load_universe() -> dict[int, list[str]]:
    text = UNIVERSE.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.rstrip("\n").encode()).hexdigest()
    if digest != UNIVERSE_SHA256:
        raise SystemExit(f"유니버스 파일이 사전등록 값과 다르다: {digest}")
    return {int(k): v for k, v in json.loads(text).items()}


def load(universe: dict[int, list[str]]) -> tuple[Bars, Funding]:
    symbols = sorted({s for members in universe.values() for s in members})
    bars: Bars = {}
    funding: Funding = defaultdict(list)
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        for symbol in symbols:
            spot = {
                int(ms): (Decimal(str(o)), Decimal(str(c)))
                for ms, o, c in conn.execute(
                    "SELECT open_ms, open, close FROM candles WHERE exchange = 'binance_spot'"
                    " AND symbol = ? AND timeframe = '1h'",
                    (symbol,),
                )
            }
            series: dict[int, MultiBar] = {}
            for ms, o, h, c in conn.execute(
                "SELECT open_ms, open, high, close FROM candles WHERE exchange = 'binance_usdm'"
                " AND symbol = ? AND timeframe = '1h'",
                (symbol,),
            ):
                s = spot.get(int(ms))
                series[int(ms)] = MultiBar(
                    Decimal(str(o)),
                    Decimal(str(h)),
                    Decimal(str(c)),
                    s[0] if s else None,
                    s[1] if s else None,
                )
            bars[symbol] = series
            funding[symbol] = [
                (int(ms), Decimal(str(rate)))
                for ms, rate in conn.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                    " AND symbol = ? ORDER BY funding_ms",
                    (symbol,),
                )
            ]
    return bars, dict(funding)


def coverage(universe: dict[int, list[str]], bars: Bars, funding: Funding) -> dict[str, Any]:
    """유니버스 종목-시간 중 선물·현물 봉이 있는 비율, 종목-달 funding 유무, 월간 funding 합 SD."""
    out: dict[str, Any] = {}
    for period, (from_ms, to_ms, _) in PERIODS.items():
        need = have_perp = have_both = 0
        months_need = months_funding = 0
        monthly_sums: list[float] = []
        for start, members in universe.items():
            if not from_ms <= start <= to_ms:
                continue
            end = min(_next_month(start), to_ms + 1)
            for symbol in members:
                series = bars.get(symbol, {})
                for hour in range(start, end, _HOUR):
                    need += 1
                    bar = series.get(hour)
                    have_perp += bar is not None
                    have_both += bar is not None and bar.spot_open is not None
                rates = [float(r) for ms, r in funding.get(symbol, []) if start <= ms < end]
                months_need += 1
                if rates:
                    months_funding += 1
                    monthly_sums.append(sum(rates) * 1e4)
        out[period] = {
            "symbol_hours": need,
            "perp_share": have_perp / need if need else math.nan,
            "perp_and_spot_share": have_both / need if need else math.nan,
            "symbol_months": months_need,
            "funding_share": months_funding / months_need if months_need else math.nan,
            "monthly_funding_sum_sd_bps": statistics.stdev(monthly_sums)
            if len(monthly_sums) > 1
            else math.nan,
        }
    return out


def _next_month(ms: int) -> int:
    moment = datetime.fromtimestamp(ms / 1000, tz=UTC)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return _ms(year, month, 1)


def replay(
    universe: dict[int, list[str]], bars: Bars, funding: Funding, period: str, *, control: bool
) -> MultiCarryResult:
    from_ms, to_ms, _ = PERIODS[period]
    return simulate_multi_carry(
        bars,
        funding,
        universe,
        from_ms=from_ms,
        to_ms=to_ms,
        initial_equity=INITIAL_EQUITY,
        cfg=MultiCarryConfig(rank_by_volume=control),
    )


def describe(result: MultiCarryResult) -> dict[str, Any]:
    top = sorted(result.funding_by_symbol.items(), key=lambda kv: kv[1], reverse=True)
    return {
        **result.summary(),
        "forced_closes": result.forced_closes,
        "symbols_held": len(result.symbols_held),
        "funding_by_symbol_top10": {k: str(v) for k, v in top[:10]},
        "funding_by_symbol_bottom5": {k: str(v) for k, v in top[-5:]},
        "annualized": annualized(result),
        "mdd": max_drawdown(result),
        "yearly": {str(k): v for k, v in yearly_returns(result).items()},
        "monthly": monthly_returns(result),
    }


def evaluate(
    period: str, universe: dict[int, list[str]], bars: Bars, funding: Funding
) -> dict[str, Any]:
    _, _, years = PERIODS[period]
    main = replay(universe, bars, funding, period, control=False)
    control = replay(universe, bars, funding, period, control=True)
    if period == "stress":
        verdict = judge_carry(main, years=years, require_return=False, max_mdd=0.10)
    else:
        verdict = judge_carry(main, years=years)
    print(f"{period}: {verdict.status}", flush=True)
    for name, passed, detail in verdict.criteria:
        print(f"  {name}: {'통과' if passed else '실패'} {detail}", flush=True)
    print(
        f"  강제 정리 {main.forced_closes} · 보유 종목 {len(main.symbols_held)} · "
        f"대조군(거래대금 순) 연환산 {annualized(control):.4f} MDD {max_drawdown(control):.4f}",
        flush=True,
    )
    return {
        "verdict": verdict.to_dict(),
        "main": describe(main),
        "control_by_volume": describe(control),
    }


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run(stage: str, output: Path) -> int:
    if stage == "coverage":
        universe = load_universe()
        bars, funding = load(universe)
        print(json.dumps(coverage(universe, bars, funding), indent=1))
        return 0
    path = output / stage / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    if stage == "validate":
        train = output / "train" / "verdict.json"
        if not train.exists():
            print("TRAIN 판정이 없다.")
            return 3
        if json.loads(train.read_text(encoding="utf-8"))["train"]["verdict"]["status"] != "PASS":
            print("TRAIN이 PASS가 아니다. 사전등록상 VALIDATION을 실행하지 않는다.")
            return 3
    universe = load_universe()
    bars, funding = load(universe)
    record: dict[str, Any] = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {k: str(v) for k, v in asdict(MultiCarryConfig()).items()},
        "universe_sha256": UNIVERSE_SHA256,
        "initial_equity": str(INITIAL_EQUITY),
    }
    for period in ("train",) if stage == "train" else ("validation", "stress"):
        record[period] = evaluate(period, universe, bars, funding)
    _write(path, record)
    print(f"기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("coverage", "train", "validate"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return run(args.stage, args.output)


if __name__ == "__main__":
    sys.exit(main())
