"""문서 62 — EW-4n 을 시간 6 배(일봉 꼭짓점·6h 하위 다리)로 돌려 BTC 대 뒤섞기 대조를 센다.

사용::

    python scripts/audit_ew62_daily.py btc
    python scripts/audit_ew62_daily.py null 1      # 난수 "ew62-null-1", 1m 을 42 일 안에서 뒤섞음

실제 6 분봉을 판독기의 1m 으로, 타임스탬프를 6 으로 나눠 붙인다(일 경계 = 4h 경계).
꼭짓점 임계 2% × √6.
DB 는 읽기 전용, 수익·방향 미조회.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew4_market import H1, _shuffled_minutes  # noqa: E402
from scripts.audit_ew4n_market import count_nested, report  # noqa: E402
from scripts.audit_ew_edge_power import H4, _aggregate, _btc_bars, _btc_minutes  # noqa: E402

from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402

SCALE = 6
THRESHOLD = (EW0_THRESHOLD_PCT * Decimal(SCALE).sqrt()).quantize(Decimal("0.0001"))
"""2% × √6 = 4.90% (문서 62 §2)."""
BLOCK_MS = 42 * 86_400_000
_BUCKET_MS = SCALE * 60_000


def _bar(bucket: list[Candle]) -> Candle:
    start = bucket[0].open_ms // _BUCKET_MS * _BUCKET_MS // SCALE
    return Candle(
        open_ms=start,
        close_ms=start + 59_999,
        open=bucket[0].open,
        high=max(c.high for c in bucket),
        low=min(c.low for c in bucket),
        close=bucket[-1].close,
        volume=sum((c.volume for c in bucket), start=Decimal(0)),
    )


def scaled(minutes: Iterator[Candle]) -> Iterator[Candle]:
    """실제 6 분 → 판독기 1 분 (타임스탬프 ÷ 6)."""
    bucket: list[Candle] = []
    for m in minutes:
        if bucket and m.open_ms // _BUCKET_MS != bucket[0].open_ms // _BUCKET_MS:
            yield _bar(bucket)
            bucket = []
        bucket.append(m)
    if bucket:
        yield _bar(bucket)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("btc", "null") or (argv[0] == "null" and len(argv) < 2):
        raise SystemExit("usage: audit_ew62_daily.py btc | null <1|2|3>")
    _, _, to_ms = _btc_bars()
    source = "BTCUSDT" if argv[0] == "btc" else f"ew62-null-{argv[1]}"
    raw: Callable[[], Iterator[Candle]] = (
        (lambda: _btc_minutes(to_ms))
        if argv[0] == "btc"
        else (lambda: _shuffled_minutes(to_ms, source, BLOCK_MS))
    )
    four = tuple(_aggregate(scaled(raw()), H4))
    hours = tuple(_aggregate(scaled(raw()), H1))
    count, per_year = count_nested(
        four, hours, scaled(raw()), threshold=THRESHOLD, time_scale=SCALE
    )
    report(f"{source} x{SCALE} thr={THRESHOLD}", count, per_year)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
