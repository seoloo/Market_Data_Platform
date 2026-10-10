"""업비트 원화 일봉 전체 받기 (공개 API · 키 없음) — 문서 197 Cruise 기대 범위용.

사용 예::

    python -m scripts.fetch_upbit_daily   # data/research/upbit_daily/KRW-<코인>.csv

* ``/v1/candles/days`` 를 200 개씩 ``to`` 로 거슬러 받는다(초당 몇 번 이하로 쉼).
* 칸: date(UTC 날짜 = KST 09:00 마감) · open · high · low · close · volume.
  이미 받은 파일은 덮어쓴다.
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "upbit_daily"
API = "https://api.upbit.com/v1/candles/days"
MARKETS = ("KRW-BTC", "KRW-ETH", "KRW-SOL")


def fetch(client: httpx.Client, market: str) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    to: str | None = None
    while True:
        params = {"market": market, "count": "200"}
        if to:
            params["to"] = to
        got = client.get(API, params=params)
        got.raise_for_status()
        batch = got.json()
        if not batch:
            break
        for r in batch:
            d = str(r["candle_date_time_utc"])[:10]
            rows[d] = {
                "date": d,
                "open": str(r["opening_price"]),
                "high": str(r["high_price"]),
                "low": str(r["low_price"]),
                "close": str(r["trade_price"]),
                "volume": str(r["candle_acc_trade_volume"]),
            }
        oldest = min(str(r["candle_date_time_utc"]) for r in batch)
        if len(batch) < 200 or oldest.replace("T", " ") == to:
            break
        to = oldest.replace("T", " ")
        time.sleep(0.15)
    return [rows[d] for d in sorted(rows)]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20.0) as client:
        for m in MARKETS:
            rows = fetch(client, m)
            with (OUT / f"{m}.csv").open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            sys.stdout.write(f"{m}: {len(rows)} 일 {rows[0]['date']} ~ {rows[-1]['date']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
