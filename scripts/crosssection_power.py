#!/usr/bin/env python
"""교차 심볼 포트폴리오의 **잡음 바닥**을 재고 검정력을 계산한다.

지표를 하나도 만들지 않는다. 신호 없이 "이 설정에서 얼마짜리 효과여야 잡히는가" 만
먼저 답한다. H1·H2 는 이 계산을 나중에 해서 답이 없는 질문에 몇 주를 썼다
(docs/phase0/18-h2-validation-result.md §2).

핵심 비교:

    단일 심볼 방향성   : "BTC 가 오를까"          → 시장 전체 움직임이 그대로 잡음
    교차 심볼 롱숏     : "어느 게 상대적으로 셀까" → 시장 방향이 상쇄됨

두 번째의 표준편차가 얼마나 작은지가 판정 가능성을 정한다.
무작위 순위로 포트폴리오를 만들어 재므로, **여기서 나오는 수익률은 0 근처여야 정상**이다.
읽어야 할 것은 평균이 아니라 **표준편차**다.

사용 예::

    python scripts/crosssection_power.py --from 2023-01-01 --to 2026-09-20
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import statistics as stats
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zlib import crc32

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

from auto_trading.config.loader import load_config  # noqa: E402
from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.storage.repositories.candles import CandleRepository  # noqa: E402

HORIZONS = {"2h": 24, "4h": 48, "6h": 72}
"""5분봉 개수 기준."""

COST_BPS = 18.0
"""BTC 기준 왕복 13bps + 안전마진 5bps.

롱숏 포트폴리오도 **자본 대비**로는 같다 — 각 포지션이 자기 명목가에 대해 비용을 내고,
명목가 합이 자본과 같기 때문이다.

⚠ 알트는 스프레드가 넓다 (2026-09-22 실측: 5분 레인지가 BTC 대비 최대 2.1배).
그래서 `--cost` 로 보수적인 값도 함께 본다. 비용을 낙관하는 것이 이 프로젝트에서
두 번 문제가 됐다 (20-signal-time-fills.md).
"""

LEG = 3
"""한쪽 다리에 담을 심볼 수. 상위 LEG 롱 + 하위 LEG 숏."""


def to_ms(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)


def load_closes(
    repo: CandleRepository, symbols: list[str], from_ms: int, to_ms_: int
) -> dict[str, dict[int, float]]:
    closes: dict[str, dict[int, float]] = {}
    for symbol in symbols:
        bars = repo.get_range(symbol, Timeframe.M5, from_ms=from_ms, to_ms=to_ms_)
        if len(bars) < 1000:
            continue
        closes[symbol] = {c.open_ms: float(c.close) for c in bars}
    return closes


def analyse(
    closes: dict[str, dict[int, float]], step_bars: int, label: str, costs: list[float]
) -> None:
    """무작위 순위로 롱숏 포트폴리오를 굴려 잡음 바닥을 잰다."""
    symbols = sorted(closes)
    common = sorted(set.intersection(*(set(closes[s]) for s in symbols)))
    step_ms = step_bars * Timeframe.M5.ms
    single: list[float] = []
    spread: list[float] = []
    dispersion: list[float] = []
    seed = 20260922

    for at_ms in common[::step_bars]:
        later = at_ms + step_ms
        rets = []
        for symbol in symbols:
            a, b = closes[symbol].get(at_ms), closes[symbol].get(later)
            if a and b:
                rets.append((symbol, (b - a) / a * 10_000))
        if len(rets) < 2 * LEG:
            continue
        values = [r for _, r in rets]
        # "심볼 하나에 건다" 의 잡음은 특정 심볼이 아니라 평균적인 심볼로 본다.
        single.extend(values)
        dispersion.append(stats.stdev(values))
        # 결정론적 유사난수로 순위를 섞는다 — 신호가 아니라 잡음을 재는 것이므로.
        # 내장 hash() 는 실행마다 값이 달라져 재현이 깨진다 (PYTHONHASHSEED).
        seed = (1103515245 * seed + 12345) % (2**31)
        order = sorted(rets, key=lambda item: (seed ^ crc32(item[0].encode())) % 9973)
        longs = [r for _, r in order[:LEG]]
        shorts = [r for _, r in order[-LEG:]]
        spread.append((stats.mean(longs) - stats.mean(shorts)) / 2)

    if len(spread) < 30:
        print(f"  {label:<4} 표본 부족")
        return

    single_sd = stats.stdev(single)
    spread_sd = stats.stdev(spread)
    print(
        f"  {label:<4} n={len(spread):<6}"
        f" 단일심볼 SD {single_sd:7.1f}bps"
        f"   롱숏 SD {spread_sd:7.1f}bps"
        f"   축소 {single_sd / spread_sd:4.1f}배"
        f"   심볼간 분산 {stats.mean(dispersion):6.1f}bps"
    )
    _requirements(spread_sd, len(spread), costs)


def _requirements(sd: float, available: int, costs: list[float]) -> None:
    """효과 크기별 필요 표본. **효과가 비용을 넘지 못하면 데이터가 아무리 많아도 소용없다.**"""
    for cost in costs:
        cells = []
        for effect in (25.0, 30.0, 40.0, 60.0):
            margin = effect - cost
            if margin <= 0:
                cells.append(f"{effect:.0f}bps→비용미달")
                continue
            need = math.ceil((2 * sd / margin) ** 2)
            cells.append(f"{effect:.0f}bps→{need:,}{'O' if need <= available else 'X'}")
        print(f"       비용 {cost:>4.0f}bps: " + "  ".join(cells))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from", dest="from_date", type=date.fromisoformat, default=date(2023, 1, 1)
    )
    parser.add_argument("--to", dest="to_date", type=date.fromisoformat, default=date(2026, 9, 20))
    parser.add_argument(
        "--cost",
        type=float,
        nargs="+",
        default=[COST_BPS, 22.0],
        help="비용 기준(bps). 기본은 BTC 18 과 알트 보수 22 를 함께 본다.",
    )
    args = parser.parse_args()

    config = load_config().config
    conn = sqlite3.connect(f"file:{REPO_ROOT / config.data.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    repo = CandleRepository(conn, exchange=config.exchange)
    known = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT symbol FROM candles WHERE timeframe = '5m' ORDER BY symbol"
        )
    ]
    closes = load_closes(repo, known, to_ms(args.from_date), to_ms(args.to_date))
    if len(closes) < 2 * LEG:
        print(f"심볼이 부족하다: {len(closes)}개 (최소 {2 * LEG}개 필요)")
        return 1

    print(f"구간 {args.from_date} ~ {args.to_date} · 심볼 {len(closes)}개")
    print(f"롱 {LEG} / 숏 {LEG} 무작위 배정 · 비용 기준 {COST_BPS}bps\n")
    print("O = 가용 표본으로 판정 가능 · X = 불가")
    for label, bars in HORIZONS.items():
        analyse(closes, bars, label, args.cost)

    print(
        "\n참고: 무작위 순위이므로 평균 수익은 0 근처가 정상이다."
        "\n      읽을 것은 **롱숏 SD** 와 그로부터 나온 필요 표본이다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
