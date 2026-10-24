#!/usr/bin/env python
"""BTCUSDT 단일 심볼의 잡음 바닥 측정 — 후보 검정력 판정의 입력.

원본 문서 BACKTEST_AND_TESTING 의 검정력 게이트는 SD 를 요구한다
(그 문서는 2026-10-24 저장소에서 뺐다).
추정치로 게이트를 통과시키면 게이트가 없는 것과 같으므로 여기서 실측한다.

**이 스크립트는 평균을 출력하지 않는다.** 읽는 값은 표준편차와 표본 수뿐이다.
사전등록 전에 효과 크기를 보면 그 뒤의 모든 판정이 오염된다
(archive/research/22 §4 가 무작위 배정으로 잡음만 잰 것과 같은 이유다).

두 가지를 잰다.

1. **무조건 SD** — 아무 때나 진입했을 때의 horizon 별 수익률 산포.
2. **변동성 수축 조건부 SD** — 직전 실현변동성이 하위 분위일 때만 진입했을 때.
   수축 국면의 진입이 잡음을 실제로 줄이는지가 후보 1의 통계적 전제다.
   줄지 않으면 그 후보는 검정력 문제를 해결하지 못한다.

표본은 **중첩을 제거**한다 (embargo = horizon). 겹친 구간은 독립 표본이 아니다.

사용 예::

    python scripts/measure_noise_floor.py
    python scripts/measure_noise_floor.py --from 2021 --to 2024
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

from auto_trading.backtest.power import DEFAULT_POWER, detectable_effect_bps  # noqa: E402
from auto_trading.config.loader import load_config  # noqa: E402

BARS_PER_HOUR = 12
HORIZON_HOURS = (2, 4, 6, 12, 24, 48, 72)

VOL_WINDOW_BARS = 24 * BARS_PER_HOUR
"""실현변동성을 재는 창 (24시간). 이보다 짧으면 5분 잡음이 지배한다."""

RANK_WINDOW_BARS = 30 * 24 * BARS_PER_HOUR
"""변동성의 순위를 매기는 causal 창 (30일).

절대 임계값을 쓰지 않는 이유: BTC 의 변동성 수준은 연도별로 수 배 차이가 난다.
2021 의 '조용함' 과 2024 의 '조용함' 은 다른 값이다.
"""

CONTRACTION_QUANTILE = 0.30
"""이 분위 이하를 '수축' 으로 본다. 사전등록이 아니라 측정용 관찰값이다."""

COST_BPS = 18.0
"""BTC 왕복 비용 (수수료 13 + 마진 5). 증명 가능 효과를 이 위에서 읽는다."""

MS_PER_YEAR = 365.25 * 24 * 3_600_000


def load_closes(
    conn: sqlite3.Connection, exchange: str, from_year: int, to_year: int
) -> tuple[list[int], list[float]]:
    start = int(datetime(from_year, 1, 1, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(to_year + 1, 1, 1, tzinfo=UTC).timestamp() * 1000)
    rows = conn.execute(
        "SELECT open_ms, close FROM candles"
        " WHERE exchange=? AND symbol='BTCUSDT' AND timeframe='5m'"
        " AND open_ms >= ? AND open_ms < ? ORDER BY open_ms",
        (exchange, start, end),
    ).fetchall()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def realized_vol(closes: list[float]) -> list[float | None]:
    """직전 ``VOL_WINDOW_BARS`` 의 5분 수익률 표준편차. causal."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= VOL_WINDOW_BARS:
        return out
    steps = [0.0] * len(closes)
    for i in range(1, len(closes)):
        before = closes[i - 1]
        steps[i] = (closes[i] - before) / before if before > 0.0 else 0.0
    window = steps[1 : VOL_WINDOW_BARS + 1]
    total = sum(window)
    total_sq = sum(v * v for v in window)
    n = VOL_WINDOW_BARS
    for i in range(VOL_WINDOW_BARS, len(closes)):
        variance = max(total_sq / n - (total / n) ** 2, 0.0)
        out[i] = variance**0.5
        if i + 1 < len(closes):
            leaving, entering = steps[i - n + 1], steps[i + 1]
            total += entering - leaving
            total_sq += entering * entering - leaving * leaving
    return out


def contraction_flags(vol: list[float | None]) -> list[bool]:
    """각 시점의 변동성이 직전 30일 분포의 하위 분위인가. causal."""
    flags = [False] * len(vol)
    history: list[float] = []
    for i, value in enumerate(vol):
        if value is None:
            continue
        if len(history) >= RANK_WINDOW_BARS:
            recent = history[-RANK_WINDOW_BARS:]
            below = sum(1 for v in recent if v < value)
            flags[i] = below / len(recent) <= CONTRACTION_QUANTILE
        history.append(value)
    return flags


def sample_sd(
    closes: list[float], horizon_bars: int, *, only_where: list[bool] | None = None
) -> tuple[float, int]:
    """중첩 없는 선행수익의 SD 와 표본 수. **평균은 돌려주지 않는다.**"""
    values: list[float] = []
    index = VOL_WINDOW_BARS + RANK_WINDOW_BARS if only_where is not None else 0
    limit = len(closes) - horizon_bars
    while index < limit:
        if only_where is not None and not only_where[index]:
            index += 1
            continue
        entry, exit_ = closes[index], closes[index + horizon_bars]
        if entry > 0.0 and exit_ > 0.0:
            values.append((exit_ - entry) / entry * 10_000)
        index += horizon_bars  # embargo = horizon
    if len(values) < 2:
        return 0.0, len(values)
    return statistics.stdev(values), len(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_year", type=int, default=2020)
    parser.add_argument("--to", dest="to_year", type=int, default=2026)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "reports/phase0/btc_noise_floor.json"
    )
    args = parser.parse_args()

    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    times, closes = load_closes(conn, config.exchange, args.from_year, args.to_year)
    if not closes:
        print("5분봉이 없다.")
        return 1
    years = (times[-1] - times[0]) / MS_PER_YEAR
    print(f"BTCUSDT 5분봉 {len(closes):,}개 · {args.from_year}~{args.to_year} ({years:.1f}년)")
    print(f"비용 {COST_BPS}bps · 검정력 {DEFAULT_POWER:.0%} 기준\n")
    print("측정값은 SD 와 표본 수뿐이다. 평균(효과)은 읽지 않는다.\n")

    flags = contraction_flags(realized_vol(closes))
    share = sum(flags) / len(flags)
    print(f"변동성 수축 국면 비중: {share:.1%} (하위 {CONTRACTION_QUANTILE:.0%} 분위 기준)\n")

    header = (
        f"{'horizon':>8} | {'SD':>7} {'표본':>7} {'연간':>6} {'증명가능':>8}"
        f" | {'수축 SD':>8} {'표본':>6} {'증명가능':>8}"
    )
    print(header)
    print("-" * len(header))

    rows = []
    for hours in HORIZON_HOURS:
        bars = hours * BARS_PER_HOUR
        sd, n = sample_sd(closes, bars)
        sd_c, n_c = sample_sd(closes, bars, only_where=flags)
        edge = detectable_effect_bps(sd, n, COST_BPS) if n > 1 else float("nan")
        edge_c = detectable_effect_bps(sd_c, n_c, COST_BPS) if n_c > 1 else float("nan")
        print(
            f"{hours:>6}h  | {sd:7.1f} {n:7,} {n / years:6.0f} {edge:7.1f}b"
            f" | {sd_c:8.1f} {n_c:6,} {edge_c:7.1f}b"
        )
        rows.append(
            {
                "horizon_hours": hours,
                "sd_bps": sd,
                "samples": n,
                "samples_per_year": n / years,
                "detectable_effect_bps": edge,
                "contracted_sd_bps": sd_c,
                "contracted_samples": n_c,
                "contracted_detectable_effect_bps": edge_c,
            }
        )

    print("\n'증명가능' = 이 표본으로 80% 검정력에서 증명할 수 있는 최소 효과.")
    print("이 값보다 작은 효과를 노리는 후보는 사전등록하지 않는다.")

    payload = {
        "symbol": "BTCUSDT",
        "from_year": args.from_year,
        "to_year": args.to_year,
        "years": years,
        "cost_bps": COST_BPS,
        "power": DEFAULT_POWER,
        "contraction_quantile": CONTRACTION_QUANTILE,
        "contraction_share": share,
        "horizons": rows,
        "note": "SD 와 표본 수만 측정한다. 평균(효과)은 의도적으로 계산하지 않는다.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n기록: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
