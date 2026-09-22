#!/usr/bin/env python
"""H2 사전 등록 선별 실행 (docs/phase0/16-h2-proposal.md §4).

전략 코드를 만들지 않는다. 포지셔닝 신호 5종의 **조건부 선행수익**만 잰다.
H1 은 파이프라인을 다 만든 뒤에야 edge 가 없음을 알았다 — 그 순서를 뒤집는 도구다.

사용 예::

    python scripts/run_h2_screen.py --years 2021 2023
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

from auto_trading.backtest.screen import SignalPoint, SignalSeries, screen_signal  # noqa: E402
from auto_trading.backtest.signals_h2 import align_inputs, build_signals  # noqa: E402
from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.hashing import sha256_hex  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402
from auto_trading.storage.repositories.funding import FundingRateRepository  # noqa: E402
from auto_trading.storage.repositories.metrics import PerpMetricsRepository  # noqa: E402

# --- 16 §4.3 에 사전 등록한 값. 여기서 낮추지 않는다. ---
REQUIRED_EDGE_BPS = 18.0
MIN_SAMPLES = 200
HORIZONS_MS = {"2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000}
DECILE = 0.10
STEP_MS = 300_000


def year_bounds(year: int) -> tuple[int, int]:
    start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1
    return start, end


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2021, 2023])
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/phase4/h2_screen.json")
    parser.add_argument(
        "--signals",
        nargs="+",
        default=None,
        help="특정 신호만 낸다. 재현 검정에서 다른 신호를 **보지 않기 위해** 쓴다 — "
        "보고 나서 고르면 사전 등록이 무의미해진다.",
    )
    args = parser.parse_args()

    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    candles_repo = CandleRepository(conn, exchange=config.exchange)
    metrics_repo = PerpMetricsRepository(conn, exchange=config.exchange)
    funding_repo = FundingRateRepository(conn, exchange=config.exchange)

    points: dict[str, list[SignalPoint]] = {}
    covered: list[str] = []
    max_horizon = max(HORIZONS_MS.values())

    for year in args.years:
        start, end = year_bounds(year)
        metrics = metrics_repo.get_range(config.symbol, start, end)
        if not metrics:
            print(f"{year}: metrics 없음 — 건너뜀")
            continue
        candles = candles_repo.get_range(
            config.symbol, Timeframe.M5, from_ms=start, to_ms=end + max_horizon + STEP_MS
        )
        funding = funding_repo.get_range(config.symbol, start - 40 * 86_400_000, end)
        inputs = align_inputs(metrics, candles, funding)
        closes = {c.open_ms: float(c.close) for c in candles}
        signals = build_signals(inputs)
        covered.append(f"{year}({len(inputs.at_ms):,})")

        for name, values in signals.items():
            if args.signals and name not in args.signals:
                continue
            bucket = points.setdefault(name, [])
            for index, at_ms in enumerate(inputs.at_ms):
                value = values[index]
                if value is None:
                    continue
                entry = closes.get(at_ms + STEP_MS)  # 다음 봉 시가 대신 종가 기준 (R-1 근사)
                if entry is None:
                    continue
                forward = {}
                for horizon, span in HORIZONS_MS.items():
                    exit_price = closes.get(at_ms + STEP_MS + span)
                    if exit_price is None:
                        break
                    forward[horizon] = (exit_price - entry) / entry * 10_000
                if len(forward) == len(HORIZONS_MS):
                    bucket.append(SignalPoint(at_ms=at_ms, value=value, forward_bps=forward))

    print(f"\n구간: {' + '.join(covered)}   (신호 계산 가능 시점 수)")
    print(f"기준: 평균-2SE > {REQUIRED_EDGE_BPS}bps, n >= {MIN_SAMPLES}, decile {DECILE:.0%}\n")

    payload: dict[str, object] = {
        "signals_requested": args.signals or "all",
        "required_edge_bps": REQUIRED_EDGE_BPS,
        "min_samples": MIN_SAMPLES,
        "horizons_ms": HORIZONS_MS,
        "years": args.years,
        "signals": {},
    }
    any_pass = False
    for name in sorted(points):
        series = SignalSeries(name=name, points=tuple(sorted(points[name], key=lambda p: p.at_ms)))
        result = screen_signal(series, horizons_ms=HORIZONS_MS, decile=DECILE)
        passing = result.passing(REQUIRED_EDGE_BPS, MIN_SAMPLES)
        any_pass = any_pass or bool(passing)
        print(f"── {name}  (관측 {result.total_points:,})")
        for outcome in result.outcomes:
            flag = "PASS" if outcome.passes(REQUIRED_EDGE_BPS, MIN_SAMPLES) else "    "
            print(
                f"   {outcome.horizon:>3} {outcome.bucket:<5} n={outcome.sample_count:<5}"
                f" 롱수익 {outcome.mean_bps:+8.2f}±{outcome.stderr_bps:5.2f}bps"
                f"  하한 {outcome.lower_bound_bps:+7.2f}  양수 {outcome.positive_rate:5.1%} {flag}"
            )
        for horizon in HORIZONS_MS:
            quints = result.quintile_means_bps.get(horizon, ())
            if quints:
                shape = " ".join(f"{v:+6.1f}" for v in quints)
                consistent = "일관" if result.is_year_consistent(horizon) else "불일치"
                print(
                    f"   {horizon:>3} 5분위 {shape}   연도 {consistent}"
                    f" {result.yearly_means_bps.get(horizon, {})}"
                )
        print()
        payload["signals"][name] = {  # type: ignore[index]
            "total_points": result.total_points,
            "outcomes": [
                {
                    "horizon": o.horizon,
                    "bucket": o.bucket,
                    "n": o.sample_count,
                    "mean_bps": o.mean_bps,
                    "stderr_bps": o.stderr_bps,
                    "positive_rate": o.positive_rate,
                    "passes": o.passes(REQUIRED_EDGE_BPS, MIN_SAMPLES),
                }
                for o in result.outcomes
            ],
            "quintiles": result.quintile_means_bps,
            "yearly": {
                h: {str(y): v for y, v in d.items()} for h, d in result.yearly_means_bps.items()
            },
        }

    verdict = "1개 이상 통과 — Phase 2 복귀 검토" if any_pass else "전부 실패 — H2 기각 (16 §4.4)"
    print(f"판정: {verdict}")
    payload["verdict"] = verdict
    payload["digest"] = sha256_hex(payload)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
