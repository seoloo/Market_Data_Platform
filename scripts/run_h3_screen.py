#!/usr/bin/env python
"""H3 교차 심볼 선별 (docs/phase0/23-h3-preregistration.md).

전략 코드를 만들지 않는다. 사전 등록한 신호 5종을 시점별 유니버스에 적용해
롱숏 포트폴리오의 조건부 수익만 잰다.

아래 상수는 23 에 **결과를 보기 전에** 고정한 값이다. 여기서 낮추지 않는다.

사용 예::

    python scripts/run_h3_screen.py --from 2023-01 --to 2024-12   # TRAIN
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

from auto_trading.backtest.crosssection import PanelObservation, evaluate_panel  # noqa: E402
from auto_trading.backtest.signals_h3 import (  # noqa: E402
    SIGNAL_NAMES,
    Metrics,
    Series,
    build_cross_signals,
)
from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.hashing import sha256_hex  # noqa: E402

# --- 23 §3·§5 에 사전 등록한 값 ---
REQUIRED_EDGE_BPS = 22.0
"""알트 보수 기준. 18bps(BTC)도 함께 보고하되 판정은 이 값으로 한다."""

REFERENCE_EDGE_BPS = 18.0

MIN_SAMPLES = 200
"""최소 독립 표본.

23 §5 는 "검정력 계산에서 나온 필요 표본 이상" 이라고만 적었다. 그 요구는 사실
``평균 − 2SE > 기준`` 검사가 이미 강제한다 — 표본이 적으면 SE 가 커져 통과하지
못한다. 그래서 이 값의 역할은 **표본이 극히 적을 때의 우연**을 막는 것이고,
H2 때와 같은 200 으로 둔다 (t 분포가 정규에 충분히 가까워지는 수준).

⚠ 이 해석은 **결과를 보기 전에** 고정했다.
"""
LEG = 3
BUCKETS = 5
HORIZONS = {"2h": 24, "4h": 48, "6h": 72}
"""5분봉 개수."""

SIGNALS = SIGNAL_NAMES

STEP_MS = 300_000

SAMPLE_BARS = 12
"""단면을 만드는 간격 (5분봉 개수). 12 = 1시간.

5분마다 만들면 44개월 × 15조합 = 1.7억 회 반복인데, embargo 가 최소 2시간이라
그중 24분의 23 은 ``evaluate_panel`` 이 곧바로 버린다. 1시간 간격이면 가장 짧은
horizon(2h) 에도 여유가 두 배라 표본이 줄지 않으면서 계산이 12배 싸진다.
"""

UNIVERSE = REPO_ROOT / "reports/phase4/universe.json"


def month_of(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, tz=UTC).strftime("%Y-%m")


def load_symbol(
    conn: sqlite3.Connection, symbol: str, exchange: str
) -> tuple[Series, Metrics, Series]:
    """한 심볼의 5분봉 종가와 지표를 시각으로 맞춰 돌려준다."""
    closes = {
        int(r[0]): float(r[1])
        for r in conn.execute(
            "SELECT open_ms, close FROM candles"
            " WHERE exchange=? AND symbol=? AND timeframe='5m' ORDER BY open_ms",
            (exchange, symbol),
        )
    }
    metrics = {
        int(r[0]): (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        for r in conn.execute(
            "SELECT at_ms, open_interest, taker_buy_sell_ratio,"
            " account_long_short_ratio, toptrader_position_ratio"
            " FROM perp_metrics WHERE exchange=? AND symbol=? ORDER BY at_ms",
            (exchange, symbol),
        )
    }
    funding = {
        int(r[0]): float(r[1])
        for r in conn.execute(
            "SELECT funding_ms, rate FROM funding_rates WHERE exchange=? AND symbol=?"
            " ORDER BY funding_ms",
            (exchange, symbol),
        )
    }
    return closes, metrics, funding


def _month_start_ms(year: int, month: int) -> int:
    while month > 12:
        year, month = year + 1, month - 12
    while month < 1:
        year, month = year - 1, month + 12
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


def month_window(month: str) -> tuple[int, int, int]:
    """``(적재 시작, 관측 시작, 관측 끝)`` 밀리초.

    적재는 **직전 달부터** — causal z-score 의 7일 warm-up 이 필요하다.
    관측은 그 달 안에서만 만들되, 선행수익이 다음 달로 넘어가므로 적재는 **다음 달까지** 한다.
    """
    year, mon = (int(part) for part in month.split("-"))
    return (
        _month_start_ms(year, mon - 1),
        _month_start_ms(year, mon),
        _month_start_ms(year, mon + 1),
    )


def load_window(
    conn: sqlite3.Connection, symbol: str, exchange: str, from_ms: int, to_ms: int
) -> tuple[Series, Metrics, Series]:
    """한 심볼의 구간 데이터만 읽는다.

    심볼 전체를 읽으면 272개를 합쳐 수 GB 가 된다 (같은 실수를 수집기에서 이미 했다,
    marketdata/bulk.py 참조). 달 단위로 끊어 읽고 달이 끝나면 버린다.
    """
    closes = {
        int(r[0]): float(r[1])
        for r in conn.execute(
            "SELECT open_ms, close FROM candles WHERE exchange=? AND symbol=?"
            " AND timeframe='5m' AND open_ms >= ? AND open_ms < ? ORDER BY open_ms",
            (exchange, symbol, from_ms, to_ms),
        )
    }
    metrics = {
        int(r[0]): (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        for r in conn.execute(
            "SELECT at_ms, open_interest, taker_buy_sell_ratio,"
            " account_long_short_ratio, toptrader_position_ratio FROM perp_metrics"
            " WHERE exchange=? AND symbol=? AND at_ms >= ? AND at_ms < ? ORDER BY at_ms",
            (exchange, symbol, from_ms, to_ms),
        )
    }
    funding = {
        int(r[0]): float(r[1])
        for r in conn.execute(
            "SELECT funding_ms, rate FROM funding_rates WHERE exchange=? AND symbol=?"
            " AND funding_ms >= ? AND funding_ms < ? ORDER BY funding_ms",
            (exchange, symbol, from_ms - 40 * 86_400_000, to_ms),
        )
    }
    return closes, metrics, funding


SOURCE_SIGNALS = {
    "candles": ("X5_rel_reversal",),
    "perp_metrics": ("X2_rel_oi_change", "X3_rel_taker", "X4_rel_crowd_gap"),
    "funding_rates": ("X1_rel_funding",),
}
"""각 테이블이 없으면 **어느 신호가 측정 불가능해지는지**."""

MIN_COVERAGE = 0.90
"""이 비율 미만이면 선별을 시작하지 않는다.

2026-09-22 에 ``funding_rates`` 가 BTCUSDT 하나뿐인 것을 발견했다. 그대로 돌렸다면
X1 은 표본 0으로 "실패" 했을 것이고, 5개 전부 실패 → H3 기각 (23 §6) 으로 이어졌다.
**없는 데이터를 없는 엣지로 읽는 것**이 이 프로젝트에서 가장 비싼 실수다.
그래서 측정 전에 멈춘다.
"""


def coverage(conn: sqlite3.Connection, exchange: str, symbols: set[str]) -> dict[str, float]:
    """테이블별로 유니버스 심볼 중 데이터가 있는 비율."""
    out: dict[str, float] = {}
    for table in SOURCE_SIGNALS:
        present = {
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT symbol FROM {table} WHERE exchange=?",
                (exchange,),
            )
        }
        out[table] = len(present & symbols) / len(symbols) if symbols else 0.0
    return out


def require_coverage(found: dict[str, float]) -> None:
    """부족하면 측정하지 않고 멈춘다."""
    short = {t: v for t, v in found.items() if v < MIN_COVERAGE}
    for table, ratio in sorted(found.items()):
        mark = "OK " if ratio >= MIN_COVERAGE else "부족"
        print(f"  {mark} {table:<14} {ratio:5.1%}  → {', '.join(SOURCE_SIGNALS[table])}")
    if not short:
        return
    blocked = sorted({s for t in short for s in SOURCE_SIGNALS[t]})
    raise SystemExit(
        f"\n[중단] 데이터가 부족해 {', '.join(blocked)} 을(를) 측정할 수 없다.\n"
        f"       이 상태로 돌리면 '엣지 없음' 이 아니라 '데이터 없음' 을 기각으로 읽게 된다.\n"
        f"       먼저 수집을 끝내라: scripts/backfill_universe_*.py"
    )


def build_panels(
    from_month: str, to_month: str
) -> tuple[dict[str, dict[str, list[PanelObservation]]], dict[str, float]]:
    """달 단위로 읽어 단면을 만든다. 달이 끝나면 그 달 데이터는 버린다."""
    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    universe = {
        month: names
        for month, names in json.loads(UNIVERSE.read_text(encoding="utf-8")).items()
        if from_month <= month <= to_month
    }
    panels: dict[str, dict[str, list[PanelObservation]]] = {
        name: {h: [] for h in HORIZONS} for name in SIGNALS
    }
    every_symbol = {symbol for names in universe.values() for symbol in names}
    print(f"데이터 점검 (유니버스 {len(every_symbol)}심볼):")
    found = coverage(conn, config.exchange, every_symbol)
    require_coverage(found)
    print(f"\n유니버스 {len(universe)}개월 처리 중...")
    for index, (month, members) in enumerate(sorted(universe.items()), 1):
        load_from, month_from, month_to = month_window(month)
        year, mon = (int(part) for part in month.split("-"))
        load_to = _month_start_ms(year, mon + 2)  # 다음 달까지 실어 월말 선행수익을 채운다
        signals: dict[str, dict[str, Series]] = {name: {} for name in SIGNALS}
        closes_by_symbol: dict[str, Series] = {}
        for symbol in members:
            closes, metrics, funding = load_window(
                conn, symbol, config.exchange, load_from, load_to
            )
            if not metrics or not closes:
                continue
            closes_by_symbol[symbol] = closes
            for name, values_by_time in build_cross_signals(closes, metrics, funding).items():
                signals[name][symbol] = values_by_time

        times = sorted(
            {t for by_time in signals["X4_rel_crowd_gap"].values() for t in by_time}
            | {t for by_time in signals["X5_rel_reversal"].values() for t in by_time}
        )
        sampled = [
            at_ms
            for at_ms in times
            if month_from <= at_ms < month_to  # warm-up 시각은 관측으로 쓰지 않는다
            and (at_ms // STEP_MS) % SAMPLE_BARS == 0
        ]
        for at_ms in sampled:
            # 선행수익은 신호와 무관하므로 horizon 당 한 번만 계산한다.
            forwards: dict[str, dict[str, float]] = {}
            for horizon, bars in HORIZONS.items():
                row: dict[str, float] = {}
                for symbol in members:
                    prices = closes_by_symbol.get(symbol)
                    if prices is None:
                        continue
                    entry = prices.get(at_ms + STEP_MS)
                    exit_ = prices.get(at_ms + STEP_MS + bars * STEP_MS)
                    if entry and exit_:
                        row[symbol] = (exit_ - entry) / entry * 10_000
                forwards[horizon] = row

            for name in SIGNALS:
                by_symbol = signals[name]
                for horizon in HORIZONS:
                    row = forwards[horizon]
                    values = {
                        symbol: value
                        for symbol in members
                        if (value := by_symbol.get(symbol, {}).get(at_ms)) is not None
                        and symbol in row
                    }
                    if len(values) >= 2 * LEG:
                        panels[name][horizon].append(
                            PanelObservation(
                                at_ms=at_ms,
                                values=values,
                                forward_bps={s: row[s] for s in values},
                            )
                        )
        if index % 6 == 0:
            counts = len(panels["X4_rel_crowd_gap"]["6h"])
            print(f"  {index}/{len(universe)} ({month}) · 단면 {counts:,}", flush=True)
    return panels, found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_month", default="2023-01")
    parser.add_argument("--to", dest="to_month", default="2024-12")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/phase4/h3_screen.json")
    args = parser.parse_args()

    panels, found = build_panels(args.from_month, args.to_month)
    print(f"\n구간 {args.from_month} ~ {args.to_month}")
    print(
        f"판정 기준: 평균 − 2SE > {REQUIRED_EDGE_BPS}bps, n >= {MIN_SAMPLES}"
        f" (참고 기준 {REFERENCE_EDGE_BPS}bps)"
    )
    print(f"롱 {LEG} / 숏 {LEG} · embargo = horizon\n")

    payload: dict[str, object] = {
        "required_edge_bps": REQUIRED_EDGE_BPS,
        "min_samples": MIN_SAMPLES,
        "coverage": found,
        "signals": {},
    }
    any_pass = False
    for name in SIGNALS:
        print(f"── {name}")
        rows = []
        for horizon, bars in HORIZONS.items():
            result = evaluate_panel(
                name,
                horizon,
                panels[name][horizon],
                embargo_ms=bars * STEP_MS,
                leg=LEG,
                buckets=BUCKETS,
            )
            passed = result.passes(REQUIRED_EDGE_BPS, MIN_SAMPLES)
            any_pass = any_pass or passed
            shape = " ".join(f"{v:+6.1f}" for v in result.quantile_means_bps)
            print(
                f"   {horizon:>3} n={result.sample_count:<6}"
                f" 롱숏 {result.mean_bps:+7.2f}±{result.stderr_bps:5.2f}"
                f"  하한 {result.lower_bound_bps:+7.2f}"
                f"  양수 {result.positive_rate:5.1%}"
                f"  {'PASS' if passed else '    '}"
            )
            print(
                f"       5분위 {shape}  단조 {'O' if result.is_monotone else 'X'}"
                f"  연도 {'일관' if result.is_year_consistent else '불일치'}"
                f" { ({y: round(v, 1) for y, v in result.yearly_means_bps.items()}) }"
            )
            rows.append(
                {
                    "horizon": horizon,
                    "n": result.sample_count,
                    "mean_bps": result.mean_bps,
                    "stderr_bps": result.stderr_bps,
                    "lower_bound_bps": result.lower_bound_bps,
                    "positive_rate": result.positive_rate,
                    "quantiles": list(result.quantile_means_bps),
                    "monotone": result.is_monotone,
                    "year_consistent": result.is_year_consistent,
                    "yearly": {str(y): v for y, v in result.yearly_means_bps.items()},
                    "passes": passed,
                }
            )
        payload["signals"][name] = rows  # type: ignore[index]
        print()

    verdict = "1개 이상 통과" if any_pass else "전부 실패 — H3 기각 (23 §6)"
    print(f"판정: {verdict}")
    payload["verdict"] = verdict
    payload["digest"] = sha256_hex(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
