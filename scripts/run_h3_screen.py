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
import math
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
from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.hashing import sha256_hex  # noqa: E402
from auto_trading.features.normalize import causal_zscore_series  # noqa: E402

# --- 23 §3·§5 에 사전 등록한 값 ---
REQUIRED_EDGE_BPS = 22.0
"""알트 보수 기준. 18bps(BTC)도 함께 보고하되 판정은 이 값으로 한다."""

REFERENCE_EDGE_BPS = 18.0
LEG = 3
BUCKETS = 5
HORIZONS = {"2h": 24, "4h": 48, "6h": 72}
"""5분봉 개수."""

SIGNALS = (
    "X1_rel_funding",
    "X2_rel_oi_change",
    "X3_rel_taker",
    "X4_rel_crowd_gap",
    "X5_rel_reversal",
)

Z_LOOKBACK = 7 * 24 * 12
"""7일 causal 창 (5분 스냅샷 기준)."""

STEP_MS = 300_000
UNIVERSE = REPO_ROOT / "reports/phase4/universe.json"


def month_of(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, tz=UTC).strftime("%Y-%m")


Series = dict[int, float]
Metrics = dict[int, tuple[float, float, float, float]]


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


def signal_series(closes: Series, metrics: Metrics, funding: Series) -> dict[str, Series]:
    """심볼 하나의 신호 5종. warm-up 미충족 구간은 아예 넣지 않는다."""
    times = sorted(metrics)
    if len(times) < Z_LOOKBACK + 13:
        return {}
    oi = [metrics[t][0] for t in times]
    taker = [metrics[t][1] for t in times]
    crowd = [metrics[t][2] for t in times]
    top = [metrics[t][3] for t in times]

    oi_change = [0.0] * len(times)
    for i in range(12, len(times)):
        before, now = oi[i - 12], oi[i]
        if before > 0 and now > 0:
            oi_change[i] = math.log(now / before)

    taker_z = causal_zscore_series(taker, Z_LOOKBACK)
    crowd_z = causal_zscore_series(crowd, Z_LOOKBACK)
    top_z = causal_zscore_series(top, Z_LOOKBACK)
    oi_z = causal_zscore_series(oi_change, Z_LOOKBACK)

    funding_times = sorted(funding)
    out: dict[str, dict[int, float]] = {name: {} for name in SIGNALS}
    for i, at_ms in enumerate(times):
        # X1 — 직전에 정산된 funding (causal)
        idx = _last_before(funding_times, at_ms)
        if idx is not None:
            out["X1_rel_funding"][at_ms] = funding[funding_times[idx]]
        oi_value, taker_value = oi_z[i], taker_z[i]
        if oi_value is not None:
            out["X2_rel_oi_change"][at_ms] = oi_value
        if taker_value is not None:
            out["X3_rel_taker"][at_ms] = taker_value
        crowd_value, top_value = crowd_z[i], top_z[i]
        if crowd_value is not None and top_value is not None:
            out["X4_rel_crowd_gap"][at_ms] = crowd_value - top_value
        past_close = closes.get(at_ms - 72 * STEP_MS)
        last_close = closes.get(at_ms)
        if past_close and last_close:
            out["X5_rel_reversal"][at_ms] = (last_close - past_close) / past_close * 10_000
    return out


def _last_before(sorted_times: list[int], at_ms: int) -> int | None:
    lo, hi = 0, len(sorted_times)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_times[mid] <= at_ms:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1 if lo else None


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


def build_panels(from_month: str, to_month: str) -> dict[str, dict[str, list[PanelObservation]]]:
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
    print(f"유니버스 {len(universe)}개월 처리 중...")

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
            for name, values_by_time in signal_series(closes, metrics, funding).items():
                signals[name][symbol] = values_by_time

        times = sorted(
            {t for by_time in signals["X4_rel_crowd_gap"].values() for t in by_time}
            | {t for by_time in signals["X5_rel_reversal"].values() for t in by_time}
        )
        for at_ms in times:
            if not month_from <= at_ms < month_to:
                continue  # warm-up 구간의 시각은 관측으로 쓰지 않는다
            for name in SIGNALS:
                for horizon, bars in HORIZONS.items():
                    values: dict[str, float] = {}
                    forward: dict[str, float] = {}
                    for symbol in members:
                        value = signals[name].get(symbol, {}).get(at_ms)
                        if value is None:
                            continue
                        prices = closes_by_symbol.get(symbol)
                        if prices is None:
                            continue
                        entry = prices.get(at_ms + STEP_MS)
                        exit_ = prices.get(at_ms + STEP_MS + bars * STEP_MS)
                        if not entry or not exit_:
                            continue
                        values[symbol] = value
                        forward[symbol] = (exit_ - entry) / entry * 10_000
                    if len(values) >= 2 * LEG:
                        panels[name][horizon].append(
                            PanelObservation(at_ms=at_ms, values=values, forward_bps=forward)
                        )
        if index % 6 == 0:
            counts = len(panels["X4_rel_crowd_gap"]["6h"])
            print(f"  {index}/{len(universe)} ({month}) · 단면 {counts:,}", flush=True)
    return panels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_month", default="2023-01")
    parser.add_argument("--to", dest="to_month", default="2024-12")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/phase4/h3_screen.json")
    args = parser.parse_args()

    panels = build_panels(args.from_month, args.to_month)
    print(f"\n구간 {args.from_month} ~ {args.to_month}")
    print(f"판정 기준: 평균 − 2SE > {REQUIRED_EDGE_BPS}bps (참고 {REFERENCE_EDGE_BPS}bps)")
    print(f"롱 {LEG} / 숏 {LEG} · embargo = horizon\n")

    payload: dict[str, object] = {"required_edge_bps": REQUIRED_EDGE_BPS, "signals": {}}
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
            passed = result.passes(REQUIRED_EDGE_BPS, 1)
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
