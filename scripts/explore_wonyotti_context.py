"""워뇨띠 v3 — 거래당 수익률과 진입 직전 시장 상태, 손실 거래의 역행·정리 시간.

탐색 단계(docs/research/06-wonyotti-exploration.md). 시장 상태는 우리 DB의 Binance USD-M
BTCUSDT(2020-01부터)로 근사하므로 2020~2021 거래만 결합한다. 체결은 BitMEX XBTUSD라
거래소 간 가격 차이가 있다. DB는 읽기 전용으로 연다.

시장 상태는 모두 **진입 시각 이전**의 값만 쓴다(인과). 선택성은 같은 기간의 모든 1시간
시점에서 잰 같은 값의 분포(기준 분포)와 비교한다. 손익 비교는 관찰이며 검증이 아니다.
"""

from __future__ import annotations

import bisect
import json
import sqlite3
import statistics
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scripts.explore_wonyotti_trips import SOURCE, Trip, build_trips, load_xbtusd

DB = Path("data/trading.db")
OUTPUT = Path("reports/wonyotti/summary_v3.json")
_MINUTE = 60_000
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_FROM = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
_TO = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)


@dataclass(frozen=True, slots=True)
class Minutes:
    """Binance 1분봉. 인덱스는 open_ms 오름차순."""

    open_ms: list[int]
    high: list[float]
    low: list[float]
    close: list[float]

    def last_closed_index(self, at_ms: int) -> int:
        """``at_ms`` 이전에 마감된 마지막 봉의 인덱스(없으면 -1)."""
        return bisect.bisect_right(self.open_ms, at_ms - _MINUTE) - 1

    def close_before(self, at_ms: int) -> float | None:
        index = self.last_closed_index(at_ms)
        return self.close[index] if index >= 0 else None

    def range_before(self, at_ms: int, span_ms: int) -> tuple[float, float] | None:
        end = self.last_closed_index(at_ms) + 1
        start = bisect.bisect_left(self.open_ms, at_ms - span_ms)
        if end - start < span_ms // _MINUTE * 0.9:
            return None
        return min(self.low[start:end]), max(self.high[start:end])


@dataclass(frozen=True, slots=True)
class Context:
    ret_1h: float
    ret_24h: float
    position_7d: float
    """0 = 7일 저가, 1 = 7일 고가."""
    funding: float | None


def context_at(
    minutes: Minutes, funding: Sequence[tuple[int, float]], at_ms: int
) -> Context | None:
    now = minutes.close_before(at_ms)
    hour_ago = minutes.close_before(at_ms - _HOUR)
    day_ago = minutes.close_before(at_ms - _DAY)
    week = minutes.range_before(at_ms, 7 * _DAY)
    if now is None or hour_ago is None or day_ago is None or week is None:
        return None
    low, high = week
    times = [ms for ms, _ in funding]
    index = bisect.bisect_right(times, at_ms) - 1
    return Context(
        ret_1h=now / hour_ago - 1,
        ret_24h=now / day_ago - 1,
        position_7d=(now - low) / (high - low) if high > low else 0.5,
        funding=funding[index][1] if index >= 0 else None,
    )


def adverse_excursion(minutes: Minutes, trip: Trip) -> tuple[float, float] | None:
    """(최대 역행 비율, 최대 역행 봉부터 청산까지 시간). 1분봉 고저로 근사한다."""
    if trip.closed is None:
        return None
    start_ms = int(trip.opened.timestamp() * 1000)
    end_ms = int(trip.closed.timestamp() * 1000)
    lo = bisect.bisect_left(minutes.open_ms, start_ms)
    hi = bisect.bisect_right(minutes.open_ms, end_ms)
    if hi <= lo:
        return 0.0, 0.0
    if trip.side > 0:
        worst = min(range(lo, hi), key=lambda i: minutes.low[i])
        excursion = max(0.0, 1 - minutes.low[worst] / trip.open_price)
    else:
        worst = max(range(lo, hi), key=lambda i: minutes.high[i])
        excursion = max(0.0, minutes.high[worst] / trip.open_price - 1)
    return excursion, max(0.0, (end_ms - minutes.open_ms[worst]) / _HOUR)


def load_market(path: Path) -> tuple[Minutes, list[tuple[int, float]]]:
    uri = f"file:{path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT open_ms, high, low, close FROM candles WHERE exchange = 'binance_usdm'"
            " AND symbol = 'BTCUSDT' AND timeframe = '1m' AND open_ms >= ? AND open_ms < ?"
            " ORDER BY open_ms",
            (_FROM - 8 * _DAY, _TO),
        ).fetchall()
        funding = conn.execute(
            "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
            " AND symbol = 'BTCUSDT' AND funding_ms >= ? AND funding_ms < ? ORDER BY funding_ms",
            (_FROM - _DAY, _TO),
        ).fetchall()
    minutes = Minutes(
        open_ms=[int(r[0]) for r in rows],
        high=[float(r[1]) for r in rows],
        low=[float(r[2]) for r in rows],
        close=[float(r[3]) for r in rows],
    )
    return minutes, [(int(ms), float(rate)) for ms, rate in funding]


def _share(values: Sequence[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _bucket_returns(
    pairs: Sequence[tuple[Context, Trip]], key: Callable[[Context, Trip], str]
) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[float]] = {}
    for context, trip in pairs:
        groups.setdefault(key(context, trip), []).append(trip.net_bps)
    return {
        name: {
            "n": len(values),
            "mean_bps": statistics.fmean(values),
            "median_bps": statistics.median(values),
            "win_rate": sum(v > 0 for v in values) / len(values),
        }
        for name, values in sorted(groups.items())
    }


def main() -> int:
    fills, funding_bitmex, _ = load_xbtusd(SOURCE)
    trips = build_trips(fills, funding_bitmex)
    per_year: dict[str, dict[str, float | int | None]] = {}
    for year in sorted({t.opened.strftime("%Y") for t in trips}):
        values = [t.net_bps for t in trips if t.opened.strftime("%Y") == year]
        per_year[year] = {
            "n": len(values),
            "mean_bps": statistics.fmean(values),
            "median_bps": statistics.median(values),
            "win_rate": sum(v > 0 for v in values) / len(values),
        }

    minutes, funding = load_market(DB)
    pairs: list[tuple[Context, Trip]] = []
    for trip in trips:
        at_ms = int(trip.opened.timestamp() * 1000)
        if _FROM <= at_ms < _TO and (context := context_at(minutes, funding, at_ms)) is not None:
            pairs.append((context, trip))
    baseline = [
        c
        for at_ms in range(_FROM, _TO, _HOUR)
        if (c := context_at(minutes, funding, at_ms)) is not None
    ]

    def fade(context: Context, trip: Trip) -> str:
        return "against_24h_move" if context.ret_24h * trip.side < 0 else "with_24h_move"

    def extreme(context: Context, trip: Trip) -> str:
        top = context.position_7d >= 0.8
        bottom = context.position_7d <= 0.2
        if trip.side < 0:
            return "short_near_7d_high" if top else "short_elsewhere"
        return "long_near_7d_low" if bottom else "long_elsewhere"

    losers = [t for _, t in pairs if t.net_bps < 0]
    excursions = [e for t in losers if (e := adverse_excursion(minutes, t)) is not None]
    shorts = [c for c, t in pairs if t.side < 0]
    longs = [c for c, t in pairs if t.side > 0]
    summary = {
        "net_bps_by_year_all_trips": per_year,
        "context_trips_2020_2021": len(pairs),
        "baseline_hours": len(baseline),
        "share_prior_24h_up": {
            "shorts": _share([c.ret_24h > 0 for c in shorts]),
            "longs": _share([c.ret_24h > 0 for c in longs]),
            "baseline": _share([c.ret_24h > 0 for c in baseline]),
        },
        "share_near_7d_high(>=0.8)": {
            "shorts": _share([c.position_7d >= 0.8 for c in shorts]),
            "longs": _share([c.position_7d >= 0.8 for c in longs]),
            "baseline": _share([c.position_7d >= 0.8 for c in baseline]),
        },
        "share_funding_positive": {
            "shorts": _share([c.funding is not None and c.funding > 0 for c in shorts]),
            "longs": _share([c.funding is not None and c.funding > 0 for c in longs]),
            "baseline": _share([c.funding is not None and c.funding > 0 for c in baseline]),
        },
        "median_prior_24h_return": {
            "shorts": _median([c.ret_24h for c in shorts]),
            "longs": _median([c.ret_24h for c in longs]),
            "baseline": _median([c.ret_24h for c in baseline]),
        },
        "net_bps_by_fade": _bucket_returns(pairs, fade),
        "net_bps_by_7d_extreme": _bucket_returns(pairs, extreme),
        "losers_2020_2021": {
            "n": len(excursions),
            "median_adverse_pct": _median([e * 100 for e, _ in excursions]),
            "share_adverse_under_0.3pct": _share([e < 0.003 for e, _ in excursions]),
            "share_closed_within_1h_of_worst": _share([h <= 1 for _, h in excursions]),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
