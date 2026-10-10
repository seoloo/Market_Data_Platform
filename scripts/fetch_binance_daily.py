"""바이낸스 현물 일봉 받기 (공개 API · 키 없음) — 문서 200 김프 뺀 Cruise 용.

사용 예::

    python -m scripts.fetch_binance_daily   # data/research/binance_daily/<심볼>.csv

* ``/api/v3/klines`` interval=1d 를 1,000 개씩 앞으로 받는다.
  봉 시작 00:00 UTC → 업비트 일봉과 같은 날.
* 칸: date(UTC 봉 시작 날짜) · open · high · low · close · volume (USDT).
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "binance_daily"
API = "https://api.binance.com/api/v3/klines"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DAY_MS = 86_400_000


def fetch(client: httpx.Client, symbol: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    start = 0
    while True:
        got = client.get(
            API, params={"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000}
        )
        got.raise_for_status()
        batch = got.json()
        if not batch:
            break
        for k in batch:
            rows.append(
                {
                    "date": datetime.fromtimestamp(k[0] / 1000, UTC).strftime("%Y-%m-%d"),
                    "open": k[1],
                    "high": k[2],
                    "low": k[3],
                    "close": k[4],
                    "volume": k[5],
                }
            )
        if len(batch) < 1000:
            break
        start = int(batch[-1][0]) + DAY_MS
        time.sleep(0.2)
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20.0) as client:
        for s in SYMBOLS:
            rows = fetch(client, s)
            with (OUT / f"{s}.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            sys.stdout.write(f"{s}: {len(rows)} 일 {rows[0]['date']} ~ {rows[-1]['date']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
