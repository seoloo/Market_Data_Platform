#!/usr/bin/env python
"""R1 중기 추세 연구에 앞서 다중 심볼 일봉의 검정 가능성만 측정한다.

추세 신호를 계산하지 않는다. 시점별 유동성 유니버스에서 심볼을 결정론적으로 섞어
LONG 3 / SHORT 3 포트폴리오를 만들고, 1일·3일·7일 수익률 잡음과 독립 표본 수를 잰다.
평균 수익은 연구 결과가 아니며 0 근처가 정상이다.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics as stats
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zlib import crc32

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.hashing import sha256_hex  # noqa: E402

DAY_MS = 86_400_000
TRAIN_FROM = date(2023, 1, 1)
TRAIN_TO = date(2024, 12, 31)
HORIZONS = {"1d": 1, "3d": 3, "7d": 7}
LOOKBACK_DAYS = 30
MIN_OBSERVED_DAYS = 15
MIN_DAILY_QUOTE = 20_000_000.0
TOP = 30
LEG = 3
COST_BPS = 22.0
EFFECTS_BPS = (30.0, 40.0, 60.0, 100.0)
NON_CRYPTO_PREFIXES = (
    "XAU",
    "XAG",
    "NVDA",
    "MSTR",
    "INTC",
    "CRCL",
    "EWY",
    "TSLA",
    "AAPL",
    "META",
    "GOOGL",
    "AMZN",
    "MSFT",
    "COIN",
    "HOOD",
    "SPY",
    "QQQ",
    "GLD",
    "PLTR",
)

DailySeries = dict[str, dict[int, tuple[float, float]]]


def to_ms(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)


def month_starts(from_date: date, to_date: date) -> list[date]:
    current = from_date.replace(day=1)
    out: list[date] = []
    while current <= to_date:
        out.append(current)
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    return out


def build_universe(
    daily: DailySeries,
    *,
    from_date: date,
    to_date: date,
    top: int = TOP,
) -> dict[str, list[str]]:
    """각 월 시작 직전 30일 거래대금만 사용해 유니버스를 고른다."""
    universe: dict[str, list[str]] = {}
    for month in month_starts(from_date, to_date):
        as_of = to_ms(month)
        window_from = as_of - LOOKBACK_DAYS * DAY_MS
        ranked: list[tuple[float, str]] = []
        for symbol, series in daily.items():
            if symbol.startswith(NON_CRYPTO_PREFIXES):
                continue
            volumes = [
                volume for at_ms, (_, volume) in series.items() if window_from <= at_ms < as_of
            ]
            if len(volumes) < MIN_OBSERVED_DAYS:
                continue
            average = stats.mean(volumes)
            if average >= MIN_DAILY_QUOTE:
                ranked.append((average, symbol))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        universe[month.strftime("%Y-%m")] = [symbol for _, symbol in ranked[:top]]
    return universe


def random_spreads(
    daily: DailySeries,
    universe: dict[str, list[str]],
    *,
    from_date: date,
    to_date: date,
    horizon_days: int,
    leg: int = LEG,
) -> list[float]:
    """embargo=horizon인 결정론적 무작위 롱숏 수익률(bps)."""
    out: list[float] = []
    current = from_date
    while current <= to_date:
        at_ms = to_ms(current)
        later_ms = at_ms + horizon_days * DAY_MS
        returns: list[tuple[str, float]] = []
        for symbol in universe.get(current.strftime("%Y-%m"), []):
            series = daily.get(symbol, {})
            entry = series.get(at_ms)
            exit_ = series.get(later_ms)
            if entry is None or exit_ is None or entry[0] <= 0:
                continue
            returns.append((symbol, (exit_[0] - entry[0]) / entry[0] * 10_000))
        if len(returns) >= 2 * leg:
            order = sorted(
                returns,
                key=lambda item: crc32(f"R1:{horizon_days}:{at_ms}:{item[0]}".encode()),
            )
            longs = [value for _, value in order[-leg:]]
            shorts = [value for _, value in order[:leg]]
            out.append((stats.mean(longs) - stats.mean(shorts)) / 2)
        current += timedelta(days=horizon_days)
    return out


def required_samples(sd_bps: float, cost_bps: float, effect_bps: float) -> int | None:
    margin = effect_bps - cost_bps
    return None if margin <= 0 else math.ceil((2 * sd_bps / margin) ** 2)


def load_daily(from_date: date, to_date: date) -> DailySeries:
    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    daily: DailySeries = defaultdict(dict)
    load_from = to_ms(from_date) - LOOKBACK_DAYS * DAY_MS
    load_to = to_ms(to_date + timedelta(days=max(HORIZONS.values()) + 1))
    for symbol, at_ms, close, quote_volume in conn.execute(
        "SELECT symbol, open_ms, close, quote_volume FROM candles"
        " WHERE exchange=? AND timeframe='1d' AND open_ms>=? AND open_ms<?",
        (config.exchange, load_from, load_to),
    ):
        daily[str(symbol)][int(at_ms)] = (float(close), float(quote_volume))
    conn.close()
    return dict(daily)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", type=date.fromisoformat, default=TRAIN_FROM)
    parser.add_argument("--to", dest="to_date", type=date.fromisoformat, default=TRAIN_TO)
    parser.add_argument("--cost", type=float, default=COST_BPS)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "reports/phase4/r1_power.json",
    )
    args = parser.parse_args()
    if args.from_date >= args.to_date:
        parser.error("--from은 --to보다 빨라야 한다")

    daily = load_daily(args.from_date, args.to_date)
    universe = build_universe(daily, from_date=args.from_date, to_date=args.to_date)
    sizes = [len(members) for members in universe.values()]
    eligible = {month: members for month, members in universe.items() if len(members) >= 2 * LEG}
    payload: dict[str, object] = {
        "research_id": "R1-A",
        "from": args.from_date.isoformat(),
        "to": args.to_date.isoformat(),
        "cost_bps": args.cost,
        "top": TOP,
        "leg": LEG,
        "universe_months": len(universe),
        "universe_min_size": min(sizes, default=0),
        "universe_max_size": max(sizes, default=0),
        "eligible_universe_months": len(eligible),
        "first_eligible_month": min(eligible, default=None),
        "last_eligible_month": max(eligible, default=None),
        "symbols_loaded": len(daily),
        "horizons": {},
    }
    print(
        f"R1-A TRAIN {args.from_date} ~ {args.to_date} · 일봉 심볼 {len(daily)} · "
        f"월 유니버스 {len(universe)}개"
    )
    for label, days in HORIZONS.items():
        spreads = random_spreads(
            daily,
            universe,
            from_date=args.from_date,
            to_date=args.to_date,
            horizon_days=days,
        )
        if len(spreads) < 30:
            raise SystemExit(f"{label} 독립 표본 부족: {len(spreads)}")
        sd_bps = stats.stdev(spreads)
        needs = {
            str(int(effect)): required_samples(sd_bps, args.cost, effect) for effect in EFFECTS_BPS
        }
        feasible = {
            effect: need is not None and need <= len(spreads) for effect, need in needs.items()
        }
        payload["horizons"][label] = {  # type: ignore[index]
            "n": len(spreads),
            "random_mean_bps": stats.mean(spreads),
            "random_sd_bps": sd_bps,
            "required_samples_by_effect_bps": needs,
            "feasible_by_effect_bps": feasible,
        }
        cells = "  ".join(
            f"{effect}bps→{needs[effect]}{'O' if feasible[effect] else 'X'}" for effect in needs
        )
        print(f"  {label}: n={len(spreads):,} · SD={sd_bps:.1f}bps · {cells}")

    payload["digest"] = sha256_hex(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
